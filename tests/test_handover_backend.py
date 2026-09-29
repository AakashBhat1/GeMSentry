"""Regression coverage for the current backend handoff batch."""

import datetime
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

import app
import paths
from gemsentry import cli, pipeline, storage
from gemsentry.analysis import analyze_rfp_pdf
from gemsentry.defaults import DEFAULT_COMPANY_PROFILE, DEFAULT_SCORING_CONFIG
from gemsentry.scoring.dates import evaluate_date_window
from gemsentry.scoring.verdict import compute_recommendation
from gemsentry.sources.gem import client as gem_client
from gemsentry.textutils import _match_indian_state
from gemsentry.web import context, tenders


@pytest.mark.parametrize("phrase, expected", [
    ("project goals and milestones", None),
    ("supply of goat feed", None),
    ("Goan port", None),
    ("cargo aircraft", None),
    ("Goa Shipyard", "Goa"),
    ("Uttar Pradesh and Andhra Pradesh", "Uttar Pradesh"),
    ("Consignee: New Delhi", "Delhi"),
    ("Tamil\nNadu", "Tamil Nadu"),
])
def test_indian_state_matches_only_complete_names_in_text_order(phrase, expected):
    assert _match_indian_state(phrase) == expected


def test_pdf_page_counts_reason_and_config_override(tmp_path):
    pdf = tmp_path / "three_pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as handle:
        writer.write(handle)

    cfg = {**DEFAULT_SCORING_CONFIG, "analysis": {"max_pdf_pages": 2},
           "fit": {**DEFAULT_SCORING_CONFIG["fit"], "fit_min": 0},
           "status_thresholds": {"shortlist_min": 0, "reject_max": 0}}
    analysis = analyze_rfp_pdf(str(pdf), scoring_config=cfg,
                               company_profile=DEFAULT_COMPANY_PROFILE,
                               card_meta={"title": "Drone supply"})
    assert analysis["analysis_status"] == "ok"
    assert (analysis["pages_total"], analysis["pages_read"]) == (3, 2)
    assert analysis["pages_truncated"] is True
    assert "Analysed 2 of 3 pages; later terms not checked." in analysis["reasons"]
    assert analysis["terms_score"] == analysis["score"]
    assert "risk_score" not in analysis

    cfg["analysis"] = {"max_pdf_pages": 3}
    full = analyze_rfp_pdf(str(pdf), scoring_config=cfg,
                           company_profile=DEFAULT_COMPANY_PROFILE,
                           card_meta={"title": "Drone supply"})
    assert (full["pages_total"], full["pages_read"]) == (3, 3)
    assert full["pages_truncated"] is False
    assert not any("later terms not checked" in r for r in full["reasons"])
    assert full["recommendation"] == "Pursue"
    assert analysis["recommendation"] == "Review"


def test_truncated_pdf_cannot_be_pursue_but_drop_is_unchanged():
    cfg = DEFAULT_SCORING_CONFIG
    eligible = {"verdict": "eligible"}
    assert compute_recommendation(80, 95, eligible, False, cfg) == "Pursue"
    assert compute_recommendation(80, 95, eligible, False, cfg, truncated=True) == "Review"
    assert compute_recommendation(0, 95, eligible, False, cfg, truncated=True) == "Drop"


def test_old_start_has_no_default_penalty_but_opt_in_applies():
    now = datetime.datetime(2026, 3, 3)
    base = evaluate_date_window("29-01-2026", "30-03-2026",
                                DEFAULT_SCORING_CONFIG, now=now)
    enabled = {**DEFAULT_SCORING_CONFIG,
               "dates": {"stale_start_penalty": True, "stale_start_days": 30}}
    penalized = evaluate_date_window("29-01-2026", "30-03-2026",
                                     enabled, now=now)
    assert base["subscore"] == 1.0
    assert penalized["subscore"] == 0.5


def test_search_retries_with_backoff(monkeypatch):
    calls = []
    sleeps = []

    def post(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://gem.gov.in", 503, "busy", {}, None)
        return {"response": {"response": {"docs": []}}}

    monkeypatch.setattr(gem_client, "_post_search_page", post)
    monkeypatch.setattr(gem_client.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(gem_client, "time", SimpleNamespace(monotonic=time.monotonic,
                                                           sleep=sleeps.append))
    assert gem_client.fetch_keyword_bids_api("radar", "", "", max_pages=1,
                                             retries=1, deadline=30) == []
    assert len(calls) == 2
    assert sleeps == [1]


def test_search_honors_retry_after(monkeypatch):
    sleeps = []
    calls = []

    def post(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://gem.gov.in", 429, "slow",
                                         {"Retry-After": "5"}, None)
        return {"response": {"response": {"docs": []}}}

    monkeypatch.setattr(gem_client, "_post_search_page", post)
    monkeypatch.setattr(gem_client.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(gem_client, "time", SimpleNamespace(monotonic=time.monotonic,
                                                           sleep=sleeps.append))
    gem_client.fetch_keyword_bids_api("radar", "", "", max_pages=1,
                                      retries=1, deadline=30)
    assert sleeps == [5]


def test_403_stops_shared_search_workers(monkeypatch):
    stop = threading.Event()
    calls = []

    def blocked(*_args, **_kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("https://gem.gov.in", 403, "forbidden", {}, None)

    monkeypatch.setattr(gem_client, "_post_search_page", blocked)
    with pytest.raises(gem_client.SessionBlocked, match="results incomplete"):
        gem_client.fetch_keyword_bids_api("radar", "", "", max_pages=1,
                                          stop_event=stop)
    assert stop.is_set()
    with pytest.raises(gem_client.SessionBlocked):
        gem_client.fetch_keyword_bids_api("camera", "", "", max_pages=1,
                                          stop_event=stop)
    assert len(calls) == 1


def test_blocked_keyword_saves_completed_results_and_continues_external_portals(
    tmp_path, monkeypatch,
):
    tender = {
        "bid_no": "GEM/2026/B/88", "title": "Radar supply", "quantity": "1",
        "department": "Defence", "start_date": "01-09-2026",
        "end_date": "30-12-2026", "keyword": "A", "downloaded": False,
        "local_pdf_path": "", "pdf_url": "", "status": "Pending Review",
    }

    class FakePage:
        def goto(self, *_args, **_kwargs):
            pass
        def wait_for_timeout(self, *_args):
            pass

    class FakeContext:
        def add_init_script(self, *_args):
            pass
        def new_page(self):
            return FakePage()
        def cookies(self):
            return []

    class FakeBrowser:
        def new_context(self, **_kwargs):
            return FakeContext()
        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(
                launch=lambda **_kwargs: FakeBrowser()))
        def __exit__(self, *_args):
            pass

    def search(keyword, *_args, stop_event=None, **_kwargs):
        if keyword == "A":
            return [tender.copy()]
        error = urllib.error.HTTPError("https://gem.gov.in", 403,
                                       "forbidden", {}, None)
        raise gem_client.SessionBlocked() from error

    monkeypatch.setattr(pipeline, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(pipeline, "fetch_keyword_bids_api", search)
    monkeypatch.setattr(pipeline, "load_company_profile", lambda: {})
    monkeypatch.setattr(pipeline, "get_active_workspace", lambda _profile: "")
    monkeypatch.setattr(pipeline, "workspace_paths",
                        lambda _workspace="": (str(tmp_path), str(tmp_path / "downloads")))
    monkeypatch.setattr(pipeline.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "load_existing_metadata", lambda _dir: {})
    monkeypatch.setattr(pipeline, "build_search_plan", lambda kw, profile=None:
                        SimpleNamespace(concept_id=None, canonical_keyword=kw))
    monkeypatch.setattr(pipeline, "plan_downloads", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(pipeline, "auto_export_summary", lambda *_args: None)

    external_calls = []
    monkeypatch.setattr(context.source_registry, "reload_sources", lambda: None)
    monkeypatch.setattr(context.source_registry, "runnable_adapters",
                        lambda: [SimpleNamespace(name="defproc")])
    monkeypatch.setattr(context.source_registry, "fetch_from_all_active",
                        lambda *_args, **_kwargs: external_calls.append(1) or [])
    monkeypatch.setattr(context.live_excel_manager, "on_scrape_completed", lambda: None)
    monkeypatch.setattr(context, "expand_keywords", lambda keywords: keywords)

    context.begin_job("scrape")
    context.run_scraper_thread(["A", "B"], 1, "Bid-Start-Date-Latest")

    assert tender["bid_no"] in storage.load_existing_metadata(str(tmp_path))
    assert context.scrape_status["outcome"] == context.OUTCOME_PARTIAL
    assert context.scrape_status["warnings"] == [
        "GeM session blocked, results incomplete"
    ]
    assert context.scrape_status["new_count"] == 1
    assert external_calls == [1]


def test_cli_returns_two_and_logs_blocked_session(monkeypatch):
    logged = []

    def scrape_with_warning(**kwargs):
        kwargs["warnings"].append("GeM session blocked, results incomplete")
        return [], 0

    monkeypatch.setattr(cli, "scrape", scrape_with_warning)
    monkeypatch.setattr(cli.logger, "error", lambda message, warning: logged.append(warning))
    assert cli.main(["--keywords", "radar"]) == 2
    assert logged == ["GeM session blocked, results incomplete"]
    assert cli.main(["--keywords", "radar", "--min-days-left", "1"]) == 2


def test_cli_clean_and_filter_only_runs_return_zero(monkeypatch):
    monkeypatch.setattr(cli, "scrape", lambda **_kwargs: ([], 0))
    monkeypatch.setattr(cli, "load_existing_metadata", lambda: {})
    assert cli.main(["--keywords", "radar"]) == 0
    assert cli.main(["--filter-only"]) == 0


def test_non_json_search_response_blocks_session(monkeypatch):
    class HtmlResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b"<html>captcha challenge</html>"

    monkeypatch.setattr(gem_client, "_urlopen", lambda *_args, **_kwargs: HtmlResponse())
    stop = threading.Event()
    with pytest.raises(gem_client.SessionBlocked):
        gem_client.fetch_keyword_bids_api("radar", "", "", max_pages=1,
                                          stop_event=stop)
    assert stop.is_set()


@pytest.fixture
def no_auth_client(monkeypatch):
    monkeypatch.setattr(paths, "load_server_config", lambda: {"auth_token": "", "host": "127.0.0.1"})
    class NoopThread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            pass
    monkeypatch.setattr(tenders.threading, "Thread", NoopThread)
    context.scrape_status["status"] = context.JOB_IDLE
    app.app.config["TESTING"] = True
    with app.app.test_client() as client:
        yield client
    context.scrape_status["status"] = context.JOB_IDLE


def test_scrape_rejects_cross_site_origin(no_auth_client):
    resp = no_auth_client.post("/api/scrape", json={"keywords": ["radar"]},
                                headers={"Origin": "https://attacker.example"})
    assert resp.status_code == 403


def test_scrape_accepts_same_origin_and_no_origin(no_auth_client):
    payload = {"keywords": ["radar"]}
    assert no_auth_client.post("/api/scrape", json=payload,
                               headers={"Origin": "http://localhost"}).status_code == 200
    context.scrape_status["status"] = context.JOB_IDLE
    assert no_auth_client.post("/api/scrape", json=payload).status_code == 200


def test_scrape_rejects_text_plain_and_cross_site_fetch_metadata(no_auth_client):
    assert no_auth_client.post("/api/scrape", data="{}",
                               content_type="text/plain").status_code == 415
    assert no_auth_client.post("/api/scrape", json={"keywords": ["radar"]},
                               headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

"""Regressions for the five confirmed bugs in BUGS_AND_SMART_DETECTION_PLAN.md.

Each test names the failure it locks down, so a reintroduction reads as the
original symptom rather than as an abstract assertion.
"""

import json
import os
import sqlite3
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry import db, storage  # noqa: E402
from gemsentry.parsing.amounts import (  # noqa: E402
    CONFLICTING, NOT_REQUIRED, NOT_STATED, PARSED, UNREADABLE,
)
from gemsentry.parsing.signals import extract_bid_signals, parse_min_turnover  # noqa: E402
from gemsentry.pipeline import refresh_cached_verdict  # noqa: E402
from gemsentry.scoring.eligibility import compute_eligibility  # noqa: E402
from gemsentry.scoring.verdict import compute_recommendation  # noqa: E402

RECORD = {
    "bid_no": "GEM/2026/B/9001",
    "title": "Surveillance drones",
    "quantity": "2",
    "department": "Ministry of Defence",
    "start_date": "01-09-2026",
    "end_date": "25-09-2026",
    "keyword": "drone",
    "downloaded": True,
    "local_pdf_path": "tenders/downloads/a.pdf",
    "pdf_url": "https://example.gov.in/a.pdf",
    "first_seen": "2026-09-01",
    "status": "Pending Review",
    "status_source": "auto",
    "source_id": "gem",
    "source_name": "Government e-Marketplace (GeM)",
    "analysis": {"score": 71, "recommendation": "Pursue"},
}

PROFILE = {"eligibility": {"annual_turnover_inr": 1000000, "years_experience": 6}}


# ---------------------------------------------------------------------------
# Bug 1 -- a scrape's stale snapshot must not undo a manual decision
# ---------------------------------------------------------------------------

def test_manual_shortlist_survives_a_stale_bulk_save(tmp_path):
    """The reported symptom: a pinned Shortlisted tender reverts to Pending Review.

    A scrape loads the workspace, the user shortlists a tender while it runs,
    and the scrape then writes back the snapshot it loaded before the click.
    """
    workspace = str(tmp_path)
    storage.save_metadata([RECORD], workspace)

    # What the scrape is holding: the record as it was before the user clicked.
    scrape_snapshot = [dict(RECORD)]

    # The user pins the tender mid-scrape.
    storage.update_record(
        RECORD["bid_no"], {"status": "Shortlisted", "status_source": "manual"},
        workspace,
    )

    storage.save_metadata(scrape_snapshot, workspace)

    stored = storage.load_existing_metadata(workspace)[RECORD["bid_no"]]
    assert stored["status"] == "Shortlisted"
    assert stored["status_source"] == "manual"


def test_the_exports_agree_with_the_merged_record(tmp_path):
    """The JSON/CSV exports must show the pin too, not the snapshot's status."""
    workspace = str(tmp_path)
    storage.save_metadata([RECORD], workspace)
    storage.update_record(
        RECORD["bid_no"], {"status": "Rejected", "status_source": "manual"},
        workspace,
    )
    storage.save_metadata([dict(RECORD)], workspace)

    exported = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert exported[0]["status"] == "Rejected"
    assert exported[0]["status_source"] == "manual"
    assert "Rejected" in (tmp_path / "metadata.csv").read_text(encoding="utf-8")


def test_an_automatic_status_is_still_free_to_change(tmp_path):
    """Only *manual* pins are protected; rescoring must still move the rest."""
    workspace = str(tmp_path)
    storage.save_metadata([RECORD], workspace)
    storage.save_metadata([{**RECORD, "status": "Rejected"}], workspace)
    stored = storage.load_existing_metadata(workspace)[RECORD["bid_no"]]
    assert stored["status"] == "Rejected"


def test_clearing_a_workspace_is_not_blocked_by_pins(tmp_path):
    """An empty save must empty the store, pins included."""
    workspace = str(tmp_path)
    storage.save_metadata([RECORD], workspace)
    storage.update_record(
        RECORD["bid_no"], {"status": "Shortlisted", "status_source": "manual"},
        workspace,
    )
    storage.save_metadata([], workspace)
    assert storage.load_existing_metadata(workspace) == {}


def test_a_database_without_the_column_is_migrated(tmp_path):
    """A workspace written by an older build gains status_source in place."""
    path = db.db_path(str(tmp_path))
    os.makedirs(str(tmp_path), exist_ok=True)
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE tenders (bid_no TEXT PRIMARY KEY, data TEXT NOT NULL, "
        "status TEXT, source_id TEXT, end_date TEXT, first_seen TEXT, score REAL);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    pinned = {**RECORD, "status": "Shortlisted", "status_source": "manual"}
    legacy.execute(
        "INSERT INTO tenders(bid_no, data, status) VALUES(?, ?, ?)",
        (pinned["bid_no"], json.dumps(pinned), "Shortlisted"),
    )
    legacy.commit()
    legacy.close()

    storage.save_metadata([dict(RECORD)], str(tmp_path))
    stored = storage.load_existing_metadata(str(tmp_path))[RECORD["bid_no"]]
    assert stored["status"] == "Shortlisted"


# ---------------------------------------------------------------------------
# Bug 2 -- turnover requirements, and never mistaking a failed parse for a pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Minimum Average Annual Turnover of the bidder (For 3 Years) 75 Lakh (s)", 7500000),
    ("Minimum Average Annual Turnover of the bidder (For 3 Years) 2000000", 2000000),
    ("Minimum Average Annual Turnover of the bidder (For 3 Years) 1.5 Crore", 15000000),
    ("Minimum Average Annual Turnover of the bidder (For 3 Years) 75,00,000", 7500000),
    ("Minimum Average Annual Turnover of the bidder (For Three Years) Rs. 50 Lakhs", 5000000),
    ("Minimum Average Annual Turnover of the bidder for the last 3 years 40 lac", 4000000),
    ("Average Annual Turnover of the bidder INR 12,50,000", 1250000),
])
def test_turnover_amounts_are_read_past_the_year_qualifier(text, expected):
    result = parse_min_turnover(text)
    assert result["state"] == PARSED
    assert result["value"] == expected


def test_the_reported_case_no_longer_reads_as_no_requirement():
    """`(For 3 Years) 75 Lakh (s)` used to yield the 3, then discard it."""
    signals, _ = extract_bid_signals(
        "Minimum Average Annual Turnover of the bidder (For 3 Years) 75 Lakh (s) "
        "Years of Past Experience 3 Year (s)"
    )
    assert signals["rfp_min_turnover_inr"] == 7500000
    verdict = compute_eligibility(signals, "no", "no", PROFILE)
    assert verdict["verdict"] == "turnover_gap"


def test_an_adjacent_fields_number_is_not_read_as_turnover():
    result = parse_min_turnover(
        "Minimum Average Annual Turnover of the bidder (For 3 Years) "
        "Years of Past Experience 3 Year (s) Estimated Bid Value 5000000"
    )
    assert result["state"] == UNREADABLE
    assert result["value"] is None


def test_an_explicit_waiver_is_not_the_same_as_a_missing_field():
    waived = parse_min_turnover("Minimum Average Annual Turnover of the bidder Nil")
    assert waived["state"] == NOT_REQUIRED
    absent = parse_min_turnover("Estimated Bid Value 5000000")
    assert absent["state"] == NOT_STATED


def test_contradictory_requirements_are_flagged_not_silently_resolved():
    result = parse_min_turnover(
        "Minimum Average Annual Turnover of the bidder (For 3 Years) 75 Lakh (s) "
        "... Minimum Average Annual Turnover of the bidder 90 Lakh"
    )
    assert result["state"] == CONFLICTING
    assert result["value"] == 9000000


def test_an_unreadable_requirement_needs_review_not_a_pass():
    """The core of the bug: an unparsed requirement was scored as eligible."""
    signals, _ = extract_bid_signals(
        "Minimum Average Annual Turnover of the bidder (For 3 Years) "
        "Years of Past Experience 3 Year (s)"
    )
    result = compute_eligibility(signals, "no", "no", PROFILE)
    assert result["verdict"] == "unknown"
    assert "turnover_req_unreadable" in result["flags"]
    assert signals["rfp_min_turnover_evidence"]


def test_a_conflicting_requirement_needs_review():
    signals, _ = extract_bid_signals(
        "Minimum Average Annual Turnover of the bidder 5 Lakh "
        "... Minimum Average Annual Turnover of the bidder 90 Lakh"
    )
    result = compute_eligibility(signals, "no", "no", PROFILE)
    assert result["verdict"] == "unknown"
    assert "turnover_req_conflicting" in result["flags"]


def test_a_waived_requirement_is_still_eligible():
    signals, _ = extract_bid_signals("Minimum Average Annual Turnover of the bidder Nil")
    assert compute_eligibility(signals, "no", "no", PROFILE)["verdict"] == "eligible"


def test_an_absent_requirement_is_still_eligible():
    signals, _ = extract_bid_signals("Estimated Bid Value 500000")
    assert compute_eligibility(signals, "no", "no", PROFILE)["verdict"] == "eligible"


def test_a_full_waiver_overrides_an_unreadable_requirement():
    signals, _ = extract_bid_signals(
        "Minimum Average Annual Turnover of the bidder (For 3 Years) "
        "Years of Past Experience 3 Year (s)"
    )
    assert compute_eligibility(signals, "complete", "no", PROFILE)["verdict"] == "eligible"


def test_records_written_before_the_state_existed_still_score():
    """Legacy analyses carry the amount but no state; infer the old meaning."""
    assert compute_eligibility(
        {"rfp_min_turnover_inr": 500000}, "no", "no", PROFILE
    )["verdict"] == "eligible"
    assert compute_eligibility(
        {"rfp_min_turnover_inr": None}, "no", "no", PROFILE
    )["verdict"] == "eligible"


def test_an_unresolved_requirement_downgrades_pursue_to_review():
    cfg = {"fit": {"fit_min": 60, "review_band": 8},
           "status_thresholds": {"shortlist_min": 70, "reject_max": 40}}
    eligibility = {"verdict": "unknown", "flags": ["turnover_req_unreadable"]}
    assert compute_recommendation(90, 90, eligibility, False, cfg) == "Review"


def test_a_card_only_unknown_still_reaches_pursue():
    """Narrow by design: no PDF is a different situation from an unreadable one."""
    cfg = {"fit": {"fit_min": 60, "review_band": 8},
           "status_thresholds": {"shortlist_min": 70, "reject_max": 40}}
    eligibility = {"verdict": "unknown", "flags": ["card_only"]}
    assert compute_recommendation(90, 90, eligibility, False, cfg) == "Pursue"


# ---------------------------------------------------------------------------
# Bug 3 -- cached tenders must not keep an expiry verdict from another day
# ---------------------------------------------------------------------------

SCORING_CFG = {
    "date_window": {"min_days": 7, "full_credit_days": 14, "min_days_to_bid": 5},
    "weights": {"date_window": 1.0},
    "fit": {"fit_min": 60, "review_band": 8},
    "status_thresholds": {"shortlist_min": 70, "reject_max": 40},
}


def _cached_tender(end_date):
    return {
        "bid_no": "GEM/2020/B/1",
        "title": "Surveillance drones",
        "department": "Ministry of Defence",
        "start_date": "01-01-2020",
        "end_date": end_date,
        "keyword": "drone",
        "downloaded": True,
        "local_pdf_path": "tenders/downloads/a.pdf",
        "status": "Shortlisted",
        "status_source": "auto",
        "analysis": {
            "analysis_status": "ok",
            "score": 82,
            "recommendation": "Pursue",
            "is_expired": False,
            "auto_reject": False,
            "breakdown": [{"criterion": "date_window", "subscore": 1.0, "detail": "fresh"}],
            "eligibility": {"verdict": "eligible", "flags": []},
        },
    }


def test_an_expired_cached_tender_loses_its_stale_pursue():
    tender = _cached_tender("31-12-2020")
    assert refresh_cached_verdict(tender, SCORING_CFG, PROFILE) is True
    assert tender["analysis"]["is_expired"] is True
    assert tender["analysis"]["recommendation"] == "Drop"
    assert tender["analysis"]["score"] == 0
    assert tender["status"] == "Rejected"


def test_refreshing_a_cached_tender_keeps_a_manual_pin():
    tender = _cached_tender("31-12-2020")
    tender["status"] = "Shortlisted"
    tender["status_source"] = "manual"
    refresh_cached_verdict(tender, SCORING_CFG, PROFILE)
    assert tender["analysis"]["recommendation"] == "Drop"
    assert tender["status"] == "Shortlisted"


def test_a_tender_still_in_its_window_is_not_expired_and_settles():
    """A live bid never gains an expiry, and a second refresh is a no-op."""
    tender = _cached_tender("31-12-2099")
    refresh_cached_verdict(tender, SCORING_CFG, PROFILE)
    assert tender["analysis"]["is_expired"] is False
    assert tender["analysis"]["score"] != 0  # not auto-rejected on dates
    assert refresh_cached_verdict(tender, SCORING_CFG, PROFILE) is False


def test_a_tender_without_a_usable_analysis_is_skipped():
    tender = _cached_tender("31-12-2020")
    tender["analysis"] = {"analysis_status": "failed"}
    assert refresh_cached_verdict(tender, SCORING_CFG, PROFILE) is False


def test_the_planner_refreshes_the_cached_record_it_skips(tmp_path):
    """End to end: the planner's processed-record shortcut no longer freezes
    the verdict. The bid is still not re-downloaded."""
    from gemsentry.pipeline import plan_downloads

    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"%PDF-1.4 cached")
    tender = _cached_tender("31-12-2020")
    tender["local_pdf_path"] = str(pdf)
    tender["pdf_url"] = "https://bidplus.gem.gov.in/showbidDocument/1"

    to_download, to_analyze = plan_downloads(
        [tender], SCORING_CFG, PROFILE, downloads_dir=str(tmp_path),
        pdf_index={}, host_index={},
    )

    assert to_download == [] and to_analyze == []
    assert tender["analysis"]["is_expired"] is True
    assert tender["analysis"]["recommendation"] == "Drop"


# ---------------------------------------------------------------------------
# Bug 5 -- a failed commit must not be reported, or exported, as a save
# ---------------------------------------------------------------------------

def test_a_failed_commit_raises_instead_of_returning_quietly(tmp_path, monkeypatch):
    storage.save_metadata([RECORD], str(tmp_path))

    def refuse(_conn, _records, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "replace_all", refuse)
    with pytest.raises(storage.StorageError):
        storage.save_metadata([{**RECORD, "status": "Rejected"}], str(tmp_path))


def test_a_failed_commit_leaves_the_existing_exports_untouched(tmp_path, monkeypatch):
    storage.save_metadata([RECORD], str(tmp_path))
    before = (tmp_path / "metadata.json").read_text(encoding="utf-8")

    def refuse(_conn, _records, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "replace_all", refuse)
    with pytest.raises(storage.StorageError):
        storage.save_metadata([{**RECORD, "status": "Rejected"}], str(tmp_path))

    assert (tmp_path / "metadata.json").read_text(encoding="utf-8") == before


def test_an_export_failure_does_not_claim_the_save_failed(tmp_path, monkeypatch):
    """The database is the store of record; a stale export is a warning."""
    monkeypatch.setattr(storage, "write_exports", lambda *_a, **_k: False)
    stored = storage.save_metadata([RECORD], str(tmp_path))
    assert stored[0]["bid_no"] == RECORD["bid_no"]
    assert storage.load_existing_metadata(str(tmp_path))[RECORD["bid_no"]] == RECORD


def test_save_returns_the_records_as_stored(tmp_path):
    workspace = str(tmp_path)
    storage.save_metadata([RECORD], workspace)
    storage.update_record(
        RECORD["bid_no"], {"status": "Shortlisted", "status_source": "manual"},
        workspace,
    )
    stored = storage.save_metadata([dict(RECORD)], workspace)
    assert [r["status"] for r in stored] == ["Shortlisted"]


# ---------------------------------------------------------------------------
# Bug 4 -- a job's outcome is tracked separately from whether one is running
# ---------------------------------------------------------------------------

@pytest.fixture
def web_context(monkeypatch):
    from gemsentry.web import context as ctx

    monkeypatch.setattr(ctx.logging_setup, "clear_log_buffer", lambda: None)
    monkeypatch.setattr(ctx.logging_setup, "get_session_path", lambda: None)
    with ctx.status_lock:
        ctx.begin_job("scrape")
    return ctx


def test_a_crashed_scrape_reports_failure_not_idle_success(web_context, monkeypatch):
    ctx = web_context

    def crash(**_kwargs):
        raise RuntimeError("portal unreachable")

    monkeypatch.setattr(ctx.scraper, "scrape", crash)
    ctx.run_scraper_thread(["drone"], 1, "Bid-Start-Date-Latest")

    assert ctx.scrape_status["status"] == ctx.JOB_IDLE
    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_FAILED
    assert "portal unreachable" in ctx.scrape_status["error"]


def test_a_clean_scrape_reports_success(web_context, monkeypatch):
    ctx = web_context
    monkeypatch.setattr(ctx.scraper, "scrape", lambda **_k: ([], 3))
    monkeypatch.setattr(ctx.source_registry, "reload_sources", lambda: None)
    monkeypatch.setattr(ctx.source_registry, "runnable_adapters", lambda: [])
    monkeypatch.setattr(ctx.live_excel_manager, "on_scrape_completed", lambda: None)

    ctx.run_scraper_thread(["drone"], 1, "Bid-Start-Date-Latest")

    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_SUCCEEDED
    assert ctx.scrape_status["error"] is None
    assert ctx.scrape_status["new_count"] == 3


def test_an_empty_but_successful_search_is_still_a_success(web_context, monkeypatch):
    ctx = web_context
    monkeypatch.setattr(ctx.scraper, "scrape", lambda **_k: ([], 0))
    monkeypatch.setattr(ctx.source_registry, "reload_sources", lambda: None)
    monkeypatch.setattr(ctx.source_registry, "runnable_adapters", lambda: [])
    monkeypatch.setattr(ctx.live_excel_manager, "on_scrape_completed", lambda: None)

    ctx.run_scraper_thread(["drone"], 1, "Bid-Start-Date-Latest")

    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_SUCCEEDED
    assert ctx.scrape_status["new_count"] == 0


def test_a_portal_that_fails_makes_the_run_partial(web_context, monkeypatch):
    ctx = web_context
    monkeypatch.setattr(ctx.scraper, "scrape", lambda **_k: ([], 2))
    monkeypatch.setattr(ctx.source_registry, "reload_sources", lambda: None)
    monkeypatch.setattr(
        ctx.source_registry, "runnable_adapters",
        lambda: [types.SimpleNamespace(name="defproc")],
    )

    def boom(*_a, **_k):
        raise RuntimeError("CPPP timed out")

    monkeypatch.setattr(ctx.source_registry, "fetch_from_all_active", boom)
    monkeypatch.setattr(ctx.live_excel_manager, "on_scrape_completed", lambda: None)

    ctx.run_scraper_thread(["drone"], 1, "Bid-Start-Date-Latest")

    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_PARTIAL
    assert any("CPPP timed out" in w for w in ctx.scrape_status["warnings"])


def test_a_single_bid_acquisition_that_found_nothing_is_not_a_success(web_context, monkeypatch):
    ctx = web_context
    monkeypatch.setattr(ctx.scraper, "scrape_single_bid", lambda **_k: None)

    ctx.run_scraper_id_thread("GEM/2026/B/404")

    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_FAILED
    assert "GEM/2026/B/404" in ctx.scrape_status["error"]


def test_a_single_bid_acquisition_that_worked_reports_success(web_context, monkeypatch):
    ctx = web_context
    monkeypatch.setattr(
        ctx.scraper, "scrape_single_bid", lambda **_k: {"bid_no": "GEM/2026/B/1"}
    )
    monkeypatch.setattr(ctx.live_excel_manager, "on_scrape_completed", lambda: None)

    ctx.run_scraper_id_thread("GEM/2026/B/1")

    assert ctx.scrape_status["outcome"] == ctx.OUTCOME_SUCCEEDED
    assert ctx.scrape_status["new_count"] == 1


def test_starting_a_job_clears_the_previous_outcome(web_context):
    ctx = web_context
    ctx.finish_job(ctx.OUTCOME_FAILED, error="boom")
    with ctx.status_lock:
        ctx.begin_job("rescore")
    assert ctx.scrape_status["outcome"] is None
    assert ctx.scrape_status["error"] is None
    assert ctx.scrape_status["status"] == ctx.JOB_RUNNING

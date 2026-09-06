"""Projection, caching and conditional-GET behaviour of /api/tenders."""

import gzip
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
from gemsentry.tender_view import (  # noqa: E402
    HEAVY_ANALYSIS_FIELDS, TenderResponseCache, project_for_list, stat_key,
)

RECORD = {
    "bid_no": "GEM/2026/B/1234",
    "title": "Supply of radar subsystems",
    "quantity": "2",
    "department": "Ministry of Defence",
    "start_date": "01-09-2026",
    "end_date": "30-09-2026",
    "keyword": "radar",
    "downloaded": True,
    "local_pdf_path": "tenders/downloads/x.pdf",
    "pdf_url": "https://bidplus.gem.gov.in/x.pdf",
    "analysis": {
        "score": 8.1,
        "fit_score": 7.4,
        "priority_score": 9.0,
        "recommendation": "Pursue",
        "business_line": {"label": "Radar"},
        "eligibility": {"verdict": "eligible", "detail": "Turnover met"},
        "breakdown": [{"criterion": "EMD", "weight": 2, "points": 1.0}],
        "fit_breakdown": [{"criterion": "Domain", "weight": 3, "points": 2.5}],
        "reasons": ["EMD within limit"],
        "field_status": {"emd": "parsed"},
    },
}


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def test_projection_strips_only_the_detail_only_members():
    lite = project_for_list(RECORD)
    for field in HEAVY_ANALYSIS_FIELDS:
        assert field not in lite["analysis"], field


def test_projection_keeps_every_field_the_collapsed_card_renders():
    lite = project_for_list(RECORD)["analysis"]
    for field in ("score", "fit_score", "priority_score", "recommendation",
                  "business_line", "eligibility"):
        assert field in lite, field


def test_projection_does_not_mutate_the_caller_record():
    original = json.loads(json.dumps(RECORD))
    project_for_list(RECORD)
    assert RECORD == original


def test_projection_flags_that_more_exists():
    assert project_for_list(RECORD)["analysis"]["_truncated"] is True


def test_projection_tolerates_records_without_analysis():
    bare = {"bid_no": "X"}
    assert project_for_list(bare) == bare


# --------------------------------------------------------------------------
# Cache keying
# --------------------------------------------------------------------------

def test_stat_key_is_none_for_a_missing_file(tmp_path):
    assert stat_key(str(tmp_path / "nope.json")) is None


def test_stat_key_changes_when_content_changes(tmp_path):
    p = tmp_path / "metadata.json"
    p.write_text("[]", encoding="utf-8")
    before = stat_key(str(p))
    p.write_text('[{"bid_no": "A"}]', encoding="utf-8")
    assert stat_key(str(p)) != before


def test_cache_misses_on_a_new_key():
    cache = TenderResponseCache()
    cache.put((1, 2), b"body")
    body, etag = cache.get((3, 4))
    assert body is None and etag is None


def test_cache_hits_and_returns_a_stable_etag():
    cache = TenderResponseCache()
    etag = cache.put((1, 2), b"body")
    assert cache.get((1, 2)) == (b"body", etag)


def test_cache_never_serves_a_none_key():
    """A missing metadata file must not collide with a cached entry."""
    cache = TenderResponseCache()
    cache.put(None, b"body")
    assert cache.get(None) == (None, None)


# --------------------------------------------------------------------------
# Endpoint behaviour
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    tenders_dir = tmp_path / "tenders"
    tenders_dir.mkdir()
    (tenders_dir / "metadata.json").write_text(
        json.dumps([RECORD]), encoding="utf-8"
    )
    monkeypatch.setattr(
        app_module.scraper, "workspace_paths",
        lambda *a, **k: (str(tenders_dir), str(tenders_dir / "downloads")),
    )
    monkeypatch.setattr(
        app_module.scraper, "load_existing_metadata",
        lambda *a, **k: {RECORD["bid_no"]: json.loads(json.dumps(RECORD))},
    )
    app_module.tenders_cache.invalidate()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _body(response):
    raw = response.get_data()
    if response.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def test_list_endpoint_returns_projected_records(client):
    payload = _body(client.get("/api/tenders"))
    assert payload["truncated"] is True
    analysis = payload["tenders"][0]["analysis"]
    assert "breakdown" not in analysis
    assert analysis["score"] == 8.1


def test_full_query_param_returns_complete_records(client):
    payload = _body(client.get("/api/tenders?full=1"))
    assert payload["truncated"] is False
    assert payload["tenders"][0]["analysis"]["breakdown"]


def test_list_endpoint_sets_an_etag(client):
    assert client.get("/api/tenders").headers.get("ETag")


def test_matching_etag_yields_304_and_no_body(client):
    etag = client.get("/api/tenders").headers["ETag"]
    second = client.get("/api/tenders", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.get_data() == b""


def test_stale_etag_yields_a_fresh_200(client):
    second = client.get("/api/tenders", headers={"If-None-Match": '"stale"'})
    assert second.status_code == 200


def test_small_responses_are_not_gzipped(client):
    """Below the threshold, compression costs more CPU than it saves."""
    resp = client.get("/api/tenders", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") is None


def test_large_responses_are_gzipped_when_accepted(client, monkeypatch):
    many = {f"GEM/2026/B/{i}": {**RECORD, "bid_no": f"GEM/2026/B/{i}"}
            for i in range(200)}
    monkeypatch.setattr(
        app_module.scraper, "load_existing_metadata", lambda *a, **k: many
    )
    app_module.tenders_cache.invalidate()
    resp = client.get("/api/tenders", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert resp.headers.get("Vary") == "Accept-Encoding"
    assert len(_body(resp)["tenders"]) == 200


def test_response_is_plain_when_gzip_not_accepted(client):
    resp = client.get("/api/tenders", headers={"Accept-Encoding": "identity"})
    assert resp.headers.get("Content-Encoding") is None


def test_detail_endpoint_returns_the_heavy_members(client):
    payload = _body(client.get(f"/api/tenders/{RECORD['bid_no']}"))
    analysis = payload["tender"]["analysis"]
    assert analysis["breakdown"]
    assert analysis["fit_breakdown"]
    assert analysis["reasons"] == ["EMD within limit"]


def test_detail_endpoint_404s_for_an_unknown_bid(client):
    assert client.get("/api/tenders/GEM/9999/NOPE").status_code == 404


def test_errors_do_not_leak_internal_exception_text(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(r"C:\secret\path\driver blew up")

    monkeypatch.setattr(app_module.scraper, "load_existing_metadata", boom)
    app_module.tenders_cache.invalidate()
    resp = client.get("/api/tenders")
    assert resp.status_code == 500
    assert "secret" not in resp.get_data(as_text=True)

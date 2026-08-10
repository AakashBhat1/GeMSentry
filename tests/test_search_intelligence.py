"""Regression tests for concept-aware discovery and actionable date defaults."""

import datetime
import json
import os
import sys
import urllib.parse
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scraper  # noqa: E402
from gemsentry.scoring.dates import evaluate_date_window, resolve_min_days_left  # noqa: E402
from gemsentry.search import build_search_plan, expand_keywords, matches_search_result  # noqa: E402
from gemsentry.sources.gem.client import fetch_keyword_bids_api  # noqa: E402


CFG = scraper.load_scoring_config()
PROFILE = scraper.load_company_profile()


def _relevance(title):
    signals = {
        "est_value_inr": 1000000,
        "primary_item": title,
        "item_category": title,
        "buyer_org": None,
        "buyer_dept": None,
        "consignee_state": None,
    }
    _, breakdown, business_line = scraper.compute_fit_score(
        {}, signals, {"verdict": "unknown"}, PROFILE, CFG,
        card_meta={"title": title},
    )
    relevance = next(
        item["subscore"] for item in breakdown if item["criterion"] == "relevance"
    )
    return relevance, (business_line or {}).get("id")


def test_facial_recognition_and_typo_share_one_search_concept():
    phrase = build_search_plan("facial recognition")
    typo = build_search_plan("rcognition")

    assert phrase.concept_id == "facial_recognition"
    assert typo.concept_id == phrase.concept_id
    assert phrase.canonical_keyword == "facial recognition"
    assert "rcognition" in phrase.queries
    assert "face recognition" in phrase.queries


def test_search_plan_deduplicates_queries_case_insensitively():
    plan = build_search_plan("  FACIAL RECOGNITION  ")
    lowered = [query.casefold() for query in plan.queries]
    assert len(lowered) == len(set(lowered))


def test_external_portal_keywords_receive_the_same_expansion():
    expanded = expand_keywords(["rcognition", "DRONE"])
    assert "facial recognition" in expanded
    assert "face recognition" in expanded
    assert "uav" in expanded
    assert len([item for item in expanded if item.casefold() == "drone"]) == 1


def test_facial_tissue_is_not_a_facial_recognition_result():
    plan = build_search_plan("facial recognition")
    tender = {
        "title": "Facial Tissue Papers(V3), Air Freshener liquid (V3)",
        "department": "Indian Army",
    }
    assert not matches_search_result(tender, plan)


def test_real_facial_attendance_variants_survive_discovery():
    plan = build_search_plan("facial recognition")
    titles = (
        "PROCUREMENT AND INSTALLATION OF BIOMETRIC FACIAL RECOGNITION ACCESS CONTRL SYSTEM",
        "Facial based Time & Attendance System Make SPECTRA",
        "Face based attendance and authentication terminal",
    )
    for title in titles:
        assert matches_search_result({"title": title}, plan), title


def test_real_facial_attendance_variants_score_as_biometrics():
    for title in (
        "Facial based Time & Attendance System Make SPECTRA",
        "Face based attendance and authentication terminal",
    ):
        relevance, business_line = _relevance(title)
        assert relevance == 1.0, title
        assert business_line == "biometrics", title


def test_unconfigured_keyword_preserves_portal_results():
    plan = build_search_plan("specialised cryogenic vessel")
    assert plan.concept_id is None
    assert plan.queries == ("specialised cryogenic vessel",)
    assert matches_search_result({"title": "Any portal result"}, plan)


def test_minimum_days_defaults_to_scoring_policy():
    assert resolve_min_days_left(None, CFG) == 5.0
    assert resolve_min_days_left(9, CFG) == 9.0


def test_closing_in_two_days_is_not_actionable():
    now = datetime.datetime(2026, 8, 10, 9, 0, 0)
    result = evaluate_date_window(
        "10-08-2026 08:00:00 AM",
        "12-08-2026 09:00:00 AM",
        CFG,
        now=now,
    )
    assert result["auto_reject"] is True
    assert result["is_expired"] is False


def test_new_bid_with_ten_days_remaining_is_actionable():
    now = datetime.datetime(2026, 8, 10, 9, 0, 0)
    result = evaluate_date_window(
        "10-08-2026 08:00:00 AM",
        "20-08-2026 09:00:00 AM",
        CFG,
        now=now,
    )
    assert result["auto_reject"] is False
    assert result["remaining_days"] >= 10


def test_unparseable_deadline_never_receives_full_credit():
    result = evaluate_date_window("N/A", "N/A", CFG)
    assert result["remaining_days"] is None
    assert result["subscore"] == CFG["unknown_subscore"]
    assert result["reasons"]


class _FakeResponse:
    def __init__(self, docs):
        self._docs = docs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        payload = {"response": {"response": {"docs": self._docs}}}
        return json.dumps(payload).encode("utf-8")


def _gem_doc(bid_no, title):
    return {
        "id": bid_no.rsplit("/", 1)[-1],
        "b_bid_number": [bid_no],
        "bd_category_name": [title],
        "b_total_quantity": ["1"],
        "final_start_date_sort": ["2026-08-10T08:00:00"],
        "final_end_date_sort": ["2030-08-30T17:00:00"],
    }


def test_gem_fetch_expands_filters_and_deduplicates_results():
    genuine = _gem_doc(
        "GEM/2026/B/9001",
        "Facial based Time & Attendance System Make SPECTRA",
    )
    tissue = _gem_doc(
        "GEM/2026/B/9002",
        "Facial Tissue Papers(V3), Air Freshener liquid (V3)",
    )
    requested_queries = []

    def fake_urlopen(request, **_kwargs):
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        query = json.loads(form["payload"][0])["param"]["searchBid"]
        requested_queries.append(query)
        if query == "facial recognition":
            return _FakeResponse([tissue])
        if query in {"face recognition", "rcognition"}:
            return _FakeResponse([genuine])
        return _FakeResponse([])

    with patch("gemsentry.sources.gem.client.urllib.request.urlopen", fake_urlopen):
        tenders = fetch_keyword_bids_api(
            "facial recognition", "", "", max_pages=1, min_days_left=5,
        )

    assert "rcognition" in requested_queries
    assert [tender["bid_no"] for tender in tenders] == ["GEM/2026/B/9001"]
    assert tenders[0]["keyword"] == "facial recognition"


def test_dashboard_defaults_to_live_newly_published_view():
    with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as handle:
        html = handle.read()
    assert "let currentDeadlineBand = 'actionable';" in html
    assert '<option value="published-newest" selected>' in html
    assert '<option value="priority-desc" selected>' not in html

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
from gemsentry.parsing.signals import extract_bid_signals  # noqa: E402
from gemsentry.scoring.dates import evaluate_date_window, resolve_min_days_left  # noqa: E402
from gemsentry.search import build_search_plan, expand_keywords, matches_search_result  # noqa: E402
from gemsentry.sources.gem.client import doc_to_tender, fetch_keyword_bids_api  # noqa: E402


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


def test_profile_keywords_create_search_intelligence_without_a_handwritten_concept():
    plan = build_search_plan("smrt meter")

    assert plan.concept_id.startswith("profile:smart_meter_ami")
    assert plan.canonical_keyword == "smart meter"
    assert "smart meter" in plan.queries
    assert "meter" in plan.queries
    assert any("meter" in query for query in plan.queries)


def test_profile_typo_expansion_works_for_other_business_lines():
    expanded = expand_keywords(["wiring harnes", "solr power plant"])

    assert "wiring harness" in expanded
    assert "solar power plant" in expanded


def test_every_business_line_strong_term_gets_an_intelligent_search_plan():
    for line in PROFILE["business_lines"]:
        for keyword in line.get("strong_keywords") or ():
            plan = build_search_plan(keyword)
            assert plan.concept_id is not None, (line["id"], keyword)
            assert plan.positive_terms, (line["id"], keyword)


def test_facial_tissue_is_not_a_facial_recognition_result():
    plan = build_search_plan("facial recognition")
    tender = {
        "title": "Facial Tissue Papers(V3), Air Freshener liquid (V3)",
        "department": "Indian Army",
    }
    assert not matches_search_result(tender, plan)


def test_generic_fingerprint_access_control_is_not_a_facial_result():
    plan = build_search_plan("facial recognition")
    tender = {"title": "Biometric fingerprint access control and attendance system"}
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


def test_unconfigured_keyword_still_requires_card_evidence():
    plan = build_search_plan("specialised cryogenic vessel")
    assert plan.concept_id is None
    assert plan.queries[0] == "specialised cryogenic vessel"
    assert "cryogenic" in plan.queries
    assert matches_search_result(
        {"title": "Supply of specialised cryogenic vessel for research"}, plan
    )
    assert not matches_search_result({"title": "Any unrelated portal result"}, plan)


def test_profile_search_rejects_unrelated_gem_full_text_result():
    plan = build_search_plan("smart meter")

    assert matches_search_result(
        {"title": "Smart prepaid energy meter with communication module"}, plan
    )
    assert not matches_search_result(
        {"title": "4 MP ANPR camera, 64 channel NVR and surveillance hard disk"},
        plan,
    )


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


def test_gem_doc_extraction_normalizes_reverse_auction_to_parent_rfp():
    tender = doc_to_tender({
        "id": "9737458",
        "b_id": [9737458],
        "b_id_parent": [9457646],
        "b_bid_number": ["GEM/2026/R/714610"],
        "b_bid_number_parent": ["GEM/2026/B/7654972"],
        "b_category_name": ["Smart Prepaid Meter, Meter Communication Module"],
        "bd_category_name": [
            "Smart Prepaid Meter, Meter Communication Module, Data Concentrator Unit"
        ],
        "b_total_quantity": [6318],
        "ba_official_details_minName": ["Ministry of Power"],
        "ba_official_details_deptName": "Energy Department",
        "final_start_date_sort": ["2030-08-12T11:00:00Z"],
        "final_end_date_sort": "2030-08-30T11:00:00Z",
        "b_is_custom_item": [1],
        "bd_details_is_boq": [True],
        "ba_is_global_tendering": [0],
        "ba_is_single_packet": [1],
        "is_high_value": [True],
        "b_estimated_value": ["31,00,000"],
    }, "smart meter")

    assert tender["bid_no"] == "GEM/2026/B/7654972"
    assert tender["gem_result_bid_no"] == "GEM/2026/R/714610"
    assert tender["gem_document_id"] == "9457646"
    assert tender["pdf_url"].endswith("/9457646")
    assert tender["primary_item"] == "Smart Prepaid Meter"
    assert "Data Concentrator Unit" in tender["item_category"]
    assert tender["department"] == "Ministry of Power | Energy Department"
    assert tender["est_value_inr"] == 3100000
    assert tender["is_reverse_auction"] is True
    assert tender["is_custom_bid"] is True
    assert tender["is_boq"] is True
    assert tender["is_single_packet"] is True
    assert tender["is_high_value"] is True


def test_card_only_analysis_uses_structured_gem_item_fields():
    tender = {
        "title": "Advanced Metering Infrastructure Service Provider",
        "primary_item": "Smart Prepaid Meter",
        "item_category": "Smart Prepaid Meter, Head End System, Meter Data Management",
        "department": "Ministry of Power | Energy Department",
        "quantity": "1000",
        "keyword": "smrt meter",
        "est_value_inr": 3100000,
        "start_date": "12-08-2030 11:00 AM",
        "end_date": "30-08-2030 11:00 AM",
    }

    analysis = scraper.analyze_from_card(tender, CFG, PROFILE)

    assert analysis["primary_item"] == "Smart Prepaid Meter"
    assert "Head End System" in analysis["item_category"]
    assert analysis["business_line"]["id"] == "smart_meter_ami"
    assert analysis["est_value_inr"] == 3100000
    assert analysis["signal_parsed"] >= 4


def test_pdf_analysis_prefers_structured_gem_item_fields_over_title_fallback():
    signals, flags = extract_bid_signals(
        "PDF text without a parseable item category label",
        card_meta={
            "title": "Custom Bid for Goods",
            "primary_item": "Smart Prepaid Meter",
            "item_category": "Smart Prepaid Meter, Head End System",
        },
    )

    assert signals["primary_item"] == "Smart Prepaid Meter"
    assert signals["item_category"] == "Smart Prepaid Meter, Head End System"
    assert flags["primary_item"] is True
    assert flags["item_category"] is True


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


def test_gem_fetch_applies_profile_wide_expansion_and_card_verification():
    unrelated = _gem_doc(
        "GEM/2026/B/9101",
        "4 MP ANPR camera, 64 channel NVR and surveillance hard disk",
    )
    genuine = _gem_doc(
        "GEM/2026/B/9102",
        "Smart prepaid energy meter with communication module",
    )
    requested_queries = []

    def fake_urlopen(request, **_kwargs):
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        query = json.loads(form["payload"][0])["param"]["searchBid"]
        requested_queries.append(query)
        return _FakeResponse([unrelated] if query == "smart meter" else [genuine])

    with patch("gemsentry.sources.gem.client.urllib.request.urlopen", fake_urlopen):
        tenders = fetch_keyword_bids_api(
            "smrt meter", "", "", max_pages=1, min_days_left=5,
        )

    assert "smart meter" in requested_queries
    assert "meter" in requested_queries
    assert len(requested_queries) > 1
    assert [tender["bid_no"] for tender in tenders] == ["GEM/2026/B/9102"]
    assert tenders[0]["keyword"] == "smart meter"


def test_dashboard_defaults_to_live_newly_published_view():
    with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as handle:
        html = handle.read()
    assert "let currentDeadlineBand = 'actionable';" in html
    assert '<option value="published-newest" selected>' in html
    assert '<option value="priority-desc" selected>' not in html

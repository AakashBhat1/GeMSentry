"""Coverage for broad discovery, deep pagination and RFP decimal values."""

import pytest

from gemsentry.config_store import load_keywords
from gemsentry.parsing.signals import extract_bid_signals
from gemsentry.pipeline import refresh_listing_metadata
from gemsentry.search import SearchPlan, build_search_plan, discovery_keyword, matches_search_result
from gemsentry.sources.gem import client
from gemsentry.textutils import _parse_inr_amount
from gemsentry.web.context import normalize_scrape_payload


@pytest.mark.parametrize("raw", ["19000.11", "19,000.11", "Rs. 19,000.11", "INR 19000.11", "₹19,000.11", 19000.11])
def test_currency_preserves_paise(raw):
    assert _parse_inr_amount(raw) == 19000.11


@pytest.mark.parametrize("raw", [None, True, float("nan"), float("inf"), "N/A", "-19.11"])
def test_invalid_currency_is_not_a_value(raw):
    assert _parse_inr_amount(raw) is None


def test_decimal_survives_pdf_and_listing_parsers():
    signals, flags = extract_bid_signals("Estimated Bid Value in INR (Inclusive of all taxes) 19000.11 Evaluation Method")
    assert signals["est_value_inr"] == 19000.11
    assert flags["est_value_inr"]
    assert client.doc_to_tender({"b_estimated_value": ["19000.11"]}, "drone")["est_value_inr"] == 19000.11
    html = '''<div class="card"><p class="bid_no"><a>GEM/2026/B/1234</a></p>
    <div class="col-md-4"><div class="row">Items: Drone</div>
    <div class="row">Estimated Bid Value: Rs. 19,000.11</div></div></div>'''
    assert client.parse_cards(html, "drone")[0]["est_value_inr"] == 19000.11


def test_pdf_reparse_corrects_old_corrupted_card_value():
    signals, _ = extract_bid_signals("Estimated Bid Value 19000.11", {"est_value_inr": 1900011})
    assert signals["est_value_inr"] == 19000.11


def test_decimal_survives_database_roundtrip(tmp_path):
    from gemsentry import db
    conn = db.connect(str(tmp_path))
    try:
        db.upsert_many(conn, [{"bid_no": "GEM/2030/B/1", "analysis": {"est_value_inr": 19000.11}}])
        assert db.load_all(conn)["GEM/2030/B/1"]["analysis"]["est_value_inr"] == 19000.11
    finally:
        conn.close()


def test_value_repair_preserves_original_and_manual_status(tmp_path, monkeypatch):
    from tools import repair_bid_values as repair
    pdf = tmp_path / "bid.pdf"
    pdf.write_bytes(b"%PDF-test")
    tender = {"bid_no": "GEM/2030/B/1", "local_pdf_path": str(pdf),
              "status": "Rejected", "status_source": "manual",
              "analysis": {"est_value_inr": 1900011}}
    monkeypatch.setattr(repair, "extract_text", lambda _: "Estimated Bid Value 19000.11")

    def reparse(updated, *args, **kwargs):
        assert kwargs["reparse"] is True
        updated["analysis"] = {"analysis_status": "ok", "est_value_inr": updated["est_value_inr"]}

    monkeypatch.setattr(repair, "rescore_tender", reparse)
    fixed = repair.corrected_record(tender, {}, {})
    assert fixed["est_value_inr"] == 19000.11
    assert fixed["status"] == "Rejected"
    assert tender["analysis"]["est_value_inr"] == 1900011


def test_missing_value_does_not_take_next_fields_number():
    signals, _ = extract_bid_signals("Estimated Bid Value Not disclosed Evaluation Method 2026")
    assert signals["est_value_inr"] is None


@pytest.mark.parametrize(("phrase", "broad"), [
    ("15 kWh solar", "solar"), ("12V 100AH battery", "battery"),
    ("250 KW SOLAR", "SOLAR"), ("1 MW solar plant", "solar plant"),
])
def test_capacity_does_not_restrict_discovery(phrase, broad):
    assert discovery_keyword(phrase) == broad
    plan = build_search_plan(phrase)
    assert plan.queries[0].casefold() == broad.casefold()
    assert matches_search_result({"title": f"Supply of 50 kW {broad}"}, plan)


def test_exact_profile_phrase_gets_broad_query_too():
    assert "meter" in build_search_plan("smart meter").queries


def test_all_configured_keywords_can_be_submitted():
    payload = normalize_scrape_payload({"keywords": load_keywords()})
    assert len(payload["keywords"]) > 250
    assert payload["max_pages"] is None


def test_auto_search_reaches_beyond_250_even_if_early_pages_are_irrelevant(monkeypatch):
    monkeypatch.setattr(client, "build_search_plan", lambda _: SearchPlan("drone", ("drone",), positive_terms=("drone",)))
    pages = []

    def page(*args, **kwargs):
        number = args[3]
        pages.append(number)
        docs = [{"id": str(i), "b_bid_number": [f"GEM/2030/B/{i}"],
                 "bd_category_name": ["Drone" if i >= 250 else "Facial tissue"]}
                for i in range((number - 1) * 10, min(number * 10, 270))]
        return {"response": {"response": {"docs": docs, "numFound": 270}}}

    monkeypatch.setattr(client, "_post_search_page", page)
    tenders = client.fetch_keyword_bids_api("drone", "", "")
    assert len(tenders) == 20
    assert pages == list(range(1, 28))


def test_gem_page_number_is_a_top_level_payload_field():
    payload = client.build_search_payload("drone", page_num=26)
    assert payload["page"] == 26
    assert "page" not in payload["param"]


def test_boq_title_is_available_to_relevance_filter():
    tender = client.doc_to_tender({"b_category_name": ["Custom Bid for Goods"], "bbt_title": ["Drone training laboratory"]}, "drone")
    assert matches_search_result(tender, build_search_plan("drone"))


def test_deadline_refresh_invalidates_cached_expired_analysis():
    tender = {"end_date": "old", "analysis": {"is_expired": True}, "downloaded": True}
    refresh_listing_metadata(tender, {"end_date": "new"})
    assert tender["analysis"] is None
    assert tender["downloaded"] is True


def test_repeated_portal_page_stops_without_losing_results(monkeypatch):
    monkeypatch.setattr(client, "build_search_plan", lambda _: SearchPlan("drone", ("drone",), positive_terms=("drone",)))
    calls = []

    def page(*args, **kwargs):
        calls.append(args[3])
        return {"response": {"response": {"docs": [{"id": "1", "b_bid_number": ["GEM/2030/B/1"], "bd_category_name": ["Drone"]}]}}}

    monkeypatch.setattr(client, "_post_search_page", page)
    assert len(client.fetch_keyword_bids_api("drone", "", "")) == 1
    assert calls == [1, 2]


def test_transient_server_error_retries_and_does_not_abandon_aliases(monkeypatch):
    import urllib.error
    monkeypatch.setattr(client, "build_search_plan", lambda _: SearchPlan("drone", ("drone", "uav"), positive_terms=("drone",)))
    calls = []

    def page(query, *args, **kwargs):
        calls.append(query)
        if query == "drone":
            raise urllib.error.HTTPError("https://bidplus.gem.gov.in/all-bids-data", 500, "temporary", {}, None)
        return {"response": {"response": {"docs": [{"id": "1", "b_bid_number": ["GEM/2030/B/1"], "bd_category_name": ["Drone"]}], "numFound": 1}}}

    monkeypatch.setattr(client, "_post_search_page", page)
    assert len(client.fetch_keyword_bids_api("drone", "", "")) == 1
    assert calls == ["drone", "drone", "uav"]


def test_rediscovery_refreshes_deadline_preserving_user_work():
    tender = {"end_date": "old", "status": "Shortlisted", "local_pdf_path": "saved.pdf", "first_seen": "2026-08-01"}
    refresh_listing_metadata(tender, {"end_date": "26-09-2026 12:00 PM", "status": "Pending Review", "first_seen": "2026-09-07"})
    assert tender == {"end_date": "26-09-2026 12:00 PM", "status": "Shortlisted", "local_pdf_path": "saved.pdf", "first_seen": "2026-08-01"}


@pytest.mark.parametrize("title", [
    "Drone Sml", "High Altitude Target Drone", "KAMIKAZE DRONE (OFC BASED)",
    "Procurement of Anti Drone Gun", "Drone Based Surveillance System",
    "Surveying or Mapping Drone or Unmanned Aerial Vehicle", "Multi Mission Tactical Drone",
    "Proc of Svl Drone", "DRONE DESIGN & ANALYTICS LABORATORY",
    "Unmanned Aerial Vehicle / Drones for Agricultural Purposes (V3)",
    "Custom Bid for Services - Invitation of Tender for Engaging Airborne Geophysical Survey Provider for carrying out UAV or Drone based Magnetic and LiDAR surveys",
])
def test_user_example_categories_remain_discoverable(title):
    assert matches_search_result({"title": title}, build_search_plan("drone"))

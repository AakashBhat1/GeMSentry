"""
Regression tests for the classification pipeline (BE-28 hardening).

Covers the rules that keep tenders from being misclassified:
  - strong keywords count as a full relevance match alone
  - cross-line keyword corroboration upgrades weak matches
  - plural-tolerant whole-word keyword matching
  - components business line catches small_supply search terms
  - exclusion keywords still veto service bids
  - review buffer band under fit_min surfaces borderline bids
  - expired bids always Drop

Run:  python tests/test_sorting.py   (or pytest tests/test_sorting.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402

CFG = scraper.load_scoring_config()
PROFILE = scraper.load_company_profile()
ELIG = {"verdict": "unknown"}
SIGNALS = {"est_value_inr": 100000, "primary_item": None, "item_category": None,
           "buyer_org": None, "buyer_dept": None, "consignee_state": None}


def _relevance(title):
    _, fb, bl = scraper.compute_fit_score(
        {}, dict(SIGNALS), ELIG, PROFILE, CFG, card_meta={"title": title})
    rel = next(c["subscore"] for c in fb if c.get("criterion") == "relevance")
    return rel, bl


def test_configs_valid():
    assert scraper.validate_scoring_config(CFG) is None
    assert scraper.validate_company_profile(PROFILE) is None


def test_strong_keyword_alone_is_full_match():
    rel, _ = _relevance("Supply of Surveillance Drone with accessories")
    assert rel == 1.0


def test_cross_line_corroboration_upgrades_weak_match():
    rel, _ = _relevance("COMPACT 4K FPV CAMERA DRONE FLY MORE COMBO")
    assert rel == 1.0


def test_plural_tolerant_matching():
    rel, bl = _relevance("Supply of RF Connectors and Terminal Blocks")
    assert rel > 0 and bl is not None


def test_components_line_catches_relay():
    rel, bl = _relevance("Feeder Protection Relay for substation")
    assert rel > 0
    assert (bl or {}).get("id") == "components"


def test_exclusion_vetoes_service_bids():
    rel, _ = _relevance("Manpower for operation of CCTV surveillance system")
    assert rel == 0.0
    rel, _ = _relevance("Facility Management Services for cable maintenance")
    assert rel == 0.0


def test_junk_title_scores_zero():
    rel, _ = _relevance("Tarpaulin sheets waterproof for godown storage")
    assert rel == 0.0


def test_review_band_surfaces_borderline_matches():
    fit_min = float(CFG["fit"]["fit_min"])
    band = float(CFG["fit"].get("review_band", 8))
    assert scraper.compute_recommendation(
        fit_min - 1, 80, ELIG, False, CFG, relevance_matched=True) == "Review"
    assert scraper.compute_recommendation(
        fit_min - band - 1, 80, ELIG, False, CFG, relevance_matched=True) == "Drop"
    assert scraper.compute_recommendation(
        fit_min - 1, 80, ELIG, False, CFG, relevance_matched=False) == "Drop"


def test_expired_always_drops():
    fit_min = float(CFG["fit"]["fit_min"])
    assert scraper.compute_recommendation(
        fit_min - 1, 80, ELIG, True, CFG, relevance_matched=True) == "Drop"
    assert scraper.compute_recommendation(90, 95, ELIG, True, CFG) == "Drop"


def test_high_fit_split_by_risk():
    assert scraper.compute_recommendation(80, 95, ELIG, False, CFG,
                                          relevance_matched=True) == "Pursue"
    assert scraper.compute_recommendation(80, 50, ELIG, False, CFG,
                                          relevance_matched=True) == "Review"


def test_priority_capped_below_fit_gate():
    fit_min = float(CFG["fit"]["fit_min"])
    pr = scraper.compute_priority_score(fit_min - 5, 100, ELIG, False, CFG)
    assert pr is not None and pr <= fit_min - 5


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

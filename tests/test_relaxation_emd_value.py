"""Relaxation ATC overrides, the high-EMD hold, and the optional value max.

The relaxation texts are taken from real GeM bid PDFs where the parser used to
trust the form checkbox while the buyer's ATC said otherwise.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.analysis import _partial_bar  # noqa: E402
from gemsentry.defaults import DEFAULT_COMPANY_PROFILE, DEFAULT_SCORING_CONFIG  # noqa: E402
from gemsentry.parsing.relaxation import (  # noqa: E402
    apply_atc_relaxation,
    detect_doc_has_exemption_table,
    parse_atc_relaxation,
    parse_relaxation_block,
)
from gemsentry.profile import _apply_active_preset, validate_company_profile  # noqa: E402
from gemsentry.scoring.exemptions import get_exemption_label  # noqa: E402
from gemsentry.scoring.fit import compute_fit_score  # noqa: E402
from gemsentry.scoring.verdict import apply_high_emd_hold  # noqa: E402

# --------------------------------------------------------------------------
# Relaxation: form field vs buyer's ATC
# --------------------------------------------------------------------------

FIELD_STARTUP_YES = (
    "MSE Relaxation for Years of Experience and Turnover No "
    "Startup Relaxation for Years Of Experience and Turnover Yes | Complete "
)
ATC_DEFERRED = (
    'STARTUP CLAUSE, WE HAVE TICKED EXEMPTION AS YES, HOWEVER BIDDER S HALL NOTE '
    'THAT " Exemption to Start-ups for Turnover and Years of experience to b e '
    'allowed fo r next tender on completion of successful trial order " 7. UNIT '
    'RATE SHOULD BE INCLUSIVE OF GST.'
)
FIELD_BOTH_NO = (
    "MSE Relaxation for Years of Experience and Turnover No "
    "Startup Relaxation for Years of Experience and Turnover No "
)
ATC_MSE_15_PCT = (
    "Micro and Small Enterprises (MSEs) will be given relaxation up to 15% on "
    "prior experience. (For example, if BQC value applicable to other than MSE "
    "bidders is Rs. 100/-, the same shall be Rs. 85/- for MSE bidders). Start Up: "
    "No relaxation in the prior Turnover and & experience criteria for Startups "
    "for this tender due to Public Safety."
)
ATC_QR_20_PCT = (
    "QUALIFYING REQUIREMENTS As Per Annexure-1. 2. For MSE and start-up bidders, "
    "relaxation of 20% on Qualifying Requirement i.e., towards work order amount/ "
    "financial criteria of MAAT shall be considered."
)
SPLIT_LABEL = (
    "MSE Exemption for Years Of Experience/ अनुभव के वष1 से एमएसई छू ट / / and "
    "Turnover / / टन%ओवर के िलए एमएसई को छू ट ;ा< है Yes Startup Exemption for "
    "Years Of Experience/ अनुभव के वष1 से =टाट%अप छू ट / / and Turnover / / "
    "टन%ओवर के िलए =टाट%अप को छू ट ;ा< है No "
)


def _merged(text, kind):
    scheme = "Startup" if kind == "startup" else "MSE"
    return apply_atc_relaxation(parse_relaxation_block(text, kind),
                                parse_atc_relaxation(text, kind), scheme)


def test_atc_deferring_the_exemption_overrides_a_yes_field():
    text = FIELD_STARTUP_YES + ATC_DEFERRED
    assert parse_relaxation_block(text, "startup")["exp"] == "complete"
    merged = _merged(text, "startup")
    assert (merged["exp"], merged["turn"]) == ("no", "no")
    assert "next tender" in merged["atc_note"]


def test_atc_percentage_grant_overrides_a_no_field_for_that_scheme_only():
    text = FIELD_BOTH_NO + ATC_MSE_15_PCT
    mse = _merged(text, "mse")
    assert (mse["exp"], mse["exp_pct"]) == ("partial", 15.0)
    assert mse["turn"] == "no"
    startup = _merged(text, "startup")
    assert (startup["exp"], startup["turn"]) == ("no", "no")
    assert startup["atc_note"] is None  # field already said No: nothing changed


def test_qualifying_requirement_relaxation_covers_both_criteria_and_schemes():
    text = FIELD_BOTH_NO + ATC_QR_20_PCT
    for kind in ("mse", "startup"):
        merged = _merged(text, kind)
        assert (merged["exp"], merged["turn"]) == ("partial", "partial")
        assert merged["turn_pct"] == 20.0


@pytest.mark.parametrize("clause", [
    "MSEs are not eligible for any exemptions with respect to furnishing of SD/PBG.",
    "Start-ups Sellers OR any other entity for exemption of EMD.",
])
def test_emd_and_pbg_exemption_clauses_do_not_touch_experience_or_turnover(clause):
    text = FIELD_BOTH_NO + clause
    for kind in ("mse", "startup"):
        assert parse_atc_relaxation(text, kind) is None


def test_hindi_split_label_is_read():
    assert detect_doc_has_exemption_table(SPLIT_LABEL)
    mse = parse_relaxation_block(SPLIT_LABEL, "mse")
    assert (mse["exp"], mse["turn"]) == ("complete", "complete")
    startup = parse_relaxation_block(SPLIT_LABEL, "startup")
    assert (startup["exp"], startup["turn"]) == ("no", "no")


def test_percentage_relaxation_becomes_a_concrete_bar_and_label():
    assert _partial_bar("partial", None, 20.0, 5_000_000) == pytest.approx(4_000_000)
    assert _partial_bar("partial", 2.0, 20.0, 3.0) == 2.0  # stated figure wins
    assert _partial_bar("complete", None, 20.0, 5_000_000) is None
    label = get_exemption_label("partial", "no", exp_pct=15.0)
    assert label == "Partial (Experience −15%)"


# --------------------------------------------------------------------------
# High EMD: kept under exemptions when everything else passes
# --------------------------------------------------------------------------

def _analysis(emd_amount, fit=80, verdict="eligible"):
    breakdown = [
        {"criterion": "emd", "weight": 2.0, "subscore": 0.0, "detail": ""},
        {"criterion": "startup_exemption", "weight": 1.5, "subscore": 1.0, "detail": ""},
        {"criterion": "mse_exemption", "weight": 1.5, "subscore": 1.0, "detail": ""},
        {"criterion": "prebid", "weight": 0.0, "subscore": 1.0, "detail": ""},
        {"criterion": "date_window", "weight": 1.0, "subscore": 1.0, "detail": ""},
        {"criterion": "epbg", "weight": 0.5, "subscore": 1.0, "detail": ""},
    ]
    return {
        "emd_amount": emd_amount, "fit_score": fit, "score": 69,
        "breakdown": breakdown, "auto_reject": False,
        "eligibility": {"verdict": verdict, "flags": []},
        "recommendation": "Review", "priority_score": 50.0, "reasons": [],
    }


def test_high_emd_bid_that_passes_everything_else_is_held_and_ranked():
    analysis = apply_high_emd_hold(_analysis(3_500_000), DEFAULT_SCORING_CONFIG)
    assert analysis["high_emd"] and analysis["high_emd_hold"]
    assert analysis["recommendation"] == "Review"
    assert analysis["priority_score"] > 50.0  # ranked as if the EMD were waived
    assert any("kept under exemptions" in r for r in analysis["reasons"])


@pytest.mark.parametrize("fit,verdict", [(40, "eligible"), (80, "turnover_gap")])
def test_high_emd_bid_failing_another_check_is_not_held(fit, verdict):
    analysis = apply_high_emd_hold(_analysis(3_500_000, fit, verdict),
                                   DEFAULT_SCORING_CONFIG)
    assert analysis["high_emd"] and not analysis["high_emd_hold"]
    assert analysis["priority_score"] == 50.0


def test_emd_at_or_below_the_cap_is_not_flagged():
    analysis = apply_high_emd_hold(_analysis(2_000_000), DEFAULT_SCORING_CONFIG)
    assert not analysis["high_emd"] and not analysis["high_emd_hold"]


def test_rescoring_twice_does_not_duplicate_the_reason():
    analysis = apply_high_emd_hold(_analysis(3_500_000), DEFAULT_SCORING_CONFIG)
    apply_high_emd_hold(analysis, DEFAULT_SCORING_CONFIG)
    assert sum("High EMD" in r for r in analysis["reasons"]) == 1


# --------------------------------------------------------------------------
# Value: no upper limit unless one is set
# --------------------------------------------------------------------------

def _value_subscore(value, sweet_max):
    profile = dict(DEFAULT_COMPANY_PROFILE)
    profile["value_preference"] = {"sweet_min_inr": 500_000, "sweet_max_inr": sweet_max}
    signals = {"est_value_inr": value, "primary_item": "drone", "consignee_state": None}
    _score, breakdown, _line = compute_fit_score(
        {}, signals, {"verdict": "eligible"}, profile, DEFAULT_SCORING_CONFIG,
        card_meta={"title": "drone"})
    return next(b["subscore"] for b in breakdown if b["criterion"] == "value_fit")


def test_large_tender_is_not_penalised_without_a_max():
    assert _value_subscore(250_000_000, None) == 1.0  # ₹25 Cr


def test_a_max_still_applies_when_the_user_sets_one():
    assert _value_subscore(250_000_000, 50_000_000) < 1.0


def test_preset_with_null_max_clears_an_old_max():
    profile = {
        "value_preference": {"sweet_min_inr": 60_000, "sweet_max_inr": 200_000},
        "active_preset": "main",
        "value_presets": {"main": {"sweet_min_inr": 500_000, "sweet_max_inr": None}},
    }
    assert _apply_active_preset(profile)["value_preference"]["sweet_max_inr"] is None


def test_profile_validation_accepts_a_blank_max_and_rejects_max_below_min():
    profile = dict(DEFAULT_COMPANY_PROFILE)
    profile["value_preference"] = {"sweet_min_inr": 500_000, "sweet_max_inr": None}
    assert validate_company_profile(profile) is None
    profile["value_preference"] = {"sweet_min_inr": 500_000, "sweet_max_inr": 100}
    assert "sweet_max_inr" in validate_company_profile(profile)

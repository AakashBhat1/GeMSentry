"""
Regression tests for Startup/MSE relaxation parsing (BE-29).

GeM bid PDFs label this field "Relaxation" (not "Exemption") and the buyer
picks both the scope and the degree:

    <Startup|MSE> Relaxation for <scope> <Yes|No>
        [ | <Complete|Partial> [ | Experience - n year (s) ]
                               [ | Turn over value - n (in lakhs) ] ]

The strings below are verbatim extractions from tenders/downloads, including
the interleaved Devanagari the PDF text layer produces.

Run:  python tests/test_relaxation.py   (or pytest tests/test_relaxation.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402

PROFILE = scraper.load_company_profile()

# --- Verbatim corpus samples -------------------------------------------------
# Both criteria, refused (GEM_2026_B_7684162)
BOTH_NO = ("/MSE Relaxation for Years of Experience and Turnover No ' % ' % "
           "/Startup Relaxation for Years of Experience and Turnover No  5  5")

# Both criteria, fully waived (GEM_2026_B_7700419)
BOTH_COMPLETE = ("MSE Relaxation for Years Of Experience and Turnover Yes | Complete "
                 "' % ' % Startup Relaxation for Years Of Experience and Turnover "
                 "Yes | Complete  6 / Bid Number")

# Turnover only, partial with amount (GEM_2026_B_7849595)
TURN_PARTIAL = ("MSE Relaxation for Turnover Yes | Partial | Turn over value - 50 "
                "(in lakhs) टन%ओवर के िलए / Startup Relaxation for Turnover "
                "Yes | Partial | Turn over value - 50 (in lakhs) \x01बड सं>या")

# Experience only, partial with amount (GEM_2026_B_7710748)
EXP_PARTIAL = ("MSE Relaxation for Years Of Experience Yes | Partial | "
               "Experience - 3 year (s) वष1 के अनुभव के िलए / "
               "Startup Relaxation for Years Of Experience Yes | Partial | "
               "Experience - 3 year (s) \x01व;ेता से")

# Both criteria, partial with both amounts (GEM_2026_B_7701840)
BOTH_PARTIAL = ("MSE Relaxation for Years Of Experience and Turnover Yes | Partial | "
                "Experience - 2 year (s) | Turn over value - 162 (in lakhs) 'टाट%अप "
                "Startup Relaxation for Years Of Experience and Turnover Yes | Partial | "
                "Experience - 2 year (s) | Turn over value - 162 (in lakhs) \x01वLेता")

# Schemes disagree: MSE relaxes experience, Startup relaxes turnover
# (GEM_2026_B_7657481)
SPLIT_SCOPES = ("MSE Relaxation for Years Of Experience Yes | Partial | "
                "Experience - 1 year (s) टन%ओवर के िलए / "
                "Startup Relaxation for Turnover Yes | Partial | "
                "Turn over value - 2 (in lakhs) \x01वMेता से")

# Decimal lakh figure (GEM_2026_B_7836961)
DECIMAL_LAKH = ("MSE Relaxation for Turnover Yes | Partial | "
                "Turn over value - 1.05 (in lakhs) ' % ' % 2")

# Only the boilerplate ATC prose — must NOT be read as a relaxation table
PROSE_ONLY = ("*In case any bidder is seeking exemption from Experience / Turnover "
              "Criteria, the supporting documents to prove his eligibility for "
              "exemption must be uploaded for evaluation by the buyer")


# --- Detection ---------------------------------------------------------------

def test_detects_relaxation_table():
    for text in (BOTH_NO, BOTH_COMPLETE, TURN_PARTIAL, EXP_PARTIAL,
                 BOTH_PARTIAL, SPLIT_SCOPES, DECIMAL_LAKH):
        assert scraper.detect_doc_has_exemption_table(text), text[:60]


def test_atc_prose_is_not_a_table():
    assert not scraper.detect_doc_has_exemption_table(PROSE_ONLY)
    assert not scraper.detect_doc_has_exemption_table("")


def test_legacy_exemption_spelling_still_parses():
    text = "Startup Exemption for Years of Experience and Turnover Yes | Complete"
    r = scraper.parse_relaxation_block(text, "startup")
    assert (r["exp"], r["turn"]) == ("complete", "complete")


# --- Scope x degree ----------------------------------------------------------

def test_both_refused():
    for kind in ("startup", "mse"):
        r = scraper.parse_relaxation_block(BOTH_NO, kind)
        assert (r["exp"], r["turn"]) == ("no", "no"), kind
        assert r["exp_parsed"] and r["turn_parsed"]


def test_both_complete():
    for kind in ("startup", "mse"):
        r = scraper.parse_relaxation_block(BOTH_COMPLETE, kind)
        assert (r["exp"], r["turn"]) == ("complete", "complete"), kind
        assert r["exp_years"] is None and r["turnover_inr"] is None


def test_turnover_only_partial_carries_amount():
    r = scraper.parse_relaxation_block(TURN_PARTIAL, "mse")
    assert r["exp"] == "no", "experience is out of scope → not relaxed"
    assert r["turn"] == "partial"
    assert r["turnover_inr"] == 5000000  # 50 lakh
    assert r["exp_years"] is None


def test_experience_only_partial_carries_amount():
    r = scraper.parse_relaxation_block(EXP_PARTIAL, "startup")
    assert r["exp"] == "partial"
    assert r["exp_years"] == 3.0
    assert r["turn"] == "no"
    assert r["turnover_inr"] is None


def test_both_partial_carries_both_amounts():
    r = scraper.parse_relaxation_block(BOTH_PARTIAL, "mse")
    assert (r["exp"], r["turn"]) == ("partial", "partial")
    assert r["exp_years"] == 2.0
    assert r["turnover_inr"] == 16200000  # 162 lakh


def test_schemes_parsed_independently():
    st = scraper.parse_relaxation_block(SPLIT_SCOPES, "startup")
    mse = scraper.parse_relaxation_block(SPLIT_SCOPES, "mse")
    assert (mse["exp"], mse["turn"]) == ("partial", "no")
    assert mse["exp_years"] == 1.0
    assert (st["exp"], st["turn"]) == ("no", "partial")
    assert st["turnover_inr"] == 200000  # 2 lakh


def test_decimal_lakh_amount():
    r = scraper.parse_relaxation_block(DECIMAL_LAKH, "mse")
    assert r["turnover_inr"] == 105000  # 1.05 lakh


def test_missing_label_is_unknown_not_denial():
    r = scraper.parse_relaxation_block(PROSE_ONLY, "mse")
    assert (r["exp"], r["turn"]) == ("unknown", "unknown")
    assert not r["found"]


# --- Labels ------------------------------------------------------------------

def test_labels_quote_the_reduced_bar():
    assert scraper.get_exemption_label("complete", "complete") == "Yes (Full)"
    assert scraper.get_exemption_label("no", "no") == "No Relaxation"
    assert scraper.get_exemption_label("unknown", "unknown") == "Unknown"
    assert scraper.get_exemption_label("complete", "no") == "Yes (Experience Only)"
    assert scraper.get_exemption_label("no", "complete") == "Yes (Turnover Only)"

    assert scraper.get_exemption_label(
        "partial", "no", exp_years=3.0) == "Partial (Experience ≤ 3 yr)"
    assert scraper.get_exemption_label(
        "no", "partial", turnover_inr=5000000) == "Partial (Turnover ≤ 50 lakh)"
    # Above a crore GeM's lakh figures get unreadable — render as crore.
    assert scraper.get_exemption_label(
        "partial", "partial", exp_years=2.0, turnover_inr=16200000
    ) == "Partial (Experience ≤ 2 yr, Turnover ≤ 1.62 crore)"
    # Mixed degrees must not silently read as a full waiver.
    assert scraper.get_exemption_label(
        "complete", "partial", turnover_inr=200000
    ) == "Partial (Turnover ≤ 2 lakh) + Experience Waived"


def test_partial_amount_missing_is_flagged_not_dropped():
    lab = scraper.get_exemption_label("no", "partial", turnover_inr=None)
    assert "amount not stated" in lab


# --- Scoring -----------------------------------------------------------------

def test_partial_scores_between_refused_and_complete():
    refused = scraper._exemption_pair_subscore("no", "no", 0.5)
    partial = scraper._exemption_pair_subscore("partial", "partial", 0.5)
    complete = scraper._exemption_pair_subscore("complete", "complete", 0.5)
    assert refused < partial < complete
    assert complete == 1.0 and refused == 0.0


def test_no_relaxation_is_neutral_not_penalised():
    """
    Absent relaxation means normal terms, not risk. Before the parser fix ~98%
    of docs were unreadable and got pinned at full credit; scoring a readable
    "not relaxed" at 0.0 would have swung ~27 pts of risk score corpus-wide.
    """
    cfg = scraper.load_scoring_config()
    floor = float(cfg.get("no_relaxation_floor", 0.5))
    assert 0.0 <= floor <= 1.0
    refused = max(scraper._exemption_pair_subscore("no", "no", 0.5), floor)
    granted = max(scraper._exemption_pair_subscore("complete", "complete", 0.5), floor)
    assert refused == floor
    assert granted > refused, "relaxations must remain a real bonus"


def test_relaxation_granted_helper():
    assert scraper.relaxation_granted("complete")
    assert scraper.relaxation_granted("partial")
    assert not scraper.relaxation_granted("no")
    assert not scraper.relaxation_granted("unknown")


# --- Eligibility -------------------------------------------------------------

def _elig(rfp_turn, st_turn, mse_turn, relax_inr=None, **extra):
    signals = {"rfp_min_turnover_inr": rfp_turn}
    signals.update(extra)
    return scraper.compute_eligibility(
        signals, st_turn, mse_turn, PROFILE, relax_turnover_inr=relax_inr)


def test_complete_waiver_clears_turnover_gap():
    # Requirement far above company turnover, but fully waived.
    res = _elig(500000000, "complete", "no")
    assert res["verdict"] == "eligible"


def test_partial_bar_we_can_clear_is_eligible():
    company = float(PROFILE["eligibility"]["annual_turnover_inr"])
    res = _elig(500000000, "no", "partial", relax_inr=int(company // 2))
    assert res["verdict"] == "eligible"
    assert "turnover_bar_relaxed" in res["flags"]


def test_partial_bar_we_cannot_clear_is_still_a_gap():
    company = float(PROFILE["eligibility"]["annual_turnover_inr"])
    res = _elig(500000000, "no", "partial", relax_inr=int(company * 10))
    assert res["verdict"] == "turnover_gap"
    assert "turnover_bar_relaxed" in res["flags"]
    assert "even after the partial relaxation" in res["detail"]


def test_partial_without_amount_is_treated_as_waived():
    res = _elig(500000000, "no", "partial", relax_inr=None)
    assert res["verdict"] == "eligible"


def test_refused_relaxation_keeps_the_gap():
    res = _elig(500000000, "no", "no")
    assert res["verdict"] == "turnover_gap"


def test_experience_bar_respects_relaxation():
    company_exp = int(PROFILE["eligibility"]["years_experience"])
    demanding = company_exp + 10
    # No relaxation → flagged tight
    res = _elig(None, "no", "no", rfp_min_experience_years=demanding,
                relax_experience_state="no")
    assert "experience_may_be_tight" in res["flags"]
    # Fully waived → not flagged
    res = _elig(None, "no", "no", rfp_min_experience_years=demanding,
                relax_experience_state="complete")
    assert "experience_may_be_tight" not in res["flags"]
    # Partially reduced to something we clear → not flagged
    res = _elig(None, "no", "no", rfp_min_experience_years=demanding,
                relax_experience_state="partial",
                relax_experience_years=1)
    assert "experience_may_be_tight" not in res["flags"]
    # Partially reduced but still above us → flagged
    res = _elig(None, "no", "no", rfp_min_experience_years=demanding,
                relax_experience_state="partial",
                relax_experience_years=demanding - 1)
    assert "experience_may_be_tight" in res["flags"]


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

"""Turnover/experience eligibility gating."""

from gemsentry.parsing.amounts import CONFLICTING, NOT_REQUIRED, NOT_STATED, PARSED

# States in which the document did give us a trustworthy answer about the
# turnover bar. Anything else has to go to a human: a field we could not read,
# or one the document contradicts itself about, is not evidence of eligibility.
_TRUSTED_TURNOVER_STATES = {PARSED, NOT_REQUIRED, NOT_STATED}


def _turnover_state(signals):
    """Extraction state for the RFP turnover bar, tolerant of older records.

    Records written before the state was tracked carry only the amount, so
    infer the state the old code implied: a value means parsed, its absence
    means the label was never found.
    """
    state = signals.get("rfp_min_turnover_state")
    if state:
        return state
    return PARSED if signals.get("rfp_min_turnover_inr") is not None else NOT_STATED


def compute_eligibility(signals, st_turn, mse_turn, profile, exemptions_na=False,
                        relax_turnover_inr=None):
    """
    Soft eligibility gate (BE-09). Credits MSE/Startup turnover relaxations.
    Returns {verdict, flags, detail}.
    BE-17: when exemption table absent-by-design (exemptions_na), do not imply denial.

    st_turn / mse_turn are per-scheme turnover states (complete|partial|no|unknown).
    relax_turnover_inr is the *reduced* turnover bar quoted by a partial
    relaxation; when present it replaces the RFP's headline requirement, since
    that lower bar is what we would actually have to clear as an MSE/Startup.
    """
    elig = profile.get("eligibility", {})
    company_turn = float(elig.get("annual_turnover_inr", 0) or 0)
    rfp_turn = signals.get("rfp_min_turnover_inr")
    flags = []
    detail_parts = []

    # A partial grant lowers the bar rather than removing it.
    partial_turn = "partial" in (st_turn, mse_turn)
    if partial_turn and relax_turnover_inr is not None:
        if rfp_turn is None or relax_turnover_inr < rfp_turn:
            if rfp_turn is not None:
                detail_parts.append(
                    f"Partial turnover relaxation lowers the bar from "
                    f"₹{rfp_turn:,} to ₹{relax_turnover_inr:,}."
                )
            else:
                detail_parts.append(
                    f"Partial turnover relaxation sets the bar at ₹{relax_turnover_inr:,}."
                )
            rfp_turn = relax_turnover_inr
            flags.append("turnover_bar_relaxed")

    # Full waiver only counts when the criterion was completely relaxed, or when
    # a partial grant gave no figure to test against (bar unknown → treat as waived).
    turn_exempt = (
        st_turn == "complete" or mse_turn == "complete"
        or (partial_turn and relax_turnover_inr is None)
    )
    turn_exempt_unknown = (st_turn == "unknown" and mse_turn == "unknown")

    # A requirement we could not read, or one the document states twice with
    # different figures, must not be scored as "no requirement" -- that is the
    # path that reported a ₹75 lakh bar as eligible. It only stops mattering
    # when the criterion is waived outright, or when a partial relaxation has
    # already replaced it with an explicit bar we can test.
    turnover_state = _turnover_state(signals)
    if (turnover_state not in _TRUSTED_TURNOVER_STATES
            and not turn_exempt
            and "turnover_bar_relaxed" not in flags):
        flags.append(
            "turnover_req_conflicting" if turnover_state == CONFLICTING
            else "turnover_req_unreadable"
        )
        if turnover_state == CONFLICTING and rfp_turn is not None:
            detail_parts.append(
                f"The document states conflicting minimum turnovers "
                f"(highest read: ₹{rfp_turn:,}); needs review before bidding."
            )
        else:
            detail_parts.append(
                "A minimum-turnover requirement is stated but its amount could "
                "not be read; eligibility needs review before bidding."
            )
        evidence = signals.get("rfp_min_turnover_evidence")
        if evidence:
            detail_parts.append(f"Source text: \"{evidence}\"")
        return {
            "verdict": "unknown",
            "flags": flags,
            "detail": " ".join(detail_parts),
        }

    if exemptions_na:
        # Bid-type doc without exemption tables — neutral, not a denial
        flags.append("no_exemption_data_in_doc_type")
        if rfp_turn is None:
            verdict = "eligible"
            detail_parts.append(
                "No exemption data in this doc type (Bid/simple PDF); "
                "no RFP min-turnover found → treated as eligible."
            )
        elif rfp_turn <= company_turn:
            verdict = "eligible"
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} ≤ company ₹{int(company_turn):,}. "
                "No exemption data in this doc type (not a denial)."
            )
        else:
            verdict = "unknown"
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,}; "
                "no exemption data in this doc type — cannot confirm waiver (not a denial)."
            )
        return {
            "verdict": verdict,
            "flags": flags,
            "detail": " ".join(detail_parts) if detail_parts else "Eligibility evaluated."
        }

    if rfp_turn is None:
        if turn_exempt:
            verdict = "eligible"
            detail_parts.append("RFP turnover requirement unparsed; turnover exemption present → eligible.")
        elif turn_exempt_unknown:
            verdict = "unknown"
            flags.append("turnover_req_unparsed")
            detail_parts.append("RFP turnover requirement and exemptions unparsed.")
        elif turnover_state == NOT_REQUIRED:
            verdict = "eligible"
            detail_parts.append(
                "The RFP explicitly states no minimum-turnover requirement."
            )
        else:
            # Label absent from the document: a genuine absence, not a failed read.
            verdict = "eligible"
            detail_parts.append("No RFP min-turnover found; treated as eligible.")
    elif rfp_turn <= company_turn:
        verdict = "eligible"
        detail_parts.append(
            f"RFP min turnover ₹{rfp_turn:,} ≤ company ₹{int(company_turn):,}."
        )
    else:
        # requirement exceeds company turnover
        if turn_exempt:
            verdict = "eligible"
            flags.append("turnover_above_profile_but_exempted")
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,} "
                f"but MSE/Startup turnover relaxation granted → eligible."
            )
        elif st_turn == "unknown" or mse_turn == "unknown":
            # partial unknown with requirement gap
            if turn_exempt:
                verdict = "eligible"
            else:
                verdict = "unknown"
                flags.append("turnover_gap_uncertain")
                detail_parts.append(
                    f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,}; "
                    f"exemption status incomplete."
                )
        else:
            verdict = "turnover_gap"
            flags.append("turnover_gap")
            gap_reason = ("even after the partial relaxation"
                          if "turnover_bar_relaxed" in flags
                          else "and no MSE/Startup turnover relaxation")
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,} "
                f"{gap_reason}."
            )

    # Experience soft flag only
    rfp_exp = signals.get("rfp_min_experience_years")
    company_exp = elig.get("years_experience")
    exp_state = signals.get("relax_experience_state", "unknown")
    relax_exp_years = signals.get("relax_experience_years")
    if rfp_exp is not None and company_exp is not None:
        try:
            required_exp = float(rfp_exp)
            if exp_state == "complete":
                required_exp = None  # fully waived
            elif exp_state == "partial":
                if relax_exp_years is not None:
                    required_exp = min(required_exp, float(relax_exp_years))
                else:
                    required_exp = None  # reduced by an unstated amount
            if required_exp is not None and required_exp > float(company_exp):
                flags.append("experience_may_be_tight")
                detail_parts.append(
                    f"RFP experience {rfp_exp}y vs company {company_exp}y "
                    f"(effective bar {required_exp:g}y, soft flag)."
                )
        except (TypeError, ValueError):
            pass

    return {
        "verdict": verdict,
        "flags": flags,
        "detail": " ".join(detail_parts) if detail_parts else "Eligibility evaluated."
    }

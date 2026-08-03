"""Turnover/experience eligibility gating."""


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
        else:
            # no requirement found; assume eligible
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

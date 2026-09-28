"""Exemption labelling and relaxation sub-scores."""

from gemsentry.constants import CRORE_INR, LAKH_INR
from gemsentry.parsing.relaxation import relaxation_granted


def _format_lakhs(inr):
    """Render an INR figure in the lakh/crore notation GeM uses."""
    if inr >= CRORE_INR:
        return f"{inr / float(CRORE_INR):g} crore"
    return f"{inr / float(LAKH_INR):g} lakh"


def _reduction_note(amount_text, pct):
    """'≤ 2 yr' for a stated bar, '−15%' for an ATC percentage, else a gap note."""
    if amount_text is not None:
        return f"≤ {amount_text}"
    if pct is not None:
        return f"−{pct:g}%"
    return None


def get_exemption_label(exp, turn, exp_years=None, turnover_inr=None,
                        exp_pct=None, turn_pct=None):
    """
    Map a relaxation pair to a UI label, quoting the reduced bar for partials.

    Examples:
        "Yes (Full)"
        "Yes (Turnover Only)"
        "Partial (Experience ≤ 2 yr, Turnover ≤ 1.62 crore)"
        "Partial (Turnover ≤ 50 lakh) + Experience Waived"
        "No Relaxation"
    """
    if exp == "unknown" and turn == "unknown":
        return "Unknown"

    def amount_note():
        parts = []
        if exp == "partial":
            note = _reduction_note(
                f"{exp_years:g} yr" if exp_years is not None else None, exp_pct)
            parts.append(f"Experience {note}" if note
                         else "Experience reduced (amount not stated)")
        if turn == "partial":
            note = _reduction_note(
                _format_lakhs(turnover_inr) if turnover_inr is not None else None,
                turn_pct)
            parts.append(f"Turnover {note}" if note
                         else "Turnover reduced (amount not stated)")
        return ", ".join(parts)

    # Both dimensions fully waived
    if exp == "complete" and turn == "complete":
        return "Yes (Full)"

    has_partial = "partial" in (exp, turn)
    if has_partial:
        label = f"Partial ({amount_note()})"
        # Call out the other dimension when it is fully waived or untouched.
        if exp == "complete":
            label += " + Experience Waived"
        elif turn == "complete":
            label += " + Turnover Waived"
        return label

    if exp == "complete":
        return "Yes (Experience Only)"
    if turn == "complete":
        return "Yes (Turnover Only)"
    if exp == "unknown" or turn == "unknown":
        return "Unknown"
    return "No Relaxation"


def _best_relaxed_bar(*values):
    """Lowest (most favourable) relaxed threshold among the schemes, or None."""
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _describe_relaxation(scheme, relax):
    """One-line human explanation of a parsed relaxation block, for reasons[]."""
    exp, turn = relax["exp"], relax["turn"]
    if exp == "unknown" and turn == "unknown":
        return f"Relaxation Check: {scheme} relaxation fields could not be parsed."
    if exp == "complete" and turn == "complete":
        return f"Relaxation Check: Full {scheme} relaxation (Experience + Turnover)."
    if not relaxation_granted(exp) and not relaxation_granted(turn):
        return f"Relaxation Check: {scheme} Experience/Turnover criteria NOT relaxed."

    bits = []
    for dim, name in (("exp", "Experience"), ("turn", "Turnover")):
        state = relax[dim]
        if state == "complete":
            bits.append(f"{name} fully waived")
        elif state == "partial":
            if dim == "exp":
                stated = (f"{relax['exp_years']:g} yr"
                          if relax["exp_years"] is not None else None)
            else:
                stated = (_format_lakhs(relax["turnover_inr"])
                          if relax["turnover_inr"] is not None else None)
            note = _reduction_note(stated, relax.get(f"{dim}_pct"))
            bits.append(f"{name} reduced ({note or 'amount not stated'})")
        elif state == "no":
            bits.append(f"{name} not relaxed")
    return f"Relaxation Check: {scheme} — " + "; ".join(bits) + "."


_PARTIAL_RELAX_CREDIT = 0.6  # partial waiver is worth 60% of a full one


def _exemption_pair_subscore(exp, turn, unknown_subscore):
    """
    Fraction of the pair relaxed. Each dimension contributes up to 0.5:
    complete = full credit, partial = a reduced bar (still a real advantage),
    unknown = unknown_subscore/2, no = nothing.
    """
    def one(v):
        if v == "complete":
            return 0.5
        if v == "partial":
            return 0.5 * _PARTIAL_RELAX_CREDIT
        if v == "unknown":
            return unknown_subscore / 2.0
        return 0.0  # "no"
    return max(0.0, min(1.0, one(exp) + one(turn)))

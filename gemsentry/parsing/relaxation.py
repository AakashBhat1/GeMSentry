"""Startup/MSE exemption-table parsing."""

import re

from gemsentry.constants import LAKH_INR


RELAX_WORD = r'(?:Relaxation|Exemption)'

# Scope phrases, longest first so "Experience and Turnover" wins over "Experience".


RELAX_SCOPES = (
    (r'Years?\s+[Oo]f\s+Experience\s+and\s+Turnover', ("exp", "turn")),
    (r'Years?\s+[Oo]f\s+Experience', ("exp",)),
    (r'Experience\s+and\s+Turnover', ("exp", "turn")),
    (r'Experience', ("exp",)),
    (r'Turnover', ("turn",)),
)

# Full field label, e.g.
#   "MSE Relaxation for Years Of Experience and Turnover Yes | Partial | ..."


RELAX_LABEL_RX = (
    r'\b(?P<kind>Startup|MSE)\s+' + RELAX_WORD + r'\s+for\s+'
    r'(?P<scope>' + r'|'.join(p for p, _ in RELAX_SCOPES) + r')\s*'
    r'(?P<answer>Yes|No)\b'
)

# Value qualifiers that follow "Yes": "| Complete" or "| Partial | <amounts>"


RELAX_GRADE_RX = r'\|\s*(?P<grade>Complete|Partial)\b'


RELAX_EXP_AMOUNT_RX = r'Experience\s*[-–]\s*(?P<n>\d+(?:\.\d+)?)\s*year'


RELAX_TURN_AMOUNT_RX = (
    r'Turn\s*over\s+value\s*[-–]\s*(?P<n>\d+(?:\.\d+)?)\s*\(?\s*in\s+lakh'
)

# Amounts sit immediately after the answer; keep the window tight so the
# bilingual noise / next field cannot leak in.


RELAX_VALUE_WINDOW = 120


RELAX_STATE_RANK = {"unknown": 0, "no": 1, "partial": 2, "complete": 3}


def relaxation_granted(state):
    """True when the buyer relaxed this criterion at all (fully or partially)."""
    return state in ("complete", "partial")


def detect_doc_has_exemption_table(text_clean):
    """
    BE-17: True only when relaxation/exemption field labels are clearly present.
    Conservative — if unsure, return False so fields stay 'unknown' not N/A.
    Requires an actual Startup/MSE field label (not ATC prose about exemptions).
    """
    if not text_clean:
        return False
    return re.search(RELAX_LABEL_RX, text_clean, re.IGNORECASE) is not None


def _empty_relaxation():
    return {
        "exp": "unknown",
        "turn": "unknown",
        "exp_years": None,
        "turnover_inr": None,
        "exp_parsed": False,
        "turn_parsed": False,
        "found": False,
    }


def parse_relaxation_block(text_clean, kind):
    """
    Parse the GeM "Startup/MSE Relaxation for ..." field into a structured result.

    The buyer chooses *which* criteria to relax and *how much*, so the field is
    a scope plus a graded answer. Observed grammar across the tender corpus:

        <Startup|MSE> Relaxation for <scope> <Yes|No>
            [ | <Complete|Partial>
              [ | Experience - <n> year (s) ]
              [ | Turn over value - <n> (in lakhs) ] ]

    <scope> is one of "Years Of Experience and Turnover", "Years Of Experience",
    or "Turnover". A dimension the buyer left out of scope is not relaxed, so it
    resolves to "no" (not "unknown") once any label for this kind was found.

    "Yes | Partial" means the criterion still applies but at a *reduced*
    threshold — the quoted experience/turnover figures are that reduced bar,
    which callers must compare against the company profile rather than treating
    the tender as fully waived.

    kind: 'startup' or 'mse'
    Returns dict: exp/turn in {complete, partial, no, unknown}, exp_years (float),
    turnover_inr (int), exp_parsed/turn_parsed (bool), found (bool).
    """
    result = _empty_relaxation()
    if not text_clean:
        return result

    want = "startup" if kind == "startup" else "mse"

    for m in re.finditer(RELAX_LABEL_RX, text_clean, re.IGNORECASE):
        if m.group("kind").lower() != want:
            continue
        result["found"] = True

        dims = _scope_dimensions(m.group("scope"))
        answer = m.group("answer").lower()
        tail = text_clean[m.end():m.end() + RELAX_VALUE_WINDOW]

        if answer == "no":
            state, exp_years, turn_inr = "no", None, None
        else:
            grade = re.match(r'\s*' + RELAX_GRADE_RX, tail, re.IGNORECASE)
            grade_word = grade.group("grade").lower() if grade else "complete"
            if grade_word == "partial":
                state = "partial"
                exp_years, turn_inr = _parse_relaxation_amounts(tail)
            else:
                state = "complete"
                exp_years = turn_inr = None

        # Dimensions inside the scope take the answer; those outside are not
        # relaxed by this label, but a different label may still cover them.
        for dim in ("exp", "turn"):
            new_state = state if dim in dims else "no"
            if RELAX_STATE_RANK[new_state] > RELAX_STATE_RANK[result[dim]]:
                result[dim] = new_state
            if dim in dims:
                result[f"{dim}_parsed"] = True

        if "exp" in dims and exp_years is not None:
            result["exp_years"] = exp_years
        if "turn" in dims and turn_inr is not None:
            result["turnover_inr"] = turn_inr

    # A partial grant whose amount never parsed is indistinguishable from a
    # complete waiver downstream; keep it partial but leave the bar unknown.
    return result


def _scope_dimensions(scope_text):
    """Map a matched scope phrase to the dimensions it covers."""
    for pattern, dims in RELAX_SCOPES:
        if re.fullmatch(pattern, scope_text, re.IGNORECASE):
            return dims
    # Fall back to substring inspection for unseen phrasings.
    low = scope_text.lower()
    dims = []
    if "experience" in low:
        dims.append("exp")
    if "turnover" in low or "turn over" in low:
        dims.append("turn")
    return tuple(dims) or ("exp", "turn")


def _parse_relaxation_amounts(tail):
    """Extract (experience_years, turnover_inr) from a 'Partial' value block."""
    exp_years = turn_inr = None
    m = re.search(RELAX_EXP_AMOUNT_RX, tail, re.IGNORECASE)
    if m:
        try:
            exp_years = float(m.group("n"))
        except ValueError:
            exp_years = None
    m = re.search(RELAX_TURN_AMOUNT_RX, tail, re.IGNORECASE)
    if m:
        try:
            turn_inr = int(round(float(m.group("n")) * LAKH_INR))
        except ValueError:
            turn_inr = None
    return exp_years, turn_inr


def parse_exemption_pair(text_clean, kind):
    """
    Back-compat wrapper around parse_relaxation_block.
    Returns (exp, turn, exp_parsed, turn_parsed) where each state is one of
    complete|partial|no|unknown.
    """
    r = parse_relaxation_block(text_clean, kind)
    return r["exp"], r["turn"], r["exp_parsed"], r["turn_parsed"]

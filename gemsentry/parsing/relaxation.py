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

# Older bilingual layout splits the label with Hindi text, e.g.
#   "MSE Exemption for Years Of Experience/ <hindi> / / and Turnover / / <hindi> Yes"
# The gaps hold no Latin letters, so the first Latin word after "Turnover" is
# the answer. Scope is always Experience + Turnover in this layout.
RELAX_SPLIT_LABEL_RX = (
    r'\b(?P<kind>Startup|MSE)\s+' + RELAX_WORD + r'\s+for\s+'
    r'Years?\s+Of\s+Experience\s*/[^A-Za-z]{0,200}?and\s+Turnover'
    r'[^A-Za-z]{0,200}?(?P<answer>Yes|No)\b'
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
    return any(re.search(rx, text_clean, re.IGNORECASE)
               for rx in (RELAX_LABEL_RX, RELAX_SPLIT_LABEL_RX))


def _label_matches(text_clean):
    """Yield (match, dims) for every relaxation label, standard or split layout."""
    strict = list(re.finditer(RELAX_LABEL_RX, text_clean, re.IGNORECASE))
    for m in strict:
        yield m, _scope_dimensions(m.group("scope"))
    covered = [m.span() for m in strict]
    for m in re.finditer(RELAX_SPLIT_LABEL_RX, text_clean, re.IGNORECASE):
        if not any(s <= m.start() < e for s, e in covered):
            yield m, ("exp", "turn")


def _empty_relaxation():
    return {
        "exp": "unknown",
        "turn": "unknown",
        "exp_years": None,
        "turnover_inr": None,
        # Percentage cut quoted by an ATC clause ("relaxation of 20%"), for
        # partial grants that give no absolute bar.
        "exp_pct": None,
        "turn_pct": None,
        "exp_parsed": False,
        "turn_parsed": False,
        "found": False,
        "atc_note": None,
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

    for m, dims in _label_matches(text_clean):
        if m.group("kind").lower() != want:
            continue
        result["found"] = True

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


# --- Buyer-written clauses (ATC / BQC) that change what the form field says ---
#
# The GeM form field is a checkbox; buyers often qualify it in the ATC. Seen in
# the corpus: the field says "Yes | Complete" while the ATC says the exemption
# only applies "for next tender on completion of successful trial order", and
# the field says "No" while the ATC grants MSEs "relaxation up to 15% on prior
# experience". The buyer's own clause is the more specific statement, so it wins.

ATC_KIND_RX = {
    "startup": r'start\s*-?\s*ups?\b',
    "mse": r'\bMSEs?\b|\bmicro\s*(?:and|&|,)\s*small',
}

# Words that tie a clause to the experience / turnover criteria (and not to
# EMD, security deposit or PBG exemptions, which are a different thing).
ATC_EXP_RX = r'experience|work\s*orders?|past\s+performance'
ATC_TURN_RX = r'turn\s*-?\s*over|financial\s+criteri|\bMAAT\b'
ATC_BOTH_RX = r'qualifying\s+requirement|\bBQC\b|\bPQC?\b|eligibility\s+criteri'

ATC_DENY_RXS = (
    r'\bno\s+(?:relaxation|exemption)s?\s+(?:in|on|for|of|to)\b',
    r'(?:relaxation|exemption)s?[^.]{0,80}?\b(?:shall|will|would)?\s*not\s+(?:be\s+)?'
    r'(?:allowed|applicable|admissible|given|granted|permitted|available|considered)',
    r'\bnot\s+(?:be\s+)?eligible\s+for\s+(?:any\s+)?(?:relaxation|exemption)',
    # Deferred to a future bid: "exemption ... allowed for next tender on
    # completion of successful trial order" is a refusal for this one.
    r'(?:relaxation|exemption)[^.]{0,160}?\bnext\s+tender',
)

ATC_GRANT_PCT_RX = (
    r'(?:relaxation|exemption)s?\s+(?:of\s+|by\s+)?(?:up\s*to\s+|upto\s+)?'
    r'(?P<pct>\d{1,2}(?:\.\d+)?)\s*%'
)

# Sentence boundary: ". " followed by an upper-case letter or "(" -- so
# "Rs. 100" is not one, but "... experience. (For example ..." is.
_SENTENCE_END_RX = re.compile(r'\.\s+(?=[A-Z(])')
_CLAUSE_REACH = 260


def _clause_around(text, start, end):
    """The sentence containing text[start:end], capped at _CLAUSE_REACH each way."""
    lo = max(0, start - _CLAUSE_REACH)
    hi = min(len(text), end + _CLAUSE_REACH)
    before = [m.end() for m in _SENTENCE_END_RX.finditer(text, lo, start)]
    after = _SENTENCE_END_RX.search(text, end, hi)
    return text[before[-1] if before else lo:after.start() + 1 if after else hi]


_FORM_LABEL_RXS = (RELAX_LABEL_RX, RELAX_SPLIT_LABEL_RX)


def _strip_form_labels(clause):
    for rx in _FORM_LABEL_RXS:
        clause = re.sub(rx, " ", clause, flags=re.IGNORECASE)
    return clause


def _inside_form_label(text, pos):
    """True when ``pos`` falls inside a GeM form relaxation label."""
    lo = max(0, pos - 120)
    for rx in _FORM_LABEL_RXS:
        for m in re.finditer(rx, text[lo:pos + 120], re.IGNORECASE):
            if lo + m.start() <= pos < lo + m.end():
                return True
    return False


def _overlapping_matches(rx, text):
    """Like re.finditer, but a rejected match does not hide one starting inside it."""
    compiled = re.compile(rx, re.IGNORECASE)
    pos = 0
    while True:
        m = compiled.search(text, pos)
        if not m:
            return
        yield m
        pos = m.start() + 1


def _clause_dimensions(clause):
    """Which criteria a clause talks about: ('exp',), ('turn',) or both."""
    if re.search(ATC_BOTH_RX, clause, re.IGNORECASE):
        return ("exp", "turn")
    dims = []
    if re.search(ATC_EXP_RX, clause, re.IGNORECASE):
        dims.append("exp")
    if re.search(ATC_TURN_RX, clause, re.IGNORECASE):
        dims.append("turn")
    return tuple(dims)


def _snippet(clause, matched, limit=200):
    """The clause, trimmed to ``limit`` chars but always showing ``matched``."""
    text = " ".join(clause.split())
    if len(text) <= limit:
        return text
    anchor = text.find(" ".join(matched.split()))
    start = max(0, min(anchor - 60, len(text) - limit)) if anchor >= 0 else 0
    piece = text[start:start + limit]
    return ("…" if start else "") + piece + ("…" if start + limit < len(text) else "")


def parse_atc_relaxation(text_clean, kind):
    """
    Find ATC clauses that grant or refuse the Startup/MSE relaxation.

    Returns None when no clause applies to ``kind``, else a dict:
        {"action": "deny" | "grant", "dims": ("exp", "turn"),
         "pct": float | None, "evidence": str}
    A refusal wins over a grant when the document contains both.
    """
    if not text_clean:
        return None
    kind_rx = ATC_KIND_RX["startup" if kind == "startup" else "mse"]

    def clauses(rx):
        for m in _overlapping_matches(rx, text_clean):
            if _inside_form_label(text_clean, m.start()):
                continue
            # The form fields carry no full stops, so a clause window can run
            # into them; blank the labels out so their "Startup ... Experience
            # and Turnover" words cannot vouch for an unrelated sentence.
            clause = _strip_form_labels(
                _clause_around(text_clean, m.start(), m.end()))
            if not re.search(kind_rx, clause, re.IGNORECASE):
                continue
            dims = _clause_dimensions(clause)
            if dims:
                yield m, clause, dims

    for rx in ATC_DENY_RXS:
        for m, clause, dims in clauses(rx):
            return {"action": "deny", "dims": dims, "pct": None,
                    "evidence": _snippet(clause, m.group(0))}

    for m, clause, dims in clauses(ATC_GRANT_PCT_RX):
        try:
            pct = float(m.group("pct"))
        except ValueError:
            continue
        if 0 < pct < 100:
            return {"action": "grant", "dims": dims, "pct": pct,
                    "evidence": _snippet(clause, m.group(0))}
    return None


def apply_atc_relaxation(relax, override, scheme):
    """
    Merge an ATC clause into a parsed form-field result. Returns a new dict.

    deny  -> the named criteria become "no" whatever the form field said.
    grant -> criteria the field left "no"/"unknown" become "partial" with the
             quoted percentage; a field that already waives fully is kept.
    """
    if not override:
        return relax
    merged = dict(relax)
    changed = []
    for dim in override["dims"]:
        before = merged[dim]
        if override["action"] == "deny":
            after = "no"
        elif before in ("no", "unknown"):
            after = "partial"
            merged[f"{dim}_pct"] = override["pct"]
        else:
            after = before
        if after != before:
            merged[dim] = after
            merged[f"{dim}_parsed"] = True
            changed.append((dim, before, after))
    if not changed:
        return merged

    names = {"exp": "Experience", "turn": "Turnover"}
    moves = ", ".join(f"{names[d]} {b} → {a}" for d, b, a in changed)
    merged["found"] = True
    merged["atc_note"] = (
        f"ATC overrides the {scheme} relaxation field ({moves}): \"{override['evidence']}\""
    )
    return merged


def parse_exemption_pair(text_clean, kind):
    """
    Back-compat wrapper around parse_relaxation_block.
    Returns (exp, turn, exp_parsed, turn_parsed) where each state is one of
    complete|partial|no|unknown.
    """
    r = parse_relaxation_block(text_clean, kind)
    return r["exp"], r["turn"], r["exp_parsed"], r["turn_parsed"]

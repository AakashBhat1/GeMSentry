"""Unit-aware INR amount parsing for labelled RFP requirement fields.

GeM RFPs write a requirement as a label, an optional parenthesised qualifier
and then the amount -- ``Minimum Average Annual Turnover of the bidder
(For 3 Years) 75 Lakh (s)``. Reading the first number after the label picks up
the ``3`` from the qualifier and then discards it as implausibly small, which
left the requirement unparsed and the bidder looking eligible. This module
strips the qualifiers first, then reads an amount that may carry a
lakh/crore multiplier, and reports *why* it could not read one when it fails.
"""

import re

from gemsentry.textutils import _parse_inr_amount

# Extraction states. A failed parse is not the same fact as an absent
# requirement, and neither is the same as an explicit waiver -- the eligibility
# gate has to tell them apart.
PARSED = "parsed"
NOT_REQUIRED = "not_required"
NOT_STATED = "not_stated"
UNREADABLE = "unreadable"
CONFLICTING = "conflicting"

UNIT_MULTIPLIERS = {
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}

_UNIT_ALTERNATION = "|".join(sorted(UNIT_MULTIPLIERS, key=len, reverse=True))

# Indian comma grouping (75,00,000) as well as plain and decimal forms.
_AMOUNT_RE = re.compile(
    r'(?:(?P<currency>INR|Rs\.?|₹)\s*)?'
    r'(?P<number>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
    r'\s*(?:\(\s*s\s*\)\s*)?'
    r'(?:(?P<unit>' + _UNIT_ALTERNATION + r')\b)?',
    re.IGNORECASE,
)

# Parenthesised or trailing qualifiers that count *years*, not rupees.
_QUALIFIER_PATTERNS = (
    re.compile(r'\((?:[^()]*\b(?:year|years|yr|yrs|month|months|financial)\b[^()]*)\)',
               re.IGNORECASE),
    re.compile(r'\bfor\s+(?:the\s+)?(?:last\s+|past\s+|preceding\s+|previous\s+)?'
               r'\d+\s*(?:financial\s+)?(?:years?|yrs?|months?)\b', re.IGNORECASE),
    re.compile(r'\bof\s+(?:the\s+)?(?:last|past|preceding|previous)\s+'
               r'\d+\s*(?:financial\s+)?(?:years?|yrs?)\b', re.IGNORECASE),
    re.compile(r'\b(?:last|past|preceding|previous)\s+\d+\s*'
               r'(?:financial\s+)?(?:years?|yrs?)\b', re.IGNORECASE),
    # Word-form counts: "(For Three Years)" survives the numeric patterns.
    re.compile(r'\((?:[^()]*\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b'
               r'[^()]*)\)', re.IGNORECASE),
)

# Phrases that state the requirement does not apply. Distinct from "we could
# not read it": an explicit Nil is evidence of eligibility, an unreadable
# field is not.
_WAIVER_RE = re.compile(
    r'\b(?:nil|none|not\s+applicable|n\s*/\s*a|not\s+required|no\s+minimum|'
    r'no\s+such\s+requirement|exempted|waived|not\s+mandatory)\b',
    re.IGNORECASE,
)

# Labels of the fields that typically follow a requirement in a GeM RFP.
# Truncating at them keeps an adjacent field's number out of this one's window.
DEFAULT_STOP_LABELS = (
    r'Years?\s+of\s+Past\s+Experience',
    r'Past\s+Experience',
    r'MSE\s+Exemption',
    r'Startup\s+Exemption',
    r'MSE\s+Purchase\s+Preference',
    r'MII\s+Purchase\s+Preference',
    r'Estimated\s+Bid\s+Value',
    r'Bid\s+(?:Number|End|Offer|Opening)',
    r'EMD\s+(?:Detail|Amount)',
    r'ePBG\s+Detail',
    r'Total\s+Quantity',
    r'Item\s+Category',
    r'Ministry/State',
    r'OEM\s+Average\s+Turnover',
    r'Bidder\s+Turnover',
    r'Document\s+required',
    r'Evaluation\s+Method',
)

# A turnover quoted in bare rupees below this, with no lakh/crore unit, is far
# more likely to be a stray year or serial number than a real bar.
DEFAULT_MIN_BARE_AMOUNT = 1000


def strip_label_qualifiers(snippet):
    """Remove ``(For 3 Years)``-style year counts so they are not read as money."""
    cleaned = snippet or ""
    for pattern in _QUALIFIER_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()


def parse_amount_with_unit(snippet):
    """First amount in ``snippet``, scaled by any lakh/crore/million suffix.

    Returns ``(amount, unit)``; ``unit`` is None for a plain rupee figure.
    """
    match = _AMOUNT_RE.search(snippet or "")
    if not match:
        return None, None
    base = _parse_inr_amount(match.group("number"))
    if base is None:
        return None, None
    unit = (match.group("unit") or "").lower() or None
    if unit:
        base = base * UNIT_MULTIPLIERS[unit]
    return (int(base) if float(base).is_integer() else base), unit


def _truncate_at_stop_label(snippet, stop_labels):
    """Cut the window at the next field label so its value cannot leak in."""
    earliest = len(snippet)
    for label in stop_labels or ():
        found = re.search(label, snippet, re.IGNORECASE)
        if found and found.start() < earliest:
            earliest = found.start()
    return snippet[:earliest]


def _evidence(label_text, snippet, max_len=160):
    return re.sub(r'\s+', ' ', f"{label_text} {snippet}").strip()[:max_len]


def _read_one(label_text, snippet, stop_labels, min_bare_amount):
    """Classify a single label occurrence into a state + value + evidence."""
    window = _truncate_at_stop_label(snippet, stop_labels)
    cleaned = strip_label_qualifiers(window)
    evidence = _evidence(label_text, window)

    amount, unit = parse_amount_with_unit(cleaned)
    amount_match = _AMOUNT_RE.search(cleaned)

    # A waiver word ahead of any figure states the requirement away.
    waiver = _WAIVER_RE.search(cleaned)
    if waiver and (amount_match is None or waiver.start() < amount_match.start()):
        return {"state": NOT_REQUIRED, "value": None, "evidence": evidence}

    if amount is None:
        return {"state": UNREADABLE, "value": None, "evidence": evidence}
    if amount == 0:
        return {"state": NOT_REQUIRED, "value": 0, "evidence": evidence}
    if unit is None and amount < min_bare_amount:
        # Almost certainly a qualifier we failed to strip, not a rupee figure.
        return {"state": UNREADABLE, "value": None, "evidence": evidence}
    return {"state": PARSED, "value": amount, "evidence": evidence}


def parse_labelled_amount(text, label_patterns, window=140,
                          stop_labels=DEFAULT_STOP_LABELS,
                          min_bare_amount=DEFAULT_MIN_BARE_AMOUNT):
    """Read a labelled INR requirement out of RFP text.

    Every occurrence of the label is read, so a document that states the same
    requirement twice with different figures is reported as ``conflicting``
    rather than silently resolved to whichever came first.

    Returns ``{"state", "value", "evidence"}``. ``value`` is None unless the
    state is ``parsed``/``conflicting`` (or an explicit zero waiver).
    """
    body = text or ""
    readings = []
    for label in label_patterns:
        for match in re.finditer(label, body, re.IGNORECASE):
            snippet = body[match.end():match.end() + window]
            readings.append(
                _read_one(match.group(0), snippet, stop_labels, min_bare_amount)
            )
        if readings:
            # The first label that matches at all is the authoritative one;
            # the later patterns are looser fallbacks for the same field.
            break

    if not readings:
        return {"state": NOT_STATED, "value": None, "evidence": None}

    parsed = [r for r in readings if r["state"] == PARSED]
    waived = [r for r in readings if r["state"] == NOT_REQUIRED]
    distinct = {r["value"] for r in parsed}

    if len(distinct) > 1 or (parsed and waived):
        best = max(parsed, key=lambda r: r["value"])
        return {
            "state": CONFLICTING,
            "value": best["value"],
            "evidence": " | ".join(
                r["evidence"] for r in readings if r["evidence"]
            )[:320],
        }
    if parsed:
        return parsed[0]
    if waived:
        return waived[0]
    return readings[0]

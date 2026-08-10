"""Whole-word keyword matching shared by the fit scorer and the classifier.

Substring matching is the wrong tool for keyword taxonomies and has bitten
this codebase twice. ``"ai" in title`` matches *maintenance*, *repair*, *air*,
*paint* and *chair*; ``"rpa"`` matches *tarpaulin*; ``"soc"`` matches
*associated*. Everything that matches a keyword list against free text must go
through :func:`keyword_hit`.
"""

import re
from functools import lru_cache


# Shortest stem for which a trailing 's' is assumed to be a plural rather than
# part of the word. "cables" -> "cable" (5) is a plural; "ups" -> "up" (2) and
# "gas" -> "ga" (2) are not.
_MIN_PLURAL_STEM = 4

# Short alphabetic keywords at or below this length are treated as acronyms for
# the part-number guard (PLC-337, API 8810). Aligns with fit.lone_acronym_max_len.
_ACRONYM_MAX_LEN = 3

# Capacity / quantity units that legitimately follow "ACRONYM NUMBER"
# (e.g. "UPS 30 KVA", "PSU 12 V"). Without this, the part-number guard would
# drop real power-supply bids.
_UNITS_AFTER_NUMBER = frozenset({
    "kva", "va", "kw", "mw", "w", "watt", "watts",
    "ah", "mah", "v", "volt", "volts", "vac", "vdc", "kv",
    "a", "amp", "amps", "ampere", "amperes",
    "hz", "khz", "mhz", "ghz",
    "mm", "cm", "m", "km", "inch", "inches", "in", "ft",
    "kg", "g", "mg", "ton", "tons", "mt",
    "hp", "rpm",
    "channel", "channels", "ch", "port", "ports",
    "tb", "gb", "mb", "kb",
})

# Hyphen/slash/underscore bound model: PLC-337, API/8810
_GLUED_MODEL = re.compile(r"^[-/_]\s*\d[A-Za-z0-9]*\b")
# Space-separated model / standard number: API 8810, API 6D, API 600
_SPACED_MODEL = re.compile(
    r"^\s+(\d[A-Za-z0-9]*)\b(?:\s+([^\s,;:/]+))?"
)
# Number token that is a capacity, not a SKU: 32A, 24V, 1KVA, 10Ah
_CAPACITY_TOKEN = re.compile(
    r"^\d+(\.\d+)?("
    r"a|v|w|ah|mah|kva|va|kw|mw|hz|khz|mhz|ghz|"
    r"mm|cm|vdc|vac|kv|hp|rpm|tb|gb|mb"
    r")$",
    re.IGNORECASE,
)


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


@lru_cache(maxsize=2048)
def _pattern(keyword: str) -> "re.Pattern":
    """Compile (and cache) the whole-word pattern for one keyword.

    Tolerates a trailing plural 's' ("connector" hits "connectors"). A keyword
    that already ends in 's' is matched exactly: appending an optional 's'
    would make the real final letter optional, so "ups" would match the bare
    word "up" -- that is how a gym bid ("Sit up bench") once scored as Power
    Supply.

    Boundaries are lookarounds rather than ``\\b`` so keywords that begin or
    end in punctuation still work: ``\\b`` after the '+' in "c++" can never
    match, since there is no word character for the boundary to sit against.
    """
    prefix = r"(?<!\w)" if _is_word_char(keyword[0]) else ""

    if not _is_word_char(keyword[-1]):
        return re.compile(prefix + re.escape(keyword) + r"(?!\w)")

    if not keyword.endswith("s"):
        return re.compile(prefix + re.escape(keyword) + r"s?(?!\w)")

    # Keyword already ends in 's'. If the stem is long enough to be a real
    # word, treat the 's' as a plural and match both forms, so the keyword
    # "cables" still finds "cable" (33 downloaded bids missed it). Short
    # stems keep the exact match: "ups" must not become "up", which is how a
    # gym bid ("Sit up bench") once scored as Power Supply.
    stem = keyword[:-1]
    if len(stem) >= _MIN_PLURAL_STEM:
        return re.compile(prefix + re.escape(stem) + r"s?(?!\w)")
    return re.compile(prefix + re.escape(keyword) + r"(?!\w)")


def _is_short_acronym(keyword: str) -> bool:
    return keyword.isalpha() and 1 <= len(keyword) <= _ACRONYM_MAX_LEN


def _looks_like_part_number(keyword: str, text: str, match_end: int) -> bool:
    """True when a short-acronym match is only a model / standard number.

    ``PLC-337`` (printer cartridge SKU) and ``API 8810`` / ``API 600`` (model
    or oil-and-gas standard codes) are whole-word hits for ``plc`` / ``api``
    under normal boundary rules, but they are not product evidence.

    Kept narrow on purpose:
    - only pure alphabetic keywords of length <= 3
    - glued ``-337`` / ``/8810`` always counts as a part number
    - space + number needs 3+ digits *or* a digit+letter token (``6D``), and
      must not be followed by a capacity unit (``UPS 30 KVA``, ``UPS 600 VA``)
    """
    if not _is_short_acronym(keyword):
        return False
    rest = text[match_end:]
    if _GLUED_MODEL.match(rest):
        return True
    spaced = _SPACED_MODEL.match(rest)
    if not spaced:
        return False
    token = spaced.group(1)
    nxt = (spaced.group(2) or "").lower().strip(".,;:()[]")
    if _CAPACITY_TOKEN.match(token):
        # "MCB 32A", "UPS 1KVA", "RELAY 24V" — rating glued to the number.
        return False
    digit_count = sum(1 for c in token if c.isdigit())
    has_letter = any(c.isalpha() for c in token)
    if not has_letter and digit_count < 3:
        # "NVR 16 channel", "UPS 30 KVA" — short counts are capacities, not SKUs.
        return False
    if nxt in _UNITS_AFTER_NUMBER:
        return False
    return True


def _iter_real_hits(keyword: str, text: str):
    """Yield match objects that are whole-word hits and not part numbers."""
    for match in _pattern(keyword).finditer(text):
        if not _looks_like_part_number(keyword, text, match.end()):
            yield match


def keyword_hit(keyword: str, text: str) -> bool:
    """True when ``keyword`` appears in ``text`` as a whole word or phrase."""
    if not keyword or not text:
        return False
    term = keyword.lower().strip()
    if not term:
        return False
    lowered = text.lower()
    return next(_iter_real_hits(term, lowered), None) is not None


def count_hits(keyword: str, text: str) -> int:
    """How many times ``keyword`` appears in ``text`` as a whole word."""
    if not keyword or not text:
        return 0
    term = keyword.lower().strip()
    if not term:
        return 0
    lowered = text.lower()
    return sum(1 for _ in _iter_real_hits(term, lowered))


def first_hit_position(keyword: str, text: str):
    """Return the first real whole-word match offset, or ``None``."""
    if not keyword or not text:
        return None
    term = keyword.lower().strip()
    if not term:
        return None
    match = next(_iter_real_hits(term, text.lower()), None)
    return match.start() if match is not None else None

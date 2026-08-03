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


def keyword_hit(keyword: str, text: str) -> bool:
    """True when ``keyword`` appears in ``text`` as a whole word or phrase."""
    if not keyword or not text:
        return False
    term = keyword.lower().strip()
    if not term:
        return False
    return _pattern(term).search(text.lower()) is not None


def count_hits(keyword: str, text: str) -> int:
    """How many times ``keyword`` appears in ``text`` as a whole word."""
    if not keyword or not text:
        return 0
    term = keyword.lower().strip()
    if not term:
        return 0
    return len(_pattern(term).findall(text.lower()))

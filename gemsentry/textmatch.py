"""Whole-word keyword matching shared by the fit scorer and the classifier.

Substring matching is the wrong tool for keyword taxonomies and has bitten
this codebase twice. ``"ai" in title`` matches *maintenance*, *repair*, *air*,
*paint* and *chair*; ``"rpa"`` matches *tarpaulin*; ``"soc"`` matches
*associated*. Everything that matches a keyword list against free text must go
through :func:`keyword_hit`.
"""

import re
from functools import lru_cache


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
    term = re.escape(keyword)
    prefix = r"(?<!\w)" if _is_word_char(keyword[0]) else ""
    if _is_word_char(keyword[-1]) and not keyword.endswith("s"):
        suffix = r"s?(?!\w)"
    else:
        suffix = r"(?!\w)"
    return re.compile(prefix + term + suffix)


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

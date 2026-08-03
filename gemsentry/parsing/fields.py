"""EMD / ePBG / pre-bid yes-no and amount field parsers."""

import re

from gemsentry.parsing.text import _first_number, _first_yes_no, _window_after
from gemsentry.textutils import _parse_inr_amount


def parse_yes_no_field(text_clean, label_patterns, window=120):
    """
    Find Yes/No after an English label (BE-15 bilingual-tolerant).
    Also accepts Yes/No immediately BEFORE the label (common GeM layout).
    Returns 'yes'|'no'|None (None = miss → caller maps to unknown).
    """
    for label in label_patterns:
        # Value after label
        snip, m = _window_after(text_clean, label, window=window)
        if m is not None:
            yn = _first_yes_no(snip)
            if yn:
                return yn
            # Value immediately before label (within 12 chars)
            pre = text_clean[max(0, m.start() - 12):m.start()]
            yn = _first_yes_no(pre)
            if yn:
                return yn
    return None


def parse_emd_required(text_clean):
    """EMD Required Yes/No — handles 'EMD Detail ... Required No' bilingual form."""
    # Prefer scoped EMD Detail block
    m = re.search(r'EMD\s+Detail', text_clean, re.IGNORECASE)
    if m:
        block = text_clean[m.start():m.start() + 200]
        # Stop before ePBG Detail if present
        stop = re.search(r'ePBG\s+Detail', block, re.IGNORECASE)
        if stop:
            block = block[:stop.start()]
        yn = _first_yes_no(block)
        # Prefer the Yes/No that follows 'Required' if present
        req = re.search(r'Required.{0,40}?(Yes|No)\b', block, re.IGNORECASE)
        if req:
            return req.group(1).lower()
        if yn:
            return yn
    return parse_yes_no_field(text_clean, [r'EMD\s+Required'], window=40)


def parse_epbg_required(text_clean):
    m = re.search(r'ePBG\s+Detail', text_clean, re.IGNORECASE)
    if m:
        block = text_clean[m.start():m.start() + 220]
        # Stop at next major section
        for stopper in (r'MII\s+Purchase', r'MSE\s+Purchase', r'Bid\s+splitting', r'Split'):
            stop = re.search(stopper, block, re.IGNORECASE)
            if stop:
                block = block[:stop.start()]
                break
        req = re.search(r'Required.{0,40}?(Yes|No)\b', block, re.IGNORECASE)
        if req:
            return req.group(1).lower()
        yn = _first_yes_no(block)
        if yn:
            return yn
    return parse_yes_no_field(text_clean, [r'ePBG\s+Required'], window=40)


def parse_emd_amount(text_clean):
    snip, _ = _window_after(text_clean, r'EMD\s+Amount(?:\s*\(INR\))?', window=60)
    if snip:
        num = _first_number(snip)
        if num:
            return _parse_inr_amount(num)
    # Alternate: "EMD value"
    snip, _ = _window_after(text_clean, r'EMD\s+value', window=60)
    if snip:
        num = _first_number(snip)
        if num:
            return _parse_inr_amount(num)
    return None


def parse_epbg_percentage(text_clean):
    snip, _ = _window_after(text_clean, r'ePBG\s+Percentage\s*(?:\(%\))?', window=40)
    if snip:
        num = _first_number(snip)
        if num:
            try:
                return float(num.replace(",", ""))
            except ValueError:
                return None
    return None


def parse_prebid_required(text_clean):
    return parse_yes_no_field(
        text_clean,
        [r'Pre-Bid\s+Meeting\s+Required', r'Pre\s*Bid\s+Meeting\s+Required'],
        window=60
    )

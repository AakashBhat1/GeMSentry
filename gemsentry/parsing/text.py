"""Low-level windowed text scanners over RFP PDF text."""

import re


def _window_after(text, label_pat, window=160, flags=re.IGNORECASE):
    """Return text slice starting at first match of label_pat (or None)."""
    m = re.search(label_pat, text, flags)
    if not m:
        return None, None
    start = m.end()
    return text[start:start + window], m


def _first_yes_no(snippet):
    if not snippet:
        return None
    m = re.search(r'\b(Yes|No)\b', snippet, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _first_ascii_phrase(snippet, max_len=100, stop_words=None):
    """First run of ASCII letters/digits after optional Devanagari noise."""
    if not snippet:
        return None
    stop_words = stop_words or ()
    # Drop leading non-ASCII / punctuation
    s = re.sub(r'^[\s/]*(?:[^\x00-\x7F]+[\s/]*)*', '', snippet)
    if not s:
        return None
    # Capture continuous ASCII phrase
    m = re.match(r'([A-Za-z0-9][A-Za-z0-9 ,\-\(\)/&\.]{1,' + str(max_len) + r'})', s)
    if not m:
        return None
    phrase = m.group(1).strip(" ,-/\t")
    # Truncate at any stop word (next English field label)
    low = phrase
    for sw in stop_words:
        idx = re.search(re.escape(sw), low, re.IGNORECASE)
        if idx and idx.start() > 0:
            phrase = phrase[:idx.start()].strip(" ,-/\t")
            break
    return phrase if phrase else None


def _first_number(snippet):
    if not snippet:
        return None
    m = re.search(r'([\d,]{1,15}(?:\.\d+)?)', snippet)
    return m.group(1) if m else None

# --- Relaxation (Startup/MSE) parsing -------------------------------------
# Real GeM bid PDFs label this field "Relaxation"; only a small minority of
# older/ATC docs say "Exemption". Both spellings are accepted everywhere.

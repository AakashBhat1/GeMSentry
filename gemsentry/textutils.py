"""Filename, date and free-text normalisation helpers."""

import datetime
import re

from gemsentry.constants import _INDIAN_STATES


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip().replace(" ", "_")


def sanitize_folder_name(name):
    sanitized = re.sub(r'[^a-zA-Z0-9_\-\s]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_').lower()


def today_iso():
    """Discovery date stamp (YYYY-MM-DD) — sorts lexicographically."""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def get_date_folder_name():
    now = datetime.datetime.now()
    return f"{now.strftime('%d')} {now.strftime('%b').lower()}{now.strftime('%y')}"


def _parse_inr_amount(raw):
    """Parse an INR amount string that may contain commas/rupees markers."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    s = re.sub(r'[₹Rs\.INR\s]', '', s, flags=re.IGNORECASE)
    s = s.replace(",", "")
    # take leading number
    m = re.match(r'([\d]+(?:\.\d+)?)', s)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def _clean_english_phrase(s, max_len=120):
    if not s:
        return None
    s = re.sub(r'\s+', ' ', s).strip(" \t\n\r/,-")
    # Drop leading non-ASCII
    s = re.sub(r'^[^\x00-\x7F]+', '', s).strip()
    if not s:
        return None
    return s[:max_len]


def _match_indian_state(text):
    if not text:
        return None
    low = text.lower()
    for st in _INDIAN_STATES:
        if st.lower() in low:
            return st
    return None

# ---------------------------------------------------------------------------
# BE-15 helpers: bilingual GeM layout — English label then value, no colon.
# Patterns use bounded .{0,N} windows only (no nested quantifiers / backtracking).
# ---------------------------------------------------------------------------

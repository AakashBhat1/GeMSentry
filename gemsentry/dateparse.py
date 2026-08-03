"""GeM/ISO date parsing and publication-window sanity checks."""

import datetime
import re


# Every date shape seen across the portals we ingest from. Numeric-month
# formats come first so "13-08-2026" is never mis-read by a name-month parser.
#   GeM      13-08-2026 07:30 PM / 13-08-2026 19:30:00
#   BHEL     13-08-2026 07:30:00 PM
#   GePNIC   20-Aug-2026 03:00 PM
#   ISRO     24-August-2026 14:30
_DATE_FORMATS = (
    "%d-%m-%Y %I:%M %p",
    "%d-%m-%Y %I:%M:%S %p",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %I:%M:%S %p",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%d-%B-%Y %I:%M %p",
    "%d-%B-%Y %I:%M:%S %p",
    "%d-%B-%Y %H:%M",
    "%d-%B-%Y",
)

_NUMERIC_DATE_RX = re.compile(r"\d{2}-\d{2}-\d{4}")
_NAMED_DATE_RX = re.compile(r"\d{1,2}-[A-Za-z]{3,9}-\d{4}")


def parse_gem_date(date_str):
    """Parse a portal date string into a datetime, or return ``None``."""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = " ".join(date_str.split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Fall back to the date portion alone when a trailing suffix (timezone
    # label, "(IST)", stray text) defeats the full-string parse.
    match = _NUMERIC_DATE_RX.search(date_str)
    if match:
        try:
            return datetime.datetime.strptime(match.group(0), "%d-%m-%Y")
        except ValueError:
            pass
    match = _NAMED_DATE_RX.search(date_str)
    if match:
        for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
            try:
                return datetime.datetime.strptime(match.group(0), fmt)
            except ValueError:
                continue
    return None


def parse_iso_date_to_gem(iso_str):
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %I:%M %p")
    except Exception:
        return str(iso_str)


def check_date_policy(start_date_str, end_date_str):
    start_date_obj = parse_gem_date(start_date_str)
    end_date_obj = parse_gem_date(end_date_str)
    current_date = datetime.datetime.now()
    
    reasons = []
    
    if not start_date_obj or not end_date_obj:
        return True, []
        
    # 1. Start date must be in the current month/year or within the last 30 days
    days_since_start = (current_date - start_date_obj).days
    if days_since_start > 30 and (start_date_obj.month != current_date.month or start_date_obj.year != current_date.year):
        reasons.append(f"Start date ({start_date_str}) is older than 30 days and not in the current month")
    
    # 2. End date must be at least 7 days (1 week) after start date
    if (end_date_obj - start_date_obj).days < 7:
        reasons.append(f"Bid duration is less than 7 days (Start: {start_date_str}, End: {end_date_str})")
        
    # 3. End date must be at least 7 days (1 week) after current date
    if (end_date_obj - current_date).days < 7:
        reasons.append(f"Remaining bid time is less than 7 days (End: {end_date_str}, Today: {current_date.strftime('%d-%m-%Y')})")
        
    return len(reasons) == 0, reasons

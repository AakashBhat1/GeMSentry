"""Bid date-window evaluation and urgency ramps."""

import datetime

from gemsentry.dateparse import parse_gem_date
from gemsentry.defaults import DEFAULT_SCORING_CONFIG


def _linear_ramp(value, free_at, zero_at):
    """1.0 at value<=free_at, 0.0 at value>=zero_at, linear in between."""
    if value <= free_at:
        return 1.0
    if value >= zero_at:
        return 0.0
    span = zero_at - free_at
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (value - free_at) / span))


def evaluate_date_window(start_date_str, end_date_str, cfg):
    """
    Graduated date-window sub-score (BE-03).
    Returns dict: is_expired, subscore, reasons, remaining_days, detail.
    """
    date_cfg = cfg.get("date_window", DEFAULT_SCORING_CONFIG["date_window"])
    min_days = int(date_cfg.get("min_days", 7))
    full_credit_days = float(date_cfg.get("full_credit_days", 14))
    if full_credit_days <= 0:
        full_credit_days = 14.0
    min_days_to_bid = float(date_cfg.get("min_days_to_bid", 5))

    start_date_obj = parse_gem_date(start_date_str)
    end_date_obj = parse_gem_date(end_date_str)
    current_date = datetime.datetime.now()
    reasons = []

    if not end_date_obj:
        # Unparseable dates: neutral full credit (legacy check_date_policy treated as ok)
        return {
            "is_expired": False,
            "auto_reject": False,
            "subscore": 1.0,
            "reasons": [],
            "remaining_days": None,
            "detail": "Bid end date not parseable; date window treated as full credit."
        }

    end_day = end_date_obj.date() if hasattr(end_date_obj, "date") else end_date_obj
    today = current_date.date()
    remaining_days = (end_date_obj - current_date).days

    # Hard reject only when bid is actually closed/expired (end_date ≤ today)
    if end_day <= today:
        return {
            "is_expired": True,
            "auto_reject": True,
            "subscore": 0.0,
            "reasons": [f"Bid expired (End: {end_date_str}, Today: {today.strftime('%d-%m-%Y')})"],
            "remaining_days": remaining_days,
            "detail": "Bid expired; hard-reject score forced to 0."
        }

    # Linear ramp: 0.0 at 0 remaining days → 1.0 at full_credit_days
    rem = max(0.0, (end_date_obj - current_date).total_seconds() / 86400.0)
    subscore = max(0.0, min(1.0, rem / full_credit_days))

    # Hard reject when closing sooner than the minimum days needed to prepare a bid
    if min_days_to_bid > 0 and rem < min_days_to_bid:
        return {
            "is_expired": False,
            "auto_reject": True,
            "subscore": 0.0,
            "reasons": [
                f"Bid closing in ~{rem:.1f} days (< {min_days_to_bid:g}-day minimum "
                f"to prepare); auto-rejected."
            ],
            "remaining_days": rem,
            "detail": f"Closing in <{min_days_to_bid:g} days; hard-reject score forced to 0.",
        }

    # Old soft rules → reasons + 0.5 multipliers (no longer force score 1)
    if start_date_obj:
        days_since_start = (current_date - start_date_obj).days
        if days_since_start > 30 and (start_date_obj.month != current_date.month or start_date_obj.year != current_date.year):
            msg = f"Start date ({start_date_str}) is older than 30 days and not in the current month"
            reasons.append(msg)
            subscore *= 0.5
        duration_days = (end_date_obj - start_date_obj).days
        if duration_days < min_days:
            msg = f"Bid duration is less than {min_days} days (Start: {start_date_str}, End: {end_date_str})"
            reasons.append(msg)
            subscore *= 0.5
    else:
        # Keep duration/start warnings only when start is parseable
        pass

    # Remaining-time soft warning (does not extra-multiply; ramp already encodes it)
    if rem < min_days:
        reasons.append(
            f"Remaining bid time is less than {min_days} days "
            f"(End: {end_date_str}, Today: {today.strftime('%d-%m-%Y')})"
        )

    detail = f"Remaining ~{rem:.1f} days; date_window subscore={subscore:.3f}."
    return {
        "is_expired": False,
        "auto_reject": False,
        "subscore": subscore,
        "reasons": reasons,
        "remaining_days": rem,
        "detail": detail
    }

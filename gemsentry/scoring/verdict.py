"""Recommendation, priority, status and verdict application."""

import hashlib
import json

from gemsentry.constants import TOTAL_ANALYSIS_FIELDS, TOTAL_SIGNAL_FIELDS
from gemsentry.defaults import DEFAULT_SCORING_CONFIG


def get_failed_analysis(reason):
    """Analysis payload for parse exception or missing PDF (BE-04)."""
    return {
        "emd_amount": None,
        "emd_status": "Not Analyzed",
        "startup_exemption": "Unknown",
        "mse_exemption": "Unknown",
        "pre_bid_required": "Unknown",
        "pre_bid_date": None,
        "epbg_required": "Unknown",
        "epbg_percentage": None,
        "score": None,
        "score_scale": 100,
        "analysis_status": "failed",
        "parsed_fields": 0,
        "na_fields": 0,
        "total_fields": TOTAL_ANALYSIS_FIELDS,
        "confidence": 0.0,
        "breakdown": [],
        "reasons": [reason],
        # Phase-2 keys (null / empty so FE can render gracefully)
        "est_value_inr": None,
        "est_value_estimated": False,
        "est_value_source": None,
        "primary_item": None,
        "item_category": None,
        "buyer_org": None,
        "buyer_dept": None,
        "consignee_state": None,
        "mii_required": "unknown",
        "mse_pref": "unknown",
        "signal_parsed": 0,
        "signal_fields": TOTAL_SIGNAL_FIELDS,
        "eligibility": {
            "verdict": "unknown",
            "flags": ["analysis_failed"],
            "detail": reason
        },
        "fit_score": None,
        "fit_breakdown": [],
        "business_line": None,
        "recommendation": None,
        "priority_score": None
    }


def build_score_breakdown(criteria, weights):
    """
    criteria: list of (name, subscore, detail)
    returns (final_score int 0-100, breakdown list)
    """
    weight_sum = sum(float(weights.get(name, 0)) for name, _, _ in criteria)
    if weight_sum <= 0:
        weight_sum = 1.0

    breakdown = []
    points_sum = 0.0
    weighted = 0.0
    for name, subscore, detail in criteria:
        w = float(weights.get(name, 0))
        sub = max(0.0, min(1.0, float(subscore)))
        points = round(100.0 * w * sub / weight_sum, 1)
        points_sum += points
        weighted += w * sub
        breakdown.append({
            "criterion": name,
            "weight": w,
            "subscore": round(sub, 4),
            "points": points,
            "detail": detail
        })

    final_score = int(round(100.0 * weighted / weight_sum))
    final_score = max(0, min(100, final_score))

    # Keep points sum aligned with score within ±1 of rounding noise
    # (points are already per-criterion rounded; final uses unrounded weighted sum)
    return final_score, breakdown


def status_from_score(score, cfg, current_status=None):
    """
    Map 0-100 score to status using config thresholds.
    Never clobber manual Shortlisted/Rejected. score None → Pending Review.
    """
    if current_status in ("Shortlisted", "Rejected"):
        return current_status
    if score is None:
        return "Pending Review"
    thresholds = cfg.get("status_thresholds", DEFAULT_SCORING_CONFIG["status_thresholds"])
    shortlist_min = float(thresholds.get("shortlist_min", 70))
    reject_max = float(thresholds.get("reject_max", 40))
    if score >= shortlist_min:
        return "Shortlisted"
    if score <= reject_max:
        return "Rejected"
    return "Pending Review"

# BE-25: single source of truth for status. Status derives from the fit-gated
# recommendation — never from the Risk score alone (which used to Shortlist
# irrelevant-but-clean bids). Manual pins (status_source == "manual") always win.


RECOMMENDATION_TO_STATUS = {
    "Pursue": "Shortlisted",
    "Review": "Pending Review",
    "Drop": "Rejected",
}


def status_from_recommendation(recommendation, current_status=None, status_source=None):
    """Map recommendation → workflow status; keep manually pinned statuses."""
    if status_source == "manual" and current_status:
        return current_status
    return RECOMMENDATION_TO_STATUS.get(recommendation, "Pending Review")


def apply_verdict(tender, analysis):
    """
    Attach analysis and derive status in one place (scrape / manual / rescore).
    Mutates and returns the tender record.
    """
    tender["analysis"] = analysis
    tender["status"] = status_from_recommendation(
        (analysis or {}).get("recommendation"),
        current_status=tender.get("status"),
        status_source=tender.get("status_source"),
    )
    return tender


def finalize_auto_reject(analysis, date_info=None):
    """
    Stamp an analysis as date-window auto-rejected (expired / closing sooner
    than min_days_to_bid): score 0, Drop, priority 0, normalized reasons.
    """
    info = date_info or {}
    expired = bool(analysis.get("is_expired") or info.get("is_expired"))
    analysis["auto_reject"] = True
    analysis["is_expired"] = expired
    analysis["score"] = 0
    analysis["recommendation"] = "Drop"
    analysis["priority_score"] = 0
    reasons = analysis.setdefault("reasons", [])
    if not any(str(r).startswith("Auto-Rejected") for r in reasons):
        msg = ("Auto-Rejected: bid expired" if expired
               else "Auto-Rejected: closing too soon to bid")
        reasons.insert(0, msg)
    for r in info.get("reasons", []):
        if r not in reasons:
            reasons.append(r)
    return analysis


def scoring_fingerprint(cfg, profile):
    """Short hash of scoring config + company profile to detect stale analyses."""
    try:
        blob = json.dumps({"cfg": cfg, "profile": profile}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return None


def compute_recommendation(fit_score, risk_score, eligibility, is_expired, cfg,
                           relevance_matched=False):
    """
    Fit-gated recommendation (BE-11): Pursue / Review / Drop.
    Fit gates first — low-fit bids Drop regardless of Risk. Among relevant bids,
    Risk splits Pursue (friendly) vs Review (has friction). Watch is retired.
    Never overwrites manual status — advisory only.

    relevance_matched: a business line matched the bid content. Bids inside
    fit.review_band points below fit_min with a real match go to Review instead
    of Drop (BE-28) — a false Drop loses a tender forever; a false Review costs
    a human ten seconds.
    """
    if risk_score is None and fit_score is None:
        return None

    fit_cfg = cfg.get("fit") or DEFAULT_SCORING_CONFIG.get("fit", {})
    fit_min = float(fit_cfg.get("fit_min", 60))
    review_band = float(fit_cfg.get("review_band", 8))
    thresholds = cfg.get("status_thresholds") or DEFAULT_SCORING_CONFIG["status_thresholds"]
    shortlist_min = float(thresholds.get("shortlist_min", 70))

    fs = fit_score if fit_score is not None else 0
    rs = risk_score if risk_score is not None else 0

    high_fit = fs >= fit_min
    high_risk = rs >= shortlist_min  # high Risk-score = friendlier tender

    # Fit is the gate: a bid that doesn't match our business lines is Dropped
    # regardless of how clean (high-Risk) the tender is. Among relevant (high-fit)
    # bids, Risk separates Pursue (friendly) from Review (has friction).
    if not high_fit:
        if relevance_matched and fs >= fit_min - review_band:
            rec = "Review"  # borderline near the gate: surface, don't silently drop
        else:
            rec = "Drop"
    elif high_risk:
        rec = "Pursue"
    else:
        rec = "Review"

    # Downgrades
    if is_expired:
        rec = "Drop"
    else:
        verdict = (eligibility or {}).get("verdict")
        if verdict == "turnover_gap" and rec == "Pursue":
            rec = "Review"
        if risk_score is None and rec == "Pursue":
            rec = "Review"

    return rec


def compute_priority_score(fit_score, risk_score, eligibility, is_expired, cfg,
                           exemptions_favorable=False):
    """
    Single blended 0-100 Priority score for best-first ranking (Feature B).
    Combines Fit (company match) and Risk (tender friendliness). Advisory only —
    never overwrites manual status. Expired bids are forced to 0.

    exemptions_favorable: when the tender explicitly grants Startup/MSE
    experience or turnover relaxations, apply a priority boost (config
    priority.exemption_boost) since such tenders are easier for us to win.
    """
    if fit_score is None and risk_score is None:
        return None
    if is_expired:
        return 0

    pr_cfg = cfg.get("priority") or DEFAULT_SCORING_CONFIG.get("priority", {})
    fw = float(pr_cfg.get("fit_weight", 0.6))
    rw = float(pr_cfg.get("risk_weight", 0.4))

    parts = []
    if fit_score is not None:
        parts.append((fw, float(fit_score)))
    if risk_score is not None:
        parts.append((rw, float(risk_score)))
    total_w = sum(w for w, _ in parts)
    if total_w <= 0:
        return None

    priority = sum(w * v for w, v in parts) / total_w

    # Soft nudge down when eligibility is uncertain (mirrors recommendation rules).
    verdict = (eligibility or {}).get("verdict")
    if verdict == "turnover_gap":
        priority *= 0.85

    # Boost tenders that explicitly relax Startup/MSE eligibility criteria.
    if exemptions_favorable:
        boost = float(pr_cfg.get("exemption_boost", 1.1))
        priority *= boost

    # Fit gate (BE-25): a low-fit bid must never outrank relevant bids purely on
    # tender friendliness (clean terms → Risk 90+ used to float manpower/banner
    # bids to the top). Below fit_min, priority is capped at the Fit score itself.
    fit_cfg = cfg.get("fit") or DEFAULT_SCORING_CONFIG.get("fit", {})
    fit_min = float(fit_cfg.get("fit_min", 60))
    if fit_score is not None and float(fit_score) < fit_min:
        priority = min(priority, float(fit_score))

    return round(max(0.0, min(100.0, priority)), 1)

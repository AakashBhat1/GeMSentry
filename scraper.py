import os
import re
import sys
import json
import csv
import time
import random
import datetime
import copy
import tempfile
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

# Configurations
TENDERS_DIR = "tenders"
DOWNLOADS_DIR = os.path.join(TENDERS_DIR, "downloads")
SCORING_CONFIG_PATH = "scoring_config.json"
COMPANY_PROFILE_PATH = "company_profile.json"
TOTAL_ANALYSIS_FIELDS = 8
TOTAL_SIGNAL_FIELDS = 8  # est_value, primary_item, item_category, buyer_org, buyer_dept, consignee_state, mii_required, mse_pref
MAX_PDF_PAGES = 12

DEFAULT_SCORING_CONFIG = {
    "version": 1,
    "weights": {
        "emd": 2.0,
        "startup_exemption": 1.5,
        "mse_exemption": 1.5,
        "prebid": 0.5,
        "date_window": 1.0,
        "epbg": 0.5
    },
    "emd": {
        "free_threshold_inr": 200000,
        "max_penalty_threshold_inr": 2000000
    },
    "date_window": {
        "min_days": 7,
        "full_credit_days": 14
    },
    "epbg": {
        "free_threshold_pct": 3.0,
        "max_penalty_pct": 10.0
    },
    "unknown_subscore": 0.5,
    "status_thresholds": {
        "shortlist_min": 70,
        "reject_max": 40
    },
    "fit": {
        "weights": {
            "relevance": 3.0,
            "serviceability": 1.0,
            "value_fit": 1.0,
            "buyer_affinity": 1.0,
            "eligibility_factor": 2.0
        },
        "fit_min": 60,
        "unknown_buyer_subscore": 0.4,
        "turnover_gap_subscore": 0.3,
        "weak_relevance_subscore": 0.5
    }
}

DEFAULT_COMPANY_PROFILE = {
    "version": 1,
    "company": {
        "legal_name": "Earnest Tactical Solutions Pvt. Ltd.",
        "short_name": "ETSPL",
        "incorporation_ym": "2020-03",
        "hq_state": "Haryana",
        "hq_city": "Gurgaon"
    },
    "eligibility": {
        "annual_turnover_inr": 1800000,
        "years_experience": 6,
        "registrations": {"mse_udyam": True, "startup_dpiit": True},
        "certifications": ["ISO 9001:2015"],
        "can_meet_make_in_india": True,
        "max_order_value_inr": None,
        "turnover_waivable_by_exemption": True
    },
    "serviceability": {
        "all_india": True,
        "soft_avoid_states": [
            "Tamil Nadu", "Kerala", "Karnataka",
            "Andhra Pradesh", "Telangana", "Puducherry"
        ],
        "soft_avoid_reason": "Local monopoly on these product categories in South India",
        "soft_avoid_penalty": 0.5
    },
    "business_lines": [
        {
            "id": "drone",
            "label": "Drone / UAV",
            "priority": 1.0,
            "keywords": [
                "drone", "drones", "uav", "unmanned aerial", "multirotor",
                "quadcopter", "aerostat", "gis", "mapping", "surveillance",
                "reconnaissance"
            ]
        },
        {
            "id": "power_supply",
            "label": "Power Supply / Electrical",
            "priority": 1.0,
            "keywords": [
                "power supply", "ac-dc", "ac dc", "rectifier", "alternator",
                "amplifier", "ups", "voltage regulator", "lvpsu", "hvpsu",
                "power unit", "static convertor", "power conversion",
                "battery charger", "solid state power amplifier", "power system",
                "psu"
            ]
        },
        {
            "id": "ai_it",
            "label": "AI / IT / Electronics",
            "priority": 1.0,
            "keywords": [
                "artificial intelligence", "ai based", "ai-based", "software",
                "server", "radar", "cctv", "camera", "connectors", "harness",
                "rugged laptop", "military grade", "repairing", "electronics",
                "data acquisition", "network switch", "router", "display",
                "laptop", "notebook"
            ]
        }
    ],
    "buyer_affinity": {
        "INDIAN AIR FORCE": 1.0,
        "INDIAN ARMY": 0.85,
        "INDIAN NAVY": 0.75,
        "HAL": 0.75,
        "DRDO": 0.65,
        "BHARAT PETROLEUM": 0.5,
        "DEFENCE": 0.6
    },
    "value_preference": {
        "sweet_min_inr": 500000,
        "sweet_max_inr": 30000000
    },
    "avoid_rules": {
        "gem_q2_category": True,
        "prefer_custom_bids": True
    }
}

# Indian states for consignee matching (lowercase keys)
_INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Puducherry", "Jammu and Kashmir", "Ladakh", "Chandigarh"
]

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip().replace(" ", "_")

def sanitize_folder_name(name):
    sanitized = re.sub(r'[^a-zA-Z0-9_\-\s]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_').lower()

def get_date_folder_name():
    now = datetime.datetime.now()
    return f"{now.strftime('%d')} {now.strftime('%b').lower()}{now.strftime('%y')}"

def load_keywords():
    keywords = []
    csv_path = "keywords.csv"
    if os.path.exists(csv_path):
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if clean.startswith('\ufeff'):
                        clean = clean.replace('\ufeff', '')
                    if clean and not clean.lower().startswith("keyword") and clean not in keywords:
                        keywords.append(clean)
        except Exception as e:
            print(f"Error reading keywords.csv: {e}")
            
    cleaned_keywords = []
    for kw in keywords:
        kw_clean = kw.strip()
        if kw_clean and kw_clean.lower() not in [k.lower() for k in cleaned_keywords]:
            cleaned_keywords.append(kw_clean)
            
    if not cleaned_keywords:
        cleaned_keywords = ["artificial intelligence", "indigenous", "power supply"]
        
    print(f"Loaded {len(cleaned_keywords)} unique keywords from keywords.csv")
    return cleaned_keywords

def find_existing_pdf_file(sanitized_bid):
    if os.path.exists(DOWNLOADS_DIR):
        for root, dirs, files in os.walk(DOWNLOADS_DIR):
            expected_filename = f"{sanitized_bid}.pdf"
            if expected_filename in files:
                full_path = os.path.join(root, expected_filename)
                if os.path.getsize(full_path) > 0:
                    rel_path = os.path.relpath(full_path, start=".").replace("\\", "/")
                    return rel_path
    return None

def load_scoring_config():
    """Load scoring_config.json; on missing/corrupt file log and return defaults."""
    defaults = copy.deepcopy(DEFAULT_SCORING_CONFIG)
    if not os.path.exists(SCORING_CONFIG_PATH):
        print(f"Warning: {SCORING_CONFIG_PATH} not found; using default scoring config.")
        return defaults
    try:
        with open(SCORING_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config root must be an object")
        # Shallow-merge known top-level keys onto defaults so partial files still work
        merged = copy.deepcopy(defaults)
        for key in defaults:
            if key in cfg:
                if isinstance(defaults[key], dict) and isinstance(cfg[key], dict):
                    nested = {**defaults[key], **cfg[key]}
                    # deep-merge one more level for fit.weights
                    if key == "fit" and isinstance(defaults[key].get("weights"), dict):
                        fw = {**defaults[key]["weights"], **(cfg[key].get("weights") or {})}
                        nested["weights"] = fw
                    merged[key] = nested
                else:
                    merged[key] = cfg[key]
        return merged
    except Exception as e:
        print(f"Warning: failed to load {SCORING_CONFIG_PATH} ({e}); using default scoring config.")
        return defaults

def validate_scoring_config(payload):
    """Return error message string if invalid, else None."""
    if not isinstance(payload, dict):
        return "Config payload must be a JSON object."

    weights = payload.get("weights")
    if not isinstance(weights, dict):
        return "weights must be an object."
    weight_keys = ("emd", "startup_exemption", "mse_exemption", "prebid", "date_window", "epbg")
    for k in weight_keys:
        if k not in weights:
            return f"weights missing required key: {k}"
        try:
            w = float(weights[k])
        except (TypeError, ValueError):
            return f"weights.{k} must be numeric."
        if w < 0:
            return f"weights.{k} must be >= 0."
    if sum(float(weights[k]) for k in weight_keys) <= 0:
        return "At least one weight must be > 0."

    try:
        unknown = float(payload.get("unknown_subscore", 0.5))
    except (TypeError, ValueError):
        return "unknown_subscore must be numeric."
    if not (0.0 <= unknown <= 1.0):
        return "unknown_subscore must be in [0, 1]."

    thresholds = payload.get("status_thresholds")
    if not isinstance(thresholds, dict):
        return "status_thresholds must be an object."
    try:
        shortlist_min = float(thresholds.get("shortlist_min"))
        reject_max = float(thresholds.get("reject_max"))
    except (TypeError, ValueError):
        return "status_thresholds.shortlist_min and reject_max must be numeric."
    if not (0 <= reject_max < shortlist_min <= 100):
        return "Require 0 <= reject_max < shortlist_min <= 100."

    # Optional fit block (Phase 2)
    if "fit" in payload and payload["fit"] is not None:
        fit = payload["fit"]
        if not isinstance(fit, dict):
            return "fit must be an object."
        fit_weights = fit.get("weights")
        if fit_weights is not None:
            if not isinstance(fit_weights, dict):
                return "fit.weights must be an object."
            fit_keys = ("relevance", "serviceability", "value_fit", "buyer_affinity", "eligibility_factor")
            for k in fit_keys:
                if k not in fit_weights:
                    return f"fit.weights missing required key: {k}"
                try:
                    w = float(fit_weights[k])
                except (TypeError, ValueError):
                    return f"fit.weights.{k} must be numeric."
                if w < 0:
                    return f"fit.weights.{k} must be >= 0."
            if sum(float(fit_weights[k]) for k in fit_keys) <= 0:
                return "At least one fit weight must be > 0."
        for opt_key in ("fit_min", "unknown_buyer_subscore", "turnover_gap_subscore", "weak_relevance_subscore"):
            if opt_key in fit and fit[opt_key] is not None:
                try:
                    v = float(fit[opt_key])
                except (TypeError, ValueError):
                    return f"fit.{opt_key} must be numeric."
                if opt_key == "fit_min":
                    if not (0 <= v <= 100):
                        return "fit.fit_min must be in [0, 100]."
                elif not (0.0 <= v <= 1.0):
                    return f"fit.{opt_key} must be in [0, 1]."

    return None

def save_scoring_config(payload):
    """Atomically write scoring_config.json (temp file then replace)."""
    dir_name = os.path.dirname(os.path.abspath(SCORING_CONFIG_PATH)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="scoring_config_", suffix=".json", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, SCORING_CONFIG_PATH)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

def load_company_profile():
    """Load company_profile.json; missing/corrupt → defaults + warning (BE-07)."""
    defaults = copy.deepcopy(DEFAULT_COMPANY_PROFILE)
    if not os.path.exists(COMPANY_PROFILE_PATH):
        print(f"Warning: {COMPANY_PROFILE_PATH} not found; using default company profile.")
        return defaults
    try:
        with open(COMPANY_PROFILE_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("profile root must be an object")
        merged = copy.deepcopy(defaults)
        for key in defaults:
            if key in cfg:
                if isinstance(defaults[key], dict) and isinstance(cfg[key], dict):
                    merged[key] = {**defaults[key], **cfg[key]}
                else:
                    merged[key] = cfg[key]
        # Prefer explicit lists/dicts from file when provided
        for list_key in ("business_lines",):
            if list_key in cfg and isinstance(cfg[list_key], list):
                merged[list_key] = cfg[list_key]
        if "buyer_affinity" in cfg and isinstance(cfg["buyer_affinity"], dict):
            merged["buyer_affinity"] = cfg["buyer_affinity"]
        return merged
    except Exception as e:
        print(f"Warning: failed to load {COMPANY_PROFILE_PATH} ({e}); using default company profile.")
        return defaults

def validate_company_profile(payload):
    """Return error message if invalid, else None (BE-13)."""
    if not isinstance(payload, dict):
        return "Profile payload must be a JSON object."

    elig = payload.get("eligibility")
    if not isinstance(elig, dict):
        return "eligibility must be an object."
    try:
        turnover = float(elig.get("annual_turnover_inr"))
    except (TypeError, ValueError):
        return "eligibility.annual_turnover_inr must be numeric."
    if turnover < 0:
        return "eligibility.annual_turnover_inr must be >= 0."

    lines = payload.get("business_lines")
    if not isinstance(lines, list) or len(lines) == 0:
        return "business_lines must be a non-empty list."
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            return f"business_lines[{i}] must be an object."
        if not line.get("id"):
            return f"business_lines[{i}].id is required."
        kws = line.get("keywords")
        if not isinstance(kws, list) or len(kws) == 0:
            return f"business_lines[{i}].keywords must be a non-empty list."

    svc = payload.get("serviceability") or {}
    if isinstance(svc, dict) and "soft_avoid_penalty" in svc and svc["soft_avoid_penalty"] is not None:
        try:
            p = float(svc["soft_avoid_penalty"])
        except (TypeError, ValueError):
            return "serviceability.soft_avoid_penalty must be numeric."
        if not (0.0 <= p <= 1.0):
            return "serviceability.soft_avoid_penalty must be in [0, 1]."

    affinity = payload.get("buyer_affinity") or {}
    if isinstance(affinity, dict):
        for k, v in affinity.items():
            try:
                av = float(v)
            except (TypeError, ValueError):
                return f"buyer_affinity.{k} must be numeric."
            if not (0.0 <= av <= 1.0):
                return f"buyer_affinity.{k} must be in [0, 1]."

    return None

def save_company_profile(payload):
    """Atomically write company_profile.json."""
    dir_name = os.path.dirname(os.path.abspath(COMPANY_PROFILE_PATH)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="company_profile_", suffix=".json", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, COMPANY_PROFILE_PATH)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

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
        "total_fields": TOTAL_ANALYSIS_FIELDS,
        "confidence": 0.0,
        "breakdown": [],
        "reasons": [reason],
        # Phase-2 keys (null / empty so FE can render gracefully)
        "est_value_inr": None,
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
        "recommendation": None
    }

def get_exemption_label(exp, turn):
    """Map tri-state exemption pair to UI label. Unknowns → 'Unknown'."""
    if exp == "unknown" or turn == "unknown":
        return "Unknown"
    if exp == "yes" and turn == "yes":
        return "Yes (Full)"
    if exp == "yes":
        return "Yes (Experience Only)"
    if turn == "yes":
        return "Yes (Turnover Only)"
    return "No Exemption"

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

    start_date_obj = parse_gem_date(start_date_str)
    end_date_obj = parse_gem_date(end_date_str)
    current_date = datetime.datetime.now()
    reasons = []

    if not end_date_obj:
        # Unparseable dates: neutral full credit (legacy check_date_policy treated as ok)
        return {
            "is_expired": False,
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
            "subscore": 0.0,
            "reasons": [f"Bid expired (End: {end_date_str}, Today: {today.strftime('%d-%m-%Y')})"],
            "remaining_days": remaining_days,
            "detail": "Bid expired; hard-reject score forced to 0."
        }

    # Linear ramp: 0.0 at 0 remaining days → 1.0 at full_credit_days
    rem = max(0.0, (end_date_obj - current_date).total_seconds() / 86400.0)
    subscore = max(0.0, min(1.0, rem / full_credit_days))

    # Old soft rules → reasons + 0.5 multipliers (no longer force score 1)
    if start_date_obj:
        if start_date_obj.month != current_date.month or start_date_obj.year != current_date.year:
            msg = f"Start date ({start_date_str}) is not in the current month"
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
        "subscore": subscore,
        "reasons": reasons,
        "remaining_days": rem,
        "detail": detail
    }

def _exemption_pair_subscore(exp, turn, unknown_subscore):
    """Fraction of pair relaxed; each unknown contributes unknown_subscore/2."""
    def one(v):
        if v == "yes":
            return 0.5
        if v == "unknown":
            return unknown_subscore / 2.0
        return 0.0  # "no"
    return max(0.0, min(1.0, one(exp) + one(turn)))

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

def extract_bid_signals(text_clean, card_meta=None):
    """
    Extract Phase-2 bid signals from full PDF text (+ optional card meta).
    Returns (signals_dict, signal_parsed_flags).
    """
    card_meta = card_meta or {}
    flags = {
        "est_value_inr": False,
        "primary_item": False,
        "item_category": False,
        "buyer_org": False,
        "buyer_dept": False,
        "consignee_state": False,
        "mii_required": False,
        "mse_pref": False,
    }
    signals = {
        "est_value_inr": None,
        "primary_item": None,
        "item_category": None,
        "buyer_org": None,
        "buyer_dept": None,
        "consignee_state": None,
        "mii_required": "unknown",
        "mse_pref": "unknown",
        "rfp_min_turnover_inr": None,
        "rfp_min_experience_years": None,
    }

    # Estimated bid value
    val_match = (
        re.search(
            r'Estimated\s+Bid\s+Value(?:\s+in\s+INR[^0-9]{0,40})?\s*([\d,]+(?:\.\d+)?)',
            text_clean, re.IGNORECASE
        )
        or re.search(
            r'Estimated\s+Bid\s+Value\s*/\s*([\d,]+)',
            text_clean, re.IGNORECASE
        )
    )
    if val_match:
        amount = _parse_inr_amount(val_match.group(1))
        if amount is not None and amount > 0:
            signals["est_value_inr"] = amount
            flags["est_value_inr"] = True

    # Item category / primary item
    item_match = re.search(
        r'Item\s+Category\s*/?\s*(?:[^\x00-\x7F]+\s*)*'
        r'([A-Za-z0-9][A-Za-z0-9 ,\-\(\)/&]{2,120}?)'
        r'(?=\s+GeMARPTS|\s+Bidder|\s+बडर|\s+Total\s+Quantity|\s+Bid\s|$)',
        text_clean, re.IGNORECASE
    )
    if item_match:
        cat = _clean_english_phrase(item_match.group(1), 120)
        if cat:
            signals["item_category"] = cat
            signals["primary_item"] = cat.split(",")[0].strip()[:100]
            flags["item_category"] = True
            flags["primary_item"] = True
    elif card_meta.get("title"):
        signals["primary_item"] = str(card_meta["title"])[:120]
        flags["primary_item"] = True

    # Ministry / Department / Organisation
    ministry_match = re.search(
        r'Ministry/State\s+Name\s*(?:[^\x00-\x7F]+\s*)*'
        r'([A-Za-z][A-Za-z0-9 &\-\.]{2,80}?)'
        r'(?=\s+Department|\s+वभाग|\s+Organisation|\s+Organization|$)',
        text_clean, re.IGNORECASE
    )
    dept_match = re.search(
        r'Department\s+Name\s*(?:[^\x00-\x7F]+\s*)*'
        r'([A-Za-z][A-Za-z0-9 &\-\.]{2,80}?)'
        r'(?=\s+Organisation|\s+Organization|\s+Office|\s+संगठन|$)',
        text_clean, re.IGNORECASE
    )
    org_match = re.search(
        r'Organisation\s+Name\s*(?:[^\x00-\x7F]+\s*)*'
        r'([A-Za-z][A-Za-z0-9 &\-\.]{2,80}?)'
        r'(?=\s+Office|\s+Total\s+Quantity|\s+Item\s+Category|\s+काया|$)',
        text_clean, re.IGNORECASE
    )

    if org_match:
        org = _clean_english_phrase(org_match.group(1), 80)
        if org:
            signals["buyer_org"] = org.upper()
            flags["buyer_org"] = True
    elif card_meta.get("department"):
        signals["buyer_org"] = str(card_meta["department"]).strip().upper()
        flags["buyer_org"] = True

    if dept_match:
        dept = _clean_english_phrase(dept_match.group(1), 80)
        if dept:
            signals["buyer_dept"] = dept.upper()
            flags["buyer_dept"] = True
    if not signals["buyer_dept"] and ministry_match:
        ministry = _clean_english_phrase(ministry_match.group(1), 80)
        if ministry:
            signals["buyer_dept"] = ministry.upper()
            flags["buyer_dept"] = True
            if not signals["buyer_org"]:
                signals["buyer_org"] = ministry.upper()
                flags["buyer_org"] = True

    # Consignee state — scan address / known state names near Consignee
    cons_match = re.search(
        r'Consignee.{0,400}',
        text_clean, re.IGNORECASE
    )
    state = None
    if cons_match:
        state = _match_indian_state(cons_match.group(0))
    if not state:
        # Ministry/State Name sometimes is a state
        if ministry_match:
            state = _match_indian_state(ministry_match.group(1))
    if not state:
        state = _match_indian_state(text_clean[:3000])
    if state:
        signals["consignee_state"] = state
        flags["consignee_state"] = True

    # MII / MSE purchase preference (tri-state)
    mii_match = re.search(
        r'MII\s+Purchase\s+Preference\s*(?:[^\x00-\x7F/]*/*\s*)*(Yes|No)',
        text_clean, re.IGNORECASE
    )
    if mii_match:
        signals["mii_required"] = mii_match.group(1).lower()
        flags["mii_required"] = True

    mse_pref_match = re.search(
        r'MSE\s+Purchase\s+Preference\s*(?:[^\x00-\x7F/]*/*\s*)*(Yes|No)',
        text_clean, re.IGNORECASE
    )
    if mse_pref_match:
        signals["mse_pref"] = mse_pref_match.group(1).lower()
        flags["mse_pref"] = True

    # Min turnover / experience (for eligibility, not signal-field count)
    turn_match = (
        re.search(
            r'(?:Minimum|Min\.?)\s+(?:Average\s+)?(?:Annual\s+)?Turnover[^\d]{0,60}([\d,]+(?:\.\d+)?)',
            text_clean, re.IGNORECASE
        )
        or re.search(
            r'Average\s+Annual\s+Turnover(?:\s+of\s+the\s+bidder)?[^\d]{0,60}([\d,]+(?:\.\d+)?)',
            text_clean, re.IGNORECASE
        )
        or re.search(
            r'Turnover\s+Criteria[^\d]{0,40}([\d,]+(?:\.\d+)?)',
            text_clean, re.IGNORECASE
        )
    )
    if turn_match:
        signals["rfp_min_turnover_inr"] = _parse_inr_amount(turn_match.group(1))

    exp_match = re.search(
        r'(?:Years?\s+of\s+)?(?:Past\s+)?Experience[^\d]{0,40}(\d{1,2})\s*(?:Years?|Yrs?)?',
        text_clean, re.IGNORECASE
    )
    if exp_match:
        try:
            signals["rfp_min_experience_years"] = int(exp_match.group(1))
        except ValueError:
            pass

    return signals, flags

def compute_eligibility(signals, st_turn, mse_turn, profile):
    """
    Soft eligibility gate (BE-09). Credits MSE/Startup turnover exemptions.
    Returns {verdict, flags, detail}.
    """
    elig = profile.get("eligibility", {})
    company_turn = float(elig.get("annual_turnover_inr", 0) or 0)
    rfp_turn = signals.get("rfp_min_turnover_inr")
    flags = []
    detail_parts = []

    # Turnover exemption granted if either MSE or Startup turnover exemption is yes
    turn_exempt = (st_turn == "yes") or (mse_turn == "yes")
    turn_exempt_unknown = (st_turn == "unknown" and mse_turn == "unknown")

    if rfp_turn is None:
        if turn_exempt:
            verdict = "eligible"
            detail_parts.append("RFP turnover requirement unparsed; turnover exemption present → eligible.")
        elif turn_exempt_unknown:
            verdict = "unknown"
            flags.append("turnover_req_unparsed")
            detail_parts.append("RFP turnover requirement and exemptions unparsed.")
        else:
            # no requirement found; assume eligible
            verdict = "eligible"
            detail_parts.append("No RFP min-turnover found; treated as eligible.")
    elif rfp_turn <= company_turn:
        verdict = "eligible"
        detail_parts.append(
            f"RFP min turnover ₹{rfp_turn:,} ≤ company ₹{int(company_turn):,}."
        )
    else:
        # requirement exceeds company turnover
        if turn_exempt:
            verdict = "eligible"
            flags.append("turnover_above_profile_but_exempted")
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,} "
                f"but MSE/Startup turnover exemption granted → eligible."
            )
        elif st_turn == "unknown" or mse_turn == "unknown":
            # partial unknown with requirement gap
            if turn_exempt:
                verdict = "eligible"
            else:
                verdict = "unknown"
                flags.append("turnover_gap_uncertain")
                detail_parts.append(
                    f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,}; "
                    f"exemption status incomplete."
                )
        else:
            verdict = "turnover_gap"
            flags.append("turnover_gap")
            detail_parts.append(
                f"RFP min turnover ₹{rfp_turn:,} > company ₹{int(company_turn):,} "
                f"and no MSE/Startup turnover exemption."
            )

    # Experience soft flag only
    rfp_exp = signals.get("rfp_min_experience_years")
    company_exp = elig.get("years_experience")
    if rfp_exp is not None and company_exp is not None:
        try:
            if int(rfp_exp) > int(company_exp) and st_turn != "yes" and mse_turn != "yes":
                # also credit experience exemptions via st_exp/mse_exp not passed — soft flag only
                flags.append("experience_may_be_tight")
                detail_parts.append(
                    f"RFP experience {rfp_exp}y vs company {company_exp}y (soft flag)."
                )
        except (TypeError, ValueError):
            pass

    return {
        "verdict": verdict,
        "flags": flags,
        "detail": " ".join(detail_parts) if detail_parts else "Eligibility evaluated."
    }

def compute_fit_score(analysis, signals, eligibility, profile, cfg, card_meta=None):
    """
    Company-aware Fit score 0-100 (BE-10).
    Returns (fit_score, fit_breakdown, business_line_dict|None).
    """
    card_meta = card_meta or {}
    fit_cfg = cfg.get("fit") or DEFAULT_SCORING_CONFIG.get("fit", {})
    fit_weights = fit_cfg.get("weights") or DEFAULT_SCORING_CONFIG["fit"]["weights"]
    unknown_sub = float(cfg.get("unknown_subscore", 0.5))
    weak_rel = float(fit_cfg.get("weak_relevance_subscore", 0.5))
    unknown_buyer = float(fit_cfg.get("unknown_buyer_subscore", 0.4))
    gap_sub = float(fit_cfg.get("turnover_gap_subscore", 0.3))

    # --- relevance ---
    title = str(card_meta.get("title") or "")
    keyword = str(card_meta.get("keyword") or "")
    haystack = " ".join([
        title,
        str(signals.get("primary_item") or ""),
        str(signals.get("item_category") or ""),
        keyword,
    ]).lower()

    best_score = 0.0
    best_line = None
    for line in profile.get("business_lines") or []:
        kws = line.get("keywords") or []
        hits = sum(1 for kw in kws if kw and kw.lower() in haystack)
        if hits >= 2:
            s = 1.0
        elif hits == 1:
            s = weak_rel
        else:
            s = 0.0
        priority = float(line.get("priority", 1.0) or 1.0)
        s = min(1.0, s * priority)
        if s > best_score:
            best_score = s
            best_line = line

    # Q2 avoid soft penalty on relevance
    avoid = profile.get("avoid_rules") or {}
    if avoid.get("gem_q2_category") and re.search(r'\(Q2\)', haystack, re.IGNORECASE):
        best_score *= 0.7
        if best_score > 0:
            rel_detail = f"Matched business line with Q2 soft penalty; subscore={best_score:.3f}."
        else:
            rel_detail = "No business-line match; Q2 category present."
    else:
        if best_line and best_score >= 1.0:
            rel_detail = f"Strong match: {best_line.get('label')}."
        elif best_line and best_score > 0:
            rel_detail = f"Weak match: {best_line.get('label')}."
        else:
            rel_detail = "No business-line keyword match."

    # --- serviceability ---
    soft_states = [s.lower() for s in (profile.get("serviceability") or {}).get("soft_avoid_states") or []]
    soft_pen = float((profile.get("serviceability") or {}).get("soft_avoid_penalty", 0.5))
    state = signals.get("consignee_state")
    if not state:
        svc_sub = unknown_sub
        svc_detail = f"Consignee state unknown; subscore={unknown_sub}."
    elif state.lower() in soft_states:
        svc_sub = soft_pen
        svc_detail = f"Soft-avoid state {state}; penalty subscore={soft_pen}."
    else:
        svc_sub = 1.0
        svc_detail = f"Serviceable state {state}."

    # --- value fit ---
    vp = profile.get("value_preference") or {}
    sweet_min = float(vp.get("sweet_min_inr", 500000))
    sweet_max = float(vp.get("sweet_max_inr", 30000000))
    val = signals.get("est_value_inr")
    if val is None:
        val_sub = unknown_sub
        val_detail = f"Est. value unknown; subscore={unknown_sub}."
    elif sweet_min <= val <= sweet_max:
        val_sub = 1.0
        val_detail = f"Value ₹{val:,} inside sweet band [{int(sweet_min):,}, {int(sweet_max):,}]."
    elif val < sweet_min:
        val_sub = max(0.0, min(1.0, val / sweet_min if sweet_min > 0 else 1.0))
        val_detail = f"Value ₹{val:,} below sweet min ₹{int(sweet_min):,}."
    else:
        upper = sweet_max * 3.0 if sweet_max > 0 else val
        val_sub = _linear_ramp(val, sweet_max, upper)
        val_detail = f"Value ₹{val:,} above sweet max ₹{int(sweet_max):,}."

    # --- buyer affinity ---
    buyer = (signals.get("buyer_org") or signals.get("buyer_dept") or "")
    buyer_u = buyer.upper()
    affinity_map = profile.get("buyer_affinity") or {}
    buy_sub = None
    matched_buyer = None
    for key, score in affinity_map.items():
        if buyer_u and (key.upper() in buyer_u or buyer_u in key.upper()):
            if buy_sub is None or float(score) > buy_sub:
                buy_sub = float(score)
                matched_buyer = key
    if buy_sub is None:
        if not buyer_u:
            buy_sub = unknown_buyer
            buy_detail = f"Buyer unknown; default subscore={unknown_buyer}."
        else:
            buy_sub = unknown_buyer
            buy_detail = f"Buyer '{buyer_u}' not in affinity map; default={unknown_buyer}."
    else:
        buy_detail = f"Buyer affinity for {matched_buyer}={buy_sub}."

    # --- eligibility factor ---
    verdict = (eligibility or {}).get("verdict", "unknown")
    if verdict == "eligible":
        elig_sub = 1.0
        elig_detail = "Eligibility: eligible."
    elif verdict == "turnover_gap":
        elig_sub = gap_sub
        elig_detail = f"Eligibility: turnover_gap; subscore={gap_sub}."
    else:
        elig_sub = unknown_sub
        elig_detail = f"Eligibility: unknown; subscore={unknown_sub}."

    criteria = [
        ("relevance", best_score, rel_detail),
        ("serviceability", svc_sub, svc_detail),
        ("value_fit", val_sub, val_detail),
        ("buyer_affinity", buy_sub, buy_detail),
        ("eligibility_factor", elig_sub, elig_detail),
    ]
    fit_score, fit_breakdown = build_score_breakdown(criteria, fit_weights)

    business_line = None
    if best_line and best_score > 0:
        business_line = {
            "id": best_line.get("id"),
            "label": best_line.get("label")
        }

    return fit_score, fit_breakdown, business_line

def compute_recommendation(fit_score, risk_score, eligibility, is_expired, cfg):
    """
    Two-axis recommendation (BE-11): Pursue / Review / Watch / Drop.
    Never overwrites manual status — advisory only.
    """
    if risk_score is None and fit_score is None:
        return None

    fit_cfg = cfg.get("fit") or DEFAULT_SCORING_CONFIG.get("fit", {})
    fit_min = float(fit_cfg.get("fit_min", 60))
    thresholds = cfg.get("status_thresholds") or DEFAULT_SCORING_CONFIG["status_thresholds"]
    shortlist_min = float(thresholds.get("shortlist_min", 70))

    fs = fit_score if fit_score is not None else 0
    rs = risk_score if risk_score is not None else 0

    high_fit = fs >= fit_min
    high_risk = rs >= shortlist_min  # high Risk-score = friendlier tender

    if high_fit and high_risk:
        rec = "Pursue"
    elif high_fit and not high_risk:
        rec = "Review"
    elif (not high_fit) and high_risk:
        rec = "Watch"
    else:
        rec = "Drop"

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

def analyze_rfp_pdf(pdf_path, start_date_str=None, end_date_str=None,
                    scoring_config=None, company_profile=None, card_meta=None):
    """
    Parse RFP PDF with tri-state fields + weighted 0-100 Risk scoring + Fit axis.
    Returns analysis dict, or None if path missing.
    """
    cfg = scoring_config if scoring_config is not None else load_scoring_config()
    profile = company_profile if company_profile is not None else load_company_profile()
    card_meta = card_meta or {}
    unknown_sub = float(cfg.get("unknown_subscore", 0.5))
    weights = cfg.get("weights", DEFAULT_SCORING_CONFIG["weights"])
    emd_cfg = cfg.get("emd", DEFAULT_SCORING_CONFIG["emd"])
    epbg_cfg = cfg.get("epbg", DEFAULT_SCORING_CONFIG["epbg"])

    analysis = {
        "emd_amount": None,
        "emd_status": "Not Required",
        "startup_exemption": "Unknown",
        "mse_exemption": "Unknown",
        "pre_bid_required": "Unknown",
        "pre_bid_date": None,
        "epbg_required": "Unknown",
        "epbg_percentage": None,
        "score": None,
        "score_scale": 100,
        "analysis_status": "ok",
        "parsed_fields": 0,
        "total_fields": TOTAL_ANALYSIS_FIELDS,
        "confidence": 0.0,
        "breakdown": [],
        "reasons": [],
        "est_value_inr": None,
        "primary_item": None,
        "item_category": None,
        "buyer_org": None,
        "buyer_dept": None,
        "consignee_state": None,
        "mii_required": "unknown",
        "mse_pref": "unknown",
        "signal_parsed": 0,
        "signal_fields": TOTAL_SIGNAL_FIELDS,
        "eligibility": None,
        "fit_score": None,
        "fit_breakdown": [],
        "business_line": None,
        "recommendation": None
    }

    if not os.path.exists(pdf_path):
        return None

    try:
        reader = PdfReader(pdf_path)
        text = ""
        # BE-08: whole PDF up to hard ceiling (keeps Phase-1 fields; more pages for signals)
        n_pages = min(MAX_PDF_PAGES, len(reader.pages))
        for i in range(n_pages):
            text += (reader.pages[i].extract_text() or "") + "\n"

        text_clean = re.sub(r'\s+', ' ', text)

        # Track which of the 8 fields specifically matched
        # 1 emd_required, 2 emd_amount, 3 st_exp, 4 st_turn, 5 mse_exp, 6 mse_turn,
        # 7 prebid_required, 8 epbg_required
        field_parsed = {
            "emd_required": False,
            "emd_amount": False,
            "st_exp": False,
            "st_turn": False,
            "mse_exp": False,
            "mse_turn": False,
            "prebid_required": False,
            "epbg_required": False,
        }

        # --- 1. EMD (tri-state) ---
        # Colon form and GeM bilingual "EMD Detail/ ... Required/ ... Yes|No"
        emd_req_match = (
            re.search(r'EMD\s+Required\s*[:/]\s*(Yes|No)', text_clean, re.IGNORECASE)
            or re.search(
                r'EMD\s+Detail\s*/.*?Required\s*/\s*(?:\S+\s+)?(Yes|No)',
                text_clean, re.IGNORECASE
            )
        )
        emd_amount_match = re.search(
            r'(?:EMD\s+Amount\s*(?:\(INR\))?|EMD\s*value)\s*[:/]\s*([\d,]+)',
            text_clean, re.IGNORECASE
        )

        if emd_req_match:
            field_parsed["emd_required"] = True
            emd_req = emd_req_match.group(1).lower()
        else:
            emd_req = "unknown"

        emd_amount = None
        if emd_amount_match:
            field_parsed["emd_amount"] = True
            amount_str = emd_amount_match.group(1).replace(",", "")
            try:
                emd_amount = int(amount_str)
            except ValueError:
                emd_amount = None
                field_parsed["emd_amount"] = False

        # If amount found but required flag unknown, treat as required for scoring
        if emd_req == "unknown" and emd_amount is not None:
            emd_req = "yes"
            field_parsed["emd_required"] = True

        analysis["emd_amount"] = emd_amount
        free_th = float(emd_cfg.get("free_threshold_inr", 200000))
        max_th = float(emd_cfg.get("max_penalty_threshold_inr", 2000000))

        if emd_req == "no":
            analysis["emd_status"] = "No EMD Required (OK)"
            analysis["reasons"].append("No EMD required.")
            emd_sub = 1.0
            emd_detail = "EMD not required."
        elif emd_req == "unknown":
            analysis["emd_status"] = "Unknown"
            analysis["reasons"].append("EMD required status could not be parsed.")
            emd_sub = unknown_sub
            emd_detail = f"EMD required unknown; subscore={unknown_sub}."
        else:
            # required yes
            if emd_amount is not None:
                analysis["emd_status"] = f"Required ({emd_amount:,} INR)"
                emd_sub = _linear_ramp(emd_amount, free_th, max_th)
                if emd_amount <= free_th:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) within free threshold ({int(free_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ≤ free threshold {int(free_th):,}."
                elif emd_amount >= max_th:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) at/above max penalty threshold ({int(max_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ≥ max penalty {int(max_th):,}."
                else:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) on graduated penalty curve "
                        f"({int(free_th):,}–{int(max_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ramped between {int(free_th):,} and {int(max_th):,}."
            else:
                analysis["emd_status"] = "Required (Amount not parsed)"
                analysis["reasons"].append("EMD required but amount could not be parsed.")
                emd_sub = unknown_sub
                emd_detail = f"EMD required, amount unknown; subscore={unknown_sub}."

        # --- 2. Startup exemptions (field-anchored only; no document-wide .*?Yes fallbacks) ---
        # GeM bilingual PDFs put Yes/No after the label + Hindi text (delimiter : or /)
        st_exp = "unknown"
        st_turn = "unknown"
        startup_match = re.search(
            r'Startup\s+Exemption\s+for\s+Years\s+of\s+Experience\s+and\s+Turnover\s*[:/]\s*'
            r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
            text_clean, re.IGNORECASE
        )
        if startup_match:
            val = startup_match.group(1).lower()
            st_exp = val
            st_turn = val
            field_parsed["st_exp"] = True
            field_parsed["st_turn"] = True
        else:
            st_exp_match = re.search(
                r'Startup\s+Exemption\s+for\s+(?:Years\s+of\s+)?Experience\s*[:/]\s*'
                r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
                text_clean, re.IGNORECASE
            )
            st_turn_match = re.search(
                r'Startup\s+Exemption\s+for\s+Turnover\s*[:/]\s*'
                r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
                text_clean, re.IGNORECASE
            )
            if st_exp_match:
                st_exp = st_exp_match.group(1).lower()
                field_parsed["st_exp"] = True
            if st_turn_match:
                st_turn = st_turn_match.group(1).lower()
                field_parsed["st_turn"] = True

        # --- 3. MSE exemptions (field-anchored only; no document-wide .*?Yes fallbacks) ---
        mse_exp = "unknown"
        mse_turn = "unknown"
        mse_match = re.search(
            r'MSE\s+Exemption\s+for\s+Years\s+of\s+Experience\s+and\s+Turnover\s*[:/]\s*'
            r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
            text_clean, re.IGNORECASE
        )
        if mse_match:
            val = mse_match.group(1).lower()
            mse_exp = val
            mse_turn = val
            field_parsed["mse_exp"] = True
            field_parsed["mse_turn"] = True
        else:
            mse_exp_match = re.search(
                r'MSE\s+Exemption\s+for\s+(?:Years\s+[Oo]f\s+)?Experience\s*[:/]\s*'
                r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
                text_clean, re.IGNORECASE
            )
            mse_turn_match = re.search(
                r'MSE\s+Exemption\s+for\s+Turnover\s*[:/]\s*'
                r'[^YN]{0,200}?(Yes|No)(?=\s|$)',
                text_clean, re.IGNORECASE
            )
            if mse_exp_match:
                mse_exp = mse_exp_match.group(1).lower()
                field_parsed["mse_exp"] = True
            if mse_turn_match:
                mse_turn = mse_turn_match.group(1).lower()
                field_parsed["mse_turn"] = True

        analysis["startup_exemption"] = get_exemption_label(st_exp, st_turn)
        analysis["mse_exemption"] = get_exemption_label(mse_exp, mse_turn)

        st_sub = _exemption_pair_subscore(st_exp, st_turn, unknown_sub)
        mse_sub = _exemption_pair_subscore(mse_exp, mse_turn, unknown_sub)

        st_yes = (1 if st_exp == "yes" else 0) + (1 if st_turn == "yes" else 0)
        mse_yes = (1 if mse_exp == "yes" else 0) + (1 if mse_turn == "yes" else 0)
        if st_exp == "unknown" and st_turn == "unknown":
            analysis["reasons"].append("Exemption Check: Startup exemption fields could not be parsed.")
        elif st_yes == 2:
            analysis["reasons"].append("Exemption Check: Full Startup exemptions (Experience + Turnover).")
        elif st_yes == 0 and st_exp != "unknown" and st_turn != "unknown":
            analysis["reasons"].append("Exemption Check: Startup Experience/Turnover criteria NOT relaxed.")
        else:
            analysis["reasons"].append(
                f"Exemption Check: Partial/unknown Startup exemptions (parsed relaxed={st_yes}/2)."
            )

        if mse_exp == "unknown" and mse_turn == "unknown":
            analysis["reasons"].append("Exemption Check: MSE exemption fields could not be parsed.")
        elif mse_yes == 2:
            analysis["reasons"].append("Exemption Check: Full MSE exemptions (Experience + Turnover).")
        elif mse_yes == 0 and mse_exp != "unknown" and mse_turn != "unknown":
            analysis["reasons"].append("Exemption Check: MSE Experience/Turnover criteria NOT relaxed.")
        else:
            analysis["reasons"].append(
                f"Exemption Check: Partial/unknown MSE exemptions (parsed relaxed={mse_yes}/2)."
            )

        st_detail = f"Startup pair subscore={st_sub:.3f} (exp={st_exp}, turn={st_turn})."
        mse_detail = f"MSE pair subscore={mse_sub:.3f} (exp={mse_exp}, turn={mse_turn})."

        # --- 4. Pre-bid ---
        prebid_req_match = re.search(
            r'Pre-Bid\s+Meeting\s+Required\s*[:/]\s*(?:\S+\s+)?(Yes|No)',
            text_clean, re.IGNORECASE
        )
        prebid_date_match = re.search(
            r'(?:Pre-Bid\s+Date\s+and\s+Time|Pre-Bid\s+Meeting\s+Date)\s*[:/]\s*'
            r'([\d\-\s\:\w\,]+?(?:AM|PM|hrs|GMT))',
            text_clean, re.IGNORECASE
        )

        if prebid_req_match:
            field_parsed["prebid_required"] = True
            prebid_req = prebid_req_match.group(1).lower()
            analysis["pre_bid_required"] = prebid_req_match.group(1).capitalize()
        else:
            prebid_req = "unknown"
            analysis["pre_bid_required"] = "Unknown"

        if prebid_req == "yes":
            if prebid_date_match:
                p_date = prebid_date_match.group(1).strip()
                analysis["pre_bid_date"] = p_date
                analysis["reasons"].append(f"Pre-Bid meeting scheduled on: {p_date}.")
                prebid_sub = 0.7
                prebid_detail = "Pre-bid required with parsed date."
            else:
                analysis["reasons"].append(
                    "Pre-bid meeting is required, but date and time are not clearly specified."
                )
                prebid_sub = 0.3
                prebid_detail = "Pre-bid required without parsed date."
        elif prebid_req == "no":
            analysis["reasons"].append("No Pre-bid meeting required.")
            prebid_sub = 1.0
            prebid_detail = "Pre-bid not required."
        else:
            analysis["reasons"].append("Pre-bid required status could not be parsed.")
            prebid_sub = unknown_sub
            prebid_detail = f"Pre-bid unknown; subscore={unknown_sub}."

        # --- 5. ePBG (field-anchored only; no loose whole-document force-yes) ---
        epbg_req_match = (
            re.search(r'ePBG\s+Required\s*[:/]\s*(Yes|No)', text_clean, re.IGNORECASE)
            or re.search(
                r'ePBG\s+Detail\s*/.*?Required\s*/\s*(?:\S+\s+)?(Yes|No)',
                text_clean, re.IGNORECASE
            )
        )
        epbg_pct_match = re.search(
            r'ePBG\s+Percentage\s*(?:\(%\))?\s*[:/]\s*([\d\.]+)', text_clean, re.IGNORECASE
        )

        if epbg_req_match:
            field_parsed["epbg_required"] = True
            epbg_req = epbg_req_match.group(1).lower()
            analysis["epbg_required"] = epbg_req_match.group(1).capitalize()
        else:
            epbg_req = "unknown"
            analysis["epbg_required"] = "Unknown"

        epbg_pct_val = None
        if epbg_pct_match:
            try:
                epbg_pct_val = float(epbg_pct_match.group(1))
                analysis["epbg_percentage"] = f"{epbg_pct_match.group(1)}%"
            except ValueError:
                epbg_pct_val = None

        # If percentage found but required unknown, treat as required
        if epbg_req == "unknown" and epbg_pct_val is not None:
            epbg_req = "yes"
            analysis["epbg_required"] = "Yes"
            field_parsed["epbg_required"] = True

        free_pct = float(epbg_cfg.get("free_threshold_pct", 3.0))
        max_pct = float(epbg_cfg.get("max_penalty_pct", 10.0))

        if epbg_req == "no":
            analysis["reasons"].append("No ePBG required.")
            epbg_sub = 1.0
            epbg_detail = "ePBG not required."
        elif epbg_req == "unknown":
            analysis["reasons"].append("ePBG required status could not be parsed.")
            epbg_sub = unknown_sub
            epbg_detail = f"ePBG unknown; subscore={unknown_sub}."
        else:
            if epbg_pct_val is not None:
                analysis["reasons"].append(
                    f"ePBG / Performance Guarantee required: {epbg_pct_val}%."
                )
                epbg_sub = _linear_ramp(epbg_pct_val, free_pct, max_pct)
                epbg_detail = f"ePBG {epbg_pct_val}% (free≤{free_pct}%, max pen≥{max_pct}%)."
            else:
                analysis["reasons"].append("ePBG required (Percentage details not parsed).")
                epbg_sub = unknown_sub
                epbg_detail = f"ePBG required, pct unknown; subscore={unknown_sub}."

        # --- 6. Date window (BE-03) ---
        date_info = evaluate_date_window(start_date_str, end_date_str, cfg)
        date_sub = date_info["subscore"]
        date_detail = date_info["detail"]
        for r in date_info["reasons"]:
            analysis["reasons"].append(r)

        # --- Confidence (8 fields) ---
        parsed_count = sum(1 for v in field_parsed.values() if v)
        analysis["parsed_fields"] = parsed_count
        analysis["total_fields"] = TOTAL_ANALYSIS_FIELDS
        analysis["confidence"] = round(parsed_count / TOTAL_ANALYSIS_FIELDS, 4)

        # --- Weighted final Risk score + breakdown (Phase-1, unchanged 6 criteria) ---
        criteria = [
            ("emd", emd_sub, emd_detail),
            ("startup_exemption", st_sub, st_detail),
            ("mse_exemption", mse_sub, mse_detail),
            ("prebid", prebid_sub, prebid_detail),
            ("date_window", date_sub, date_detail),
            ("epbg", epbg_sub, epbg_detail),
        ]
        final_score, breakdown = build_score_breakdown(criteria, weights)
        analysis["score"] = final_score
        analysis["breakdown"] = breakdown
        analysis["analysis_status"] = "ok"
        analysis["is_expired"] = bool(date_info["is_expired"])

        # Hard-reject expired bids: force score 0 (BE-03)
        if date_info["is_expired"]:
            analysis["score"] = 0
            analysis["reasons"].insert(0, "Auto-Rejected: bid expired")

        # --- BE-08: new bid signals (separate confidence tally) ---
        signals, signal_flags = extract_bid_signals(text_clean, card_meta=card_meta)
        for k in (
            "est_value_inr", "primary_item", "item_category",
            "buyer_org", "buyer_dept", "consignee_state",
            "mii_required", "mse_pref"
        ):
            analysis[k] = signals.get(k)
        analysis["signal_parsed"] = sum(1 for v in signal_flags.values() if v)
        analysis["signal_fields"] = TOTAL_SIGNAL_FIELDS

        # --- BE-09: soft eligibility gate ---
        eligibility = compute_eligibility(signals, st_turn, mse_turn, profile)
        analysis["eligibility"] = eligibility
        if eligibility.get("verdict") == "turnover_gap":
            analysis["reasons"].append(f"Eligibility: {eligibility.get('detail')}")

        # --- BE-10: Fit score ---
        fit_score, fit_breakdown, business_line = compute_fit_score(
            analysis, signals, eligibility, profile, cfg, card_meta=card_meta
        )
        analysis["fit_score"] = fit_score
        analysis["fit_breakdown"] = fit_breakdown
        analysis["business_line"] = business_line

        # --- BE-11: two-axis recommendation ---
        analysis["recommendation"] = compute_recommendation(
            fit_score,
            analysis.get("score"),
            eligibility,
            bool(analysis.get("is_expired")),
            cfg
        )

    except Exception as e:
        print(f"Error parsing PDF metadata: {e}")
        failed = get_failed_analysis(f"PDF parsing error: {e}")
        return failed

    return analysis

def load_existing_metadata():
    existing_tenders = {}
    csv_path = os.path.join(TENDERS_DIR, "metadata.csv")
    if os.path.exists(csv_path):
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bid_no = row.get("Bid Number")
                    if bid_no:
                        # Load parsed analysis JSON if it exists
                        analysis = None
                        analysis_str = row.get("Analysis")
                        if analysis_str:
                            try:
                                analysis = json.loads(analysis_str)
                            except:
                                pass
                                
                        existing_tenders[bid_no] = {
                            "bid_no": bid_no,
                            "title": row.get("Title"),
                            "quantity": row.get("Quantity"),
                            "department": row.get("Department"),
                            "start_date": row.get("Start Date"),
                            "end_date": row.get("End Date"),
                            "keyword": row.get("Keyword"),
                            "downloaded": row.get("Downloaded") == "True",
                            "local_pdf_path": row.get("Local PDF Path"),
                            "pdf_url": row.get("PDF URL"),
                            "status": row.get("Status", "Pending Review"),
                            "analysis": analysis
                        }
            print(f"Loaded {len(existing_tenders)} existing records from metadata.csv")
        except Exception as e:
            print(f"Error reading existing CSV metadata: {e}")
    return existing_tenders

def save_metadata(tenders_list):
    # Save JSON
    json_path = os.path.join(TENDERS_DIR, "metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tenders_list, f, indent=2, ensure_ascii=False)

    # Save JS (for dashboard)
    js_path = os.path.join(TENDERS_DIR, "metadata.js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("// GeM Scraper Output Metadata\n")
        f.write(f"const TENDER_DATA = {json.dumps(tenders_list, indent=2, ensure_ascii=False)};\n")

    # Save CSV
    csv_path = os.path.join(TENDERS_DIR, "metadata.csv")
    try:
        with open(csv_path, mode="w", encoding="utf-8", newline="") as f:
            fieldnames = ["Bid Number", "Title", "Quantity", "Department", "Start Date", "End Date", "Keyword", "Downloaded", "Local PDF Path", "PDF URL", "Status", "Analysis"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in tenders_list:
                writer.writerow({
                    "Bid Number": t["bid_no"],
                    "Title": t["title"],
                    "Quantity": t["quantity"],
                    "Department": t["department"],
                    "Start Date": t["start_date"],
                    "End Date": t["end_date"],
                    "Keyword": t["keyword"],
                    "Downloaded": str(t["downloaded"]),
                    "Local PDF Path": t["local_pdf_path"],
                    "PDF URL": t["pdf_url"],
                    "Status": t.get("status", "Pending Review"),
                    "Analysis": json.dumps(t.get("analysis")) if t.get("analysis") else ""
                })
        print(f"Saved metadata CSV: {csv_path}")
    except Exception as e:
        print(f"Error saving CSV metadata: {e}")

def parse_cards(html, keyword):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#bidCard") or soup
    cards = container.select("div.card")
    results = []

    for card in cards:
        try:
            bid_link = card.select_one("p.bid_no a.bid_no_hover, p.bid_no a, a.bid_no_hover")
            if not bid_link:
                continue
            bid_no = bid_link.get_text(strip=True)
            if not bid_no or len(bid_no) < 5:
                continue

            pdf_href = bid_link.get("href", "")
            if pdf_href.startswith("http"):
                pdf_url = pdf_href
            elif pdf_href:
                pdf_url = urllib.parse.urljoin("https://bidplus.gem.gov.in/all-bids", pdf_href)
            else:
                pdf_url = f"https://bidplus.gem.gov.in/showbidDocument/{bid_no}"

            title = ""
            col4 = card.select_one("div.col-md-4")
            if col4:
                popover = col4.select_one("a[data-toggle='popover']")
                if popover:
                    title = popover.get("data-content") or popover.get("title") or popover.get_text(strip=True)
                else:
                    rows = col4.select("div.row")
                    if rows:
                        title = rows[0].get_text(strip=True).replace("Items:", "").strip()

            if not title:
                continue

            quantity = "N/A"
            if col4:
                rows = col4.select("div.row")
                for r in rows:
                    txt = r.get_text(strip=True)
                    if "Quantity:" in txt:
                        quantity = txt.replace("Quantity:", "").strip()
                        break

            department = "N/A"
            col5 = card.select_one("div.col-md-5")
            if col5:
                rows = col5.select("div.row")
                if len(rows) >= 2:
                    department = rows[1].get_text(separator=" | ", strip=True).replace("Department Name And Address:", "").strip()
                elif rows:
                    department = rows[0].get_text(separator=" | ", strip=True).replace("Department Name And Address:", "").strip()
            
            department = re.sub(r'\s+', ' ', department)

            start_date_el = card.select_one(".start_date")
            start_date = start_date_el.get_text(strip=True) if start_date_el else "N/A"

            end_date_el = card.select_one(".end_date, span.end_date")
            end_date = end_date_el.get_text(strip=True) if end_date_el else "N/A"

            results.append({
                "bid_no": bid_no,
                "title": title,
                "quantity": quantity,
                "department": department,
                "start_date": start_date,
                "end_date": end_date,
                "pdf_url": pdf_url,
                "keyword": keyword,
                "downloaded": False,
                "local_pdf_path": "",
                "status": "Pending Review",
                "analysis": None
            })
        except Exception as e:
            print(f"Error parsing card: {e}")
            continue

    return results

def select_sort_order(page, sort_order="Bid-End-Date-Latest"):
    sort_map = {
        "Bid-Start-Date-Latest": ("Bid Start Date: Latest First", "#Bid-Start-Date-Latest"),
        "Bid-Start-Date-Oldest": ("Bid Start Date: Oldest First", "#Bid-Start-Date-Oldest"),
        "Bid-End-Date-Latest": ("Bid End Date: Latest First", "#Bid-End-Date-Latest"),
        "Bid-End-Date-Oldest": ("Bid End Date: Oldest First", "#Bid-End-Date-Oldest")
    }
    
    label, selector_id = sort_map.get(sort_order, ("Bid End Date: Latest First", "#Bid-End-Date-Latest"))
    print(f"Setting sorting to '{label}'...")
    try:
        sort_button = page.locator("#currentSort")
        if sort_button.count() > 0:
            sort_button.click()
            page.wait_for_timeout(800)
            
            option = page.locator(selector_id)
            if option.count() > 0:
                option.click()
                page.wait_for_timeout(3000)  # Wait for AJAX refresh
                print(f"Successfully set sort order to '{label}'")
                return True
        print(f"Could not find the sort button (#currentSort) or target option ({selector_id}) on the page.")
    except Exception as e:
        print(f"Failed to set sorting option: {e}")
    return False

def download_rfp_pdf(context, pdf_url, save_path):
    page = None
    try:
        page = context.new_page()
        
        # Listen for download event
        download_container = []
        page.on("download", lambda d: download_container.append(d))
        
        # Navigate to the PDF URL
        try:
            response = page.goto(pdf_url, wait_until="commit", timeout=40000)
        except Exception as e:
            if "download" in str(e).lower() or "navigated to a download" in str(e).lower():
                response = None
            else:
                raise e
        
        page.wait_for_timeout(2000)
        
        # Scenario A: Download triggered
        if download_container:
            download = download_container[0]
            download.save_as(save_path)
            print(f"Successfully saved PDF via page download event: {os.path.basename(save_path)}")
            return True
            
        # Scenario B: Loaded inline
        if response and response.status == 200:
            body = response.body()
            if body.startswith(b"%PDF") or "pdf" in response.headers.get("content-type", "").lower():
                with open(save_path, "wb") as f:
                    f.write(body)
                print(f"Successfully saved PDF via page response body: {os.path.basename(save_path)}")
                return True
            else:
                print(f"Response was not a PDF (Content-Type: {response.headers.get('content-type')}).")
                
    except Exception as e:
        print(f"Download failed for {pdf_url}: {e}")
    finally:
        if page:
            try:
                page.close()
            except:
                pass
    return False

def parse_gem_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    for fmt in ("%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    match = re.search(r'(\d{2})-(\d{2})-(\d{4})', date_str)
    if match:
        try:
            return datetime.datetime.strptime(match.group(0), "%d-%m-%Y")
        except ValueError:
            pass
    return None

def check_date_policy(start_date_str, end_date_str):
    start_date_obj = parse_gem_date(start_date_str)
    end_date_obj = parse_gem_date(end_date_str)
    current_date = datetime.datetime.now()
    
    reasons = []
    
    if not start_date_obj or not end_date_obj:
        return True, []
        
    # 1. Start date must be in current month & year
    if start_date_obj.month != current_date.month or start_date_obj.year != current_date.year:
        reasons.append(f"Start date ({start_date_str}) is not in the current month")
    
    # 2. End date must be at least 7 days (1 week) after start date
    if (end_date_obj - start_date_obj).days < 7:
        reasons.append(f"Bid duration is less than 7 days (Start: {start_date_str}, End: {end_date_str})")
        
    # 3. End date must be at least 7 days (1 week) after current date
    if (end_date_obj - current_date).days < 7:
        reasons.append(f"Remaining bid time is less than 7 days (End: {end_date_str}, Today: {current_date.strftime('%d-%m-%Y')})")
        
    return len(reasons) == 0, reasons

def scrape(selected_keywords=None, max_pages=2, sort_order="Bid-End-Date-Latest", log_callback=None):
    class LogStream:
        def __init__(self, callback):
            self.callback = callback
            self.buffer = ""
            self.is_writing = False
        def write(self, buf):
            sys.__stdout__.write(buf)
            if self.is_writing:
                return
            self.is_writing = True
            try:
                self.buffer += buf
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    self.callback(line)
            finally:
                self.is_writing = False
        def flush(self):
            sys.__stdout__.flush()
            
    original_stdout = sys.stdout
    if log_callback:
        sys.stdout = LogStream(log_callback)

    try:
        print("Initializing directories...")
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

        # 1. Load dynamic keywords
        if selected_keywords:
            KEYWORDS = selected_keywords
            print(f"Scraping {len(KEYWORDS)} selected keyword(s) for search.")
        else:
            KEYWORDS = load_keywords()
        
        # 2. Load existing metadata records
        all_tenders = load_existing_metadata()
        
        new_tenders_count = 0
        
        with sync_playwright() as p:
            print("Launching browser with stealth settings...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                accept_downloads=True
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = context.new_page()

            # Scrape keyword listings
            for keyword in KEYWORDS:
                print(f"\n--- Searching for keyword: '{keyword}' ---")
                encoded_term = urllib.parse.quote(keyword)
                search_url = f"https://bidplus.gem.gov.in/all-bids?bid_number=&items_per_page=&search_under=&search={encoded_term}"
                
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    
                    has_cards = True
                    try:
                        page.wait_for_selector("div.card, #bidCard", timeout=10000)
                    except Exception:
                        print(f"No bid cards displayed for '{keyword}' on page 1.")
                        has_cards = False
                    
                    if not has_cards:
                        continue

                    # Set Sorting Order
                    select_sort_order(page, sort_order)

                    # Page 1 parsing
                    tenders = parse_cards(page.content(), keyword)
                    print(f"Page 1: Found {len(tenders)} tenders")
                    for t in tenders:
                        # Log date policy checks but do not skip discovery
                        date_ok, reasons = check_date_policy(t.get("start_date"), t.get("end_date"))
                        if not date_ok:
                            print(f"  [Date Policy Alert] {t['bid_no']}: {', '.join(reasons)}")

                        if t["bid_no"] not in all_tenders:
                            all_tenders[t["bid_no"]] = t
                            new_tenders_count += 1
                            print(f"  [New Tender Discovered] {t['bid_no']}")
                        else:
                            existing = all_tenders[t["bid_no"]]
                            if keyword not in existing["keyword"]:
                                existing["keyword"] += f", {keyword}"

                    # Paginate pages up to max_pages
                    for page_num in range(2, max_pages + 1):
                        next_selector = f'a[href="#page-{page_num}"].page-link'
                        next_btn = page.query_selector(next_selector)
                        if not next_btn:
                            break
                        
                        print(f"Navigating to page {page_num}...")
                        next_btn.click()
                        page.wait_for_timeout(2500)
                        
                        page_tenders = parse_cards(page.content(), keyword)
                        print(f"Page {page_num}: Found {len(page_tenders)} tenders")
                        if not page_tenders:
                            break
                            
                        for t in page_tenders:
                            # Log date policy checks but do not skip discovery
                            date_ok, reasons = check_date_policy(t.get("start_date"), t.get("end_date"))
                            if not date_ok:
                                print(f"  [Date Policy Alert] {t['bid_no']}: {', '.join(reasons)}")

                            if t["bid_no"] not in all_tenders:
                                all_tenders[t["bid_no"]] = t
                                new_tenders_count += 1
                                print(f"  [New Tender Discovered] {t['bid_no']}")
                            else:
                                existing = all_tenders[t["bid_no"]]
                                if keyword not in existing["keyword"]:
                                    existing["keyword"] += f", {keyword}"

                except Exception as e:
                    print(f"Error searching for '{keyword}': {e}")
                
                time.sleep(random.uniform(2.0, 4.0))

            if new_tenders_count == 0:
                print(f"\nFor today ({get_date_folder_name()}), no new tenders could be found.")

            # Download RFP documents
            print(f"\n--- Checking RFP Downloads for {len(all_tenders)} total tenders ---")
            tenders_list = list(all_tenders.values())
            # Load scoring config + company profile once per scrape run
            scoring_cfg = load_scoring_config()
            company_profile = load_company_profile()
            
            for idx, tender in enumerate(tenders_list):
                bid_no = tender["bid_no"]
                
                # 1. Skip already successfully processed tenders (having local PDF and analysis)
                if tender.get("downloaded") and tender.get("analysis") and tender.get("local_pdf_path"):
                    if os.path.exists(tender["local_pdf_path"]):
                        continue
                        
                pdf_url = tender["pdf_url"]
                keyword = tender["keyword"].split(",")[0].strip()
                
                sanitized_bid = sanitize_filename(bid_no)
                sanitized_keyword = sanitize_folder_name(keyword)
                date_folder = get_date_folder_name()
                
                # 2. Try to download RFP PDF first
                existing_path = find_existing_pdf_file(sanitized_bid)
                
                target_dir = os.path.join(DOWNLOADS_DIR, sanitized_keyword, date_folder, sanitized_bid)
                save_path = os.path.join(target_dir, f"{sanitized_bid}.pdf")
                
                pdf_location = None
                if existing_path:
                    tender["downloaded"] = True
                    tender["local_pdf_path"] = existing_path
                    pdf_location = existing_path
                else:
                    os.makedirs(target_dir, exist_ok=True)
                    print(f"[{idx+1}/{len(tenders_list)}] Downloading RFP for Bid: {bid_no}...")
                    success = download_rfp_pdf(context, pdf_url, save_path)
                    if success:
                        tender["downloaded"] = True
                        tender["local_pdf_path"] = save_path.replace("\\", "/")
                        pdf_location = save_path
                    else:
                        tender["downloaded"] = False

                # 3. Date window evaluation (graduated; hard-reject only when expired)
                date_info = evaluate_date_window(
                    tender.get("start_date"), tender.get("end_date"), scoring_cfg
                )

                # 4. Scan and analyze RFP PDF if it is downloaded
                if tender["downloaded"] and pdf_location and os.path.exists(pdf_location):
                    analysis = analyze_rfp_pdf(
                        pdf_location,
                        start_date_str=tender.get("start_date"),
                        end_date_str=tender.get("end_date"),
                        scoring_config=scoring_cfg,
                        company_profile=company_profile,
                        card_meta={
                            "title": tender.get("title"),
                            "department": tender.get("department"),
                            "quantity": tender.get("quantity"),
                            "keyword": tender.get("keyword"),
                        }
                    )
                    if analysis:
                        if analysis.get("is_expired") or date_info.get("is_expired"):
                            analysis["score"] = 0
                            analysis["recommendation"] = "Drop"
                            if "Auto-Rejected: bid expired" not in analysis.get("reasons", []):
                                analysis.setdefault("reasons", []).insert(0, "Auto-Rejected: bid expired")
                            if tender.get("status") != "Shortlisted":
                                tender["status"] = "Rejected"
                        elif analysis.get("analysis_status") == "failed" or analysis.get("score") is None:
                            # Never auto Shortlist/Reject on analysis failure
                            if tender.get("status") not in ["Shortlisted", "Rejected"]:
                                tender["status"] = "Pending Review"
                        else:
                            tender["status"] = status_from_score(
                                analysis.get("score"), scoring_cfg, tender.get("status")
                            )
                        tender["analysis"] = analysis
                else:
                    # PDF not downloaded / not available → analysis failed (BE-04)
                    analysis = get_failed_analysis("RFP PDF document is not available for analysis.")
                    if date_info.get("is_expired"):
                        analysis["score"] = 0
                        analysis["reasons"].insert(0, "Auto-Rejected: bid expired")
                        for r in date_info.get("reasons", []):
                            if r not in analysis["reasons"]:
                                analysis["reasons"].append(r)
                        if tender.get("status") != "Shortlisted":
                            tender["status"] = "Rejected"
                    else:
                        if tender.get("status") not in ["Shortlisted", "Rejected"]:
                            tender["status"] = "Pending Review"
                    tender["analysis"] = analysis

                time.sleep(random.uniform(1.5, 3.0))

            browser.close()

        save_metadata(tenders_list)
        return tenders_list, new_tenders_count
    finally:
        sys.stdout = original_stdout

def scrape_single_bid(bid_id, log_callback=None):
    class LogStream:
        def __init__(self, callback):
            self.callback = callback
            self.buffer = ""
            self.is_writing = False
        def write(self, buf):
            sys.__stdout__.write(buf)
            if self.is_writing:
                return
            self.is_writing = True
            try:
                self.buffer += buf
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    self.callback(line)
            finally:
                self.is_writing = False
        def flush(self):
            sys.__stdout__.flush()
            
    original_stdout = sys.stdout
    if log_callback:
        sys.stdout = LogStream(log_callback)

    try:
        print("Initializing directories for manual ID acquisition...")
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        
        bid_id_clean = bid_id.strip()
        print(f"Targeting Bid ID / Number: '{bid_id_clean}'")
        
        all_tenders = load_existing_metadata()
        
        # Build search url by querying GeM with general search parameter
        encoded_term = urllib.parse.quote(bid_id_clean)
        search_url = f"https://bidplus.gem.gov.in/all-bids?bid_number=&items_per_page=&search_under=&search={encoded_term}"
        
        target_tender = None
        
        with sync_playwright() as p:
            print("Launching browser with stealth settings...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                accept_downloads=True
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = context.new_page()
            
            print("Navigating to base search page...")
            page.goto("https://bidplus.gem.gov.in/all-bids", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            
            # Fill the search input and click search button
            print(f"Typing search query '{bid_id_clean}' in search box...")
            page.fill("#searchBid", bid_id_clean)
            page.wait_for_timeout(500)
            
            print("Clicking search button...")
            page.click("#searchBidRA")
            page.wait_for_timeout(3000) # Wait for AJAX refresh
            
            # Wait for card
            try:
                page.wait_for_selector("div.card, #bidCard", timeout=12000)
                tenders = parse_cards(page.content(), "MANUAL_REQUEST")
                
                # Try to find matching card (partial or exact)
                for t in tenders:
                    if bid_id_clean.lower() in t["bid_no"].lower() or t["bid_no"].lower() in bid_id_clean.lower():
                        target_tender = t
                        break
                
                # Do not default to a random card if no match is found
                pass
                    
            except Exception as e:
                print(f"Failed to find or parse bid cards for ID '{bid_id_clean}': {e}")
                
            if not target_tender:
                print(f"No tender found on GeM matching ID: '{bid_id_clean}'")
                browser.close()
                return None
                
            bid_no = target_tender["bid_no"]
            pdf_url = target_tender["pdf_url"]
            print(f"Tender found: {bid_no} - {target_tender['title']}")
            
            # Since this is a manual request, we BYPASS the Date Policy Gate check
            print("Manual acquisition request: Bypassing Date Policy Gate check.")
            
            sanitized_bid = sanitize_filename(bid_no)
            sanitized_keyword = "manual_downloads"
            date_folder = get_date_folder_name()
            
            target_dir = os.path.join(DOWNLOADS_DIR, sanitized_keyword, date_folder, sanitized_bid)
            save_path = os.path.join(target_dir, f"{sanitized_bid}.pdf")
            
            existing_path = find_existing_pdf_file(sanitized_bid)
            pdf_location = None
            
            if existing_path:
                print(f"RFP PDF already exists in local downloads cache: {existing_path}")
                target_tender["downloaded"] = True
                target_tender["local_pdf_path"] = existing_path
                pdf_location = existing_path
            else:
                os.makedirs(target_dir, exist_ok=True)
                print(f"Downloading RFP PDF from: {pdf_url}...")
                success = download_rfp_pdf(context, pdf_url, save_path)
                if success:
                    target_tender["downloaded"] = True
                    target_tender["local_pdf_path"] = save_path.replace("\\", "/")
                    pdf_location = save_path
                else:
                    target_tender["downloaded"] = False
                    print("Download failed for RFP PDF.")
                    
            # Scan and analyze RFP PDF (manual path: still scores date_window from dates)
            scoring_cfg = load_scoring_config()
            company_profile = load_company_profile()
            if target_tender["downloaded"] and pdf_location and os.path.exists(pdf_location):
                print("Scanning and scoring RFP PDF contents...")
                analysis = analyze_rfp_pdf(
                    pdf_location,
                    start_date_str=target_tender.get("start_date"),
                    end_date_str=target_tender.get("end_date"),
                    scoring_config=scoring_cfg,
                    company_profile=company_profile,
                    card_meta={
                        "title": target_tender.get("title"),
                        "department": target_tender.get("department"),
                        "quantity": target_tender.get("quantity"),
                        "keyword": target_tender.get("keyword"),
                    }
                )
                if analysis:
                    target_tender["analysis"] = analysis
                    if analysis.get("analysis_status") == "failed" or analysis.get("score") is None:
                        target_tender["status"] = "Pending Review"
                    elif analysis.get("is_expired"):
                        analysis["score"] = 0
                        analysis["recommendation"] = "Drop"
                        if "Auto-Rejected: bid expired" not in analysis.get("reasons", []):
                            analysis.setdefault("reasons", []).insert(0, "Auto-Rejected: bid expired")
                        target_tender["status"] = "Rejected"
                    else:
                        target_tender["status"] = status_from_score(
                            analysis.get("score"), scoring_cfg, None
                        )
            else:
                target_tender["status"] = "Pending Review"
                target_tender["analysis"] = get_failed_analysis(
                    "RFP PDF document is not available for analysis."
                )
                
            # If the user previously searched for it, update the keyword or preserve it
            if bid_no in all_tenders:
                existing = all_tenders[bid_no]
                kw_list = [k.strip() for k in existing["keyword"].split(",")]
                if "MANUAL_REQUEST" not in kw_list:
                    kw_list.append("MANUAL_REQUEST")
                target_tender["keyword"] = ", ".join(kw_list)
            else:
                target_tender["keyword"] = "MANUAL_REQUEST"
                
            # Save or update in database
            all_tenders[bid_no] = target_tender
            save_metadata(list(all_tenders.values()))
            print(f"Successfully processed and updated metadata for Bid: {bid_no}")
            
            browser.close()
            return target_tender
            
    finally:
        sys.stdout = original_stdout

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GeM RFP Acquisition CLI Scraper")
    parser.add_argument("--keywords", nargs="+", help="Keywords list to search")
    parser.add_argument("--pages", type=int, default=2, help="Max pages limit per keyword")
    parser.add_argument("--sort", default="Bid-End-Date-Latest", 
                        choices=["Bid-End-Date-Latest", "Bid-End-Date-Oldest", "Bid-Start-Date-Latest", "Bid-Start-Date-Oldest"], 
                        help="Sort order option")
    
    args = parser.parse_args()
    scrape(selected_keywords=args.keywords, max_pages=args.pages, sort_order=args.sort)

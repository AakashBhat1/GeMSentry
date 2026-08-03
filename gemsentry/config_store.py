"""Keyword list and scoring-config load/validate/save."""

import copy
import json
import os
import paths
import tempfile

from gemsentry.constants import KEYWORDS_PATH, SCORING_CONFIG_PATH, logger
from gemsentry.defaults import DEFAULT_SCORING_CONFIG


def _resolve_config_path(primary: str, legacy: str, label: str) -> str | None:
    """Prefer new path; fall back to legacy root with a one-time warning."""
    if os.path.exists(primary):
        return primary
    if os.path.exists(legacy):
        logger.warning("legacy path used: %s; please use %s", legacy, primary)
        return legacy
    return None


def load_keywords():
    keywords = []
    csv_path = _resolve_config_path(
        KEYWORDS_PATH, paths.LEGACY_KEYWORDS_PATH, "keywords.csv"
    )
    if csv_path:
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if clean.startswith('\ufeff'):
                        clean = clean.replace('\ufeff', '')
                    if clean and not clean.lower().startswith("keyword") and clean not in keywords:
                        keywords.append(clean)
        except Exception as e:
            logger.error("Error reading keywords.csv: %s", e)

    cleaned_keywords = []
    for kw in keywords:
        kw_clean = kw.strip()
        if kw_clean and kw_clean.lower() not in [k.lower() for k in cleaned_keywords]:
            cleaned_keywords.append(kw_clean)

    if not cleaned_keywords:
        cleaned_keywords = ["artificial intelligence", "indigenous", "power supply"]

    logger.info("Loaded %d unique keywords from keywords.csv", len(cleaned_keywords))
    return cleaned_keywords


def load_scoring_config():
    """Load scoring_config.json; on missing/corrupt file log and return defaults."""
    defaults = copy.deepcopy(DEFAULT_SCORING_CONFIG)
    cfg_path = _resolve_config_path(
        SCORING_CONFIG_PATH, paths.LEGACY_SCORING_CONFIG_PATH, "scoring_config.json"
    )
    if not cfg_path:
        logger.warning("%s not found; using default scoring config.", SCORING_CONFIG_PATH)
        return defaults
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
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
        logger.warning("failed to load %s (%s); using default scoring config.", cfg_path, e)
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

    try:
        floor = float(payload.get("no_relaxation_floor", 0.5))
    except (TypeError, ValueError):
        return "no_relaxation_floor must be numeric."
    if not (0.0 <= floor <= 1.0):
        return "no_relaxation_floor must be in [0, 1]."

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
        for opt_key in ("fit_min", "review_band", "unknown_buyer_subscore",
                        "turnover_gap_subscore", "weak_relevance_subscore",
                        "omnibus_min_items", "omnibus_min_match_ratio",
                        "lone_acronym_max_len"):
            if opt_key in fit and fit[opt_key] is not None:
                try:
                    v = float(fit[opt_key])
                except (TypeError, ValueError):
                    return f"fit.{opt_key} must be numeric."
                if opt_key == "fit_min":
                    if not (0 <= v <= 100):
                        return "fit.fit_min must be in [0, 100]."
                elif opt_key == "review_band":
                    if not (0 <= v <= 30):
                        return "fit.review_band must be in [0, 30]."
                elif opt_key == "omnibus_min_items":
                    if not (2 <= v <= 100):
                        return "fit.omnibus_min_items must be in [2, 100]."
                elif opt_key == "lone_acronym_max_len":
                    if not (0 <= v <= 10):
                        return "fit.lone_acronym_max_len must be in [0, 10]."
                elif not (0.0 <= v <= 1.0):
                    return f"fit.{opt_key} must be in [0, 1]."

    # Optional download policy (BE-27 fast pipeline)
    if "download_policy" in payload and payload["download_policy"] is not None:
        dp = payload["download_policy"]
        if not isinstance(dp, dict):
            return "download_policy must be an object."
        if "download_workers" in dp and dp["download_workers"] is not None:
            try:
                w = int(dp["download_workers"])
            except (TypeError, ValueError):
                return "download_policy.download_workers must be an integer."
            if not (1 <= w <= 10):
                return "download_policy.download_workers must be in [1, 10]."
        if "analysis_workers" in dp and dp["analysis_workers"] is not None:
            try:
                a = int(dp["analysis_workers"])
            except (TypeError, ValueError):
                return "download_policy.analysis_workers must be an integer."
            # 0 means "pick a sensible number from the CPU count".
            if not (0 <= a <= 16):
                return "download_policy.analysis_workers must be in [0, 16]."

    return None


def save_scoring_config(payload):
    """Atomically write scoring_config.json (temp file then replace)."""
    paths.ensure_dirs()
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

"""Company profile load/validate/save and preset workspaces."""

import copy
import json
import os
import paths
import tempfile

from gemsentry.config_store import _resolve_config_path
from gemsentry.constants import COMPANY_PROFILE_PATH, TENDERS_DIR, logger
from gemsentry.defaults import DEFAULT_COMPANY_PROFILE


def load_company_profile():
    """Load company_profile.json; missing/corrupt → defaults + warning (BE-07)."""
    defaults = copy.deepcopy(DEFAULT_COMPANY_PROFILE)
    cfg_path = _resolve_config_path(
        COMPANY_PROFILE_PATH, paths.LEGACY_COMPANY_PROFILE_PATH, "company_profile.json"
    )
    if not cfg_path:
        logger.warning("%s not found; using default company profile.", COMPANY_PROFILE_PATH)
        return defaults
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
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
        if "value_presets" in cfg and isinstance(cfg["value_presets"], dict):
            merged["value_presets"] = cfg["value_presets"]
        return _apply_active_preset(merged)
    except Exception as e:
        logger.warning("failed to load %s (%s); using default company profile.", cfg_path, e)
        return defaults


def _apply_active_preset(profile):
    """Overlay the active value-preset's band onto value_preference so all
    downstream scoring reads the switched band with no other code changes.
    Unknown/absent active_preset falls back to whatever value_preference holds."""
    presets = profile.get("value_presets") or {}
    active = profile.get("active_preset")
    preset = presets.get(active) if active else None
    if isinstance(preset, dict):
        vp = dict(profile.get("value_preference") or {})
        if preset.get("sweet_min_inr") is not None:
            vp["sweet_min_inr"] = preset["sweet_min_inr"]
        if preset.get("sweet_max_inr") is not None:
            vp["sweet_max_inr"] = preset["sweet_max_inr"]
        profile["value_preference"] = vp
    return profile


def workspace_label(tenders_dir):
    """Human label for a workspace dir: tenders/ → 'main', tenders/personel → 'personel'."""
    rel = os.path.relpath(tenders_dir, TENDERS_DIR)
    return "main" if rel in (".", "") else rel.replace(os.sep, "_")


def profile_for_workspace(workspace):
    """
    Company profile with the value-band preset MATCHING a workspace applied
    ('main'/'' → the preset with empty workspace, 'personel' → its preset).
    Prevents the globally active preset's sweet band from mis-scoring
    value_fit when rescoring a different workspace. Falls back to the active
    preset when no preset maps to the workspace.
    """
    profile = load_company_profile()
    ws = "" if workspace in (None, "", "main") else str(workspace).strip().strip("/\\")
    for pid, preset in (profile.get("value_presets") or {}).items():
        if isinstance(preset, dict) and (preset.get("workspace") or "").strip().strip("/\\") == ws:
            profile["active_preset"] = pid
            return _apply_active_preset(profile)
    return profile


def get_active_workspace(profile=None):
    """Folder name for the active preset's isolated workspace.
    '' → default root (tenders/). Non-empty (e.g. 'personel') → tenders/<name>/."""
    profile = profile if profile is not None else load_company_profile()
    presets = profile.get("value_presets") or {}
    preset = presets.get(profile.get("active_preset")) or {}
    return (preset.get("workspace") or "").strip().strip("/\\")


def workspace_paths(workspace=None):
    """Return (tenders_dir, downloads_dir) for a workspace name.
    None → resolve the active preset's workspace; '' → default root."""
    if workspace is None:
        workspace = get_active_workspace()
    tdir = os.path.join(TENDERS_DIR, workspace) if workspace else TENDERS_DIR
    return tdir, os.path.join(tdir, "downloads")


def validate_company_profile(payload):
    """
    Return error message if invalid, else None (BE-13 + BE-18).
    Rejects destructive partial profiles that omit required top-level keys
    or business_line labels (the corruption vector fixed in BE-18).
    """
    if not isinstance(payload, dict):
        return "Profile payload must be a JSON object."

    # BE-18: require top-level keys the Fit engine relies on
    required_top = (
        "business_lines",
        "eligibility",
        "serviceability",
        "buyer_affinity",
        "value_preference",
    )
    for key in required_top:
        if key not in payload:
            return f"Missing required top-level key: {key}"

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
        label = line.get("label")
        if not isinstance(label, str) or not label.strip():
            return f"business_lines[{i}].label is required (non-empty string)."
        kws = line.get("keywords")
        if not isinstance(kws, list) or len(kws) == 0:
            return f"business_lines[{i}].keywords must be a non-empty list."
        for opt_list in ("strong_keywords", "exclude_keywords"):
            if opt_list in line and line[opt_list] is not None:
                if not isinstance(line[opt_list], list):
                    return f"business_lines[{i}].{opt_list} must be a list."

    svc = payload.get("serviceability")
    if not isinstance(svc, dict):
        return "serviceability must be an object."
    if "soft_avoid_penalty" in svc and svc["soft_avoid_penalty"] is not None:
        try:
            p = float(svc["soft_avoid_penalty"])
        except (TypeError, ValueError):
            return "serviceability.soft_avoid_penalty must be numeric."
        if not (0.0 <= p <= 1.0):
            return "serviceability.soft_avoid_penalty must be in [0, 1]."

    affinity = payload.get("buyer_affinity")
    if not isinstance(affinity, dict):
        return "buyer_affinity must be an object."
    if len(affinity) == 0:
        return "buyer_affinity must not be empty."
    for k, v in affinity.items():
        try:
            av = float(v)
        except (TypeError, ValueError):
            return f"buyer_affinity.{k} must be numeric."
        if not (0.0 <= av <= 1.0):
            return f"buyer_affinity.{k} must be in [0, 1]."

    vp = payload.get("value_preference")
    if not isinstance(vp, dict):
        return "value_preference must be an object."
    for nk in ("sweet_min_inr", "sweet_max_inr"):
        if nk not in vp:
            return f"value_preference.{nk} is required."
        try:
            float(vp[nk])
        except (TypeError, ValueError):
            return f"value_preference.{nk} must be numeric."

    return None


def save_company_profile(payload):
    """Atomically write company_profile.json. Rejects invalid payloads (BE-18)."""
    err = validate_company_profile(payload)
    if err:
        raise ValueError(f"Invalid company profile: {err}")
    paths.ensure_dirs()
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

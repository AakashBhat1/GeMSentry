"""Scoring config, company profile, presets, sources, logs and PDF serving."""

import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory

import logging_setup
import paths
import scraper
from gemsentry.web.context import fail, source_registry

logger = logging.getLogger("gemsentry")


settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/api/scoring-config", methods=["GET"])
def get_scoring_config():
    """Return current scoring config (defaults if file absent)."""
    try:
        cfg = scraper.load_scoring_config()
        return jsonify(cfg)
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/scoring-config", methods=["POST"])
def update_scoring_config():
    """Validate and atomically persist scoring_config.json."""
    try:
        data = request.json
        if data is None:
            return jsonify({"error": "JSON body required."}), 400

        err = scraper.validate_scoring_config(data)
        if err:
            return jsonify({"error": err}), 400

        scraper.save_scoring_config(data)
        return jsonify({
            "message": "Scoring config updated successfully. Applies on next scrape.",
            "config": data,
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/company-profile", methods=["GET"])
def get_company_profile():
    """Return current company profile (defaults if file absent)."""
    try:
        profile = scraper.load_company_profile()
        return jsonify(profile)
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/company-profile", methods=["POST"])
def update_company_profile():
    """Validate and atomically persist company_profile.json."""
    try:
        data = request.json
        if data is None:
            return jsonify({"error": "JSON body required."}), 400

        err = scraper.validate_company_profile(data)
        if err:
            return jsonify({"error": err}), 400

        scraper.save_company_profile(data)
        return jsonify({
            "message": "Company profile updated successfully. Applies on next scrape.",
            "profile": data,
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/presets", methods=["GET"])
def get_presets():
    """List value-band presets and which one is active."""
    try:
        profile = scraper.load_company_profile()
        return jsonify({
            "active_preset": profile.get("active_preset"),
            "value_presets": profile.get("value_presets") or {},
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/preset", methods=["POST"])
def set_active_preset():
    """Switch the active value-band preset and persist it. Returns the preset's
    suggested keywords so the dashboard can auto-select them for the next scrape."""
    try:
        data = request.json or {}
        preset_id = data.get("id")
        profile = scraper.load_company_profile()
        presets = profile.get("value_presets") or {}
        if preset_id not in presets:
            return jsonify({"error": f"Unknown preset: {preset_id}"}), 400

        preset = presets[preset_id]
        profile["active_preset"] = preset_id
        vp = dict(profile.get("value_preference") or {})
        if preset.get("sweet_min_inr") is not None:
            vp["sweet_min_inr"] = preset["sweet_min_inr"]
        if preset.get("sweet_max_inr") is not None:
            vp["sweet_max_inr"] = preset["sweet_max_inr"]
        profile["value_preference"] = vp

        err = scraper.validate_company_profile(profile)
        if err:
            return jsonify({"error": err}), 400
        scraper.save_company_profile(profile)
        return jsonify({
            "message": f"Active preset set to {preset.get('label', preset_id)}. Applies on next scrape/re-analysis.",
            "active_preset": preset_id,
            "keywords": preset.get("keywords", []),
            "value_preference": vp,
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/logs", methods=["GET"])
def get_logs():
    """List app log + recent scrape sessions and return a tail (BE-24)."""
    try:
        sessions = logging_setup.list_session_logs(limit=20)
        latest = sessions[0] if sessions else None
        # Prefer latest session tail; else app log
        if latest:
            abs_latest = os.path.join(paths.SCRAPE_LOGS_DIR, latest["name"])
            safe = logging_setup.safe_logs_path(abs_latest)
            tail = logging_setup.tail_file(safe, lines=100) if safe else []
        else:
            safe = logging_setup.safe_logs_path(paths.APP_LOG_PATH)
            tail = logging_setup.tail_file(safe, lines=100) if safe else []

        return jsonify({
            "app_log": paths.repo_relative(paths.APP_LOG_PATH),
            "sessions": sessions,
            "latest_session": latest,
            "tail": tail,
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/api/sources", methods=["GET", "POST"])
def manage_sources():
    """GET configured multi-source portals or POST toggle enabled status."""
    try:
        if request.method == "POST":
            data = request.json or {}
            source_id = data.get("id")
            enabled = data.get("enabled", True)
            if not source_id:
                return jsonify({"error": "Missing source id"}), 400
            success = source_registry.toggle_source(source_id, enabled)
            if not success:
                return jsonify({"error": f"Unknown source id '{source_id}'"}), 404
            return jsonify({"success": True, "id": source_id, "enabled": bool(enabled)})

        sources = source_registry.get_all_sources()
        return jsonify({
            "sources": sources,
            "total": len(sources),
            "runnable": len(source_registry.runnable_adapters()),
        })
    except Exception as e:
        return fail(e)


@settings_bp.route("/tenders/<path:filename>")
def serve_pdf(filename):
    # Serve PDF files securely from the tenders root so per-workspace
    # subfolders (e.g. tenders/personel/downloads/...) resolve too.
    safe_path = filename.replace("\\", "/")
    return send_from_directory(paths.TENDERS_DIR, safe_path)

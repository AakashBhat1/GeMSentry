import os
import threading
import datetime
import webbrowser
import logging
from flask import Flask, jsonify, request, send_from_directory, send_file

import paths
import logging_setup

# Ensure dirs + logging before scraper import side-effects matter
paths.ensure_dirs()
logging_setup.setup_logging()

import scraper  # noqa: E402
from gemsentry.search import expand_keywords  # noqa: E402
from gemsentry.sources import SourceRegistry, annotate_sources  # noqa: E402
from gemsentry.live_excel import live_excel_manager  # noqa: E402

logger = logging.getLogger("gemsentry")

# One registry for the process; config/sources.json is re-read on demand.
source_registry = SourceRegistry()

app = Flask(__name__, static_folder=".", static_url_path="")

# Threading and status control
status_lock = threading.Lock()
scrape_status = {
    "status": "idle",
    "current_keyword": "",
    "new_count": 0,
    "start_time": None,
    "log_session_path": None,
}

# Bounded live buffer (also mirrored via logging_setup.log_buffer)
LOG_BUFFER_MAX = logging_setup.LOG_BUFFER_MAX
scrape_logs = logging_setup.log_buffer  # deque, maxlen=500

ALLOWED_SCRAPE_SORTS = {
    "Bid-End-Date-Latest",
    "Bid-End-Date-Oldest",
    "Bid-Start-Date-Latest",
    "Bid-Start-Date-Oldest",
}


def _number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if integer and not parsed.is_integer():
        raise ValueError(f"{name} must be a whole number")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(parsed) if integer else parsed


def normalize_scrape_payload(data):
    """Validate and normalize the background scrape request."""
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")

    raw_keywords = data.get("keywords")
    if not isinstance(raw_keywords, list):
        raise ValueError("keywords must be a non-empty list")
    keywords = []
    seen = set()
    for raw in raw_keywords:
        clean = " ".join(str(raw or "").split()).strip()
        key = clean.casefold()
        if clean and key not in seen:
            keywords.append(clean)
            seen.add(key)
    if not keywords:
        raise ValueError("keywords must contain at least one non-empty value")
    if len(keywords) > 250:
        raise ValueError("keywords may contain at most 250 values")

    sort_order = data.get("sort_order", "Bid-Start-Date-Latest")
    if sort_order not in ALLOWED_SCRAPE_SORTS:
        raise ValueError("sort_order is not supported")

    cfg = scraper.load_scoring_config()
    default_min_days = float(
        (cfg.get("date_window") or {}).get("min_days_to_bid", 5)
    )
    min_days_left = _number(
        data.get("min_days_left", default_min_days),
        "min_days_left", 0, 365,
    )
    raw_max_days = data.get("max_days_left")
    max_days_left = (
        None if raw_max_days is None
        else _number(raw_max_days, "max_days_left", 0, 365)
    )
    if max_days_left is not None and max_days_left < min_days_left:
        raise ValueError("max_days_left must be greater than or equal to min_days_left")

    raw_target = data.get("target_count")
    target_count = (
        None if raw_target is None
        else _number(raw_target, "target_count", 1, 1000, integer=True)
    )
    return {
        "keywords": keywords,
        "max_pages": _number(data.get("max_pages", 2), "max_pages", 1, 30, integer=True),
        "sort_order": sort_order,
        "target_count": target_count,
        "min_days_left": min_days_left,
        "max_days_left": max_days_left,
    }


def add_log(message):
    """Log to gemsentry logger (console + file + bounded buffer + session)."""
    # Strip leading timestamps if callers already formatted; logger formats again
    logger.info("%s", message)


def run_scraper_thread(keywords, max_pages, sort_order, target_count=None, min_days_left=None, max_days_left=None):
    global scrape_status
    try:
        if target_count:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) with target goal: {target_count} tenders per keyword in [{min_days_left}-{max_days_left}] days window...")
        elif min_days_left is not None:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) sorted by '{sort_order}' (filtering tenders closing in < {min_days_left} days)...")
        else:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) sorted by '{sort_order}'...")

        # Scraper logs via logger; BufferHandler fills live buffer.
        # Optional callback reserved for future UI hooks (no re-log).
        def scraper_log_callback(msg):
            pass

        tenders_list, new_count = scraper.scrape(
            selected_keywords=keywords,
            max_pages=max_pages,
            sort_order=sort_order,
            log_callback=scraper_log_callback,
            target_count=target_count,
            min_days_left=min_days_left,
            max_days_left=max_days_left,
        )

        # Fan out to the non-GeM portals (DefProc, BEL, CPPP, NTPC, states...).
        try:
            source_registry.reload_sources()
            runnable = source_registry.runnable_adapters()
            if runnable:
                add_log(f"Querying {len(runnable)} external portal(s) in parallel...")
                external_keywords = expand_keywords(keywords)
                extra_tenders = source_registry.fetch_from_all_active(
                    external_keywords, max_pages=max_pages
                )
                if extra_tenders:
                    add_log(f"Multi-source portals returned {len(extra_tenders)} unique tenders.")
                    new_count += scraper.ingest_external_tenders(extra_tenders)
        except Exception as ms_err:
            add_log(f"Multi-source fetch error: {ms_err}")

        with status_lock:
            # Capture session path before scraper's finally clears handler
            # (scrape ends session in finally after return — path still set)
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)

        add_log(f"Scraping completed. Discovered {new_count} total new tenders across all portals.")
        try:
            live_excel_manager.on_scrape_completed()
            add_log("Live Excel session started after scrape (10-minute curation window open).")
        except Exception as le_err:
            logger.warning("Could not initiate live excel session after scrape: %s", le_err)

        with status_lock:
            scrape_status["status"] = "idle"
            scrape_status["new_count"] = new_count
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)
    except Exception as e:
        add_log(f"Scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"


def run_scraper_id_thread(bid_id):
    global scrape_status
    try:
        add_log(f"Starting background single bid acquisition for ID: '{bid_id}'...")

        def scraper_log_callback(msg):
            pass

        tender = scraper.scrape_single_bid(
            bid_id=bid_id,
            log_callback=scraper_log_callback,
        )

        if tender:
            add_log(f"Acquisition completed. Tender {tender['bid_no']} successfully imported.")
            with status_lock:
                scrape_status["new_count"] = 1
            try:
                live_excel_manager.on_scrape_completed()
            except Exception:
                pass
        else:
            add_log(f"Acquisition failed. No tender was imported for ID: '{bid_id}'.")
            with status_lock:
                scrape_status["new_count"] = 0

        with status_lock:
            scrape_status["status"] = "idle"
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)
    except Exception as e:
        add_log(f"Single bid scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"


@app.route("/")
def serve_dashboard():
    return send_from_directory(paths.ROOT, "dashboard.html")


@app.route("/api/keywords", methods=["GET"])
def get_keywords():
    try:
        keywords = scraper.load_keywords()
        return jsonify({"keywords": keywords})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tenders", methods=["GET"])
def get_tenders():
    try:
        tenders_dict = scraper.load_existing_metadata()
        # Legacy records predate source_id; derive it so the dashboard's portal
        # filter covers the full history, not just post-refactor scrapes.
        tenders = annotate_sources(tenders_dict.values(), source_registry.get_all_sources())
        return jsonify({"tenders": tenders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status", methods=["GET"])
def get_status():
    with status_lock:
        logs = list(logging_setup.log_buffer)
        sess = logging_setup.get_session_path()
        session_rel = (
            paths.repo_relative(sess)
            if sess
            else scrape_status.get("log_session_path")
        )
        return jsonify({
            "status": scrape_status["status"],
            "current_keyword": scrape_status["current_keyword"],
            "new_count": scrape_status["new_count"],
            "logs": logs,
            "log_session_path": session_rel,
            "log_count": len(logs),
        })


@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    global scrape_status
    try:
        params = normalize_scrape_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400

        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        scrape_status["log_session_path"] = None
        logging_setup.clear_log_buffer()

    thread = threading.Thread(
        target=run_scraper_thread,
        args=(
            params["keywords"], params["max_pages"], params["sort_order"],
            params["target_count"], params["min_days_left"], params["max_days_left"],
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Scraper started successfully."})


@app.route("/api/scrape/id", methods=["POST"])
def trigger_scrape_id():
    global scrape_status
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400

        data = request.json or {}
        bid_id = data.get("bid_id")

        if not bid_id:
            return jsonify({"error": "No Bid ID provided."}), 400

        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        scrape_status["log_session_path"] = None
        logging_setup.clear_log_buffer()

    thread = threading.Thread(
        target=run_scraper_id_thread,
        args=(bid_id,),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Single bid scraper started successfully."})


@app.route("/api/tenders/status", methods=["POST"])
def update_tender_status():
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        new_status = data.get("status")

        if not bid_no or not new_status:
            return jsonify({"error": "Missing bid_no or status in request."}), 400

        if new_status not in ["Shortlisted", "Rejected", "Pending Review"]:
            return jsonify({"error": "Invalid status value."}), 400

        tenders_dict = scraper.load_existing_metadata()
        if bid_no in tenders_dict:
            tenders_dict[bid_no]["status"] = new_status
            # User-set statuses are pinned: rescore/scrape never overwrite them.
            tenders_dict[bid_no]["status_source"] = "manual"
            scraper.save_metadata(list(tenders_dict.values()))
            scraper.auto_export_summary(*scraper.workspace_paths())
            return jsonify({"message": f"Status for bid {bid_no} updated to {new_status} (pinned)."})
        else:
            return jsonify({"error": f"Bid {bid_no} not found in database."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rescore", methods=["POST"])
def trigger_rescore():
    """Re-score all tenders in the active workspace from local data (no network).

    Re-parses local PDFs with current config/profile; falls back to
    card-metadata scoring when a PDF is missing. Makes config changes
    take effect immediately instead of 'on next scrape'."""
    global scrape_status
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400
        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        logging_setup.clear_log_buffer()

    data = request.json or {}
    # Default fast mode: re-derive from stored signals (~instant).
    # Pass {"reparse": true} to fully re-parse local PDFs.
    reparse = bool(data.get("reparse", False))

    def run_rescore():
        global scrape_status
        try:
            add_log("Starting local rescore of active workspace...")
            summary = scraper.rescore_metadata(reparse=reparse)
            add_log(
                f"Rescore complete: {summary['total']} tenders. "
                f"Status: {summary['status_counts']}. "
                f"Recommendations: {summary['recommendation_counts']}."
            )
        except Exception as e:
            add_log(f"Rescore failed: {e}")
        finally:
            with status_lock:
                scrape_status["status"] = "idle"

    threading.Thread(target=run_rescore, daemon=True).start()
    return jsonify({"message": "Rescore started (local, no network)."})


@app.route("/api/clear-workspace", methods=["POST"])
def clear_workspace():
    """Reset the ACTIVE profile's workspace: back up + wipe its metadata,
    downloaded PDFs, and generated report. Other workspaces are untouched.
    Requires {"confirm": true} so the dashboard must ask the user first."""
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is running; wait for it to finish."}), 400

    data = request.json or {}
    if data.get("confirm") is not True:
        return jsonify({"error": "Missing confirmation. Send {\"confirm\": true}."}), 400

    try:
        workspace = scraper.get_active_workspace() or "main"
        summary = scraper.clear_workspace()
        return jsonify({
            "message": (
                f"Workspace '{workspace}' cleared: {summary['records_removed']} records "
                f"and {summary['pdfs_removed']} PDFs removed."
            ),
            "workspace": workspace,
            **summary,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring-config", methods=["GET"])
def get_scoring_config():
    """Return current scoring config (defaults if file absent)."""
    try:
        cfg = scraper.load_scoring_config()
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring-config", methods=["POST"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/api/company-profile", methods=["GET"])
def get_company_profile():
    """Return current company profile (defaults if file absent)."""
    try:
        profile = scraper.load_company_profile()
        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company-profile", methods=["POST"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets", methods=["GET"])
def get_presets():
    """List value-band presets and which one is active."""
    try:
        profile = scraper.load_company_profile()
        return jsonify({
            "active_preset": profile.get("active_preset"),
            "value_presets": profile.get("value_presets") or {},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/preset", methods=["POST"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/api/sources", methods=["GET", "POST"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/tenders/<path:filename>")
def serve_pdf(filename):
    # Serve PDF files securely from the tenders root so per-workspace
    # subfolders (e.g. tenders/personel/downloads/...) resolve too.
    safe_path = filename.replace("\\", "/")
    return send_from_directory(paths.TENDERS_DIR, safe_path)


# -------------------------------------------------------------------------
# Live Excel Session & Curation Endpoints
# -------------------------------------------------------------------------

@app.route("/api/live-excel", methods=["GET"])
def get_live_excel_status():
    """Read-only live Excel session status. Does NOT reset inactivity timer."""
    try:
        return jsonify(live_excel_manager.get_status(touch=False))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-excel/toggle", methods=["POST"])
def toggle_live_excel_tender():
    """Toggle a tender in the live Excel session (resets 10m timer; starts session if idle)."""
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        if not bid_no:
            return jsonify({"error": "Missing bid_no in request."}), 400
        res = live_excel_manager.toggle_tender(bid_no)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-excel/add-batch", methods=["POST"])
def add_batch_live_excel():
    """Add multiple tenders at once to the live Excel session (resets 10m timer)."""
    try:
        data = request.json or {}
        bid_nos = data.get("bid_nos", [])
        if not isinstance(bid_nos, list):
            return jsonify({"error": "bid_nos must be a list."}), 400
        res = live_excel_manager.add_batch(bid_nos)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-excel/download/live", methods=["GET"])
def download_live_excel():
    """Download current live working Excel file. Does NOT reset inactivity timer."""
    try:
        live_path = live_excel_manager.live_excel_path
        if not os.path.exists(live_path):
            return jsonify({"error": "No active live Excel file currently on disk."}), 404
        today = datetime.date.today().isoformat()
        return send_file(
            live_path,
            as_attachment=True,
            download_name=f"tenders_live_curation_{today}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-excel/download/saved/<path:filename>", methods=["GET"])
def download_saved_daily_excel(filename):
    """Download a previously finalized daily export (e.g. 2026-09-04_1.xlsx)."""
    try:
        safe_name = os.path.basename(filename)
        return send_from_directory(
            live_excel_manager.daily_dir,
            safe_name,
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-excel/close", methods=["POST"])
def close_live_excel():
    """Manually save & close or discard the active session."""
    try:
        data = request.json or {}
        save = bool(data.get("save", True))
        res = live_excel_manager.manual_close(save=save)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = 5000
    logger.info("=" * 58)
    logger.info("GeMSentry RFP Acquisition Dashboard Running on Local Server")
    logger.info("Access dashboard at: http://localhost:%s", port)
    logger.info("=" * 58)

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    import time
    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host="127.0.0.1", port=port, debug=False)

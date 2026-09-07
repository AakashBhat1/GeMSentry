"""Dashboard, tender list/detail, scrape control and workspace reset."""

import logging
import threading

from flask import Blueprint, current_app, jsonify, request, send_from_directory

import logging_setup
import paths
import scraper
from gemsentry import tender_view
from gemsentry import storage
from gemsentry.sources import annotate_sources
from gemsentry.web.context import (
    JOB_RUNNING, OUTCOME_FAILED, OUTCOME_SUCCEEDED, _gzip_if_accepted, add_log,
    begin_job, fail, finish_job, normalize_scrape_payload, run_scraper_id_thread,
    run_scraper_thread, scrape_status, source_registry, status_lock,
    tenders_cache,
)

logger = logging.getLogger("gemsentry")


tenders_bp = Blueprint("tenders", __name__)


@tenders_bp.route("/")
def serve_dashboard():
    return send_from_directory(paths.ROOT, "dashboard.html")


@tenders_bp.route("/api/keywords", methods=["GET"])
def get_keywords():
    try:
        keywords = scraper.load_keywords()
        return jsonify({"keywords": keywords})
    except Exception as e:
        return fail(e)


@tenders_bp.route("/api/tenders", methods=["GET"])
def get_tenders():
    """Tender list for the dashboard.

    Detail-only analysis members are stripped (see gemsentry.tender_view); the
    full record for one bid is available at /api/tenders/<bid_no>. Pass
    ?full=1 to opt back into complete records.
    """
    try:
        want_full = request.args.get("full") in ("1", "true", "yes")
        key = storage.store_revision()

        body, etag = (None, None) if want_full else tenders_cache.get(key)
        if body is None:
            tenders_dict = scraper.load_existing_metadata()
            # Legacy records predate source_id; derive it so the dashboard's
            # portal filter covers the full history, not just post-refactor
            # scrapes.
            tenders = annotate_sources(
                tenders_dict.values(), source_registry.get_all_sources()
            )
            if not want_full:
                tenders = [tender_view.project_for_list(t) for t in tenders]
            body = tender_view.serialize({"tenders": tenders, "truncated": not want_full})
            if not want_full:
                etag = tenders_cache.put(key, body)

        if etag and request.headers.get("If-None-Match") == etag:
            resp = current_app.response_class(status=304)
            resp.headers["ETag"] = etag
            return resp

        payload, encoding = _gzip_if_accepted(body)
        resp = current_app.response_class(payload, mimetype="application/json")
        if etag:
            resp.headers["ETag"] = etag
        # Always revalidate: the corpus changes whenever a scrape lands.
        resp.headers["Cache-Control"] = "no-cache"
        if encoding:
            resp.headers["Content-Encoding"] = encoding
            resp.headers["Vary"] = "Accept-Encoding"
        return resp
    except Exception as e:
        return fail(e)


@tenders_bp.route("/api/tenders/<path:bid_no>", methods=["GET"])
def get_tender_detail(bid_no):
    """Complete stored record for a single bid, including the heavy analysis."""
    try:
        tenders_dict = scraper.load_existing_metadata()
        record = tenders_dict.get(bid_no)
        if record is None:
            return jsonify({"error": f"Unknown bid number: {bid_no}"}), 404
        annotated = annotate_sources([record], source_registry.get_all_sources())
        return jsonify({"tender": annotated[0]})
    except Exception as e:
        return fail(e)


@tenders_bp.route("/api/status", methods=["GET"])
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
            # "status" is only whether a job is running; "outcome" says how the
            # last one ended, so the dashboard never has to guess from the mere
            # presence of log lines.
            "status": scrape_status["status"],
            "job": scrape_status.get("job"),
            "outcome": scrape_status.get("outcome"),
            "error": scrape_status.get("error"),
            "warnings": list(scrape_status.get("warnings") or []),
            "current_keyword": scrape_status["current_keyword"],
            "new_count": scrape_status["new_count"],
            "logs": logs,
            "log_session_path": session_rel,
            "log_count": len(logs),
        })


@tenders_bp.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    global scrape_status
    try:
        params = normalize_scrape_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    with status_lock:
        if scrape_status["status"] == JOB_RUNNING:
            return jsonify({"error": "Scraper is already running."}), 400
        begin_job("scrape")

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


@tenders_bp.route("/api/scrape/id", methods=["POST"])
def trigger_scrape_id():
    global scrape_status
    with status_lock:
        if scrape_status["status"] == JOB_RUNNING:
            return jsonify({"error": "Scraper is already running."}), 400

        data = request.json or {}
        bid_id = data.get("bid_id")

        if not bid_id:
            return jsonify({"error": "No Bid ID provided."}), 400

        begin_job("single_bid")

    thread = threading.Thread(
        target=run_scraper_id_thread,
        args=(bid_id,),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Single bid scraper started successfully."})


@tenders_bp.route("/api/tenders/status", methods=["POST"])
def update_tender_status():
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        new_status = data.get("status")

        if not bid_no or not new_status:
            return jsonify({"error": "Missing bid_no or status in request."}), 400

        if new_status not in ["Shortlisted", "Rejected", "Pending Review"]:
            return jsonify({"error": "Invalid status value."}), 400

        # One row, one UPDATE. This used to reload, mutate and rewrite the
        # entire ~8 MB corpus for a single field.
        # User-set statuses are pinned: rescore/scrape never overwrite them.
        record = storage.update_record(
            bid_no, {"status": new_status, "status_source": "manual"}
        )
        if record is None:
            return jsonify({"error": f"Bid {bid_no} not found in database."}), 404

        # The JSON/CSV exports and the Excel workbook are refreshed by the
        # debounced writer that update_record scheduled, so triaging a list of
        # tenders does not pay for a full workbook rebuild per click.
        return jsonify({"message": f"Status for bid {bid_no} updated to {new_status} (pinned)."})
    except Exception as e:
        return fail(e)


@tenders_bp.route("/api/rescore", methods=["POST"])
def trigger_rescore():
    """Re-score all tenders in the active workspace from local data (no network).

    Re-parses local PDFs with current config/profile; falls back to
    card-metadata scoring when a PDF is missing. Makes config changes
    take effect immediately instead of 'on next scrape'."""
    global scrape_status
    with status_lock:
        if scrape_status["status"] == JOB_RUNNING:
            return jsonify({"error": "Scraper is already running."}), 400
        begin_job("rescore")

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
            finish_job(OUTCOME_SUCCEEDED)
        except Exception as e:
            # A rescore that could not save leaves the stored verdicts as they
            # were; saying so beats a green message over an unchanged corpus.
            add_log(f"Rescore failed: {e}")
            logger.exception("Rescore failed")
            finish_job(OUTCOME_FAILED, error=str(e))

    threading.Thread(target=run_rescore, daemon=True).start()
    return jsonify({"message": "Rescore started (local, no network)."})


@tenders_bp.route("/api/clear-workspace", methods=["POST"])
def clear_workspace():
    """Reset the ACTIVE profile's workspace: back up + wipe its metadata,
    downloaded PDFs, and generated report. Other workspaces are untouched.
    Requires {"confirm": true} so the dashboard must ask the user first."""
    with status_lock:
        if scrape_status["status"] == JOB_RUNNING:
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
        return fail(e)

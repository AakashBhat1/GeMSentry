"""Live Excel curation session and summary-workbook export."""

import datetime
import logging
import os

from flask import Blueprint, jsonify, request, send_file, send_from_directory

import paths
import scraper
from gemsentry.live_excel import live_excel_manager
from gemsentry.web.context import fail

logger = logging.getLogger("gemsentry")


live_excel_bp = Blueprint("live_excel", __name__)


# -------------------------------------------------------------------------
# Live Excel Session & Curation Endpoints
# -------------------------------------------------------------------------

@live_excel_bp.route("/api/live-excel", methods=["GET"])
def get_live_excel_status():
    """Read-only live Excel session status. Does NOT reset inactivity timer."""
    try:
        return jsonify(live_excel_manager.get_status(touch=False))
    except Exception as e:
        return fail(e)


@live_excel_bp.route("/api/live-excel/toggle", methods=["POST"])
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
        return fail(e)


@live_excel_bp.route("/api/live-excel/add-batch", methods=["POST"])
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
        return fail(e)


@live_excel_bp.route("/api/live-excel/download/live", methods=["GET"])
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
        return fail(e)


@live_excel_bp.route("/api/live-excel/download/saved/<path:filename>", methods=["GET"])
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
        return fail(e)


@live_excel_bp.route("/api/live-excel/close", methods=["POST"])
def close_live_excel():
    """Manually save & close or discard the active session."""
    try:
        data = request.json or {}
        save = bool(data.get("save", True))
        res = live_excel_manager.manual_close(save=save)
        return jsonify(res)
    except Exception as e:
        return fail(e)


@live_excel_bp.route("/api/export/summary.xlsx", methods=["GET"])
def download_summary_excel():
    """Generate (if needed) and stream the latest tender_summary Excel workbook."""
    try:
        active_profile = scraper.load_company_profile()
        workspace = scraper.get_active_workspace(active_profile) or "main"
        tenders_dir, _ = scraper.workspace_paths(workspace)
        label = scraper.workspace_label(tenders_dir)
        reports_dir = os.path.join(paths.TENDERS_DIR, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        # Refresh the Excel workbook
        scraper.auto_export_summary(tenders_dir)

        report_file = os.path.join(reports_dir, f"tender_summary_{label}.xlsx")
        if not os.path.exists(report_file):
            report_file = os.path.join(reports_dir, "tender_summary_main.xlsx")

        if os.path.exists(report_file):
            stamp = datetime.date.today().isoformat()
            return send_from_directory(
                os.path.dirname(report_file),
                os.path.basename(report_file),
                as_attachment=True,
                download_name=f"tender_summary_{label}_{stamp}.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    except Exception as e:
        return fail(e)

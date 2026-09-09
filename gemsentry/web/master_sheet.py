"""Finalized tenders, master sheet and Google Sheets sync."""

import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import paths
from gemsentry.master_sheet import master_sheet_manager
from gemsentry.google_webhook import post_webhook, public_config
from gemsentry.web.context import fail, is_apps_script_url

logger = logging.getLogger("gemsentry")


master_sheet_bp = Blueprint("master_sheet", __name__)


# -------------------------------------------------------------------------
# Master Sheet & Google Sheets Integration Endpoints
# -------------------------------------------------------------------------

@master_sheet_bp.route("/api/finalized", methods=["GET"])
def get_finalized_tenders():
    """Get all finalized tenders with serial numbering and Google Sheet status."""
    try:
        return jsonify(master_sheet_manager.get_all_finalized())
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/finalize", methods=["POST"])
def finalize_tender_endpoint():
    """Finalize a tender to Master Sheet with sequential serial number and dual sync."""
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        if not bid_no:
            return jsonify({"error": "Missing bid_no in request."}), 400

        from gemsentry.storage import load_existing_metadata
        all_metadata = load_existing_metadata()
        tender = all_metadata.get(bid_no)
        if not tender:
            tender = {"bid_no": bid_no, "title": data.get("title") or "Unknown Title"}

        target_sheet = data.get("target_sheet") or "UNDER DETAILED STUDY"
        custom_fields = data.get("custom_fields") or {}
        if "assigned_vendor" in data and "assigned_vendor" not in custom_fields:
            custom_fields["assigned_vendor"] = data["assigned_vendor"]
        if "sl_no" in data and "sl_no" not in custom_fields:
            custom_fields["sl_no"] = data["sl_no"]

        req_sl = data.get("sl_no") or custom_fields.get("sl_no")
        if req_sl is not None:
            try:
                req_sl = int(req_sl)
            except (ValueError, TypeError):
                req_sl = None

        res = master_sheet_manager.finalize_tender(
            tender=tender,
            target_sheet=target_sheet,
            custom_fields=custom_fields,
            sl_no=req_sl
        )
        return jsonify(res)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/update-spec-sheet", methods=["POST"])
def update_spec_sheet_endpoint():
    """Attach or update the Google Doc or Tech Spec link for a finalized tender."""
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        if not bid_no:
            return jsonify({"error": "Missing bid_no in request."}), 400
        tech_spec_url = (data.get("tech_spec_url") or "").strip()
        filename = (data.get("filename") or "").strip()
        assigned_vendor = data.get("assigned_vendor") or data.get("vendor_id")
        res = master_sheet_manager.update_tech_spec(
            bid_no=bid_no,
            tech_spec_url=tech_spec_url or None,
            filename=filename or None,
            assigned_vendor=assigned_vendor
        )
        return jsonify(res)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/upload-spec-sheet", methods=["POST"])
def upload_spec_sheet_endpoint():
    """Upload a technical specification document (.pdf, .doc, .docx, etc.) for a tender."""
    try:
        bid_no = request.form.get("bid_no")
        if not bid_no:
            return jsonify({"error": "Missing bid_no in form data."}), 400
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded."}), 400
        uploaded_file = request.files["file"]
        if not uploaded_file.filename:
            return jsonify({"error": "Empty filename."}), 400

        filename = secure_filename(uploaded_file.filename)
        file_bytes = uploaded_file.read()
        tech_spec_url = (request.form.get("tech_spec_url") or "").strip()
        assigned_vendor = request.form.get("assigned_vendor") or request.form.get("vendor_id")

        res = master_sheet_manager.update_tech_spec(
            bid_no=bid_no,
            tech_spec_url=tech_spec_url or None,
            filename=filename,
            file_bytes=file_bytes,
            assigned_vendor=assigned_vendor
        )
        return jsonify(res)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/spec-sheet/<path:bid_slug>", methods=["GET"])
def serve_spec_sheet_endpoint(bid_slug: str):
    """Serve locally stored technical specification document."""
    try:
        clean_slug = os.path.basename(bid_slug)
        folder = os.path.join(paths.TECH_SPECS_DIR, clean_slug)
        if not os.path.exists(folder):
            return jsonify({"error": "Spec sheet directory not found."}), 404
        files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        if not files:
            return jsonify({"error": "No files found in spec sheet directory."}), 404
        return send_from_directory(folder, files[0], as_attachment=False)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/delete", methods=["POST"])
def delete_finalized_tender_endpoint():
    """Delete a tender from Google Sheet and Master Excel (e.g. accidental addition)."""
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        sl_no = data.get("sl_no")
        if not bid_no and not sl_no:
            return jsonify({"error": "Must provide bid_no or sl_no to delete."}), 400

        res = master_sheet_manager.delete_tender(bid_no or sl_no)
        return jsonify(res)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/move-to-participated", methods=["POST"])
def move_to_participated_endpoint():
    """Move a finalized tender to '(TENDER DETAILS (PARTICIPATED)' with Won/Lost status."""
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        if not bid_no:
            return jsonify({"error": "Missing bid_no in request."}), 400

        res = master_sheet_manager.move_to_participated(
            bid_no=bid_no,
            won_lost_result=data.get("won_lost_result", "WON L - 1"),
            tender_value=data.get("tender_value"),
            so_link=data.get("so_link"),
            submission_status=data.get("submission_status", "SUBMITTED"),
            final_remarks=data.get("final_remarks")
        )
        return jsonify(res)
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/sync-all", methods=["POST"])
def api_sync_all_finalized():
    """Pushes all finalized tenders from local store into Google Sheet."""
    try:
        res = master_sheet_manager.sync_all_to_google_sheet()
        return jsonify(res)
    except Exception as e:
        return fail(e)



@master_sheet_bp.route("/api/finalized/config", methods=["GET", "POST"])
def finalized_config_endpoint():
    """Get or update Google Sheet & Drive integration configuration."""
    try:
        if request.method == "POST":
            data = request.json or {}
            saved = master_sheet_manager.save_config(data)
            return jsonify({"status": "ok", "config": public_config(saved)})
        return jsonify(public_config(master_sheet_manager.load_config()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return fail(e)


@master_sheet_bp.route("/api/finalized/test-webhook", methods=["POST"])
def test_google_webhook_endpoint():
    """Test ping to the Google Apps Script Webhook."""
    try:
        data = request.json or {}
        url = data.get("apps_script_url") or master_sheet_manager.config.get("apps_script_url")
        if not url:
            return jsonify({"status": "error", "message": "No Apps Script URL provided."}), 400

        # The URL arrives in the request body, so without this check the
        # endpoint would POST to any host the caller names -- a server-side
        # request forgery reachable from the dashboard.
        if not is_apps_script_url(url):
            return jsonify({
                "status": "error",
                "message": "Only https://script.google.com/ URLs can be pinged.",
            }), 400

        config = {**master_sheet_manager.load_config(), "apps_script_url": url}
        if data.get("webhook_secret"):
            config["webhook_secret"] = data["webhook_secret"]
        return jsonify(post_webhook(config, {"action": "ping"}, timeout=10))
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("Webhook ping failed: %s", e)
        return jsonify({"status": "error", "message": "Webhook ping failed."}), 502


@master_sheet_bp.route("/api/finalized/script", methods=["GET"])
def get_apps_script_code_endpoint():
    """Get the source code of the Google Apps Script helper."""
    try:
        script_path = os.path.join(paths.ROOT, "gemsentry", "google_sync_script.example.gs")
        if os.path.exists(script_path):
            with open(script_path, encoding="utf-8") as f:
                code = f.read()
            return jsonify({"status": "ok", "script": code})
        return jsonify({"error": "Script file not found."}), 404
    except Exception as e:
        return fail(e)

"""Admin-only API for offline bid preparation. No GeM portal submission actions."""
from __future__ import annotations

import os

from flask import Blueprint, g, jsonify, request, send_file

from gemsentry import storage
from gemsentry.bid_preparation import service
from gemsentry.web.context import fail

bid_preparation_bp = Blueprint("bid_preparation", __name__)


def _admin_error():
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Authentication required."}), 401
    if user.get("role") != "admin":
        return jsonify({"error": "Forbidden: administrator access required."}), 403
    return None


def _actor():
    user = getattr(g, "current_user", {}) or {}
    return user.get("username") or user.get("id") or "admin"


@bid_preparation_bp.before_request
def _require_admin():
    return _admin_error()


@bid_preparation_bp.get("/api/crm/bid-preparation")
def list_preparations():
    try:
        return jsonify({"preparations": service.list_all()})
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.route("/api/crm/bid-preparation/<path:bid_no>", methods=["GET", "PUT"])
def bid_preparation_detail(bid_no):
    try:
        bid_no = service.validate_bid(bid_no)
        tender = storage.get_record(bid_no)
        if tender is None:
            return jsonify({"error": "Tender not found in the active workspace."}), 404
        if request.method == "GET":
            return jsonify({"preparation": service.get(bid_no)})
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "JSON object body is required."}), 400
        return jsonify({"preparation": service.save(bid_no, payload, _actor())})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.post("/api/crm/bid-preparation/<path:bid_no>/documents")
def upload_document(bid_no):
    try:
        bid_no = service.validate_bid(bid_no)
        if storage.get_record(bid_no) is None:
            return jsonify({"error": "Tender not found in the active workspace."}), 404
        uploaded = request.files.get("file")
        if uploaded is None:
            return jsonify({"error": "Choose a PDF file."}), 400
        result = service.add_document(bid_no, request.form.get("category", "other"),
                                      uploaded.filename, uploaded.read(25 * 1024 * 1024 + 1), _actor())
        return jsonify({"preparation": result}), 201
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.post("/api/crm/bid-preparation/documents/<int:document_id>/verification")
def verify_document(document_id):
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("verified"), bool):
            return jsonify({"error": "verified must be true or false."}), 400
        return jsonify({"preparation": service.verify_document(document_id, payload["verified"], _actor())})
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.get("/api/crm/bid-preparation/documents/<int:document_id>/download")
def download_document(document_id):
    try:
        found = service.document_path(document_id)
        if not found or not os.path.isfile(found[0]):
            return jsonify({"error": "Document not found."}), 404
        return send_file(found[0], as_attachment=True, download_name=found[1], mimetype="application/pdf")
    except Exception as exc:
        return fail(exc)

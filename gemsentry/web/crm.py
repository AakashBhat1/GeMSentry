"""CRM workspace, company/subfolder routing, file serving, and user RBAC endpoints."""

from __future__ import annotations

import logging
import os
import re

from flask import Blueprint, g, jsonify, request, send_file
from werkzeug.utils import secure_filename

import paths
from gemsentry.crm.auth_service import crm_auth_service
from gemsentry.web.context import fail

# TODO(security): Integrate antivirus/CDR scanning for external file uploads.

logger = logging.getLogger("gemsentry")

crm_bp = Blueprint("crm", __name__)

# Lazily resolved on first request (or monkeypatched in isolated unit tests).
crm_service = None

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXTS = frozenset({
    ".pdf", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".txt",
    ".png", ".jpg", ".jpeg", ".zip",
})
ALLOWED_FILE_KINDS = frozenset({"rfp", "tech_spec", "boq", "atc", "annexure", "other"})
ALLOWED_COMPANY_TYPES = frozenset({"admin", "vendor", "internal"})
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _crm():
    if crm_service is not None:
        return crm_service
    from gemsentry.crm import crm_service as _svc
    return _svc


@crm_bp.before_request
def _ensure_crm_ready():
    _crm().ensure_initialized()


def _current_user() -> dict | None:
    return getattr(g, "current_user", None)


def _is_admin(user: dict | None) -> bool:
    return bool(user and user.get("role") == "admin")


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"error": "Authentication required."}), 401)
    return user, None


def _require_admin():
    user, err_resp = _require_user()
    if err_resp is not None:
        return None, err_resp
    if not _is_admin(user):
        return None, (jsonify({"error": "Forbidden: administrator access required."}), 403)
    return user, None


def _json_object():
    data = request.get_json(silent=True)
    if data is None:
        return None, (jsonify({"error": "JSON object body is required."}), 400)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "JSON body must be an object."}), 400)
    return data, None


def _parse_optional_int(raw, field_name: str) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be an integer.")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _validate_safe_folder_segment(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or len(cleaned) > 120:
        raise ValueError(f"{field_name} must be 1-120 characters.")
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned or ":" in cleaned:
        raise ValueError(f"{field_name} contains disallowed path characters.")
    return cleaned


def _error_status_from_message(msg: str, default_status: int = 403) -> int:
    lower = (msg or "").lower()
    if "not found" in lower or "missing" in lower:
        return 404
    if "forbidden" in lower or "denied" in lower or "access" in lower or "unauthorized" in lower:
        return 403
    if "invalid" in lower or "not a csv" in lower:
        return 400
    return default_status


def _is_within_dir(root_dir: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root_dir, candidate]) == root_dir
    except ValueError:
        return False


# ----------------------------------------------------------------------
# Bootstrap, Companies & Subfolders
# ----------------------------------------------------------------------

@crm_bp.route("/api/crm/bootstrap", methods=["GET"])
def crm_bootstrap():
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        return jsonify(_crm().get_bootstrap(user))
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/companies", methods=["POST"])
def upsert_crm_company():
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    data, err_resp = _json_object()
    if err_resp is not None:
        return err_resp
    try:
        payload = dict(data)
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 120:
            return jsonify({"error": "Company name must be 1-120 characters."}), 400
        payload["name"] = name

        if payload.get("id"):
            cid = str(payload["id"]).strip()
            if not ID_RE.match(cid):
                return jsonify({"error": "Invalid company id format."}), 400
            payload["id"] = cid

        if payload.get("disk_folder"):
            payload["disk_folder"] = _validate_safe_folder_segment(payload["disk_folder"], "disk_folder")

        ctype = payload.get("company_type") or payload.get("type")
        if ctype is not None:
            ctype_clean = str(ctype).strip().lower()
            if ctype_clean not in ALLOWED_COMPANY_TYPES:
                return jsonify({"error": "company_type must be admin, vendor, or internal."}), 400
            payload["company_type"] = ctype_clean

        company = _crm().create_or_update_company(payload)
        return jsonify({"company": company})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/subfolders", methods=["POST"])
def create_crm_subfolder():
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    data, err_resp = _json_object()
    if err_resp is not None:
        return err_resp
    try:
        company_id = str(data.get("company_id") or "").strip()
        if not ID_RE.match(company_id):
            return jsonify({"error": "Valid company_id is required."}), 400
        name = _validate_safe_folder_segment(data.get("name") or "", "name")
        parent_id = _parse_optional_int(data.get("parent_id"), "parent_id")
        subfolder = _crm().create_subfolder(company_id, name, parent_id=parent_id)
        return jsonify({"subfolder": subfolder})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


# ----------------------------------------------------------------------
# Tenders, Preview & Folder Move
# ----------------------------------------------------------------------

@crm_bp.route("/api/crm/tenders", methods=["GET"])
def list_crm_tenders():
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        req_company_id = (request.args.get("company_id") or "").strip() or None
        if req_company_id and not ID_RE.match(req_company_id):
            return jsonify({"error": "Invalid company_id."}), 400

        if not _is_admin(user):
            if req_company_id and req_company_id != user.get("company_id"):
                return jsonify({"error": "Forbidden: cannot access another company's tenders."}), 403
            req_company_id = user.get("company_id")

        subfolder_id = _parse_optional_int(request.args.get("subfolder_id"), "subfolder_id")
        subfolder_slug = (request.args.get("subfolder_slug") or "").strip() or None
        if not _is_admin(user) and user.get("subfolder_scope"):
            if subfolder_slug and subfolder_slug != user["subfolder_scope"]:
                return jsonify({"error": "Forbidden: outside your assigned subfolder scope."}), 403

        q = (request.args.get("q") or "").strip()[:200]
        stage = (request.args.get("stage") or "").strip()[:64]
        vendor_decision = (request.args.get("vendor_decision") or "").strip()[:64]

        tenders = _crm().list_tenders(
            user,
            company_id=req_company_id,
            subfolder_id=subfolder_id,
            subfolder_slug=subfolder_slug,
            q=q,
            stage=stage,
            vendor_decision=vendor_decision,
        )
        return jsonify({"tenders": tenders, "count": len(tenders)})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/tender-preview/<path:bid_no>", methods=["GET"])
def preview_crm_tender(bid_no):
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        clean_bid = (bid_no or "").strip()
        if not clean_bid or len(clean_bid) > 128 or ".." in clean_bid:
            return jsonify({"error": "Invalid bid number."}), 400
        detail = _crm().inspect_tender(clean_bid, user)
        if detail is None:
            return jsonify({"error": "Tender not found."}), 404
        return jsonify(detail)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/move", methods=["POST"])
def move_crm_tenders():
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    data, err_resp = _json_object()
    if err_resp is not None:
        return err_resp
    try:
        raw_bids = data.get("bid_nos")
        if not isinstance(raw_bids, list) or not raw_bids:
            return jsonify({"error": "bid_nos must be a non-empty list."}), 400
        if len(raw_bids) > 200:
            return jsonify({"error": "Cannot move more than 200 tenders per request."}), 400
        bid_nos = []
        for b in raw_bids:
            s = str(b or "").strip()
            if not s or len(s) > 128:
                return jsonify({"error": "Each bid_no must be a non-empty string (<= 128 chars)."}), 400
            bid_nos.append(s)

        company_id = str(data.get("company_id") or "").strip()
        if not ID_RE.match(company_id):
            return jsonify({"error": "Valid company_id is required."}), 400

        subfolder_id = _parse_optional_int(data.get("subfolder_id"), "subfolder_id")
        new_subfolder_name = str(data.get("new_subfolder_name") or "").strip()
        if new_subfolder_name:
            new_subfolder_name = _validate_safe_folder_segment(new_subfolder_name, "new_subfolder_name")

        fetch_gem_annexures = bool(data.get("fetch_gem_annexures", True))
        sync_master = data.get("sync_master_and_sheets")
        if sync_master is not None:
            sync_master = bool(sync_master)

        target_sheet = str(data.get("target_sheet") or "UNDER DETAILED STUDY").strip()[:100]
        work_category = str(data.get("work_category") or "").strip()[:120]
        remarks = str(data.get("remarks") or "").strip()[:2000]

        res = _crm().move_tenders_to_folder(
            bid_nos=bid_nos,
            company_id=company_id,
            subfolder_id=subfolder_id,
            new_subfolder_name=new_subfolder_name,
            fetch_gem_annexures=fetch_gem_annexures,
            sync_master_and_sheets=sync_master,
            target_sheet=target_sheet,
            work_category=work_category,
            remarks=remarks,
        )
        return jsonify(res)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


# ----------------------------------------------------------------------
# Tender Actions: Annexures, Upload, Decision, Remove
# ----------------------------------------------------------------------

@crm_bp.route("/api/crm/tenders/<int:crm_tender_id>/fetch-annexures", methods=["POST"])
def fetch_tender_annexures(crm_tender_id: int):
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    try:
        res = _crm().fetch_annexures_for_tender(crm_tender_id)
        return jsonify(res)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/tenders/<int:crm_tender_id>/upload", methods=["POST"])
def upload_tender_file(crm_tender_id: int):
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES + 1024 * 1024:
        return jsonify({"error": "Upload exceeds 25 MB limit."}), 413
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    uploaded = request.files["file"]
    raw_name = (uploaded.filename or "").strip()
    filename = secure_filename(os.path.basename(raw_name))
    if not filename:
        return jsonify({"error": "Invalid or empty filename."}), 400

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        return jsonify({"error": f"Unsupported file extension '{ext}'."}), 400

    file_kind = (request.form.get("file_kind") or "tech_spec").strip().lower()
    if file_kind not in ALLOWED_FILE_KINDS:
        return jsonify({"error": "Invalid file_kind."}), 400

    file_bytes = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if not file_bytes:
        return jsonify({"error": "Uploaded file is empty."}), 400
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "Uploaded file exceeds 25 MB limit."}), 413

    try:
        res = _crm().attach_uploaded_file(crm_tender_id, filename, file_bytes, file_kind=file_kind)
        return jsonify(res)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/tenders/<int:crm_tender_id>/decision", methods=["POST"])
def update_tender_decision(crm_tender_id: int):
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    data, err_resp = _json_object()
    if err_resp is not None:
        return err_resp

    try:
        if not _is_admin(user):
            if data.get("stage") is not None or data.get("internal_notes") is not None:
                return jsonify({"error": "Forbidden: only administrators may update stage or internal notes."}), 403

        vendor_decision = data.get("vendor_decision")
        if vendor_decision is not None:
            vendor_decision = str(vendor_decision).strip()
            if len(vendor_decision) > 64:
                return jsonify({"error": "vendor_decision is too long."}), 400

        vendor_remarks = data.get("vendor_remarks")
        if vendor_remarks is not None:
            vendor_remarks = str(vendor_remarks).strip()
            if len(vendor_remarks) > 4000:
                return jsonify({"error": "vendor_remarks exceeds 4000 characters."}), 400

        stage = None
        internal_notes = None
        if _is_admin(user):
            if data.get("stage") is not None:
                stage = str(data.get("stage")).strip()
                if len(stage) > 64:
                    return jsonify({"error": "stage is too long."}), 400
            if data.get("internal_notes") is not None:
                internal_notes = str(data.get("internal_notes")).strip()
                if len(internal_notes) > 4000:
                    return jsonify({"error": "internal_notes exceeds 4000 characters."}), 400

        res = _crm().update_decision(
            crm_tender_id,
            user,
            vendor_decision=vendor_decision,
            vendor_remarks=vendor_remarks,
            stage=stage,
            internal_notes=internal_notes,
        )
        if res is None:
            return jsonify({"error": "Tender assignment not found."}), 404
        return jsonify(res)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/tenders/<int:crm_tender_id>/remove", methods=["POST"])
def remove_crm_tender(crm_tender_id: int):
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    try:
        removed = _crm().remove_tender_assignment(crm_tender_id)
        if not removed:
            return jsonify({"error": "CRM tender assignment not found."}), 404
        return jsonify({"removed": True, "id": crm_tender_id})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


# ----------------------------------------------------------------------
# Authorized File Serving, CSV Preview & Excel Export
# ----------------------------------------------------------------------

@crm_bp.route("/api/crm/files/<int:file_id>", methods=["GET"])
def download_crm_file(file_id: int):
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        file_row, abs_path, err = _crm().get_authorized_file(file_id, user)
        if err:
            return jsonify({"error": err}), _error_status_from_message(err, default_status=403)
        if not file_row or not abs_path:
            return jsonify({"error": "File not found."}), 404

        base_dir = os.path.realpath(paths.TENDERS_DIR)
        real_path = os.path.realpath(abs_path)
        if not _is_within_dir(base_dir, real_path) or not os.path.isfile(real_path):
            return jsonify({"error": "File not found."}), 404

        inline_req = request.args.get("inline") in ("1", "true", "yes")
        ext = os.path.splitext(real_path)[1].lower()
        safe_download_name = secure_filename(
            os.path.basename(file_row.get("filename") or os.path.basename(real_path))
        ) or os.path.basename(real_path)

        # Only PDFs may be rendered inline inside the viewer iframe; all other
        # file types are forced as attachments so active content never executes.
        as_attachment = not (inline_req and ext == ".pdf")
        mimetype = "application/pdf" if ext == ".pdf" else None
        return send_file(
            real_path,
            as_attachment=as_attachment,
            download_name=safe_download_name,
            mimetype=mimetype,
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/files/<int:file_id>/csv-preview", methods=["GET"])
def preview_crm_csv(file_id: int):
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        raw_max = request.args.get("max_rows", "100")
        max_rows = _parse_optional_int(raw_max, "max_rows") or 100
        max_rows = max(1, min(max_rows, 500))

        preview, err = _crm().preview_csv_file(file_id, user, max_rows=max_rows)
        if err:
            return jsonify({"error": err}), _error_status_from_message(err, default_status=403)
        if preview is None:
            return jsonify({"error": "CSV preview not available."}), 404
        return jsonify(preview)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/export/<company_id>.xlsx", methods=["GET"])
def export_company_workbook(company_id: str):
    user, err_resp = _require_user()
    if err_resp is not None:
        return err_resp
    try:
        if not ID_RE.match(company_id or ""):
            return jsonify({"error": "Invalid company_id."}), 400
        if not _is_admin(user) and company_id != user.get("company_id"):
            return jsonify({"error": "Forbidden: cannot export another company's workbook."}), 403

        subfolder_id = _parse_optional_int(request.args.get("subfolder_id"), "subfolder_id")
        xlsx_path = _crm().export_company_excel(company_id, user, subfolder_id=subfolder_id)
        if not xlsx_path:
            return jsonify({"error": "Workbook not found."}), 404
        real_path = os.path.realpath(xlsx_path)
        if not real_path.lower().endswith(".xlsx") or not os.path.isfile(real_path):
            return jsonify({"error": "Workbook not found."}), 404
        # Ensure export path stays under TENDERS_DIR or DATA_DIR.
        tenders_root = os.path.realpath(paths.TENDERS_DIR)
        data_root = os.path.realpath(paths.DATA_DIR)
        if not (_is_within_dir(tenders_root, real_path) or _is_within_dir(data_root, real_path)):
            return jsonify({"error": "Forbidden"}), 403

        return send_file(
            real_path,
            as_attachment=True,
            download_name=os.path.basename(real_path),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (FileNotFoundError, LookupError) as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


# ----------------------------------------------------------------------
# Users & Legacy Folder Migration (Admin Only)
# ----------------------------------------------------------------------

@crm_bp.route("/api/crm/users", methods=["GET", "POST"])
def manage_crm_users():
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    try:
        if request.method == "GET":
            users = crm_auth_service.list_users()
            for u in users:
                u.pop("password_hash", None)
            return jsonify({"users": users})

        data, err_resp = _json_object()
        if err_resp is not None:
            return err_resp
        saved = crm_auth_service.upsert_user_account(data)
        if isinstance(saved, dict):
            saved.pop("password_hash", None)
        return jsonify({"user": saved})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@crm_bp.route("/api/crm/migrate-folders", methods=["POST"])
def migrate_legacy_folders():
    _, err_resp = _require_admin()
    if err_resp is not None:
        return err_resp
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "JSON body must be an object."}), 400
        force = bool(data.get("force", False))
        summary = _crm().run_folder_migration(force=force)
        return jsonify(summary)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)

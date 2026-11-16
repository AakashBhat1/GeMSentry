"""Business service layer for GeMSentry Multi-Company CRM, Dossiers, Uploads, and Excel Export."""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import logging
import mimetypes
import os
import pathlib
import re
import shutil
import threading
import zipfile
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import paths
from gemsentry import storage
from gemsentry.crm.annexures import (
    DEFAULT_MAX_ANNEXURE_BYTES,
    download_gem_annexures,
    extract_pdf_annexure_links,
    sanitize_filename,
)
from gemsentry.crm.migration import organize_and_import_legacy_folders
from gemsentry.crm.repository import CRMRepository, extract_sheet_id, safe_bid_slug, slugify
from gemsentry.master_sheet import master_sheet_manager

logger = logging.getLogger("gemsentry.crm.service")

MAX_UPLOAD_BYTES = DEFAULT_MAX_ANNEXURE_BYTES  # 25 MiB
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MiB zip-bomb guard
MAX_CSV_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MiB preview read guard

ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    ".pdf",
    ".csv",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".zip",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
})

ALLOWED_FILE_KINDS = frozenset({
    "rfp",
    "tech_spec",
    "boq",
    "atc",
    "annexure",
    "other",
})

VALID_COMPANY_TYPES = frozenset({"admin", "vendor", "internal"})

VALID_STAGES = (
    "assigned",
    "review",
    "shortlisted",
    "participating",
    "submitted",
    "won",
    "lost",
    "rejected",
)

VALID_DECISIONS = (
    "PENDING",
    "YES",
    "NO",
    "CLARIFICATION",
)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_vendor_decision(raw: str) -> str:
    val = (raw or "").strip()
    low = val.lower()
    if low in ("", "pending"):
        return "pending"
    if low in ("yes", "participate", "yes - participate", "yes — participate"):
        return "YES"
    if low in ("no", "skip", "no - skip", "no — skip"):
        return "NO"
    if low in ("clarification", "need_clarification", "need clarification"):
        return "CLARIFICATION"
    raise ValueError(f"Invalid vendor_decision '{raw}'. Expected YES, NO, CLARIFICATION, or pending.")


def _normalize_stage(raw: str) -> str:
    val = (raw or "").strip()
    low = val.lower()
    stage_aliases = {
        "assigned": "assigned",
        "under detailed study": "review",
        "under_study": "review",
        "review": "review",
        "shortlisted": "shortlisted",
        "participated": "participating",
        "participating": "participating",
        "submitted": "submitted",
        "won": "won",
        "lost": "lost",
        "dropped": "rejected",
        "rejected": "rejected",
    }
    if low in stage_aliases:
        return stage_aliases[low]
    raise ValueError(f"Invalid stage '{raw}'.")


def _validate_safe_folder_segment(segment: str, field_label: str = "folder") -> str:
    val = (segment or "").strip()
    if not val or val in (".", ".."):
        raise ValueError(f"Invalid {field_label} name.")
    if any(ch in val for ch in ("/", "\\", ":", "\x00")) or ".." in val:
        raise ValueError(f"Unsafe {field_label} name: path separators and '..' are prohibited.")
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", val):
        raise ValueError(f"Invalid characters in {field_label} name.")
    return val


def validate_upload_payload(filename: str, file_bytes: bytes) -> tuple[str, str, str]:
    """Validate uploaded filename, size, magic bytes, and archive members.

    Returns ``(clean_original_name, extension, mime_type)`` or raises ``ValueError``.
    """
    # TODO(security): Integrate antivirus (ClamAV/cloud AV) and CDR active-content
    # stripping before storing external uploads in multi-tenant cloud deployments.
    if not filename or "\x00" in filename:
        raise ValueError("A valid filename is required.")
    orig_base = os.path.basename(filename.replace("\\", "/")).strip()
    if not orig_base or orig_base in (".", ".."):
        raise ValueError("Invalid upload filename.")

    ext = os.path.splitext(orig_base)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext or '(none)'}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}."
        )

    if not isinstance(file_bytes, (bytes, bytearray)) or len(file_bytes) == 0:
        raise ValueError("Uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded file exceeds maximum allowed size ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    raw = bytes(file_bytes)

    if ext == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF upload: missing %PDF- header signature.")
    elif ext == ".png":
        if not raw.startswith(_PNG_MAGIC):
            raise ValueError("Invalid PNG upload: header signature mismatch.")
    elif ext in (".jpg", ".jpeg"):
        if not raw.startswith(_JPEG_MAGIC):
            raise ValueError("Invalid JPEG upload: header signature mismatch.")
    elif ext in (".doc", ".xls"):
        if not (raw.startswith(_OLE2_MAGIC) or (ext == ".doc" and raw.startswith(b"{\\rtf"))):
            raise ValueError(f"Invalid {ext} upload: expected OLE2 compound document header.")
    elif ext in (".docx", ".xlsx", ".zip"):
        if not raw.startswith(_ZIP_MAGICS):
            raise ValueError(f"Invalid {ext} upload: expected ZIP/OOXML container signature.")
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
                total_uncompressed = 0
                names_seen: set[str] = set()
                for info in zf.infolist():
                    mname = info.filename
                    if "\x00" in mname:
                        raise ValueError("Archive contains NUL byte in member path.")
                    norm_m = mname.replace("\\", "/")
                    if norm_m.startswith("/") or re.match(r"^[A-Za-z]:", norm_m):
                        raise ValueError("Archive contains unsafe absolute member path.")
                    p_parts = pathlib.PurePosixPath(norm_m).parts
                    if ".." in p_parts:
                        raise ValueError("Archive contains path traversal ('..') entry.")
                    total_uncompressed += max(0, info.file_size)
                    if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                        raise ValueError("Archive uncompressed payload exceeds safety limit.")
                    names_seen.add(norm_m)
                if ext in (".docx", ".xlsx") and "[Content_Types].xml" not in names_seen:
                    raise ValueError(f"Invalid {ext} upload: missing [Content_Types].xml.")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Corrupt {ext} archive: {exc}") from exc
    elif ext in (".csv", ".txt"):
        if b"\x00" in raw:
            raise ValueError(f"Invalid {ext} upload: binary NUL bytes detected.")
        head_low = raw[:512].lstrip().lower()
        if head_low.startswith((b"mz", b"\x7felf", b"<script", b"<!doctype html", b"<html")):
            raise ValueError(f"Invalid {ext} upload: executable or HTML content rejected.")
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                raw.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Invalid {ext} text encoding.") from exc

    mime = mimetypes.guess_type(orig_base)[0] or "application/octet-stream"
    return orig_base, ext, mime


class CRMService:
    """Orchestrates CRM companies, subfolders, tender movement, annexures, RBAC views, and exports."""

    def __init__(self, repo: CRMRepository | None = None) -> None:
        self.repo = repo or CRMRepository()
        self._init_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Path boundary helpers
    # ------------------------------------------------------------------

    def _tenders_root(self) -> str:
        root = os.path.abspath(paths.TENDERS_DIR)
        os.makedirs(root, exist_ok=True)
        return root

    def _safe_tenders_join(self, *parts: str) -> str:
        root = self._tenders_root()
        candidate = os.path.abspath(os.path.join(root, *parts))
        try:
            if os.path.commonpath([root, candidate]) != root:
                raise ValueError("Path escapes TENDERS_DIR boundary.")
        except ValueError as exc:
            raise ValueError("Path escapes TENDERS_DIR boundary.") from exc
        return candidate

    def _tenders_rel(self, abs_path: str) -> str:
        root = self._tenders_root()
        abs_target = os.path.abspath(abs_path)
        if os.path.commonpath([root, abs_target]) != root:
            raise ValueError("Path escapes TENDERS_DIR boundary.")
        return os.path.relpath(abs_target, root).replace("\\", "/")

    # ------------------------------------------------------------------
    # Initialization & Migration
    # ------------------------------------------------------------------

    def ensure_initialized(self, run_migration: bool = True) -> dict[str, Any]:
        with self._init_lock:
            abs_tenders = self._tenders_root()
            self.repo.init_db()
            self.repo.seed_defaults()

            migrated_for = self.repo.get_meta("legacy_migration_tenders_dir")
            mig_result: dict[str, Any] | None = None
            if run_migration and migrated_for != abs_tenders:
                mig_result = organize_and_import_legacy_folders(self.repo, tenders_dir=abs_tenders)
                self.repo.set_meta("legacy_migration_tenders_dir", abs_tenders)

            return {
                "initialized": True,
                "tenders_dir": abs_tenders,
                "db_path": self.repo.db_path,
                "migration": mig_result,
            }

    def run_folder_migration(self, force: bool = False) -> dict[str, Any]:
        with self._init_lock:
            abs_tenders = self._tenders_root()
            self.repo.init_db()
            self.repo.seed_defaults()
            res = organize_and_import_legacy_folders(self.repo, tenders_dir=abs_tenders, force=force)
            self.repo.set_meta("legacy_migration_tenders_dir", abs_tenders)
            return res

    # ------------------------------------------------------------------
    # RBAC & Redaction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin(caller_user: dict[str, Any] | None) -> bool:
        return bool(caller_user and caller_user.get("role") == "admin")

    def _check_company_access(
        self,
        caller_user: dict[str, Any] | None,
        company_id: str,
        subfolder_slug: str | None = None,
    ) -> None:
        if not caller_user:
            raise PermissionError("Forbidden: authentication required.")
        if self._is_admin(caller_user):
            return
        user_cid = (caller_user.get("company_id") or "").strip()
        if not user_cid or user_cid != company_id:
            raise PermissionError("Forbidden: access denied to another company's CRM data.")
        scope = (caller_user.get("subfolder_scope") or "").strip().lower()
        if scope and subfolder_slug is not None and subfolder_slug.strip().lower() != scope:
            raise PermissionError("Forbidden: access denied outside assigned subfolder scope.")

    def _sanitize_tender_for_user(
        self, row: dict[str, Any], caller_user: dict[str, Any] | None
    ) -> dict[str, Any]:
        out = dict(row)
        out["crm_tender_id"] = out["id"]
        is_vendor = bool(caller_user and caller_user.get("role") == "vendor")
        is_conf = bool(out.get("confidential_mode", 1))

        if is_vendor and is_conf:
            out["emd_amount"] = None
            out["est_value_inr"] = None
            out["internal_notes"] = ""
            out["metadata_json"] = "{}"
            out["analysis"] = None
        else:
            raw_meta = out.get("metadata_json") or "{}"
            try:
                out["analysis"] = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            except ValueError:
                out["analysis"] = {}
        return out

    # ------------------------------------------------------------------
    # Bootstrap, Companies & Subfolders
    # ------------------------------------------------------------------

    def get_bootstrap(self, user: dict[str, Any] | None) -> dict[str, Any]:
        self.ensure_initialized()
        is_admin = self._is_admin(user)
        user_cid = (user or {}).get("company_id")
        scope = ((user or {}).get("subfolder_scope") or "").strip().lower()

        if is_admin:
            companies = self.repo.list_companies()
            subfolders = self.repo.list_subfolders()
            counts = self.repo.counts_summary()
            assignments_by_bid = self.repo.list_assignments_map()
        elif user_cid:
            companies = self.repo.list_companies(company_id=user_cid)
            raw_sfs = self.repo.list_subfolders(company_id=user_cid)
            subfolders = [s for s in raw_sfs if not scope or s["slug"].lower() == scope]
            counts = self.repo.counts_summary(company_id=user_cid, subfolder_scope=scope or None)
            assignments_by_bid = self.repo.list_assignments_map(
                company_id=user_cid, subfolder_scope=scope or None
            )
        else:
            companies = []
            subfolders = []
            counts = {"by_company": {}, "by_subfolder": {}, "total": 0}
            assignments_by_bid = {}

        sf_by_company: dict[str, list[dict[str, Any]]] = {}
        for sf in subfolders:
            sf_copy = {**sf, "tender_count": counts["by_subfolder"].get(sf["id"], 0)}
            sf_by_company.setdefault(sf["company_id"], []).append(sf_copy)

        enriched_companies: list[dict[str, Any]] = []
        for comp in companies:
            c_copy = dict(comp)
            if not is_admin and c_copy.get("confidential_mode"):
                c_copy["google_sheet_id"] = ""
                c_copy["google_sheet_url"] = ""
            c_copy["tender_count"] = counts["by_company"].get(c_copy["id"], 0)
            c_copy["subfolders"] = sf_by_company.get(c_copy["id"], [])
            enriched_companies.append(c_copy)

        enriched_subfolders = [
            {**sf, "tender_count": counts["by_subfolder"].get(sf["id"], 0)}
            for sf in subfolders
        ]

        return {
            "user": user,
            "companies": enriched_companies,
            "subfolders": enriched_subfolders,
            "counts": counts,
            "assignments_by_bid": assignments_by_bid,
            "stages": list(VALID_STAGES),
            "decisions": list(VALID_DECISIONS),
        }

    def create_or_update_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Company payload must be a JSON object.")
        self.ensure_initialized(run_migration=False)

        raw_id = str(payload.get("id") or "").strip()
        raw_name = str(payload.get("name") or "").strip()
        existing = self.repo.get_company(slugify(raw_id)) if raw_id else None
        if not raw_name and not existing:
            raise ValueError("Company name is required.")

        cid = existing["id"] if existing else slugify(raw_id or raw_name)
        ctype = str(
            payload.get("type")
            or payload.get("company_type")
            or (existing or {}).get("type")
            or "vendor"
        ).strip().lower()
        if ctype not in VALID_COMPANY_TYPES:
            raise ValueError(f"Invalid company type '{ctype}'.")

        raw_disk = str(
            payload.get("disk_folder") or (existing or {}).get("disk_folder") or cid
        ).strip()
        safe_disk = _validate_safe_folder_segment(raw_disk, "disk_folder")
        company_dir = self._safe_tenders_join(safe_disk)
        os.makedirs(company_dir, exist_ok=True)

        color = str(payload.get("color") or (existing or {}).get("color") or "#E2E8F0").strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError("Color must be a 6-digit hex code such as #BFDBFE.")

        upsert_data: dict[str, Any] = {
            "id": cid,
            "name": raw_name or existing["name"],
            "type": ctype,
            "disk_folder": safe_disk,
            "color": color.upper(),
        }
        for opt_key in (
            "category",
            "google_sheet_id",
            "google_sheet_url",
            "vendor_sheet_key",
            "confidential_mode",
            "auto_sync_master",
            "is_active",
        ):
            if opt_key in payload:
                upsert_data[opt_key] = payload[opt_key]

        company = self.repo.upsert_company(upsert_data)
        sfs = self.repo.list_subfolders(company["id"])
        if not sfs:
            default_sf = self.repo.ensure_subfolder(company["id"], "General", slug="general")
            os.makedirs(self._safe_tenders_join(company["disk_folder"], default_sf["slug"]), exist_ok=True)
            sfs = [default_sf]

        # Keep MasterSheetManager's in-memory vendor_sheets aware of custom/updated vendor sheets
        vkey = company.get("vendor_sheet_key") or (company["id"] if company["type"] == "vendor" else "")
        if vkey and (company.get("google_sheet_id") or company.get("google_sheet_url")):
            with master_sheet_manager.lock:
                vs = master_sheet_manager.config.setdefault("vendor_sheets", {})
                vs[vkey] = {
                    **(vs.get(vkey) or {}),
                    "name": company["name"],
                    "category": company.get("category") or company["name"],
                    "spreadsheet_id": company.get("google_sheet_id") or extract_sheet_id(company.get("google_sheet_url") or ""),
                    "spreadsheet_url": company.get("google_sheet_url") or "",
                    "color": company["color"],
                }

        company["subfolders"] = sfs
        return company

    def create_subfolder(
        self, company_id: str, name: str, parent_id: int | None = None
    ) -> dict[str, Any]:
        self.ensure_initialized(run_migration=False)
        company = self.repo.get_company(company_id)
        if not company:
            raise ValueError(f"Unknown company_id: {company_id}")

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Subfolder name is required.")
        sf_slug = slugify(clean_name)
        _validate_safe_folder_segment(sf_slug, "subfolder")

        if parent_id is not None:
            parent_sf = self.repo.get_subfolder(int(parent_id))
            if not parent_sf or parent_sf["company_id"] != company_id:
                raise ValueError("Invalid parent_id for company.")

        sf_abs = self._safe_tenders_join(company["disk_folder"], sf_slug)
        os.makedirs(sf_abs, exist_ok=True)
        return self.repo.ensure_subfolder(company_id, clean_name, slug=sf_slug, parent_id=parent_id)

    # ------------------------------------------------------------------
    # Listing & Inspecting Tenders
    # ------------------------------------------------------------------

    def list_tenders(
        self,
        caller_user: dict[str, Any],
        company_id: str | None = None,
        subfolder_id: int | None = None,
        subfolder_slug: str | None = None,
        q: str = "",
        stage: str = "",
        vendor_decision: str = "",
    ) -> list[dict[str, Any]]:
        self.ensure_initialized()
        if not caller_user:
            raise PermissionError("Forbidden: authentication required.")

        if not self._is_admin(caller_user):
            user_cid = (caller_user.get("company_id") or "").strip()
            if not user_cid:
                raise PermissionError("Forbidden: user has no assigned company.")
            if company_id and company_id != user_cid:
                raise PermissionError("Forbidden: access denied to another company's tenders.")
            company_id = user_cid

            scope = (caller_user.get("subfolder_scope") or "").strip().lower()
            if subfolder_id is not None:
                sf = self.repo.get_subfolder(int(subfolder_id))
                if not sf or sf["company_id"] != user_cid:
                    raise PermissionError("Forbidden: subfolder belongs to another company.")
                if scope and sf["slug"].lower() != scope:
                    raise PermissionError("Forbidden: access denied outside subfolder scope.")

            if scope:
                if subfolder_slug and subfolder_slug.strip().lower() != scope:
                    raise PermissionError("Forbidden: access denied outside subfolder scope.")
                subfolder_slug = scope

        rows = self.repo.list_crm_tenders(
            company_id=company_id,
            subfolder_id=subfolder_id,
            subfolder_slug=subfolder_slug,
            q=q,
            stage=stage,
            vendor_decision=vendor_decision,
        )
        return [self._sanitize_tender_for_user(r, caller_user) for r in rows]

    def _get_scraped_record(self, bid_no: str) -> tuple[dict[str, Any] | None, str | None]:
        """Look up a scraped tender in TENDERS_DIR (and optional personel workspace)."""
        if not bid_no:
            return None, None
        troot = self._tenders_root()
        candidates = [troot, os.path.join(troot, "personel")]
        for ws_dir in candidates:
            if not os.path.isdir(ws_dir):
                continue
            if not any(
                os.path.exists(os.path.join(ws_dir, fn))
                for fn in ("metadata.db", "metadata.json", "metadata.csv")
            ):
                continue
            try:
                rec = storage.get_record(bid_no, tenders_dir=ws_dir)
                if rec is not None:
                    return rec, ws_dir
            except Exception:
                continue
        return None, None

    def _resolve_scraped_pdf_abs(self, scraped: dict[str, Any] | None, bid_no: str) -> str | None:
        troot = self._tenders_root()
        parent_root = os.path.dirname(troot)
        if scraped and scraped.get("local_pdf_path"):
            lpath = str(scraped["local_pdf_path"])
            for base_dir in (paths.ROOT, parent_root, troot):
                cand = lpath if os.path.isabs(lpath) else os.path.join(base_dir, lpath)
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
            stripped = re.sub(r"^tenders[/\\]+", "", lpath.strip("/\\"), flags=re.IGNORECASE)
            cand_rel = os.path.join(troot, stripped)
            if os.path.isfile(cand_rel):
                return os.path.abspath(cand_rel)

        sanitized = re.sub(r'[\\/*?:"<>|]', "_", bid_no)
        for dl_dir in (
            os.path.join(troot, "downloads"),
            os.path.join(troot, "personel", "downloads"),
        ):
            if not os.path.isdir(dl_dir):
                continue
            found = storage.find_existing_pdf_file(sanitized, downloads_dir=dl_dir)
            if found:
                for base_dir in (paths.ROOT, parent_root, troot):
                    cand = found if os.path.isabs(found) else os.path.join(base_dir, found)
                    if os.path.isfile(cand):
                        return os.path.abspath(cand)
        return None

    def inspect_tender(
        self, bid_no: str, caller_user: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.ensure_initialized()
        if not caller_user:
            raise PermissionError("Forbidden: authentication required.")
        if not bid_no or not str(bid_no).strip():
            return None

        clean_bid = str(bid_no).strip()
        is_admin = self._is_admin(caller_user)
        assignments = self.repo.get_assignments_for_bid(clean_bid)

        if not is_admin:
            user_cid = (caller_user.get("company_id") or "").strip()
            scope = (caller_user.get("subfolder_scope") or "").strip().lower()
            visible_assignments = [
                a for a in assignments
                if a["company_id"] == user_cid
                and (not scope or (a.get("subfolder_slug") or "").lower() == scope)
            ]
            if not visible_assignments:
                raise PermissionError("Forbidden: tender is not assigned to your company/folder.")
            assignments = visible_assignments

        scraped, _ws_dir = self._get_scraped_record(clean_bid) if is_admin else (None, None)
        if scraped is None and is_admin and assignments:
            scraped, _ws_dir = self._get_scraped_record(assignments[0]["bid_no"])

        if scraped is None and not assignments:
            return None

        sanitized_assignments = [self._sanitize_tender_for_user(a, caller_user) for a in assignments]
        primary_crm = sanitized_assignments[0] if sanitized_assignments else None

        # Collect CRM files and find best local PDF for inline preview & embedded link extraction
        combined_files: list[dict[str, Any]] = []
        seen_file_ids: set[int] = set()
        for a in sanitized_assignments:
            for f in a.get("files") or []:
                if f["id"] not in seen_file_ids:
                    seen_file_ids.add(f["id"])
                    combined_files.append(f)

        abs_pdf: str | None = None
        inline_pdf_url = ""
        for f in combined_files:
            if f["filename"].lower().endswith(".pdf") and f["file_kind"] == "rfp":
                try:
                    cand = self._safe_tenders_join(*f["rel_path"].split("/"))
                    if os.path.isfile(cand):
                        abs_pdf = cand
                        inline_pdf_url = f["inline_url"]
                        break
                except ValueError:
                    continue

        if abs_pdf is None and is_admin:
            abs_pdf = self._resolve_scraped_pdf_abs(scraped, clean_bid)
            if abs_pdf:
                try:
                    rel_to_tenders = self._tenders_rel(abs_pdf)
                    inline_pdf_url = f"/tenders/{rel_to_tenders}"
                except ValueError:
                    inline_pdf_url = ""

        embedded_annexures = extract_pdf_annexure_links(abs_pdf) if abs_pdf else []

        is_conf_vendor = bool(
            caller_user.get("role") == "vendor"
            and primary_crm
            and primary_crm.get("confidential_mode", 1)
        )
        analysis = (scraped or {}).get("analysis") if (is_admin and scraped) else (
            primary_crm.get("analysis") if primary_crm and not is_conf_vendor else None
        )
        fin_rec = master_sheet_manager.get_record(clean_bid) if is_admin else None

        canonical_bid = (
            (primary_crm or {}).get("bid_no")
            or (scraped or {}).get("bid_no")
            or clean_bid
        )
        title = (
            (primary_crm or {}).get("title")
            or (scraped or {}).get("title")
            or ((analysis or {}).get("primary_item") if isinstance(analysis, dict) else None)
            or canonical_bid
        )
        dept = (
            (primary_crm or {}).get("department")
            or (scraped or {}).get("department")
            or ""
        )
        org = (
            (primary_crm or {}).get("organisation")
            or ((analysis or {}).get("buyer_org") if isinstance(analysis, dict) else None)
            or dept
        )

        return {
            "bid_no": canonical_bid,
            "title": title,
            "department": dept,
            "organisation": org,
            "location": (primary_crm or {}).get("location") or ((analysis or {}).get("consignee_state") if isinstance(analysis, dict) else "") or "",
            "work_category": (primary_crm or {}).get("work_category") or (((analysis or {}).get("business_line") or {}).get("label") if isinstance(analysis, dict) else "") or "",
            "start_date": (primary_crm or {}).get("start_date") or (scraped or {}).get("start_date") or "",
            "end_date": (primary_crm or {}).get("end_date") or (scraped or {}).get("end_date") or "",
            "emd_amount": None if is_conf_vendor else ((primary_crm or {}).get("emd_amount") if primary_crm else ((analysis or {}).get("emd_amount") if isinstance(analysis, dict) else None)),
            "est_value_inr": None if is_conf_vendor else ((primary_crm or {}).get("est_value_inr") if primary_crm else ((analysis or {}).get("est_value_inr") if isinstance(analysis, dict) else None)),
            "status": (scraped or {}).get("status") or ((primary_crm or {}).get("stage") or "Pending Review"),
            "pdf_url": "" if is_conf_vendor else ((primary_crm or {}).get("pdf_url") or (scraped or {}).get("pdf_url") or ""),
            "local_pdf_path": (scraped or {}).get("local_pdf_path") or ((primary_crm or {}).get("rfp_pdf_rel_path") or ""),
            "inline_pdf_url": inline_pdf_url,
            "local_pdf_url": inline_pdf_url,
            "analysis": None if is_conf_vendor else analysis,
            "crm_assignments": sanitized_assignments,
            "assignments": sanitized_assignments,
            "crm_tender": primary_crm,
            "crm_files": combined_files,
            "files": combined_files,
            "embedded_annexures": [] if is_conf_vendor else embedded_annexures,
            "finalized": fin_rec,
            "sl_no": (primary_crm or {}).get("sl_no") or (fin_rec or {}).get("sl_no"),
        }

    # ------------------------------------------------------------------
    # Move & Sync Pipeline
    # ------------------------------------------------------------------

    def _build_company_vendor_sheet_override(self, company: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if company["type"] != "vendor":
            return "none", {}
        vkey = (company.get("vendor_sheet_key") or company["id"]).strip()
        extra_vs: dict[str, Any] = {}
        sheet_id = (company.get("google_sheet_id") or "").strip()
        sheet_url = (company.get("google_sheet_url") or "").strip()
        if sheet_id or sheet_url or vkey not in ("drone", "power_supply", "biometrics"):
            extra_vs[vkey] = {
                "name": company["name"],
                "category": company.get("category") or company["name"],
                "spreadsheet_id": sheet_id or extract_sheet_id(sheet_url),
                "spreadsheet_url": sheet_url,
                "color": company.get("color") or "#E2E8F0",
            }
        return vkey, extra_vs

    def move_tenders_to_folder(
        self,
        bid_nos: list[str],
        company_id: str,
        subfolder_id: int | None = None,
        new_subfolder_name: str = "",
        fetch_gem_annexures: bool = True,
        sync_master_and_sheets: bool | None = None,
        target_sheet: str = "UNDER DETAILED STUDY",
        work_category: str = "",
        remarks: str = "",
    ) -> dict[str, Any]:
        self.ensure_initialized()
        if isinstance(bid_nos, str):
            bid_nos = [bid_nos]
        clean_bids = [str(b).strip() for b in (bid_nos or []) if str(b).strip()]
        if not clean_bids:
            raise ValueError("At least one bid_no is required.")

        company = self.repo.get_company(company_id)
        if not company:
            raise ValueError(f"Unknown company_id: {company_id}")

        if new_subfolder_name and new_subfolder_name.strip():
            subfolder = self.create_subfolder(company_id, new_subfolder_name.strip())
        elif subfolder_id is not None:
            subfolder = self.repo.get_subfolder(int(subfolder_id))
            if not subfolder or subfolder["company_id"] != company_id:
                raise ValueError("Invalid subfolder_id for target company.")
        else:
            sfs = self.repo.list_subfolders(company_id)
            subfolder = sfs[0] if sfs else self.create_subfolder(company_id, "General")

        os.makedirs(self._safe_tenders_join(company["disk_folder"], subfolder["slug"]), exist_ok=True)
        should_sync = (
            bool(company.get("auto_sync_master", 1))
            if sync_master_and_sheets is None
            else bool(sync_master_and_sheets)
        )

        moved_tenders: list[dict[str, Any]] = []
        warnings: list[str] = []

        for bid_no in clean_bids:
            scraped, scraped_ws_dir = self._get_scraped_record(bid_no)
            existing_assignments = self.repo.get_assignments_for_bid(bid_no)
            prev_same_company = next(
                (a for a in existing_assignments if a["company_id"] == company_id),
                None,
            )
            donor_assignment = prev_same_company or (existing_assignments[0] if existing_assignments else None)

            canonical_bid = (
                (scraped or {}).get("bid_no")
                or (donor_assignment or {}).get("bid_no")
                or bid_no
            )
            b_slug = safe_bid_slug(canonical_bid)
            dossier_abs = self._safe_tenders_join(company["disk_folder"], subfolder["slug"], b_slug)
            os.makedirs(dossier_abs, exist_ok=True)
            dossier_rel = self._tenders_rel(dossier_abs)

            # If moving between subfolders within the same company, carry over existing dossier files
            if prev_same_company and prev_same_company.get("dossier_rel_path"):
                try:
                    old_abs = self._safe_tenders_join(*prev_same_company["dossier_rel_path"].split("/"))
                    if os.path.isdir(old_abs) and os.path.abspath(old_abs) != os.path.abspath(dossier_abs):
                        for fname in os.listdir(old_abs):
                            src_f = os.path.join(old_abs, fname)
                            dst_f = os.path.join(dossier_abs, fname)
                            if os.path.isfile(src_f) and not os.path.exists(dst_f):
                                shutil.copy2(src_f, dst_f)
                except (ValueError, OSError):
                    pass

            # Copy primary RFP PDF into dossier
            src_pdf = self._resolve_scraped_pdf_abs(scraped, canonical_bid)
            if src_pdf is None and donor_assignment and donor_assignment.get("rfp_pdf_rel_path"):
                try:
                    cand = self._safe_tenders_join(*donor_assignment["rfp_pdf_rel_path"].split("/"))
                    if os.path.isfile(cand):
                        src_pdf = cand
                except ValueError:
                    pass

            rfp_dest_name = f"{b_slug}.pdf"
            rfp_dest_abs = os.path.join(dossier_abs, rfp_dest_name)
            rfp_rel_path = ""
            if src_pdf and os.path.isfile(src_pdf):
                if os.path.abspath(src_pdf) != os.path.abspath(rfp_dest_abs):
                    shutil.copy2(src_pdf, rfp_dest_abs)
                rfp_rel_path = self._tenders_rel(rfp_dest_abs)
            elif os.path.isfile(rfp_dest_abs):
                rfp_rel_path = self._tenders_rel(rfp_dest_abs)

            # Pin status in scraped workspace store if present
            pin_status = "Rejected" if subfolder["slug"] == "rejected" else "Shortlisted"
            stage_val = "rejected" if subfolder["slug"] == "rejected" else "shortlisted"
            if scraped_ws_dir:
                try:
                    storage.update_record(
                        canonical_bid,
                        {"status": pin_status, "status_source": "manual"},
                        tenders_dir=scraped_ws_dir,
                    )
                except Exception as exc:
                    logger.debug("Could not pin status in metadata.db for %s: %s", canonical_bid, exc)

            analysis = (scraped or {}).get("analysis") or {}
            title = (
                (scraped or {}).get("title")
                or (donor_assignment or {}).get("title")
                or analysis.get("primary_item")
                or canonical_bid
            )
            dept = (scraped or {}).get("department") or (donor_assignment or {}).get("department") or ""
            org = analysis.get("buyer_org") or (donor_assignment or {}).get("organisation") or dept
            loc = analysis.get("consignee_state") or (donor_assignment or {}).get("location") or ""
            wcat = (
                work_category.strip()
                or (analysis.get("business_line") or {}).get("label")
                or company.get("category")
                or (donor_assignment or {}).get("work_category")
                or "SUPPLY"
            )

            tender_row = self.repo.upsert_tender(
                {
                    "bid_no": canonical_bid,
                    "bid_slug": b_slug,
                    "company_id": company_id,
                    "subfolder_id": subfolder["id"],
                    "title": title,
                    "organisation": org,
                    "department": dept,
                    "location": loc,
                    "work_category": wcat,
                    "source_id": (scraped or {}).get("source_id") or "gem",
                    "source_name": (scraped or {}).get("source_name") or "GEM",
                    "start_date": (scraped or {}).get("start_date") or "",
                    "end_date": (scraped or {}).get("end_date") or (donor_assignment or {}).get("end_date") or "",
                    "emd_amount": analysis.get("emd_amount"),
                    "est_value_inr": analysis.get("est_value_inr") or (scraped or {}).get("est_value_inr"),
                    "dossier_rel_path": dossier_rel,
                    "rfp_pdf_rel_path": rfp_rel_path,
                    "pdf_url": (scraped or {}).get("pdf_url") or (donor_assignment or {}).get("pdf_url") or "",
                    "stage": stage_val,
                    "internal_notes": remarks or (prev_same_company or {}).get("internal_notes") or "",
                    "metadata_json": json.dumps(analysis, ensure_ascii=False) if analysis else "{}",
                }
            )

            # Register all files currently in dossier_abs (including RFP)
            if os.path.isfile(rfp_dest_abs):
                self.repo.upsert_file(
                    {
                        "crm_tender_id": tender_row["id"],
                        "filename": rfp_dest_name,
                        "original_filename": rfp_dest_name,
                        "rel_path": rfp_rel_path,
                        "file_kind": "rfp",
                        "mime_type": "application/pdf",
                        "size_bytes": os.path.getsize(rfp_dest_abs),
                        "sha256": _sha256_file(rfp_dest_abs),
                        "source_url": (scraped or {}).get("pdf_url") or "",
                    }
                )

            # Fetch embedded GeM annexures when requested
            if fetch_gem_annexures and os.path.isfile(rfp_dest_abs):
                links = extract_pdf_annexure_links(rfp_dest_abs)
                if links:
                    saved_annexures, dl_warnings = download_gem_annexures(links, dossier_abs)
                    warnings.extend(dl_warnings)
                    for sf_item in saved_annexures:
                        self.repo.upsert_file(
                            {
                                "crm_tender_id": tender_row["id"],
                                "filename": sf_item["filename"],
                                "original_filename": sf_item["original_filename"],
                                "rel_path": self._tenders_rel(sf_item["abs_path"]),
                                "file_kind": sf_item["file_kind"],
                                "mime_type": sf_item["mime_type"],
                                "size_bytes": sf_item["size_bytes"],
                                "sha256": sf_item["sha256"],
                                "source_url": sf_item["source_url"],
                            }
                        )
                self.repo.update_crm_tender_fields(tender_row["id"], {"annexures_fetched": 1})

            # Sync with Master Sheet & Vendor Google Sheets if enabled
            if should_sync and subfolder["slug"] != "rejected":
                files_now = self.repo.list_files_for_tender(tender_row["id"])
                spec_file = next((f for f in files_now if f["file_kind"] == "tech_spec"), None)
                spec_abs = (
                    self._safe_tenders_join(*spec_file["rel_path"].split("/"))
                    if spec_file
                    else None
                )
                assigned_vkey, extra_vs = self._build_company_vendor_sheet_override(company)

                tender_payload = {
                    **(scraped or {}),
                    "bid_no": canonical_bid,
                    "title": title,
                    "department": dept,
                    "organisation": org,
                    "location": loc,
                    "end_date": tender_row["end_date"],
                    "local_pdf_path": rfp_dest_abs if os.path.isfile(rfp_dest_abs) else "",
                    "pdf_url": tender_row["pdf_url"],
                    "analysis": analysis,
                }
                custom_fields: dict[str, Any] = {
                    "assigned_vendor": assigned_vkey,
                    "work_category": wcat,
                    "remarks": remarks,
                    "extra_vendor_sheets": extra_vs,
                }
                if company["type"] == "vendor" and company.get("color"):
                    custom_fields["vendor_color"] = company["color"]
                if spec_abs and os.path.isfile(spec_abs):
                    custom_fields["tech_spec_local_path"] = spec_abs
                    custom_fields["tech_spec_filename"] = spec_file["filename"]  # type: ignore[index]

                try:
                    fin_res = master_sheet_manager.finalize_tender(
                        tender=tender_payload,
                        target_sheet=target_sheet or "UNDER DETAILED STUDY",
                        custom_fields=custom_fields,
                    )
                    fin_rec = fin_res.get("record") or {}
                    self.repo.update_crm_tender_fields(
                        tender_row["id"],
                        {
                            "sl_no": fin_res.get("sl_no"),
                            "drive_link": fin_rec.get("rfp_link") or "",
                            "tech_spec_url": fin_rec.get("tech_spec_url") or "",
                            "master_synced": 1,
                            "gsheet_synced": 1 if fin_res.get("google_sheet_synced") else 0,
                        },
                    )
                except Exception as exc:
                    warnings.append(f"Master sheet sync warning for {canonical_bid}: {exc}")

            refreshed = self.repo.get_crm_tender(tender_row["id"])
            if refreshed:
                moved_tenders.append(refreshed)

        excel_abs = self._generate_company_excel_internal(company_id)
        return {
            "status": "ok",
            "company_id": company_id,
            "subfolder": subfolder,
            "moved_count": len(moved_tenders),
            "tenders": moved_tenders,
            "warnings": warnings,
            "excel_path": paths.repo_relative(excel_abs) if excel_abs else None,
        }

    def fetch_annexures_for_tender(self, crm_tender_id: int) -> dict[str, Any]:
        self.ensure_initialized(run_migration=False)
        tender = self.repo.get_crm_tender(crm_tender_id)
        if not tender:
            raise LookupError(f"CRM tender {crm_tender_id} not found.")

        company = self.repo.get_company(tender["company_id"])
        if not company:
            raise ValueError("Company record missing for tender.")
        sf_slug = tender.get("subfolder_slug") or "general"

        dossier_abs = (
            self._safe_tenders_join(*tender["dossier_rel_path"].split("/"))
            if tender.get("dossier_rel_path")
            else self._safe_tenders_join(company["disk_folder"], sf_slug, tender["bid_slug"])
        )
        os.makedirs(dossier_abs, exist_ok=True)

        rfp_abs: str | None = None
        if tender.get("rfp_pdf_rel_path"):
            cand = self._safe_tenders_join(*tender["rfp_pdf_rel_path"].split("/"))
            if os.path.isfile(cand):
                rfp_abs = cand
        if rfp_abs is None:
            for f in tender.get("files") or []:
                if f["filename"].lower().endswith(".pdf"):
                    cand = self._safe_tenders_join(*f["rel_path"].split("/"))
                    if os.path.isfile(cand):
                        rfp_abs = cand
                        break
        if rfp_abs is None:
            scraped_rec, _ws = self._get_scraped_record(tender["bid_no"])
            rfp_abs = self._resolve_scraped_pdf_abs(scraped_rec, tender["bid_no"])

        if not rfp_abs or not os.path.isfile(rfp_abs):
            return {
                "status": "ok",
                "crm_tender_id": int(crm_tender_id),
                "links_found": 0,
                "saved_count": 0,
                "files": self.repo.list_files_for_tender(crm_tender_id),
                "warnings": ["Primary RFP PDF not found on disk; cannot extract embedded GeM links."],
                "tender": self.repo.get_crm_tender(crm_tender_id),
            }

        links = extract_pdf_annexure_links(rfp_abs)
        saved, warnings = download_gem_annexures(links, dossier_abs)
        new_spec_item: dict[str, Any] | None = None

        for item in saved:
            self.repo.upsert_file(
                {
                    "crm_tender_id": int(crm_tender_id),
                    "filename": item["filename"],
                    "original_filename": item["original_filename"],
                    "rel_path": self._tenders_rel(item["abs_path"]),
                    "file_kind": item["file_kind"],
                    "mime_type": item["mime_type"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                    "source_url": item["source_url"],
                }
            )
            if item["file_kind"] == "tech_spec" and new_spec_item is None:
                new_spec_item = item

        updates: dict[str, Any] = {"annexures_fetched": 1}
        if new_spec_item and master_sheet_manager.is_tender_finalized(tender["bid_no"]) and not tender.get("tech_spec_url"):
            try:
                with open(new_spec_item["abs_path"], "rb") as fh:
                    spec_bytes = fh.read()
                spec_res = master_sheet_manager.update_tech_spec(
                    bid_no=tender["bid_no"],
                    filename=new_spec_item["filename"],
                    file_bytes=spec_bytes,
                )
                if spec_res.get("tech_spec_url"):
                    updates["tech_spec_url"] = spec_res["tech_spec_url"]
            except Exception as exc:
                warnings.append(f"Tech spec sync warning: {exc}")

        updated_tender = self.repo.update_crm_tender_fields(crm_tender_id, updates)
        return {
            "status": "ok",
            "crm_tender_id": int(crm_tender_id),
            "links_found": len(links),
            "saved_count": len(saved),
            "saved_files": saved,
            "files": self.repo.list_files_for_tender(crm_tender_id),
            "warnings": warnings,
            "tender": updated_tender,
        }

    # ------------------------------------------------------------------
    # Uploads, Decisions & Removal
    # ------------------------------------------------------------------

    def attach_uploaded_file(
        self,
        crm_tender_id: int,
        filename: str,
        file_bytes: bytes,
        file_kind: str = "tech_spec",
    ) -> dict[str, Any]:
        self.ensure_initialized(run_migration=False)
        clean_kind = (file_kind or "tech_spec").strip().lower()
        if clean_kind not in ALLOWED_FILE_KINDS:
            raise ValueError(f"Unsupported file_kind '{file_kind}'.")

        orig_name, ext, mime = validate_upload_payload(filename, file_bytes)

        tender = self.repo.get_crm_tender(crm_tender_id)
        if not tender:
            raise LookupError(f"CRM tender {crm_tender_id} not found.")
        company = self.repo.get_company(tender["company_id"])
        if not company:
            raise ValueError("Company missing for tender.")

        sf_slug = tender.get("subfolder_slug") or "general"
        dossier_abs = (
            self._safe_tenders_join(*tender["dossier_rel_path"].split("/"))
            if tender.get("dossier_rel_path")
            else self._safe_tenders_join(company["disk_folder"], sf_slug, tender["bid_slug"])
        )
        os.makedirs(dossier_abs, exist_ok=True)

        raw = bytes(file_bytes)
        sha_hex = _sha256_bytes(raw)
        safe_base = sanitize_filename(orig_name, fallback=f"upload{ext}")
        stem, _ = os.path.splitext(safe_base)
        stored_name = f"{stem[:120]}_{sha_hex[:8]}{ext}"
        dest_abs = os.path.abspath(os.path.join(dossier_abs, stored_name))
        if os.path.commonpath([dossier_abs, dest_abs]) != dossier_abs:
            raise ValueError("Unsafe upload destination path.")

        tmp_abs = f"{dest_abs}.{os.getpid()}.tmp"
        with open(tmp_abs, "wb") as fh:
            fh.write(raw)
        os.replace(tmp_abs, dest_abs)

        file_row = self.repo.upsert_file(
            {
                "crm_tender_id": int(crm_tender_id),
                "filename": stored_name,
                "original_filename": orig_name,
                "rel_path": self._tenders_rel(dest_abs),
                "file_kind": clean_kind,
                "mime_type": mime,
                "size_bytes": len(raw),
                "sha256": sha_hex,
            }
        )

        if clean_kind == "tech_spec":
            spec_url = file_row["inline_url"]
            if master_sheet_manager.is_tender_finalized(tender["bid_no"]):
                spec_res = master_sheet_manager.update_tech_spec(
                    bid_no=tender["bid_no"],
                    filename=orig_name,
                    file_bytes=raw,
                )
                if spec_res.get("tech_spec_url"):
                    spec_url = str(spec_res["tech_spec_url"])
            self.repo.update_crm_tender_fields(crm_tender_id, {"tech_spec_url": spec_url})

        self._generate_company_excel_internal(tender["company_id"])
        return {
            "status": "ok",
            "file": file_row,
            "tender": self.repo.get_crm_tender(crm_tender_id),
        }

    def update_decision(
        self,
        crm_tender_id: int,
        caller_user: dict[str, Any],
        vendor_decision: str | None = None,
        vendor_remarks: str | None = None,
        stage: str | None = None,
        internal_notes: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_initialized(run_migration=False)
        tender = self.repo.get_crm_tender(crm_tender_id)
        if not tender:
            raise LookupError(f"CRM tender {crm_tender_id} not found.")

        self._check_company_access(
            caller_user,
            tender["company_id"],
            tender.get("subfolder_slug"),
        )

        updates: dict[str, Any] = {}
        if vendor_decision is not None:
            norm_dec = _normalize_vendor_decision(vendor_decision)
            updates["vendor_decision"] = norm_dec
            if stage is None and norm_dec == "YES" and tender.get("stage") in ("review", "shortlisted"):
                updates["stage"] = "participating"
            elif stage is None and norm_dec == "NO" and tender.get("stage") == "review":
                updates["stage"] = "rejected"

        if vendor_remarks is not None:
            updates["vendor_remarks"] = str(vendor_remarks).strip()[:2000]

        if stage is not None and str(stage).strip():
            # Vendors cannot override admin commercial won/lost stages arbitrarily unless allowed
            updates["stage"] = _normalize_stage(stage)

        if internal_notes is not None and caller_user.get("role") != "vendor":
            updates["internal_notes"] = str(internal_notes).strip()[:4000]

        updated = self.repo.update_crm_tender_fields(crm_tender_id, updates)
        if not updated:
            raise LookupError(f"CRM tender {crm_tender_id} not found.")

        self._generate_company_excel_internal(tender["company_id"])
        sanitized = self._sanitize_tender_for_user(updated, caller_user)
        return {**sanitized, "tender": dict(sanitized)}

    def remove_tender_assignment(self, crm_tender_id: int) -> bool:
        self.ensure_initialized(run_migration=False)
        tender = self.repo.get_crm_tender(crm_tender_id)
        if not tender:
            return False
        deleted = self.repo.delete_crm_tender(crm_tender_id)
        if deleted:
            self._generate_company_excel_internal(tender["company_id"])
        return deleted

    # ------------------------------------------------------------------
    # Authorized File Serving, CSV Preview & Company Excel Export
    # ------------------------------------------------------------------

    def get_authorized_file(
        self, file_id: int, caller_user: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        self.ensure_initialized(run_migration=False)
        if not caller_user:
            return None, None, "Forbidden: authentication required."

        file_row = self.repo.get_file(file_id)
        if not file_row:
            return None, None, "File not found."

        try:
            self._check_company_access(
                caller_user,
                file_row["company_id"],
                file_row.get("subfolder_slug"),
            )
        except PermissionError as exc:
            return None, None, str(exc)

        try:
            abs_path = self._safe_tenders_join(*file_row["rel_path"].split("/"))
            if not self._is_admin(caller_user):
                company_root = self._safe_tenders_join(file_row["disk_folder"])
                if os.path.commonpath([company_root, abs_path]) != company_root:
                    return None, None, "Forbidden: file path outside company boundary."
        except ValueError:
            return None, None, "File not found."

        if not os.path.isfile(abs_path):
            return None, None, "File not found on disk."

        return file_row, abs_path, None

    def preview_csv_file(
        self, file_id: int, caller_user: dict[str, Any], max_rows: int = 100
    ) -> tuple[dict[str, Any] | None, str | None]:
        file_row, abs_path, err = self.get_authorized_file(file_id, caller_user)
        if err or not file_row or not abs_path:
            return None, err or "File not found."

        if not abs_path.lower().endswith(".csv"):
            return None, "CSV preview is only available for .csv files."

        max_rows = max(1, min(int(max_rows or 100), 500))
        try:
            with open(abs_path, "rb") as fh:
                raw = fh.read(MAX_CSV_PREVIEW_BYTES)
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")

            reader = csv.reader(io.StringIO(text))
            headers: list[str] = []
            rows: list[list[str]] = []
            total_data_rows = 0

            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue
                if not headers:
                    headers = [cell.strip() for cell in row]
                    continue
                total_data_rows += 1
                if len(rows) < max_rows:
                    rows.append(list(row))

            return (
                {
                    "file_id": int(file_id),
                    "filename": file_row.get("original_filename") or file_row["filename"],
                    "headers": headers,
                    "rows": rows,
                    "total_rows": total_data_rows,
                    "truncated": total_data_rows > max_rows,
                },
                None,
            )
        except OSError as exc:
            return None, f"Could not read CSV file: {exc}"

    def _generate_company_excel_internal(
        self,
        company_id: str,
        *,
        subfolder_id: int | None = None,
        subfolder_slug: str | None = None,
        confidential_view: bool = False,
    ) -> str | None:
        company = self.repo.get_company(company_id)
        if not company:
            return None

        company_dir = self._safe_tenders_join(company["disk_folder"])
        os.makedirs(company_dir, exist_ok=True)

        rows = self.repo.list_crm_tenders(
            company_id=company_id,
            subfolder_id=subfolder_id,
            subfolder_slug=subfolder_slug,
        )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Project Requirements" if confidential_view else "CRM Tracker"

        header_fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        border = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="thin", color="CBD5E1"),
        )
        tint_hex = (company.get("color") or "").lstrip("#").upper()
        row_fill = (
            PatternFill(start_color=tint_hex, end_color=tint_hex, fill_type="solid")
            if len(tint_hex) == 6 and tint_hex != "FFFFFF"
            else None
        )

        if confidential_view:
            columns = [
                ("SL. NO", 10),
                ("FOLDER", 18),
                ("ITEM / WORK DESCRIPTION", 52),
                ("CATEGORY", 24),
                ("END DATE", 18),
                ("FILES", 12),
                ("PARTICIPATE OR NOT", 20),
                ("REMARKS", 34),
            ]
        else:
            columns = [
                ("SL. NO", 10),
                ("SUBFOLDER", 18),
                ("BID NUMBER", 24),
                ("TITLE / DESCRIPTION", 48),
                ("ORGANISATION", 32),
                ("LOCATION", 18),
                ("WORK CATEGORY", 22),
                ("END DATE", 18),
                ("EMD (INR)", 15),
                ("EST. VALUE (INR)", 18),
                ("STAGE", 15),
                ("VENDOR DECISION", 18),
                ("VENDOR REMARKS", 28),
                ("FILES", 10),
                ("TECH SPEC LINK", 24),
                ("RFP / DRIVE LINK", 24),
                ("INTERNAL NOTES", 28),
            ]

        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        banner = ws.cell(
            row=1,
            column=1,
            value=(
                f"{company['name']} — Technical Review Tracker"
                if confidential_view
                else f"{company['name']} — GeMSentry CRM Folder Tracker ({datetime.date.today().isoformat()})"
            ),
        )
        banner.fill = header_fill
        banner.font = Font(name="Arial", size=12, bold=True, color="FFFFFF")
        banner.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 32

        ws.row_dimensions[2].height = 28
        for idx, (col_title, width) in enumerate(columns, start=1):
            c = ws.cell(row=2, column=idx, value=col_title)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = border
            ws.column_dimensions[get_column_letter(idx)].width = width
        ws.freeze_panes = "A3"

        for r_idx, item in enumerate(rows, start=3):
            ws.row_dimensions[r_idx].height = 30
            if confidential_view:
                vals = [
                    item.get("sl_no") or (r_idx - 2),
                    item.get("subfolder_name") or item.get("subfolder_slug") or "",
                    item.get("title") or "",
                    item.get("work_category") or "",
                    item.get("end_date") or "",
                    len(item.get("files") or []),
                    item.get("vendor_decision") or "pending",
                    item.get("vendor_remarks") or "",
                ]
            else:
                vals = [
                    item.get("sl_no") or "",
                    item.get("subfolder_name") or item.get("subfolder_slug") or "",
                    item.get("bid_no") or "",
                    item.get("title") or "",
                    item.get("organisation") or item.get("department") or "",
                    item.get("location") or "",
                    item.get("work_category") or "",
                    item.get("end_date") or "",
                    item.get("emd_amount"),
                    item.get("est_value_inr"),
                    item.get("stage") or "review",
                    item.get("vendor_decision") or "pending",
                    item.get("vendor_remarks") or "",
                    len(item.get("files") or []),
                    item.get("tech_spec_url") or "",
                    item.get("drive_link") or item.get("pdf_url") or "",
                    item.get("internal_notes") or "",
                ]

            for c_idx, v in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=v)
                cell.font = Font(name="Arial", size=10)
                cell.border = border
                if row_fill:
                    cell.fill = row_fill
                cell.alignment = Alignment(
                    vertical="center",
                    horizontal="left" if c_idx in (3, 4, 5, 8, 13, 17) else "center",
                    wrap_text=True,
                )
                if not confidential_view and c_idx in (9, 10) and isinstance(v, (int, float)):
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(vertical="center", horizontal="right")

        suffix_parts = [company_id]
        if subfolder_id is not None:
            suffix_parts.append(f"sf{subfolder_id}")
        elif subfolder_slug:
            suffix_parts.append(slugify(subfolder_slug))
        prefix = "Vendor_Requirements" if confidential_view else "CRM_Tracker"
        out_name = f"{prefix}_{'_'.join(suffix_parts)}.xlsx"
        out_abs = self._safe_tenders_join(company["disk_folder"], out_name)

        try:
            wb.save(out_abs)
        except PermissionError:
            logger.warning("Company workbook %s is open in another program; skipping refresh.", out_abs)
        finally:
            wb.close()
        return out_abs

    def export_company_excel(
        self,
        company_id: str,
        caller_user: dict[str, Any],
        subfolder_id: int | None = None,
    ) -> str:
        self.ensure_initialized()
        company = self.repo.get_company(company_id)
        if not company:
            raise LookupError(f"Unknown company_id: {company_id}")

        scope_slug: str | None = None
        if not self._is_admin(caller_user):
            self._check_company_access(caller_user, company_id)
            scope = (caller_user.get("subfolder_scope") or "").strip().lower()
            if subfolder_id is not None:
                sf = self.repo.get_subfolder(int(subfolder_id))
                if not sf or sf["company_id"] != company_id:
                    raise PermissionError("Forbidden: subfolder belongs to another company.")
                if scope and sf["slug"].lower() != scope:
                    raise PermissionError("Forbidden: access denied outside subfolder scope.")
            elif scope:
                scope_slug = scope
        elif subfolder_id is not None:
            sf = self.repo.get_subfolder(int(subfolder_id))
            if not sf or sf["company_id"] != company_id:
                raise ValueError("Invalid subfolder_id for company.")

        confidential_view = bool(
            caller_user.get("role") == "vendor" and company.get("confidential_mode", 1)
        )
        out_abs = self._generate_company_excel_internal(
            company_id,
            subfolder_id=subfolder_id,
            subfolder_slug=scope_slug,
            confidential_view=confidential_view,
        )
        if not out_abs or not os.path.isfile(out_abs):
            raise RuntimeError("Failed generating company Excel export.")
        return out_abs

"""SQLite repository for GeMSentry Multi-Company CRM & RBAC."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any

import paths

logger = logging.getLogger("gemsentry.crm.repository")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL DEFAULT 'vendor',
    disk_folder       TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT '',
    color             TEXT NOT NULL DEFAULT '#E2E8F0',
    google_sheet_id   TEXT NOT NULL DEFAULT '',
    google_sheet_url  TEXT NOT NULL DEFAULT '',
    vendor_sheet_key  TEXT NOT NULL DEFAULT '',
    confidential_mode INTEGER NOT NULL DEFAULT 1,
    auto_sync_master  INTEGER NOT NULL DEFAULT 1,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subfolders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    parent_id  INTEGER REFERENCES subfolders(id) ON DELETE SET NULL,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(company_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_subfolders_company ON subfolders(company_id);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name    TEXT NOT NULL DEFAULT '',
    password_hash   TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'vendor',
    company_id      TEXT REFERENCES companies(id) ON DELETE SET NULL,
    subfolder_scope TEXT NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS crm_tenders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_no             TEXT NOT NULL,
    bid_slug           TEXT NOT NULL,
    company_id         TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    subfolder_id       INTEGER REFERENCES subfolders(id) ON DELETE SET NULL,
    sl_no              INTEGER,
    title              TEXT NOT NULL DEFAULT '',
    organisation       TEXT NOT NULL DEFAULT '',
    department         TEXT NOT NULL DEFAULT '',
    location           TEXT NOT NULL DEFAULT '',
    work_category      TEXT NOT NULL DEFAULT '',
    source_id          TEXT NOT NULL DEFAULT 'gem',
    source_name        TEXT NOT NULL DEFAULT 'GEM',
    start_date         TEXT NOT NULL DEFAULT '',
    end_date           TEXT NOT NULL DEFAULT '',
    emd_amount         REAL,
    est_value_inr      REAL,
    dossier_rel_path   TEXT NOT NULL DEFAULT '',
    rfp_pdf_rel_path   TEXT NOT NULL DEFAULT '',
    pdf_url            TEXT NOT NULL DEFAULT '',
    drive_link         TEXT NOT NULL DEFAULT '',
    tech_spec_url      TEXT NOT NULL DEFAULT '',
    stage              TEXT NOT NULL DEFAULT 'review',
    vendor_decision    TEXT NOT NULL DEFAULT 'pending',
    vendor_remarks     TEXT NOT NULL DEFAULT '',
    internal_notes     TEXT NOT NULL DEFAULT '',
    master_synced      INTEGER NOT NULL DEFAULT 0,
    gsheet_synced      INTEGER NOT NULL DEFAULT 0,
    annexures_fetched  INTEGER NOT NULL DEFAULT 0,
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(company_id, bid_no)
);
CREATE INDEX IF NOT EXISTS idx_crm_tenders_company   ON crm_tenders(company_id);
CREATE INDEX IF NOT EXISTS idx_crm_tenders_subfolder ON crm_tenders(subfolder_id);
CREATE INDEX IF NOT EXISTS idx_crm_tenders_bid_no    ON crm_tenders(bid_no);
CREATE INDEX IF NOT EXISTS idx_crm_tenders_bid_slug  ON crm_tenders(bid_slug);

CREATE TABLE IF NOT EXISTS crm_files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    crm_tender_id     INTEGER NOT NULL REFERENCES crm_tenders(id) ON DELETE CASCADE,
    filename          TEXT NOT NULL,
    original_filename TEXT NOT NULL DEFAULT '',
    rel_path          TEXT NOT NULL,
    file_kind         TEXT NOT NULL DEFAULT 'other',
    mime_type         TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    sha256            TEXT NOT NULL DEFAULT '',
    source_url        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    UNIQUE(crm_tender_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_crm_files_tender ON crm_files(crm_tender_id);

CREATE TABLE IF NOT EXISTS crm_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

COMPANY_DISPLAY_ORDER = {
    "etspl": 0,
    "hanmars": 1,
    "rajiv_mital": 2,
    "rajiv_tyagi": 3,
    "watchdogs": 4,
    "small": 5,
}

FILE_KIND_ORDER = {
    "rfp": 0,
    "tech_spec": 1,
    "boq": 2,
    "atc": 3,
    "annexure": 4,
    "other": 5,
}

ALLOWED_TENDER_UPDATE_COLUMNS = frozenset({
    "subfolder_id",
    "sl_no",
    "title",
    "organisation",
    "department",
    "location",
    "work_category",
    "source_id",
    "source_name",
    "start_date",
    "end_date",
    "emd_amount",
    "est_value_inr",
    "dossier_rel_path",
    "rfp_pdf_rel_path",
    "pdf_url",
    "drive_link",
    "tech_spec_url",
    "stage",
    "vendor_decision",
    "vendor_remarks",
    "internal_notes",
    "master_synced",
    "gsheet_synced",
    "annexures_fetched",
    "metadata_json",
})

DEFAULT_COMPANIES: tuple[dict[str, Any], ...] = (
    {
        "id": "etspl",
        "name": "ETSPL (Earnest Tactical)",
        "type": "admin",
        "disk_folder": "etspl",
        "category": "Command & Tactical Systems",
        "color": "#10B981",
        "vendor_sheet_key": "",
        "confidential_mode": 0,
        "auto_sync_master": 1,
        "subfolders": (
            ("general", "General"),
            ("tech", "Tech"),
            ("gis", "GIS"),
            ("solar", "Solar"),
            ("defproc", "DefProc"),
            ("ioe", "IOE"),
            ("crime_analytics", "Crime Analytics"),
            ("shortlisted", "Shortlisted"),
            ("rejected", "Rejected"),
        ),
        "users": (
            {"username": "etspl", "display_name": "ETSPL Admin", "role": "admin", "subfolder_scope": ""},
        ),
    },
    {
        "id": "hanmars",
        "name": "Hanmars",
        "type": "vendor",
        "disk_folder": "hanmars",
        "category": "Biometrics & Surveillance",
        "color": "#BFDBFE",
        "vendor_sheet_key": "biometrics",
        "confidential_mode": 1,
        "auto_sync_master": 1,
        "subfolders": (
            ("biometrics", "Biometrics"),
            ("sandeep", "Sandeep"),
        ),
        "users": (
            {"username": "hanmars", "display_name": "Hanmars", "role": "vendor", "subfolder_scope": ""},
            {"username": "sandeep", "display_name": "Sandeep (Hanmars)", "role": "vendor", "subfolder_scope": "sandeep"},
        ),
    },
    {
        "id": "rajiv_mital",
        "name": "Rajiv Mittal",
        "type": "vendor",
        "disk_folder": "rajiv mital",
        "category": "Drone / UAV",
        "color": "#FEE2E2",
        "vendor_sheet_key": "drone",
        "confidential_mode": 1,
        "auto_sync_master": 1,
        "subfolders": (
            ("drone", "Drone"),
            ("ai_interceptor", "AI Interceptor"),
            ("enterprise_drone", "Enterprise Drone"),
            ("lidar_drones", "LiDAR Drones"),
        ),
        "users": (
            {"username": "rajiv_mital", "display_name": "Rajiv Mittal", "role": "vendor", "subfolder_scope": ""},
        ),
    },
    {
        "id": "rajiv_tyagi",
        "name": "Rajiv Tyagi",
        "type": "vendor",
        "disk_folder": "rajiv tyagi",
        "category": "Power Supply / Electrical",
        "color": "#FEF08A",
        "vendor_sheet_key": "power_supply",
        "confidential_mode": 1,
        "auto_sync_master": 1,
        "subfolders": (
            ("power_supply", "Power Supply"),
        ),
        "users": (
            {"username": "rajiv_tyagi", "display_name": "Rajiv Tyagi", "role": "vendor", "subfolder_scope": ""},
        ),
    },
    {
        "id": "watchdogs",
        "name": "Watchdogs",
        "type": "vendor",
        "disk_folder": "watchdogs",
        "category": "Security & Watchdogs",
        "color": "#DDD6FE",
        "vendor_sheet_key": "watchdogs",
        "confidential_mode": 1,
        "auto_sync_master": 1,
        "subfolders": (
            ("general", "General"),
        ),
        "users": (
            {"username": "watchdogs", "display_name": "Watchdogs", "role": "vendor", "subfolder_scope": ""},
        ),
    },
    {
        "id": "small",
        "name": "Small / Misc",
        "type": "internal",
        "disk_folder": "small",
        "category": "Small & Miscellaneous",
        "color": "#E2E8F0",
        "vendor_sheet_key": "",
        "confidential_mode": 0,
        "auto_sync_master": 0,
        "subfolders": (
            ("general", "General"),
            ("mangesh", "Mangesh"),
        ),
        "users": (
            {"username": "mangesh", "display_name": "Mangesh", "role": "member", "subfolder_scope": "mangesh"},
        ),
    },
)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", (value or "").strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned or "general"


def safe_bid_slug(bid_no: str) -> str:
    raw = (bid_no or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or "UNKNOWN_BID"


def extract_sheet_id(url_or_id: str) -> str:
    val = (url_or_id or "").strip()
    if not val:
        return ""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", val)
    return match.group(1) if match else val


def _decorate_file_row(row_dict: dict[str, Any]) -> dict[str, Any]:
    fid = row_dict["id"]
    rel = (row_dict.get("rel_path") or "").replace("\\", "/").lstrip("/")
    row_dict["rel_path"] = rel
    row_dict["inline_url"] = f"/api/crm/files/{fid}?inline=1"
    row_dict["download_url"] = f"/api/crm/files/{fid}"
    row_dict["tenders_url"] = f"/tenders/{rel}" if rel.lower().endswith(".pdf") else None
    return row_dict


def _sanitize_user_row(row: sqlite3.Row | dict[str, Any], *, include_hash: bool = False) -> dict[str, Any]:
    raw = dict(row)
    pw_hash = raw.pop("password_hash", "") or ""
    out = {
        "id": raw["id"],
        "username": raw["username"],
        "display_name": raw.get("display_name") or raw["username"],
        "role": raw.get("role") or "vendor",
        "company_id": raw.get("company_id"),
        "company_name": raw.get("company_name") or "",
        "disk_folder": raw.get("disk_folder") or "",
        "subfolder_scope": raw.get("subfolder_scope") or "",
        "is_active": int(raw.get("is_active", 1)),
        "has_password": bool(pw_hash),
        "created_at": raw.get("created_at") or "",
        "updated_at": raw.get("updated_at") or "",
    }
    if include_hash:
        out["password_hash"] = pw_hash
    return out


class CRMRepository:
    """Parameterized SQLite data-access layer for companies, folders, users, sessions, and dossiers."""

    def __init__(self, db_path: str | None = None) -> None:
        self._explicit_db_path = db_path
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def db_path(self) -> str:
        return self._explicit_db_path or os.path.join(paths.DATA_DIR, "crm.db")

    def _connect(self, *, auto_seed: bool = True) -> sqlite3.Connection:
        resolved = self.db_path
        abs_path = os.path.abspath(resolved)
        existed = os.path.exists(abs_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        conn = sqlite3.connect(abs_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if not existed or abs_path not in self._initialized_paths:
            conn.executescript(SCHEMA)
            self._initialized_paths.add(abs_path)
            if auto_seed:
                cnt = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
                if cnt == 0:
                    self._seed_defaults_on_conn(conn)
        return conn

    def init_db(self) -> str:
        with self._lock:
            conn = self._connect(auto_seed=False)
            try:
                conn.executescript(SCHEMA)
            finally:
                conn.close()
            return self.db_path

    def _load_vendor_sheets_config(self) -> dict[str, dict[str, Any]]:
        cfg_path = os.path.join(paths.CONFIG_DIR, "google_sync_config.json")
        if not os.path.exists(cfg_path):
            return {}
        try:
            with open(cfg_path, encoding="utf-8") as handle:
                data = json.load(handle)
            vs = data.get("vendor_sheets") if isinstance(data, dict) else None
            return vs if isinstance(vs, dict) else {}
        except Exception as exc:
            logger.debug("Could not read google_sync_config.json during seed: %s", exc)
            return {}

    def _seed_defaults_on_conn(self, conn: sqlite3.Connection) -> None:
        now = _utc_now_iso()
        vendor_sheets = self._load_vendor_sheets_config()
        with conn:
            for comp in DEFAULT_COMPANIES:
                vkey = comp["vendor_sheet_key"]
                vs_entry = vendor_sheets.get(vkey) if vkey and isinstance(vendor_sheets.get(vkey), dict) else {}
                sheet_url = str(vs_entry.get("spreadsheet_url") or "").strip()
                sheet_id = extract_sheet_id(str(vs_entry.get("spreadsheet_id") or sheet_url))

                conn.execute(
                    """
                    INSERT OR IGNORE INTO companies (
                        id, name, type, disk_folder, category, color,
                        google_sheet_id, google_sheet_url, vendor_sheet_key,
                        confidential_mode, auto_sync_master, is_active,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        comp["id"],
                        comp["name"],
                        comp["type"],
                        comp["disk_folder"],
                        comp["category"],
                        comp["color"],
                        sheet_id,
                        sheet_url,
                        vkey,
                        comp["confidential_mode"],
                        comp["auto_sync_master"],
                        now,
                        now,
                    ),
                )

                if sheet_id or sheet_url:
                    conn.execute(
                        """
                        UPDATE companies
                        SET google_sheet_id = CASE WHEN google_sheet_id = '' THEN ? ELSE google_sheet_id END,
                            google_sheet_url = CASE WHEN google_sheet_url = '' THEN ? ELSE google_sheet_url END
                        WHERE id = ?
                        """,
                        (sheet_id, sheet_url, comp["id"]),
                    )

                for sf_slug, sf_name in comp["subfolders"]:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO subfolders (company_id, parent_id, name, slug, created_at)
                        VALUES (?, NULL, ?, ?, ?)
                        """,
                        (comp["id"], sf_name, sf_slug, now),
                    )

                for usr in comp["users"]:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO users (
                            username, display_name, password_hash, role,
                            company_id, subfolder_scope, is_active, created_at, updated_at
                        ) VALUES (?, ?, '', ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            usr["username"],
                            usr["display_name"],
                            usr["role"],
                            comp["id"],
                            usr["subfolder_scope"],
                            now,
                            now,
                        ),
                    )

    def seed_defaults(self) -> None:
        """Idempotently seed the 6 default companies, subfolders, and accounts."""
        with self._lock:
            conn = self._connect(auto_seed=False)
            try:
                self._seed_defaults_on_conn(conn)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Companies & Subfolders
    # ------------------------------------------------------------------

    def list_companies(
        self, company_id: str | None = None, *, include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_inactive:
            clauses.append("is_active = 1")
        if company_id is not None:
            clauses.append("id = ?")
            params.append(company_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM companies {where_sql}", params).fetchall()
        finally:
            conn.close()

        items = [dict(r) for r in rows]
        items.sort(key=lambda c: (COMPANY_DISPLAY_ORDER.get(c["id"], 100), c["name"].lower()))
        return items

    def get_company(self, company_id: str) -> dict[str, Any] | None:
        if not company_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def upsert_company(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        cid = slugify(str(data.get("id") or data.get("name") or ""))
        if not cid:
            raise ValueError("Company id or name is required.")

        with self._lock:
            conn = self._connect()
            try:
                existing_row = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
                existing = dict(existing_row) if existing_row else {}

                name = str(data.get("name") or existing.get("name") or cid).strip()
                ctype = str(data.get("type") or existing.get("type") or "vendor").strip().lower()
                disk_folder = str(data.get("disk_folder") or existing.get("disk_folder") or cid).strip()
                category = str(
                    data["category"] if "category" in data else existing.get("category", "")
                ).strip()
                color = str(data.get("color") or existing.get("color") or "#E2E8F0").strip()
                sheet_url = str(
                    data["google_sheet_url"]
                    if "google_sheet_url" in data
                    else existing.get("google_sheet_url", "")
                ).strip()
                raw_sheet_id = str(
                    data["google_sheet_id"]
                    if "google_sheet_id" in data
                    else existing.get("google_sheet_id", "")
                ).strip()
                sheet_id = extract_sheet_id(raw_sheet_id or sheet_url)
                vkey = str(
                    data["vendor_sheet_key"]
                    if "vendor_sheet_key" in data
                    else existing.get("vendor_sheet_key", cid if ctype == "vendor" else "")
                ).strip()
                conf = int(
                    bool(data["confidential_mode"])
                    if "confidential_mode" in data
                    else existing.get("confidential_mode", 1 if ctype == "vendor" else 0)
                )
                auto_sync = int(
                    bool(data["auto_sync_master"])
                    if "auto_sync_master" in data
                    else existing.get("auto_sync_master", 1)
                )
                is_active = int(
                    bool(data["is_active"]) if "is_active" in data else existing.get("is_active", 1)
                )

                with conn:
                    conn.execute(
                        """
                        INSERT INTO companies (
                            id, name, type, disk_folder, category, color,
                            google_sheet_id, google_sheet_url, vendor_sheet_key,
                            confidential_mode, auto_sync_master, is_active,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name,
                            type = excluded.type,
                            disk_folder = excluded.disk_folder,
                            category = excluded.category,
                            color = excluded.color,
                            google_sheet_id = excluded.google_sheet_id,
                            google_sheet_url = excluded.google_sheet_url,
                            vendor_sheet_key = excluded.vendor_sheet_key,
                            confidential_mode = excluded.confidential_mode,
                            auto_sync_master = excluded.auto_sync_master,
                            is_active = excluded.is_active,
                            updated_at = excluded.updated_at
                        """,
                        (
                            cid,
                            name,
                            ctype,
                            disk_folder,
                            category,
                            color,
                            sheet_id,
                            sheet_url,
                            vkey,
                            conf,
                            auto_sync,
                            is_active,
                            existing.get("created_at") or now,
                            now,
                        ),
                    )
                    row = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
                return dict(row)
            finally:
                conn.close()

    def list_subfolders(self, company_id: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if company_id:
                rows = conn.execute(
                    "SELECT * FROM subfolders WHERE company_id = ? ORDER BY id ASC",
                    (company_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM subfolders ORDER BY company_id ASC, id ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_subfolder(self, subfolder_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM subfolders WHERE id = ?", (int(subfolder_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_subfolder_by_slug(self, company_id: str, slug: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM subfolders WHERE company_id = ? AND slug = ?",
                (company_id, slugify(slug)),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def ensure_subfolder(
        self,
        company_id: str,
        name: str,
        slug: str | None = None,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        sf_slug = slugify(slug or name)
        sf_name = (name or sf_slug).strip()
        now = _utc_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO subfolders (company_id, parent_id, name, slug, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(company_id, slug) DO NOTHING
                        """,
                        (company_id, parent_id, sf_name, sf_slug, now),
                    )
                row = conn.execute(
                    "SELECT * FROM subfolders WHERE company_id = ? AND slug = ?",
                    (company_id, sf_slug),
                ).fetchone()
                return dict(row)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Tenders & Files
    # ------------------------------------------------------------------

    def _batch_attach_files(
        self, conn: sqlite3.Connection, tender_rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not tender_rows:
            return []
        ids = [r["id"] for r in tender_rows]
        placeholders = ",".join("?" for _ in ids)
        file_rows = conn.execute(
            f"SELECT * FROM crm_files WHERE crm_tender_id IN ({placeholders})",
            ids,
        ).fetchall()

        by_tender: dict[int, list[dict[str, Any]]] = {tid: [] for tid in ids}
        for f in file_rows:
            decorated = _decorate_file_row(dict(f))
            by_tender.setdefault(decorated["crm_tender_id"], []).append(decorated)

        for flist in by_tender.values():
            flist.sort(key=lambda x: (FILE_KIND_ORDER.get(x["file_kind"], 99), x["filename"].lower()))

        for r in tender_rows:
            r["crm_tender_id"] = r["id"]
            r["files"] = by_tender.get(r["id"], [])
        return tender_rows

    def upsert_tender(self, data: dict[str, Any]) -> dict[str, Any]:
        bid_no = str(data.get("bid_no") or "").strip()
        company_id = str(data.get("company_id") or "").strip()
        if not bid_no or not company_id:
            raise ValueError("bid_no and company_id are required.")

        now = _utc_now_iso()
        bid_slug = str(data.get("bid_slug") or safe_bid_slug(bid_no))

        with self._lock:
            conn = self._connect()
            try:
                existing_row = conn.execute(
                    "SELECT * FROM crm_tenders WHERE company_id = ? AND bid_no = ?",
                    (company_id, bid_no),
                ).fetchone()
                ex = dict(existing_row) if existing_row else {}

                def _pick(key: str, default: Any = "") -> Any:
                    if key in data and data[key] is not None:
                        return data[key]
                    return ex.get(key, default)

                subfolder_id = _pick("subfolder_id", None)
                sl_no = _pick("sl_no", None)
                emd_amount = _pick("emd_amount", None)
                est_value_inr = _pick("est_value_inr", None)
                meta_val = _pick("metadata_json", "{}")
                if isinstance(meta_val, dict):
                    meta_val = json.dumps(meta_val, ensure_ascii=False)

                with conn:
                    conn.execute(
                        """
                        INSERT INTO crm_tenders (
                            bid_no, bid_slug, company_id, subfolder_id, sl_no,
                            title, organisation, department, location, work_category,
                            source_id, source_name, start_date, end_date,
                            emd_amount, est_value_inr,
                            dossier_rel_path, rfp_pdf_rel_path, pdf_url,
                            drive_link, tech_spec_url,
                            stage, vendor_decision, vendor_remarks, internal_notes,
                            master_synced, gsheet_synced, annexures_fetched,
                            metadata_json, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?,
                            ?, ?, ?,
                            ?, ?,
                            ?, ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?
                        )
                        ON CONFLICT(company_id, bid_no) DO UPDATE SET
                            bid_slug = excluded.bid_slug,
                            subfolder_id = excluded.subfolder_id,
                            sl_no = COALESCE(excluded.sl_no, crm_tenders.sl_no),
                            title = CASE WHEN excluded.title != '' THEN excluded.title ELSE crm_tenders.title END,
                            organisation = CASE WHEN excluded.organisation != '' THEN excluded.organisation ELSE crm_tenders.organisation END,
                            department = CASE WHEN excluded.department != '' THEN excluded.department ELSE crm_tenders.department END,
                            location = CASE WHEN excluded.location != '' THEN excluded.location ELSE crm_tenders.location END,
                            work_category = CASE WHEN excluded.work_category != '' THEN excluded.work_category ELSE crm_tenders.work_category END,
                            source_id = excluded.source_id,
                            source_name = excluded.source_name,
                            start_date = CASE WHEN excluded.start_date != '' THEN excluded.start_date ELSE crm_tenders.start_date END,
                            end_date = CASE WHEN excluded.end_date != '' THEN excluded.end_date ELSE crm_tenders.end_date END,
                            emd_amount = COALESCE(excluded.emd_amount, crm_tenders.emd_amount),
                            est_value_inr = COALESCE(excluded.est_value_inr, crm_tenders.est_value_inr),
                            dossier_rel_path = CASE WHEN excluded.dossier_rel_path != '' THEN excluded.dossier_rel_path ELSE crm_tenders.dossier_rel_path END,
                            rfp_pdf_rel_path = CASE WHEN excluded.rfp_pdf_rel_path != '' THEN excluded.rfp_pdf_rel_path ELSE crm_tenders.rfp_pdf_rel_path END,
                            pdf_url = CASE WHEN excluded.pdf_url != '' THEN excluded.pdf_url ELSE crm_tenders.pdf_url END,
                            drive_link = CASE WHEN excluded.drive_link != '' THEN excluded.drive_link ELSE crm_tenders.drive_link END,
                            tech_spec_url = CASE WHEN excluded.tech_spec_url != '' THEN excluded.tech_spec_url ELSE crm_tenders.tech_spec_url END,
                            stage = excluded.stage,
                            vendor_decision = excluded.vendor_decision,
                            vendor_remarks = excluded.vendor_remarks,
                            internal_notes = excluded.internal_notes,
                            master_synced = MAX(crm_tenders.master_synced, excluded.master_synced),
                            gsheet_synced = MAX(crm_tenders.gsheet_synced, excluded.gsheet_synced),
                            annexures_fetched = MAX(crm_tenders.annexures_fetched, excluded.annexures_fetched),
                            metadata_json = CASE WHEN excluded.metadata_json != '{}' THEN excluded.metadata_json ELSE crm_tenders.metadata_json END,
                            updated_at = excluded.updated_at
                        """,
                        (
                            bid_no,
                            bid_slug,
                            company_id,
                            subfolder_id,
                            sl_no,
                            str(_pick("title", "")),
                            str(_pick("organisation", "")),
                            str(_pick("department", "")),
                            str(_pick("location", "")),
                            str(_pick("work_category", "")),
                            str(_pick("source_id", "gem")),
                            str(_pick("source_name", "GEM")),
                            str(_pick("start_date", "")),
                            str(_pick("end_date", "")),
                            emd_amount,
                            est_value_inr,
                            str(_pick("dossier_rel_path", "")).replace("\\", "/"),
                            str(_pick("rfp_pdf_rel_path", "")).replace("\\", "/"),
                            str(_pick("pdf_url", "")),
                            str(_pick("drive_link", "")),
                            str(_pick("tech_spec_url", "")),
                            str(_pick("stage", "review")),
                            str(_pick("vendor_decision", "pending")),
                            str(_pick("vendor_remarks", "")),
                            str(_pick("internal_notes", "")),
                            int(bool(_pick("master_synced", 0))),
                            int(bool(_pick("gsheet_synced", 0))),
                            int(bool(_pick("annexures_fetched", 0))),
                            str(meta_val),
                            ex.get("created_at") or now,
                            now,
                        ),
                    )
                    row = conn.execute(
                        "SELECT id FROM crm_tenders WHERE company_id = ? AND bid_no = ?",
                        (company_id, bid_no),
                    ).fetchone()
                    tender_id = int(row["id"])
            finally:
                conn.close()
        return self.get_crm_tender(tender_id)  # type: ignore[return-value]

    def get_crm_tender(self, crm_tender_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT t.*,
                       c.name AS company_name,
                       c.disk_folder AS disk_folder,
                       c.color AS company_color,
                       c.confidential_mode AS confidential_mode,
                       s.name AS subfolder_name,
                       s.slug AS subfolder_slug
                FROM crm_tenders t
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                WHERE t.id = ?
                """,
                (int(crm_tender_id),),
            ).fetchone()
            if not row:
                return None
            return self._batch_attach_files(conn, [dict(row)])[0]
        finally:
            conn.close()

    def get_assignments_for_bid(self, bid_no: str) -> list[dict[str, Any]]:
        if not bid_no:
            return []
        slug = safe_bid_slug(bid_no)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT t.*,
                       c.name AS company_name,
                       c.disk_folder AS disk_folder,
                       c.color AS company_color,
                       c.confidential_mode AS confidential_mode,
                       s.name AS subfolder_name,
                       s.slug AS subfolder_slug
                FROM crm_tenders t
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                WHERE LOWER(t.bid_no) = LOWER(?) OR LOWER(t.bid_slug) = LOWER(?)
                ORDER BY t.updated_at DESC, t.id DESC
                """,
                (bid_no.strip(), slug),
            ).fetchall()
            return self._batch_attach_files(conn, [dict(r) for r in rows])
        finally:
            conn.close()

    def list_crm_tenders(
        self,
        company_id: str | None = None,
        subfolder_id: int | None = None,
        subfolder_slug: str | None = None,
        q: str = "",
        stage: str = "",
        vendor_decision: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if company_id:
            clauses.append("t.company_id = ?")
            params.append(company_id)
        if subfolder_id is not None:
            clauses.append("t.subfolder_id = ?")
            params.append(int(subfolder_id))
        if subfolder_slug:
            clauses.append("LOWER(s.slug) = LOWER(?)")
            params.append(subfolder_slug.strip())
        if stage:
            st_low = stage.strip().lower()
            if st_low in ("under detailed study", "under_study", "review", "assigned"):
                clauses.append("LOWER(t.stage) IN ('under detailed study', 'review', 'assigned')")
            elif st_low in ("participated", "participating", "submitted"):
                clauses.append("LOWER(t.stage) IN ('participated', 'participating', 'submitted')")
            elif st_low in ("dropped", "rejected"):
                clauses.append("LOWER(t.stage) IN ('dropped', 'rejected')")
            else:
                clauses.append("LOWER(t.stage) = LOWER(?)")
                params.append(stage.strip())
        if vendor_decision:
            vd_low = vendor_decision.strip().lower()
            if vd_low in ("clarification", "need_clarification", "need clarification"):
                clauses.append("LOWER(t.vendor_decision) IN ('clarification', 'need_clarification')")
            else:
                clauses.append("LOWER(t.vendor_decision) = LOWER(?)")
                params.append(vendor_decision.strip())
        if q and q.strip():
            like_val = f"%{q.strip()}%"
            clauses.append(
                "("
                "t.bid_no LIKE ? OR "
                "t.title LIKE ? OR "
                "t.organisation LIKE ? OR "
                "t.department LIKE ? OR "
                "t.work_category LIKE ? OR "
                "CAST(COALESCE(t.sl_no, '') AS TEXT) LIKE ?"
                ")"
            )
            params.extend([like_val] * 6)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT t.*,
                       c.name AS company_name,
                       c.disk_folder AS disk_folder,
                       c.color AS company_color,
                       c.confidential_mode AS confidential_mode,
                       s.name AS subfolder_name,
                       s.slug AS subfolder_slug
                FROM crm_tenders t
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                {where_sql}
                ORDER BY COALESCE(t.sl_no, 0) DESC, t.updated_at DESC, t.id DESC
                """,
                params,
            ).fetchall()
            return self._batch_attach_files(conn, [dict(r) for r in rows])
        finally:
            conn.close()

    def update_crm_tender_fields(
        self, crm_tender_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        clean_updates: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in ALLOWED_TENDER_UPDATE_COLUMNS:
                raise ValueError(f"Unsupported tender column update: {k}")
            if k == "metadata_json" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            elif k in ("dossier_rel_path", "rfp_pdf_rel_path") and isinstance(v, str):
                v = v.replace("\\", "/")
            clean_updates[k] = v

        if not clean_updates:
            return self.get_crm_tender(crm_tender_id)

        clean_updates["updated_at"] = _utc_now_iso()
        cols = list(clean_updates.keys())
        set_clause = ", ".join(f"{col} = ?" for col in cols)
        params = [clean_updates[col] for col in cols] + [int(crm_tender_id)]

        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        f"UPDATE crm_tenders SET {set_clause} WHERE id = ?",
                        params,
                    )
                    if cur.rowcount == 0:
                        return None
            finally:
                conn.close()
        return self.get_crm_tender(crm_tender_id)

    def delete_crm_tender(self, crm_tender_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM crm_tenders WHERE id = ?",
                        (int(crm_tender_id),),
                    )
                    return cur.rowcount > 0
            finally:
                conn.close()

    def upsert_file(self, data: dict[str, Any]) -> dict[str, Any]:
        crm_tender_id = int(data["crm_tender_id"])
        filename = str(data["filename"]).strip()
        rel_path = str(data["rel_path"]).replace("\\", "/").lstrip("/")
        if not filename or not rel_path:
            raise ValueError("filename and rel_path are required.")

        now = _utc_now_iso()
        orig_name = str(data.get("original_filename") or filename).strip()
        file_kind = str(data.get("file_kind") or "other").strip()
        mime_type = str(data.get("mime_type") or "application/octet-stream").strip()
        size_bytes = int(data.get("size_bytes") or 0)
        sha256 = str(data.get("sha256") or "").strip()
        source_url = str(data.get("source_url") or "").strip()

        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO crm_files (
                            crm_tender_id, filename, original_filename, rel_path,
                            file_kind, mime_type, size_bytes, sha256, source_url, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(crm_tender_id, filename) DO UPDATE SET
                            original_filename = excluded.original_filename,
                            rel_path = excluded.rel_path,
                            file_kind = excluded.file_kind,
                            mime_type = excluded.mime_type,
                            size_bytes = excluded.size_bytes,
                            sha256 = excluded.sha256,
                            source_url = CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE crm_files.source_url END
                        """,
                        (
                            crm_tender_id,
                            filename,
                            orig_name,
                            rel_path,
                            file_kind,
                            mime_type,
                            size_bytes,
                            sha256,
                            source_url,
                            now,
                        ),
                    )
                row = conn.execute(
                    "SELECT * FROM crm_files WHERE crm_tender_id = ? AND filename = ?",
                    (crm_tender_id, filename),
                ).fetchone()
                return _decorate_file_row(dict(row))
            finally:
                conn.close()

    def get_file(self, file_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT f.*,
                       t.bid_no AS bid_no,
                       t.company_id AS company_id,
                       t.subfolder_id AS subfolder_id,
                       c.disk_folder AS disk_folder,
                       c.confidential_mode AS confidential_mode,
                       s.slug AS subfolder_slug
                FROM crm_files f
                JOIN crm_tenders t ON t.id = f.crm_tender_id
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                WHERE f.id = ?
                """,
                (int(file_id),),
            ).fetchone()
            return _decorate_file_row(dict(row)) if row else None
        finally:
            conn.close()

    def list_files_for_tender(self, crm_tender_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM crm_files WHERE crm_tender_id = ?",
                (int(crm_tender_id),),
            ).fetchall()
            items = [_decorate_file_row(dict(r)) for r in rows]
            items.sort(key=lambda x: (FILE_KIND_ORDER.get(x["file_kind"], 99), x["filename"].lower()))
            return items
        finally:
            conn.close()

    def list_assignments_map(
        self, company_id: str | None = None, subfolder_scope: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Return ``{bid_no: [lightweight_assignment_dict, ...]}`` for bootstrap."""
        clauses: list[str] = []
        params: list[Any] = []
        if company_id:
            clauses.append("t.company_id = ?")
            params.append(company_id)
        if subfolder_scope:
            clauses.append("LOWER(s.slug) = LOWER(?)")
            params.append(subfolder_scope.strip())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT t.id,
                       t.bid_no,
                       t.company_id,
                       c.name AS company_name,
                       c.color AS company_color,
                       t.subfolder_id,
                       s.slug AS subfolder_slug,
                       s.name AS subfolder_name,
                       t.stage,
                       t.vendor_decision,
                       t.sl_no
                FROM crm_tenders t
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                {where_sql}
                ORDER BY t.updated_at DESC, t.id DESC
                """,
                params,
            ).fetchall()
            out: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                bid = r["bid_no"]
                out.setdefault(bid, []).append(
                    {
                        "id": r["id"],
                        "crm_tender_id": r["id"],
                        "bid_no": bid,
                        "company_id": r["company_id"],
                        "company_name": r["company_name"] or "",
                        "company_color": r["company_color"] or "",
                        "subfolder_id": r["subfolder_id"],
                        "subfolder_slug": r["subfolder_slug"] or "",
                        "subfolder_name": r["subfolder_name"] or "",
                        "stage": r["stage"] or "review",
                        "vendor_decision": r["vendor_decision"] or "pending",
                        "sl_no": r["sl_no"],
                    }
                )
            return out
        finally:
            conn.close()

    def counts_summary(
        self, company_id: str | None = None, subfolder_scope: str | None = None
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if company_id:
            clauses.append("t.company_id = ?")
            params.append(company_id)
        if subfolder_scope:
            clauses.append("LOWER(s.slug) = LOWER(?)")
            params.append(subfolder_scope.strip())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            by_company: dict[str, int] = {}
            by_subfolder: dict[int, int] = {}

            comp_rows = (
                conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchall()
                if company_id
                else conn.execute("SELECT id FROM companies WHERE is_active = 1").fetchall()
            )
            for cr in comp_rows:
                by_company[cr["id"]] = 0

            sf_rows = (
                conn.execute("SELECT id, slug FROM subfolders WHERE company_id = ?", (company_id,)).fetchall()
                if company_id
                else conn.execute("SELECT id, slug FROM subfolders").fetchall()
            )
            for sr in sf_rows:
                if subfolder_scope and sr["slug"].lower() != subfolder_scope.strip().lower():
                    continue
                by_subfolder[int(sr["id"])] = 0

            for r in conn.execute(
                f"""
                SELECT t.company_id, COUNT(*) AS cnt
                FROM crm_tenders t
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                {where_sql}
                GROUP BY t.company_id
                """,
                params,
            ).fetchall():
                by_company[r["company_id"]] = int(r["cnt"])

            for r in conn.execute(
                f"""
                SELECT t.subfolder_id, COUNT(*) AS cnt
                FROM crm_tenders t
                LEFT JOIN subfolders s ON s.id = t.subfolder_id
                {where_sql}
                GROUP BY t.subfolder_id
                """,
                params,
            ).fetchall():
                if r["subfolder_id"] is not None:
                    by_subfolder[int(r["subfolder_id"])] = int(r["cnt"])

            total = sum(by_company.values())
            return {
                "by_company": by_company,
                "by_subfolder": by_subfolder,
                "total": total,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Users & Sessions
    # ------------------------------------------------------------------

    def list_users(self, company_id: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if company_id:
                rows = conn.execute(
                    """
                    SELECT u.*, c.name AS company_name, c.disk_folder AS disk_folder
                    FROM users u
                    LEFT JOIN companies c ON c.id = u.company_id
                    WHERE u.company_id = ?
                    ORDER BY u.id ASC
                    """,
                    (company_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT u.*, c.name AS company_name, c.disk_folder AS disk_folder
                    FROM users u
                    LEFT JOIN companies c ON c.id = u.company_id
                    ORDER BY u.id ASC
                    """
                ).fetchall()
            return [_sanitize_user_row(r, include_hash=False) for r in rows]
        finally:
            conn.close()

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        if not username:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT u.*, c.name AS company_name, c.disk_folder AS disk_folder
                FROM users u
                LEFT JOIN companies c ON c.id = u.company_id
                WHERE u.username = ? COLLATE NOCASE
                """,
                (username.strip(),),
            ).fetchone()
            return _sanitize_user_row(row, include_hash=True) if row else None
        finally:
            conn.close()

    def get_user_by_id(self, user_id: int | str) -> dict[str, Any] | None:
        if user_id is None or str(user_id).strip() == "":
            return None
        s_id = str(user_id).strip()
        conn = self._connect()
        try:
            if s_id.isdigit():
                row = conn.execute(
                    """
                    SELECT u.*, c.name AS company_name, c.disk_folder AS disk_folder
                    FROM users u
                    LEFT JOIN companies c ON c.id = u.company_id
                    WHERE u.id = ?
                    """,
                    (int(s_id),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT u.*, c.name AS company_name, c.disk_folder AS disk_folder
                    FROM users u
                    LEFT JOIN companies c ON c.id = u.company_id
                    WHERE u.username = ? COLLATE NOCASE
                    """,
                    (s_id,),
                ).fetchone()
            return _sanitize_user_row(row, include_hash=False) if row else None
        finally:
            conn.close()

    def upsert_user(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                existing_row = None
                raw_id = data.get("id")
                if raw_id is not None and str(raw_id).strip() != "":
                    s_id = str(raw_id).strip()
                    if s_id.isdigit():
                        existing_row = conn.execute(
                            "SELECT * FROM users WHERE id = ?",
                            (int(s_id),),
                        ).fetchone()
                    else:
                        existing_row = conn.execute(
                            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                            (s_id,),
                        ).fetchone()
                if existing_row is None and data.get("username"):
                    existing_row = conn.execute(
                        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                        (str(data["username"]).strip(),),
                    ).fetchone()

                ex = dict(existing_row) if existing_row else {}
                username = str(data.get("username") or ex.get("username") or "").strip()
                if not username:
                    raise ValueError("username is required.")

                display_name = str(
                    data["display_name"]
                    if "display_name" in data and data["display_name"] is not None
                    else (ex.get("display_name") or username)
                ).strip()
                role = str(data.get("role") or ex.get("role") or "vendor").strip().lower()
                company_id = (
                    data["company_id"] if "company_id" in data else ex.get("company_id")
                )
                company_id = str(company_id).strip() if company_id else None
                subfolder_scope = str(
                    data["subfolder_scope"]
                    if "subfolder_scope" in data and data["subfolder_scope"] is not None
                    else ex.get("subfolder_scope", "")
                ).strip()
                is_active = int(
                    bool(data["is_active"]) if "is_active" in data else ex.get("is_active", 1)
                )

                if "password_hash" in data and data["password_hash"] is not None:
                    pw_hash = str(data["password_hash"])
                else:
                    pw_hash = str(ex.get("password_hash") or "")

                with conn:
                    if ex:
                        conn.execute(
                            """
                            UPDATE users
                            SET username = ?,
                                display_name = ?,
                                password_hash = ?,
                                role = ?,
                                company_id = ?,
                                subfolder_scope = ?,
                                is_active = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                username,
                                display_name,
                                pw_hash,
                                role,
                                company_id,
                                subfolder_scope,
                                is_active,
                                now,
                                int(ex["id"]),
                            ),
                        )
                        target_id = int(ex["id"])
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO users (
                                username, display_name, password_hash, role,
                                company_id, subfolder_scope, is_active, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                username,
                                display_name,
                                pw_hash,
                                role,
                                company_id,
                                subfolder_scope,
                                is_active,
                                now,
                                now,
                            ),
                        )
                        target_id = int(cur.lastrowid)
            finally:
                conn.close()
        return self.get_user_by_id(target_id)  # type: ignore[return-value]

    def create_session(self, token_hash: str, user_id: int, expires_at: str) -> dict[str, Any]:
        if not token_hash:
            raise ValueError("token_hash is required.")
        now = _utc_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO sessions (token_hash, user_id, created_at, expires_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (token_hash, int(user_id), now, str(expires_at)),
                    )
            finally:
                conn.close()
        return {
            "token_hash": token_hash,
            "user_id": int(user_id),
            "created_at": now,
            "expires_at": str(expires_at),
        }

    def get_session_user(self, token_hash: str, now_iso: str | None = None) -> dict[str, Any] | None:
        if not token_hash:
            return None
        check_now = now_iso or _utc_now_iso()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT u.*,
                       c.name AS company_name,
                       c.disk_folder AS disk_folder,
                       s.expires_at AS session_expires_at
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN companies c ON c.id = u.company_id
                WHERE s.token_hash = ?
                  AND u.is_active = 1
                  AND s.expires_at > ?
                """,
                (token_hash, check_now),
            ).fetchone()
            if not row:
                return None
            out = _sanitize_user_row(row, include_hash=False)
            out["session_expires_at"] = row["session_expires_at"]
            return out
        finally:
            conn.close()

    def delete_session(self, token_hash: str) -> bool:
        if not token_hash:
            return False
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                    return cur.rowcount > 0
            finally:
                conn.close()

    def delete_sessions_for_user(self, user_id: int | str) -> int:
        s_id = str(user_id).strip()
        if not s_id:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    if s_id.isdigit():
                        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(s_id),))
                    else:
                        cur = conn.execute(
                            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = ? COLLATE NOCASE)",
                            (s_id,),
                        )
                    return int(cur.rowcount)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Metadata KV
    # ------------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM crm_meta WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default
        finally:
            conn.close()

    def set_meta(self, key: str, value: str) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO crm_meta (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (key, str(value), now),
                    )
            finally:
                conn.close()

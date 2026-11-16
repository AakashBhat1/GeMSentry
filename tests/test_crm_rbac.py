"""RBAC, scrypt auth, session lifecycle, IDOR, upload, and path-traversal tests for CRM."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
import paths  # noqa: E402
from gemsentry.crm.auth_service import CRMAuthService, crm_auth_service  # noqa: E402
from gemsentry.web import crm as crm_web  # noqa: E402

MASTER_TOKEN = "master-admin-secret-token-value"


class _FakeRepo:
    """In-memory CRMRepository stub matching the core-crm contract."""

    def __init__(self):
        self.companies = {
            "etspl": {
                "id": "etspl",
                "name": "ETSPL",
                "company_type": "admin",
                "disk_folder": "etspl",
                "confidential_mode": 0,
            },
            "hanmars": {
                "id": "hanmars",
                "name": "Hanmars",
                "company_type": "vendor",
                "disk_folder": "hanmars",
                "confidential_mode": 1,
            },
            "rajiv_mital": {
                "id": "rajiv_mital",
                "name": "Rajiv Mittal",
                "company_type": "vendor",
                "disk_folder": "rajiv mital",
                "confidential_mode": 1,
            },
        }
        self.users = {
            "etspl": {
                "id": "etspl",
                "username": "etspl",
                "display_name": "ETSPL Admin",
                "role": "admin",
                "company_id": "etspl",
                "subfolder_scope": "",
                "password_hash": "",
                "is_active": 1,
            },
            "hanmars": {
                "id": "hanmars",
                "username": "hanmars",
                "display_name": "Hanmars Vendor",
                "role": "vendor",
                "company_id": "hanmars",
                "subfolder_scope": "",
                "password_hash": CRMAuthService.hash_password("HanmarsPass!123"),
                "is_active": 1,
            },
            "sandeep": {
                "id": "sandeep",
                "username": "sandeep",
                "display_name": "Sandeep Scope",
                "role": "vendor",
                "company_id": "hanmars",
                "subfolder_scope": "sandeep",
                "password_hash": CRMAuthService.hash_password("SandeepPass!123"),
                "is_active": 1,
            },
            "rajiv_mital": {
                "id": "rajiv_mital",
                "username": "rajiv_mital",
                "display_name": "Rajiv Mittal",
                "role": "vendor",
                "company_id": "rajiv_mital",
                "subfolder_scope": "",
                "password_hash": CRMAuthService.hash_password("RajivPass!1234"),
                "is_active": 1,
            },
        }
        self.sessions: dict[str, dict] = {}
        self.tenders: dict[int, dict] = {
            1: {"id": 1, "bid_no": "GEM/2026/B/1001", "company_id": "hanmars", "subfolder_slug": "biometrics"},
            2: {"id": 2, "bid_no": "GEM/2026/B/2002", "company_id": "rajiv_mital", "subfolder_slug": "drone"},
        }
        self.files: dict[int, dict] = {}

    def _enrich_user(self, row: dict | None) -> dict | None:
        if not row:
            return None
        comp = self.companies.get(row.get("company_id") or "")
        out = dict(row)
        out["company_name"] = comp["name"] if comp else None
        out["disk_folder"] = comp["disk_folder"] if comp else None
        return out

    def list_companies(self, company_id=None):
        if company_id:
            c = self.companies.get(company_id)
            return [c] if c else []
        return list(self.companies.values())

    def get_company(self, company_id):
        return self.companies.get(company_id)

    def list_users(self):
        out = []
        for u in self.users.values():
            enriched = self._enrich_user(u)
            pw = enriched.pop("password_hash", "")
            enriched["has_password"] = bool(pw)
            out.append(enriched)
        return out

    def get_user_by_username(self, username):
        for u in self.users.values():
            if u["username"] == username:
                return self._enrich_user(u)
        return None

    def get_user_by_id(self, user_id):
        return self._enrich_user(self.users.get(user_id))

    def upsert_user(self, data):
        uid = data["id"]
        self.users[uid] = dict(data)
        enriched = self._enrich_user(self.users[uid])
        pw = enriched.pop("password_hash", "")
        enriched["has_password"] = bool(pw)
        return enriched

    def create_session(self, token_hash, user_id, expires_at):
        self.sessions[token_hash] = {"user_id": user_id, "expires_at": expires_at}

    def get_session_user(self, token_hash, now_iso):
        sess = self.sessions.get(token_hash)
        if not sess or sess["expires_at"] <= now_iso:
            return None
        user = self.users.get(sess["user_id"])
        if not user or int(user.get("is_active", 1)) != 1:
            return None
        return self._enrich_user(user)

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def delete_sessions_for_user(self, user_id):
        for k in [k for k, v in self.sessions.items() if v["user_id"] == user_id]:
            del self.sessions[k]


class _FakeCRMService:
    def __init__(self, repo: _FakeRepo, tenders_dir: str):
        self.repo = repo
        self.tenders_dir = tenders_dir
        self.initialized = False

    def ensure_initialized(self, run_migration=True):
        self.initialized = True
        return {"ok": True}

    def get_bootstrap(self, user):
        if user.get("role") == "admin":
            return {"user": user, "companies": list(self.repo.companies.values())}
        comp = self.repo.get_company(user.get("company_id"))
        return {"user": user, "companies": [comp] if comp else []}

    def create_or_update_company(self, payload):
        cid = payload.get("id") or payload["name"].lower().replace(" ", "_")
        comp = {"id": cid, **payload}
        self.repo.companies[cid] = comp
        return comp

    def create_subfolder(self, company_id, name, parent_id=None):
        if not self.repo.get_company(company_id):
            raise ValueError(f"Unknown company: {company_id}")
        return {"id": 99, "company_id": company_id, "name": name, "slug": name.lower()}

    def list_tenders(self, caller_user, company_id=None, subfolder_id=None, subfolder_slug=None, q="", stage="", vendor_decision=""):
        target_cid = company_id if caller_user.get("role") == "admin" else caller_user.get("company_id")
        return [t for t in self.repo.tenders.values() if not target_cid or t["company_id"] == target_cid]

    def inspect_tender(self, bid_no, caller_user):
        for t in self.repo.tenders.values():
            if t["bid_no"] == bid_no:
                if caller_user.get("role") != "admin" and t["company_id"] != caller_user.get("company_id"):
                    raise PermissionError("Forbidden")
                return t
        return None

    def move_tenders_to_folder(self, **kwargs):
        return {"moved": len(kwargs.get("bid_nos") or [])}

    def fetch_annexures_for_tender(self, crm_tender_id):
        return {"crm_tender_id": crm_tender_id, "downloaded": 0}

    def attach_uploaded_file(self, crm_tender_id, filename, file_bytes, file_kind="tech_spec"):
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".pdf" and not file_bytes.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature.")
        if ext == ".png" and not file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG signature.")
        if ext in (".jpg", ".jpeg") and not file_bytes.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG signature.")
        if ext in (".xlsx", ".docx", ".zip"):
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                for info in zf.infolist():
                    norm = info.filename.replace("\\", "/")
                    if norm.startswith("/") or ".." in norm.split("/"):
                        raise ValueError("Unsafe zip entry path.")
        if ext in (".csv", ".txt") and b"\x00" in file_bytes:
            raise ValueError("NUL byte not allowed.")
        return {"crm_tender_id": crm_tender_id, "filename": filename, "file_kind": file_kind, "size": len(file_bytes)}

    def update_decision(self, crm_tender_id, caller_user, vendor_decision=None, vendor_remarks=None, stage=None, internal_notes=None):
        tender = self.repo.tenders.get(crm_tender_id)
        if not tender:
            return None
        if caller_user.get("role") != "admin" and tender["company_id"] != caller_user.get("company_id"):
            raise PermissionError("Forbidden: tender belongs to another company.")
        return {"id": crm_tender_id, "vendor_decision": vendor_decision, "vendor_remarks": vendor_remarks}

    def remove_tender_assignment(self, crm_tender_id):
        return self.repo.tenders.pop(crm_tender_id, None) is not None

    def get_authorized_file(self, file_id, caller_user):
        f = self.repo.files.get(file_id)
        if not f:
            return None, None, "File not found."
        if caller_user.get("role") != "admin" and f["company_id"] != caller_user.get("company_id"):
            return None, None, "Forbidden: file belongs to another company."
        return f, f["abs_path"], None

    def preview_csv_file(self, file_id, caller_user, max_rows=100):
        f, abs_path, err = self.get_authorized_file(file_id, caller_user)
        if err:
            return None, err
        if not abs_path.lower().endswith(".csv"):
            return None, "Invalid: not a CSV file."
        return {"headers": ["Item", "Qty"], "rows": [["Scanner", "2"]], "truncated": False}, None

    def export_company_excel(self, company_id, caller_user, subfolder_id=None):
        if caller_user.get("role") != "admin" and company_id != caller_user.get("company_id"):
            raise PermissionError("Forbidden")
        out_path = os.path.join(self.tenders_dir, "reports", f"{company_id}.xlsx")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as fp:
            fp.write(b"PK\x03\x04dummy-xlsx")
        return out_path

    def run_folder_migration(self, force=False):
        return {"migrated": True, "force": force}


@pytest.fixture
def rbac_env(monkeypatch, tmp_path):
    tenders_dir = tmp_path / "tenders"
    (tenders_dir / "hanmars" / "biometrics").mkdir(parents=True)
    (tenders_dir / "hanmars" / "sandeep").mkdir(parents=True)
    (tenders_dir / "hanmars_other").mkdir(parents=True)
    (tenders_dir / "rajiv mital" / "drone").mkdir(parents=True)
    (tenders_dir / "downloads").mkdir(parents=True)

    hanmars_pdf = tenders_dir / "hanmars" / "biometrics" / "hanmars_rfp.pdf"
    hanmars_pdf.write_bytes(b"%PDF-1.4\n%hanmars")
    sandeep_pdf = tenders_dir / "hanmars" / "sandeep" / "sandeep_rfp.pdf"
    sandeep_pdf.write_bytes(b"%PDF-1.4\n%sandeep")
    hanmars_other_pdf = tenders_dir / "hanmars_other" / "leak.pdf"
    hanmars_other_pdf.write_bytes(b"%PDF-1.4\n%other")
    rajiv_pdf = tenders_dir / "rajiv mital" / "drone" / "drone_rfp.pdf"
    rajiv_pdf.write_bytes(b"%PDF-1.4\n%rajiv")
    dl_pdf = tenders_dir / "downloads" / "GEM_1.pdf"
    dl_pdf.write_bytes(b"%PDF-1.4\n%dl")
    boq_csv = tenders_dir / "hanmars" / "biometrics" / "boq.csv"
    boq_csv.write_text("Item,Qty\nScanner,2\n", encoding="utf-8")
    (tenders_dir / "hanmars" / "metadata.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "outside_secret.pdf").write_bytes(b"%PDF-1.4\n%secret")

    repo = _FakeRepo()
    repo.files = {
        10: {"id": 10, "company_id": "hanmars", "filename": "hanmars_rfp.pdf", "abs_path": str(hanmars_pdf)},
        11: {"id": 11, "company_id": "hanmars", "filename": "boq.csv", "abs_path": str(boq_csv)},
        20: {"id": 20, "company_id": "rajiv_mital", "filename": "drone_rfp.pdf", "abs_path": str(rajiv_pdf)},
    }
    service = _FakeCRMService(repo, str(tenders_dir))

    monkeypatch.setattr(paths, "TENDERS_DIR", str(tenders_dir))
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        paths,
        "load_server_config",
        lambda: {"auth_token": MASTER_TOKEN, "host": "127.0.0.1", "port": 5000, "tenders_dir": "", "logs_dir": ""},
    )
    monkeypatch.setattr(crm_auth_service, "_repo", repo)
    monkeypatch.setattr(crm_auth_service, "_failures", {})
    monkeypatch.setattr(crm_web, "crm_service", service)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client, repo, service, tmp_path


def _login(client, username: str, password: str) -> tuple[str, dict]:
    resp = client.post("/api/auth/verify", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    return body["session_token"], body


# --------------------------------------------------------------------------
# Password hashing & user sanitization
# --------------------------------------------------------------------------

def test_scrypt_hashing_unique_salts_and_validation():
    h1 = CRMAuthService.hash_password("StrongPass!123")
    h2 = CRMAuthService.hash_password("StrongPass!123")
    assert h1.startswith("scrypt$16384$8$1$")
    assert h1 != h2
    assert "StrongPass!123" not in h1
    assert CRMAuthService.verify_password("StrongPass!123", h1) is True
    assert CRMAuthService.verify_password("WrongPass!123", h1) is False
    assert CRMAuthService.verify_password("StrongPass!123", "bad-format") is False

    with pytest.raises(ValueError):
        CRMAuthService.hash_password("short")
    with pytest.raises(ValueError):
        CRMAuthService.hash_password("x" * 129)


def test_users_api_never_returns_password_hash(rbac_env):
    client, _, _, _ = rbac_env
    admin_tok, _ = _login(client, "etspl", MASTER_TOKEN)

    resp = client.get("/api/crm/users", headers={"Authorization": f"Bearer {admin_tok}"})
    assert resp.status_code == 200
    users = resp.get_json()["users"]
    assert len(users) >= 3
    for u in users:
        assert "password_hash" not in u
        assert "has_password" in u

    create_resp = client.post(
        "/api/crm/users",
        json={"username": "watchdogs", "role": "vendor", "company_id": "hanmars", "password": "Watchdogs!123"},
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert create_resp.status_code == 200
    created = create_resp.get_json()["user"]
    assert "password_hash" not in created
    assert created["has_password"] is True


# --------------------------------------------------------------------------
# Auth lifecycle, session invalidation, rate limit & CSRF
# --------------------------------------------------------------------------

def test_initial_etspl_login_and_session_invalidation(rbac_env):
    client, _, _, _ = rbac_env
    # Initial login using server auth_token when etspl has no password_hash yet
    etspl_tok, body = _login(client, "etspl", MASTER_TOKEN)
    assert body["user"]["role"] == "admin"
    assert "password_hash" not in body["user"]

    # Vendor login
    hanmars_tok, h_body = _login(client, "hanmars", "HanmarsPass!123")
    assert h_body["user"]["company_id"] == "hanmars"

    # Session works
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {hanmars_tok}"}).status_code == 200

    # Password reset invalidates existing vendor sessions
    reset_resp = client.post(
        "/api/crm/users",
        json={"id": "hanmars", "username": "hanmars", "role": "vendor", "company_id": "hanmars", "password": "NewPassword!999"},
        headers={"Authorization": f"Bearer {etspl_tok}"},
    )
    assert reset_resp.status_code == 200
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {hanmars_tok}"}).status_code == 401

    # Re-login with new password succeeds, then deactivation revokes session
    hanmars_tok2, _ = _login(client, "hanmars", "NewPassword!999")
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {hanmars_tok2}"}).status_code == 200

    deact = client.post(
        "/api/crm/users",
        json={"id": "hanmars", "username": "hanmars", "role": "vendor", "company_id": "hanmars", "is_active": 0},
        headers={"Authorization": f"Bearer {etspl_tok}"},
    )
    assert deact.status_code == 200
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {hanmars_tok2}"}).status_code == 401


def test_logout_revokes_session_token(rbac_env):
    client, _, _, _ = rbac_env
    tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {tok}"}).status_code == 200

    out = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    assert out.status_code == 200
    assert "gemsentry_token=;" in " ".join(out.headers.getlist("Set-Cookie"))
    assert client.get("/api/crm/bootstrap", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_failed_login_rate_limiter_triggers_429(rbac_env):
    client, _, _, _ = rbac_env
    for _ in range(15):
        r = client.post("/api/auth/verify", json={"username": "hanmars", "password": "wrong-password"})
        assert r.status_code == 401
    blocked = client.post("/api/auth/verify", json={"username": "hanmars", "password": "HanmarsPass!123"})
    assert blocked.status_code == 429


def test_cookie_only_post_rejected_for_crm_and_admin(rbac_env):
    client, _, _, _ = rbac_env
    tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    # Login set gemsentry_token cookie on client; clear Authorization header on POST
    client.set_cookie("gemsentry_token", tok)
    resp = client.post("/api/crm/tenders/1/decision", json={"vendor_decision": "YES"})
    assert resp.status_code == 401

    cross = client.post(
        "/api/crm/tenders/1/decision",
        json={"vendor_decision": "YES"},
        headers={"Authorization": f"Bearer {tok}", "Sec-Fetch-Site": "cross-site"},
    )
    assert cross.status_code == 403


def test_session_token_stored_hashed_not_plaintext(rbac_env):
    client, repo, _, _ = rbac_env
    tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    assert tok not in repo.sessions
    expected_hash = hashlib.sha256(tok.encode("utf-8")).hexdigest()
    assert expected_hash in repo.sessions


# --------------------------------------------------------------------------
# RBAC, IDOR & path traversal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/tenders"),
    ("GET", "/api/keywords"),
    ("GET", "/api/status"),
    ("POST", "/api/scrape"),
    ("POST", "/api/rescore"),
    ("POST", "/api/clear-workspace"),
    ("GET", "/api/scoring-config"),
    ("GET", "/api/company-profile"),
    ("GET", "/api/presets"),
    ("GET", "/api/tuning/export"),
    ("GET", "/api/logs"),
    ("GET", "/api/sources"),
    ("GET", "/api/live-excel"),
    ("GET", "/api/export/summary.xlsx"),
    ("GET", "/api/finalized"),
    ("GET", "/api/crm/users"),
    ("POST", "/api/crm/users"),
    ("POST", "/api/crm/companies"),
    ("POST", "/api/crm/subfolders"),
    ("POST", "/api/crm/move"),
    ("POST", "/api/crm/tenders/1/fetch-annexures"),
    ("POST", "/api/crm/tenders/1/remove"),
    ("POST", "/api/crm/migrate-folders"),
])
def test_vendor_blocked_from_admin_routes(rbac_env, method, path):
    client, _, _, _ = rbac_env
    tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    headers = {"Authorization": f"Bearer {tok}"}
    resp = client.open(path, method=method, headers=headers, json={})
    assert resp.status_code == 403


def test_vendor_idor_isolation_across_companies_and_files(rbac_env):
    client, _, _, _ = rbac_env
    hanmars_tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    h_headers = {"Authorization": f"Bearer {hanmars_tok}"}

    # Cannot query another company's tenders or export
    assert client.get("/api/crm/tenders?company_id=rajiv_mital", headers=h_headers).status_code == 403
    assert client.get("/api/crm/export/rajiv_mital.xlsx", headers=h_headers).status_code == 403
    assert client.get("/api/crm/export/hanmars.xlsx", headers=h_headers).status_code == 200

    # Cannot inspect or mutate another company's tender or admin fields
    assert client.get("/api/crm/tender-preview/GEM/2026/B/2002", headers=h_headers).status_code == 403
    assert client.post(
        "/api/crm/tenders/2/decision",
        json={"vendor_decision": "YES"},
        headers=h_headers,
    ).status_code == 403
    assert client.post(
        "/api/crm/tenders/1/decision",
        json={"vendor_decision": "YES", "stage": "WON"},
        headers=h_headers,
    ).status_code == 403
    assert client.post(
        "/api/crm/tenders/1/decision",
        json={"vendor_decision": "YES", "vendor_remarks": "Competitive fit"},
        headers=h_headers,
    ).status_code == 200

    # File IDOR & inline vs attachment behavior
    assert client.get("/api/crm/files/20", headers=h_headers).status_code == 403
    assert client.get("/api/crm/files/20/csv-preview", headers=h_headers).status_code == 403

    inline_pdf = client.get("/api/crm/files/10?inline=1", headers=h_headers)
    assert inline_pdf.status_code == 200
    assert inline_pdf.headers["Content-Disposition"].startswith("inline")
    assert inline_pdf.headers["X-Content-Type-Options"] == "nosniff"
    assert inline_pdf.headers["X-Frame-Options"] == "SAMEORIGIN"

    # Non-PDFs never serve inline even with ?inline=1
    csv_dl = client.get("/api/crm/files/11?inline=1", headers=h_headers)
    assert csv_dl.status_code == 200
    assert csv_dl.headers["Content-Disposition"].startswith("attachment")

    csv_prev = client.get("/api/crm/files/11/csv-preview", headers=h_headers)
    assert csv_prev.status_code == 200
    assert csv_prev.get_json()["headers"] == ["Item", "Qty"]


def test_serve_pdf_company_and_subfolder_isolation_and_traversal(rbac_env):
    client, _, _, _ = rbac_env
    hanmars_tok, _ = _login(client, "hanmars", "HanmarsPass!123")
    sandeep_tok, _ = _login(client, "sandeep", "SandeepPass!123")

    # Hanmars can read own company PDF
    assert client.get(
        "/tenders/hanmars/biometrics/hanmars_rfp.pdf",
        headers={"Authorization": f"Bearer {hanmars_tok}"},
    ).status_code == 200

    # Hanmars blocked from rajiv mital, downloads, prefix-confused hanmars_other, and traversal
    for forbidden_path in (
        "/tenders/rajiv%20mital/drone/drone_rfp.pdf",
        "/tenders/downloads/GEM_1.pdf",
        "/tenders/hanmars_other/leak.pdf",
        "/tenders/hanmars/../../outside_secret.pdf",
    ):
        assert client.get(forbidden_path, headers={"Authorization": f"Bearer {hanmars_tok}"}).status_code == 403

    # Non-PDF inside own folder still 404
    assert client.get(
        "/tenders/hanmars/metadata.db",
        headers={"Authorization": f"Bearer {hanmars_tok}"},
    ).status_code == 404

    # Subfolder-scoped user sandeep blocked from biometrics, allowed in sandeep
    assert client.get(
        "/tenders/hanmars/biometrics/hanmars_rfp.pdf",
        headers={"Authorization": f"Bearer {sandeep_tok}"},
    ).status_code == 403
    assert client.get(
        "/tenders/hanmars/sandeep/sandeep_rfp.pdf",
        headers={"Authorization": f"Bearer {sandeep_tok}"},
    ).status_code == 200


# --------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------

def test_upload_validation_rejects_bad_extensions_magic_and_zip_traversal(rbac_env):
    client, _, _, _ = rbac_env
    admin_tok, _ = _login(client, "etspl", MASTER_TOKEN)
    headers = {"Authorization": f"Bearer {admin_tok}"}

    # Bad extensions (.html, .exe)
    for bad_name, payload in (("xss.html", b"<script>alert(1)</script>"), ("payload.exe", b"MZ\x90\x00")):
        r_ext = client.post(
            "/api/crm/tenders/1/upload",
            data={"file": (io.BytesIO(payload), bad_name), "file_kind": "tech_spec"},
            content_type="multipart/form-data",
            headers=headers,
        )
        assert r_ext.status_code == 400

    # Fake PDF magic bytes
    r_pdf = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (io.BytesIO(b"MZ-not-a-pdf"), "spec.pdf"), "file_kind": "tech_spec"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_pdf.status_code == 400

    # Zip traversal inside .xlsx
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")
    buf.seek(0)
    r_zip = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (buf, "boq.xlsx"), "file_kind": "boq"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_zip.status_code == 400

    # NUL byte inside CSV
    r_csv = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (io.BytesIO(b"a,b\n1,\x00"), "boq.csv"), "file_kind": "boq"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_csv.status_code == 400

    # Valid PDF upload succeeds
    r_ok = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (io.BytesIO(b"%PDF-1.4\nvalid"), "spec.pdf"), "file_kind": "tech_spec"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_ok.status_code == 200
    assert r_ok.get_json()["filename"] == "spec.pdf"

    # Valid PNG upload succeeds
    r_png = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nvalid-png"), "diagram.png"), "file_kind": "tech_spec"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_png.status_code == 200
    assert r_png.get_json()["filename"] == "diagram.png"

    # Valid ZIP upload succeeds
    clean_zip = io.BytesIO()
    with zipfile.ZipFile(clean_zip, "w") as zf:
        zf.writestr("annexure.txt", "clean content")
    clean_zip.seek(0)
    r_clean_zip = client.post(
        "/api/crm/tenders/1/upload",
        data={"file": (clean_zip, "bundle.zip"), "file_kind": "annexure"},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_clean_zip.status_code == 200
    assert r_clean_zip.get_json()["filename"] == "bundle.zip"


def test_serve_pdf_fails_closed_without_current_user(rbac_env):
    from gemsentry.web.settings import serve_pdf

    with app_module.app.test_request_context("/tenders/hanmars/biometrics/hanmars_rfp.pdf"):
        resp, status = serve_pdf("hanmars/biometrics/hanmars_rfp.pdf")
        assert status == 403

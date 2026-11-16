"""Comprehensive unit & integration tests for GeMSentry Core CRM, Annexures, Sync, and Migration."""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from typing import Any

import openpyxl
import pypdf
import pytest
from pypdf.annotations import Link

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import logging_setup
import paths
from gemsentry import storage
from gemsentry.crm import (
    CRMRepository,
    CRMService,
    classify_local_filename,
    download_gem_annexures,
    extract_pdf_annexure_links,
    is_safe_gem_url,
    organize_and_import_legacy_folders,
)
from gemsentry.master_sheet import MasterSheetManager, detect_vendor


def _make_pdf_bytes(urls: list[str] | None = None, marker: str = "GeMSentry Test PDF") -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=400, height=400)
    writer.add_metadata({"/Title": marker})
    for idx, url in enumerate(urls or []):
        writer.add_annotation(0, Link(rect=(10, 10 + idx * 12, 120, 20 + idx * 12), url=url))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_docx_bytes(extra_member: tuple[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
        zf.writestr("word/document.xml", "<w:document><w:body>Spec</w:body></w:document>")
        if extra_member is not None:
            zf.writestr(extra_member[0], extra_member[1])
    return buf.getvalue()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolate DATA_DIR, CONFIG_DIR, TENDERS_DIR, TECH_SPECS_DIR, and MasterSheetManager to tmp_path."""
    import gemsentry.constants as constants
    import gemsentry.master_sheet as ms_mod
    import gemsentry.profile as profile_mod

    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    tenders_dir = tmp_path / "tenders"
    downloads_dir = tenders_dir / "downloads"
    specs_dir = data_dir / "tech_specs"
    for d in (cfg_dir, data_dir, tenders_dir, downloads_dir, specs_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(paths, "ROOT", str(tmp_path))
    monkeypatch.setattr(paths, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(paths, "TENDERS_DIR", str(tenders_dir))
    monkeypatch.setattr(paths, "DOWNLOADS_DIR", str(downloads_dir))
    monkeypatch.setattr(paths, "TECH_SPECS_DIR", str(specs_dir))
    monkeypatch.setattr(constants, "TENDERS_DIR", str(tenders_dir))
    monkeypatch.setattr(profile_mod, "TENDERS_DIR", str(tenders_dir))

    sync_cfg_path = cfg_dir / "google_sync_config.json"
    sync_cfg_path.write_text(
        json.dumps(
            {
                "vendor_sheets": {
                    "drone": {"name": "Rajiv Mittal Drone", "spreadsheet_id": "sheet_drone_123"},
                    "power_supply": {"name": "Rajiv Tyagi Power", "spreadsheet_id": "sheet_power_456"},
                    "biometrics": {"name": "Hanmars Bio", "spreadsheet_id": "sheet_bio_789"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ms_mod, "CONFIG_PATH", str(sync_cfg_path))
    monkeypatch.setattr(ms_mod, "FINALIZED_STORE_PATH", str(data_dir / "finalized.local.json"))

    mgr = MasterSheetManager()
    master_xlsx = tmp_path / "test_master.xlsx"
    wb = openpyxl.Workbook()
    ws_m = wb.active
    ws_m.title = "MASTER"
    ws_s = wb.create_sheet("UNDER DETAILED STUDY")
    ws_p = wb.create_sheet("(TENDER DETAILS (PARTICIPATED)")
    for _ in range(3):
        ws_m.append([])
        ws_s.append([])
    ws_m.append(["SL. NO", "DOWNLOAD FROM", "WORK CATEGORY", "DOWNLOAD DATE", "MONTH", "ORGANISATION", "LOCATION/SITE", "TENDER ID"])
    ws_s.append(["SL. NO", "DOWNLOAD FROM", "WORK CATEGORY", "DOWNLOAD DATE", "MONTH", "ORGANISATION", "LOCATION/SITE", "TENDER ID"])
    ws_p.append([])
    ws_p.append(["SL. NO", "STATUS", "DOWNLOAD FROM"])
    wb.save(str(master_xlsx))
    wb.close()

    mgr._get_active_master_paths = lambda: [str(master_xlsx)]
    mgr._sync_to_google_sheet = lambda payload: {"status": "ok", "mocked": True, "payload": payload}
    monkeypatch.setattr(ms_mod, "master_sheet_manager", mgr)
    import gemsentry.crm.migration as mig_mod
    import gemsentry.crm.service as svc_mod

    monkeypatch.setattr(svc_mod, "master_sheet_manager", mgr)
    monkeypatch.setattr(mig_mod, "master_sheet_manager", mgr)

    repo = CRMRepository()
    service = CRMService(repo=repo)
    return {
        "tmp_path": tmp_path,
        "tenders_dir": tenders_dir,
        "downloads_dir": downloads_dir,
        "data_dir": data_dir,
        "cfg_dir": cfg_dir,
        "master_manager": mgr,
        "repo": repo,
        "service": service,
    }


# ---------------------------------------------------------------------------
# 1. Logging Setup Resilience
# ---------------------------------------------------------------------------

def test_logging_setup_falls_back_when_log_file_locked(tmp_path, monkeypatch):
    logger = logging_setup.get_logger()
    old_handlers = list(logger.handlers)
    old_configured = logging_setup._CONFIGURED

    def _raise_locked(*_args, **_kwargs):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    try:
        logger.handlers.clear()
        logging_setup._CONFIGURED = False
        logging_setup.clear_log_buffer()
        monkeypatch.setattr(paths, "LOGS_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(paths, "SCRAPE_LOGS_DIR", str(tmp_path / "logs" / "scrapes"))
        monkeypatch.setattr(paths, "APP_LOG_PATH", str(tmp_path / "logs" / "gemsentry.log"))
        monkeypatch.setattr(logging_setup, "RotatingFileHandler", _raise_locked)

        configured_logger = logging_setup.setup_logging()
        configured_logger.info("Server booted cleanly without file lock crash")

        lines = logging_setup.get_log_buffer_lines()
        assert any("File logging unavailable" in ln for ln in lines)
        assert any("Server booted cleanly" in ln for ln in lines)
    finally:
        logger.handlers[:] = old_handlers
        logging_setup._CONFIGURED = old_configured


# ---------------------------------------------------------------------------
# 2. Repository: Dynamic Path, Seed Defaults, Users, Sessions & Queries
# ---------------------------------------------------------------------------

def test_repository_seed_users_sessions_and_dynamic_db_path(isolated_env, monkeypatch):
    repo: CRMRepository = isolated_env["repo"]
    assert os.path.normpath(repo.db_path) == os.path.normpath(str(isolated_env["data_dir"] / "crm.db"))

    repo.init_db()
    repo.seed_defaults()

    companies = repo.list_companies()
    assert [c["id"] for c in companies] == [
        "etspl",
        "hanmars",
        "rajiv_mital",
        "rajiv_tyagi",
        "watchdogs",
        "small",
    ]
    hanmars = repo.get_company("hanmars")
    assert hanmars is not None
    assert hanmars["google_sheet_id"] == "sheet_bio_789"
    assert hanmars["confidential_mode"] == 1

    rm = repo.get_company("rajiv_mital")
    assert rm is not None
    assert rm["disk_folder"] == "rajiv mital"
    assert rm["google_sheet_id"] == "sheet_drone_123"

    etspl_sfs = {s["slug"] for s in repo.list_subfolders("etspl")}
    assert {
        "general",
        "tech",
        "gis",
        "solar",
        "defproc",
        "ioe",
        "crime_analytics",
        "shortlisted",
        "rejected",
    }.issubset(etspl_sfs)

    # Users must NEVER expose password_hash in list_users / get_user_by_id / upsert_user
    users = repo.list_users()
    usernames = {u["username"] for u in users}
    assert {"etspl", "hanmars", "sandeep", "rajiv_mital", "rajiv_tyagi", "watchdogs", "mangesh"}.issubset(usernames)
    for u in users:
        assert "password_hash" not in u
        assert u["has_password"] is False

    updated_sandeep = repo.upsert_user({"username": "sandeep", "password_hash": "scrypt$16384$8$1$aa$bb"})
    assert "password_hash" not in updated_sandeep
    assert updated_sandeep["has_password"] is True

    # Re-seeding must be idempotent and preserve password_hash
    repo.seed_defaults()
    sandeep_auth = repo.get_user_by_username("sandeep")
    assert sandeep_auth is not None
    assert sandeep_auth["password_hash"] == "scrypt$16384$8$1$aa$bb"
    assert sandeep_auth["subfolder_scope"] == "sandeep"
    assert sandeep_auth["disk_folder"] == "hanmars"

    # Session lifecycle & expiry/active enforcement
    repo.create_session("hash_valid", sandeep_auth["id"], "2030-01-01T00:00:00+00:00")
    repo.create_session("hash_expired", sandeep_auth["id"], "2020-01-01T00:00:00+00:00")

    sess_user = repo.get_session_user("hash_valid", "2026-11-16T12:00:00+00:00")
    assert sess_user is not None
    assert sess_user["username"] == "sandeep"
    assert "password_hash" not in sess_user

    assert repo.get_session_user("hash_expired", "2026-11-16T12:00:00+00:00") is None

    # Deactivating user blocks session lookup immediately
    repo.upsert_user({"id": sandeep_auth["id"], "is_active": 0})
    assert repo.get_session_user("hash_valid", "2026-11-16T12:00:00+00:00") is None

    repo.upsert_user({"id": sandeep_auth["id"], "is_active": 1})
    assert repo.delete_session("hash_valid") is True
    assert repo.get_session_user("hash_valid", "2026-11-16T12:00:00+00:00") is None

    # Monkeypatching paths.DATA_DIR switches repository DB dynamically
    alt_data = isolated_env["tmp_path"] / "alt_data"
    monkeypatch.setattr(paths, "DATA_DIR", str(alt_data))
    assert os.path.normpath(repo.db_path) == os.path.normpath(str(alt_data / "crm.db"))
    repo.set_meta("hello", "world")
    assert repo.get_meta("hello") == "world"
    assert (alt_data / "crm.db").exists()


# ---------------------------------------------------------------------------
# 3. Annexures: Extraction, Classification & SSRF / Size Guardrails
# ---------------------------------------------------------------------------

def test_annexure_link_extraction_and_ssrf_guards(tmp_path, monkeypatch):
    assert is_safe_gem_url("https://mkp.gem.gov.in/uploaded_documents/boq.csv") is True
    assert is_safe_gem_url("https://gem.gov.in/doc.pdf") is True
    assert is_safe_gem_url("http://mkp.gem.gov.in/doc.pdf") is False
    assert is_safe_gem_url("https://gem.gov.in.evil.example/doc.pdf") is False
    assert is_safe_gem_url("https://evilgem.gov.in/doc.pdf") is False
    assert is_safe_gem_url("https://169.254.169.254/latest/meta-data") is False
    assert is_safe_gem_url("https://user:pass@mkp.gem.gov.in/doc.pdf") is False
    assert is_safe_gem_url("https://mkp.gem.gov.in:8443/doc.pdf") is False

    pdf_file = tmp_path / "GEM_2026_B_9000001.pdf"
    pdf_file.write_bytes(
        _make_pdf_bytes(
            [
                "https://mkp.gem.gov.in/uploaded_documents/OrderItem/BoqLineItemsDocument/2026/items_boq.csv",
                "https://mkp.gem.gov.in/uploaded_documents/OrderItem/BoqLineItemsDocument/2026/items_boq.csv",  # duplicate
                "https://mkp.gem.gov.in/catalog_data/SpecificationDocument/2026/uav_tech_specification.pdf",
                "https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=SLA_UPLOAD_PATH/2026/BuyerATC_test.pdf",
                "https://bidplus.gem.gov.in/bidding/bid/documentdownload/999/1786074864.pdf",
                "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20260810",  # GTC boilerplate
                "https://bidplus.gem.gov.in/bidding/downloadOmppdfile/",  # OM boilerplate
                "http://mkp.gem.gov.in/insecure.pdf",  # non-HTTPS
                "https://127.0.0.1/internal.pdf",  # SSRF
            ]
        )
    )

    links = extract_pdf_annexure_links(str(pdf_file))
    assert len(links) == 4
    by_kind = {lnk["kind"]: lnk for lnk in links}
    assert set(by_kind.keys()) == {"boq", "tech_spec", "atc", "annexure"}
    assert by_kind["atc"]["filename"] == "BuyerATC_test.pdf"

    assert classify_local_filename("GEM_2026_B_7774996.pdf") == "rfp"
    assert classify_local_filename("Technical_Specification_GEM2026B7733568.docx") == "tech_spec"
    assert classify_local_filename("boq_item_sample.csv") == "boq"
    assert classify_local_filename("ATC_Buyer_Terms.pdf") == "atc"
    assert classify_local_filename("1785212402.pdf", primary_bid_filename="GEM_2026_B_7842120.pdf") == "annexure"
    assert classify_local_filename("notes.txt", primary_bid_filename="GEM_2026_B_7842120.pdf") == "other"

    # Mock HTTP session to verify redirect host validation & byte cap enforcement
    class FakeResponse:
        def __init__(self, status_code: int, body: bytes = b"", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self._body = body
            self.headers = headers or {}

        def iter_content(self, chunk_size: int = 65536):
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i : i + chunk_size]

        def close(self):
            pass

    class FakeSession:
        def __init__(self):
            self.verify = None
            self.headers: dict[str, str] = {}
            self.calls: list[tuple[str, Any]] = []

        def get(self, url: str, **kwargs):
            self.calls.append((url, kwargs.get("verify")))
            if "good_spec" in url:
                return FakeResponse(200, b"%PDF-1.4 spec content", {"Content-Type": "application/pdf"})
            if "evil_redirect" in url:
                return FakeResponse(302, b"", {"Location": "http://169.254.169.254/latest/meta-data"})
            if "safe_redirect" in url:
                return FakeResponse(302, b"", {"Location": "https://mkp.gem.gov.in/final_boq.csv"})
            if "final_boq.csv" in url:
                return FakeResponse(200, b"Item,Qty\nDrone,4\n", {"Content-Type": "text/csv"})
            if "huge_file" in url:
                return FakeResponse(200, b"X" * 2048, {"Content-Type": "application/pdf"})
            return FakeResponse(404)

        def close(self):
            pass

    fake_session = FakeSession()
    import gemsentry.crm.annexures as ann_mod

    monkeypatch.setattr(ann_mod, "_create_verified_session", lambda: fake_session)

    dest = tmp_path / "dl_dossier"
    saved, warnings = download_gem_annexures(
        [
            "https://mkp.gem.gov.in/docs/good_spec.pdf",
            "https://mkp.gem.gov.in/docs/evil_redirect.pdf",
            "https://bidplus.gem.gov.in/docs/safe_redirect",
            "https://mkp.gem.gov.in/docs/huge_file.pdf",
            "https://evil.example.com/ssrf.pdf",
        ],
        str(dest),
        max_bytes=1024,
    )

    saved_names = {s["filename"] for s in saved}
    assert "good_spec.pdf" in saved_names
    assert "final_boq.csv" in saved_names
    assert len(saved) == 2
    assert any("Blocked untrusted redirect" in w for w in warnings)
    assert any("exceeded 1024 byte cap" in w for w in warnings)
    assert any("Blocked non-GeM" in w for w in warnings)
    assert all(v is not None and str(v).endswith(".pem") for _u, v in fake_session.calls)


# ---------------------------------------------------------------------------
# 4. Dynamic Vendor Detection & Tech Spec Sync in MasterSheetManager
# ---------------------------------------------------------------------------

def test_dynamic_crm_vendors_and_finalize_tech_spec(isolated_env):
    mgr: MasterSheetManager = isolated_env["master_manager"]
    vs = mgr.config["vendor_sheets"]

    assert detect_vendor({}, custom_vendor="hanmars", vendor_sheets=vs)["id"] == "biometrics"
    assert detect_vendor({}, custom_vendor="rajiv_mital", vendor_sheets=vs)["id"] == "drone"
    assert detect_vendor({}, custom_vendor="rajiv_tyagi", vendor_sheets=vs)["id"] == "power_supply"
    wd = detect_vendor({}, custom_vendor="watchdogs", vendor_sheets=vs)
    assert wd["id"] == "watchdogs"
    assert wd["hex_color"] == "DDD6FE"

    custom_vs = {**vs, "tactical_partner": {"name": "Tactical Partner", "category": "Radar", "color": "#FCA5A5", "spreadsheet_id": "sheet_radar"}}
    tp = detect_vendor({}, custom_vendor="tactical_partner", vendor_sheets=custom_vs)
    assert tp["id"] == "tactical_partner"
    assert tp["hex_color"] == "FCA5A5"

    spec_path = isolated_env["tmp_path"] / "radar_spec.pdf"
    spec_path.write_bytes(_make_pdf_bytes())

    res = mgr.finalize_tender(
        {"bid_no": "GEM/2026/B/7000111", "title": "3D Tactical Radar"},
        custom_fields={
            "assigned_vendor": "tactical_partner",
            "extra_vendor_sheets": {"tactical_partner": custom_vs["tactical_partner"]},
            "tech_spec_local_path": str(spec_path),
            "tech_spec_filename": "radar_spec.pdf",
        },
    )
    assert res["status"] == "ok"
    assert res["record"]["vendor_id"] == "tactical_partner"
    assert res["record"]["tech_spec_filename"] == "radar_spec.pdf"
    assert "spec-sheet" in res["record"]["tech_spec_url"]


# ---------------------------------------------------------------------------
# 5. CRMService: Move + Sync, RBAC Redaction, Uploads, CSV Preview & Excel Export
# ---------------------------------------------------------------------------

def test_crm_service_move_rbac_uploads_csv_and_excel(isolated_env, monkeypatch):
    service: CRMService = isolated_env["service"]
    tenders_dir = isolated_env["tenders_dir"]
    downloads_dir = isolated_env["downloads_dir"]

    # Seed a scraped tender in metadata.db + downloaded RFP PDF with embedded spec & BOQ links
    bid_no = "GEM/2026/B/8888001"
    rfp_pdf = downloads_dir / "GEM_2026_B_8888001.pdf"
    rfp_pdf.write_bytes(
        _make_pdf_bytes(
            [
                "https://mkp.gem.gov.in/SpecificationDocument/2026/biometric_spec.pdf",
                "https://mkp.gem.gov.in/BoqLineItemsDocument/2026/biometric_boq.csv",
            ]
        )
    )
    storage.save_metadata(
        [
            {
                "bid_no": bid_no,
                "title": "Facial Recognition Turnstile System",
                "quantity": "12",
                "department": "Ministry of Home Affairs",
                "start_date": "01-11-2026 10:00:00",
                "end_date": "30-11-2026 15:00:00",
                "keyword": "biometric",
                "downloaded": True,
                "local_pdf_path": paths.repo_relative(str(rfp_pdf)),
                "pdf_url": "https://bidplus.gem.gov.in/showbidDocument/8888001",
                "status": "Pending Review",
                "analysis": {
                    "buyer_org": "CISF HQ",
                    "consignee_state": "Delhi",
                    "emd_amount": 150000.0,
                    "est_value_inr": 7500000.0,
                    "startup_exemption": "Yes",
                    "mse_exemption": "Yes",
                    "business_line": {"id": "biometrics", "label": "Biometrics & Surveillance"},
                    "score": 82,
                },
            }
        ],
        str(tenders_dir),
    )

    import gemsentry.crm.service as svc_mod

    def _fake_download(links, dest_dir, timeout=12, max_bytes=25 * 1024 * 1024):
        spec_out = os.path.join(dest_dir, "biometric_spec.pdf")
        boq_out = os.path.join(dest_dir, "biometric_boq.csv")
        spec_bytes = _make_pdf_bytes()
        boq_bytes = b"Item,Qty,Unit\nFace Terminal,12,Nos\nController,4,Nos\n"
        with open(spec_out, "wb") as f1:
            f1.write(spec_bytes)
        with open(boq_out, "wb") as f2:
            f2.write(boq_bytes)
        return (
            [
                {
                    "filename": "biometric_spec.pdf",
                    "original_filename": "biometric_spec.pdf",
                    "abs_path": spec_out,
                    "size_bytes": len(spec_bytes),
                    "sha256": "aaa111",
                    "file_kind": "tech_spec",
                    "mime_type": "application/pdf",
                    "source_url": links[0]["url"],
                },
                {
                    "filename": "biometric_boq.csv",
                    "original_filename": "biometric_boq.csv",
                    "abs_path": boq_out,
                    "size_bytes": len(boq_bytes),
                    "sha256": "bbb222",
                    "file_kind": "boq",
                    "mime_type": "text/csv",
                    "source_url": links[1]["url"],
                },
            ],
            [],
        )

    monkeypatch.setattr(svc_mod, "download_gem_annexures", _fake_download)

    admin_user = {"id": 1, "username": "etspl", "role": "admin", "company_id": "etspl", "subfolder_scope": ""}
    hanmars_user = {"id": 2, "username": "hanmars", "role": "vendor", "company_id": "hanmars", "subfolder_scope": ""}
    sandeep_user = {"id": 3, "username": "sandeep", "role": "vendor", "company_id": "hanmars", "subfolder_scope": "sandeep"}
    rm_user = {"id": 4, "username": "rajiv_mital", "role": "vendor", "company_id": "rajiv_mital", "subfolder_scope": ""}

    # Move tender into hanmars/sandeep with annexure fetch + Master/GSheet sync
    sandeep_sf = service.repo.get_subfolder_by_slug("hanmars", "sandeep")
    assert sandeep_sf is not None

    move_res = service.move_tenders_to_folder(
        bid_nos=[bid_no],
        company_id="hanmars",
        subfolder_id=sandeep_sf["id"],
        fetch_gem_annexures=True,
        sync_master_and_sheets=True,
        remarks="Priority airport gate tender",
    )
    assert move_res["status"] == "ok"
    assert move_res["moved_count"] == 1
    moved = move_res["tenders"][0]
    crm_tid = moved["id"]
    assert moved["sl_no"] == 1017
    assert moved["master_synced"] == 1
    assert len(moved["files"]) == 3
    assert (tenders_dir / "hanmars" / "sandeep" / "GEM_2026_B_8888001" / "GEM_2026_B_8888001.pdf").is_file()

    # Scraped record status pinned to Shortlisted
    pinned = storage.get_record(bid_no, str(tenders_dir))
    assert pinned is not None and pinned["status"] == "Shortlisted"

    # Admin sees full financials; confidential vendor sees redacted financials
    admin_list = service.list_tenders(admin_user, company_id="hanmars")
    assert admin_list[0]["emd_amount"] == 150000.0
    assert admin_list[0]["est_value_inr"] == 7500000.0
    assert admin_list[0]["internal_notes"] == "Priority airport gate tender"

    vendor_list = service.list_tenders(sandeep_user)
    assert len(vendor_list) == 1
    assert vendor_list[0]["emd_amount"] is None
    assert vendor_list[0]["est_value_inr"] is None
    assert vendor_list[0]["internal_notes"] == ""
    assert vendor_list[0]["metadata_json"] == "{}"

    # Subfolder-scoped user blocked from biometrics subfolder; other vendor blocked from hanmars
    with pytest.raises(PermissionError):
        service.list_tenders(sandeep_user, subfolder_slug="biometrics")
    with pytest.raises(PermissionError):
        service.list_tenders(rm_user, company_id="hanmars")
    with pytest.raises(PermissionError):
        service.inspect_tender(bid_no, rm_user)

    # Vendor updates participation decision
    dec = service.update_decision(
        crm_tid,
        sandeep_user,
        vendor_decision="YES",
        vendor_remarks="Ready with STQC certified terminals",
        internal_notes="Vendor should not overwrite internal notes",
    )
    assert dec["vendor_decision"] == "YES"
    assert dec["stage"] == "participating"
    assert service.repo.get_crm_tender(crm_tid)["internal_notes"] == "Priority airport gate tender"

    # CSV preview & cross-tenant file isolation
    boq_file = next(f for f in moved["files"] if f["file_kind"] == "boq")
    preview, err = service.preview_csv_file(boq_file["id"], sandeep_user)
    assert err is None and preview is not None
    assert preview["headers"] == ["Item", "Qty", "Unit"]
    assert preview["total_rows"] == 2

    blocked_row, blocked_path, blocked_err = service.get_authorized_file(boq_file["id"], rm_user)
    assert blocked_row is None and blocked_path is None
    assert blocked_err is not None and "forbidden" in blocked_err.lower()

    # Upload validation: reject bad extensions, fake PDF magic bytes, and zip-slip archives
    with pytest.raises(ValueError, match="extension"):
        service.attach_uploaded_file(crm_tid, "malware.exe", b"MZ\x90\x00")
    with pytest.raises(ValueError, match="PDF"):
        service.attach_uploaded_file(crm_tid, "fake.pdf", b"<html>not a pdf</html>")
    with pytest.raises(ValueError, match="traversal"):
        service.attach_uploaded_file(crm_tid, "slip.docx", _make_docx_bytes(("../evil.txt", b"pwn")))

    up_res = service.attach_uploaded_file(crm_tid, "Compliance_Matrix.docx", _make_docx_bytes(), file_kind="tech_spec")
    assert up_res["status"] == "ok"
    assert up_res["file"]["original_filename"] == "Compliance_Matrix.docx"

    # Excel export: admin workbook has Bid No & EMD; confidential vendor workbook hides them
    admin_xlsx = service.export_company_excel("hanmars", admin_user)
    vendor_xlsx = service.export_company_excel("hanmars", hanmars_user)
    with pytest.raises(PermissionError):
        service.export_company_excel("hanmars", rm_user)

    wb_adm = openpyxl.load_workbook(admin_xlsx)
    adm_headers = [wb_adm.active.cell(row=2, column=c).value for c in range(1, 12)]
    wb_adm.close()
    assert "BID NUMBER" in adm_headers
    assert "EMD (INR)" in adm_headers

    wb_vnd = openpyxl.load_workbook(vendor_xlsx)
    vnd_headers = [wb_vnd.active.cell(row=2, column=c).value for c in range(1, 9)]
    vnd_values = [
        wb_vnd.active.cell(row=3, column=c).value for c in range(1, 9)
    ]
    wb_vnd.close()
    assert "BID NUMBER" not in vnd_headers
    assert "EMD (INR)" not in vnd_headers
    assert bid_no not in [str(v) for v in vnd_values]


# ---------------------------------------------------------------------------
# 6. Legacy Folder Migration: Backup, Deduplication, New Folder Unbundling
# ---------------------------------------------------------------------------

def test_legacy_folder_migration_backup_dedup_and_cleanup(isolated_env):
    repo: CRMRepository = isolated_env["repo"]
    tenders_dir = isolated_env["tenders_dir"]

    # Seed metadata.db so GeM-Bidding-9347652.pdf enriches to GEM/2026/B/7556479
    storage.save_metadata(
        [
            {
                "bid_no": "GEM/2026/B/7556479",
                "title": "Tethered Surveillance Drone System",
                "quantity": "2",
                "department": "BSF Frontier HQ",
                "start_date": "01-07-2026",
                "end_date": "25-07-2026",
                "keyword": "drone",
                "downloaded": True,
                "local_pdf_path": "tenders/downloads/GeM-Bidding-9347652.pdf",
                "pdf_url": "https://bidplus.gem.gov.in/showbidDocument/9347652",
                "status": "Shortlisted",
                "analysis": {"buyer_org": "Border Security Force", "emd_amount": 50000.0},
            }
        ],
        str(tenders_dir),
    )

    # 1) etspl\New folder bundle (RFP + BOQ + Spec)
    nf_etspl = tenders_dir / "etspl" / "New folder"
    nf_etspl.mkdir(parents=True)
    (nf_etspl / "GEM_2026_B_7842120.pdf").write_bytes(_make_pdf_bytes(marker="7842120"))
    (nf_etspl / "boq_2026-07-28.csv").write_text("Item,Qty\nBattery,10\n", encoding="utf-8")
    (nf_etspl / "tech_specification_7842120.pdf").write_bytes(_make_pdf_bytes(marker="spec_7842120"))

    # 2) Copy-pasted duplicate tree: etspl\RFPs\Shortlisted vs etspl\TENDERS\RFPs\Shortlisted
    dup_bytes = _make_pdf_bytes(marker="9347652")
    for prefix in (
        tenders_dir / "etspl" / "RFPs" / "Shortlisted" / "GEM" / "July26" / "GEM-2026-B-7556479 - Tethered Drones",
        tenders_dir / "etspl" / "TENDERS" / "RFPs" / "Shortlisted" / "GEM" / "July26" / "GEM-2026-B-7556479 - Tethered Drones",
    ):
        prefix.mkdir(parents=True)
        (prefix / "GeM-Bidding-9347652.pdf").write_bytes(dup_bytes)

    # 3) hanmars\sandeep\New folder (4) with exact "(1)" SHA-256 duplicate copy
    nf_hanmars = tenders_dir / "hanmars" / "sandeep" / "New folder (4)"
    nf_hanmars.mkdir(parents=True)
    (nf_hanmars / "GEM_2026_B_7891051.pdf").write_bytes(_make_pdf_bytes(marker="7891051"))
    same_csv = b"Item,Qty\nBiometric Reader,5\n"
    (nf_hanmars / "boq_items.csv").write_bytes(same_csv)
    (nf_hanmars / "boq_items (1).csv").write_bytes(same_csv)

    # 4) Multi-bid folder: rajiv mital\16-11 with 2 distinct GeM bids
    rm_batch = tenders_dir / "rajiv mital" / "16-11"
    rm_batch.mkdir(parents=True)
    (rm_batch / "GEM_2026_B_8060046.pdf").write_bytes(_make_pdf_bytes(marker="8060046"))
    (rm_batch / "GEM_2026_B_8171917.pdf").write_bytes(_make_pdf_bytes(marker="8171917"))

    # 5) Nested small\small\mangesh + watchdogs root loose PDF
    sm_mangesh = tenders_dir / "small" / "small" / "mangesh"
    sm_mangesh.mkdir(parents=True)
    (sm_mangesh / "GEM_2026_B_7721780.pdf").write_bytes(_make_pdf_bytes(marker="7721780"))

    wd_root = tenders_dir / "watchdogs"
    wd_root.mkdir(parents=True)
    (wd_root / "GEM_2026_B_7881442.pdf").write_bytes(_make_pdf_bytes(marker="7881442"))

    res = organize_and_import_legacy_folders(repo, tenders_dir=str(tenders_dir))
    assert res["status"] == "ok"
    assert res["backup_dir"] is not None
    assert os.path.isdir(os.path.join(res["backup_dir"], "etspl", "New folder"))
    assert os.path.isdir(os.path.join(res["backup_dir"], "hanmars", "sandeep", "New folder (4)"))
    assert res["duplicates_removed"] >= 2

    # Legacy shells ("New folder*", "TENDERS", "16-11", "small/small") must be gone!
    assert not (tenders_dir / "etspl" / "New folder").exists()
    assert not (tenders_dir / "etspl" / "TENDERS").exists()
    assert not (tenders_dir / "hanmars" / "sandeep" / "New folder (4)").exists()
    assert not (tenders_dir / "rajiv mital" / "16-11").exists()
    assert not (tenders_dir / "small" / "small").exists()

    # Clean dossiers exist on disk with all unique files intact
    assert (tenders_dir / "etspl" / "general" / "GEM_2026_B_7842120" / "GEM_2026_B_7842120.pdf").is_file()
    assert (tenders_dir / "etspl" / "general" / "GEM_2026_B_7842120" / "boq_2026-07-28.csv").is_file()
    assert (tenders_dir / "etspl" / "shortlisted" / "GEM_2026_B_7556479" / "GeM-Bidding-9347652.pdf").is_file()
    assert (tenders_dir / "hanmars" / "sandeep" / "GEM_2026_B_7891051" / "boq_items.csv").is_file()
    assert not (tenders_dir / "hanmars" / "sandeep" / "GEM_2026_B_7891051" / "boq_items_1.csv").exists()
    assert (tenders_dir / "rajiv mital" / "drone" / "GEM_2026_B_8060046" / "GEM_2026_B_8060046.pdf").is_file()
    assert (tenders_dir / "rajiv mital" / "drone" / "GEM_2026_B_8171917" / "GEM_2026_B_8171917.pdf").is_file()
    assert (tenders_dir / "small" / "mangesh" / "GEM_2026_B_7721780" / "GEM_2026_B_7721780.pdf").is_file()
    assert (tenders_dir / "watchdogs" / "general" / "GEM_2026_B_7881442" / "GEM_2026_B_7881442.pdf").is_file()

    # Enriched metadata imported into crm.db
    shortlisted = repo.list_crm_tenders(company_id="etspl", subfolder_slug="shortlisted")
    assert len(shortlisted) == 1
    assert shortlisted[0]["bid_no"] == "GEM/2026/B/7556479"
    assert shortlisted[0]["organisation"] == "Border Security Force"
    assert shortlisted[0]["stage"] == "shortlisted"

    # Second run is completely idempotent
    res2 = organize_and_import_legacy_folders(repo, tenders_dir=str(tenders_dir))
    assert res2["status"] == "ok"
    assert repo.counts_summary()["total"] == 7


def test_bootstrap_company_crud_path_guards_and_remove(isolated_env):
    service: CRMService = isolated_env["service"]

    admin_user = {"id": 1, "username": "etspl", "role": "admin", "company_id": "etspl", "subfolder_scope": ""}
    hanmars_user = {"id": 2, "username": "hanmars", "role": "vendor", "company_id": "hanmars", "subfolder_scope": ""}
    sandeep_user = {"id": 3, "username": "sandeep", "role": "vendor", "company_id": "hanmars", "subfolder_scope": "sandeep"}

    # Seed assignments in hanmars/biometrics, hanmars/sandeep, and watchdogs/general
    bio_sf = service.repo.get_subfolder_by_slug("hanmars", "biometrics")
    san_sf = service.repo.get_subfolder_by_slug("hanmars", "sandeep")
    wd_sf = service.repo.get_subfolder_by_slug("watchdogs", "general")
    assert bio_sf and san_sf and wd_sf

    service.move_tenders_to_folder(
        bid_nos=["GEM/2026/B/1001"],
        company_id="hanmars",
        subfolder_id=bio_sf["id"],
        fetch_gem_annexures=False,
        sync_master_and_sheets=False,
    )
    service.move_tenders_to_folder(
        bid_nos=["GEM/2026/B/1002"],
        company_id="hanmars",
        subfolder_id=san_sf["id"],
        fetch_gem_annexures=False,
        sync_master_and_sheets=False,
    )
    service.move_tenders_to_folder(
        bid_nos=["GEM/2026/B/1003"],
        company_id="watchdogs",
        subfolder_id=wd_sf["id"],
        fetch_gem_annexures=False,
        sync_master_and_sheets=False,
    )

    boot_admin = service.get_bootstrap(admin_user)
    assert len(boot_admin["companies"]) == 6
    assert "assignments_by_bid" in boot_admin
    assert {"GEM/2026/B/1001", "GEM/2026/B/1002", "GEM/2026/B/1003"}.issubset(
        boot_admin["assignments_by_bid"].keys()
    )
    assert boot_admin["assignments_by_bid"]["GEM/2026/B/1002"][0]["subfolder_slug"] == "sandeep"

    boot_hanmars = service.get_bootstrap(hanmars_user)
    assert set(boot_hanmars["assignments_by_bid"].keys()) == {"GEM/2026/B/1001", "GEM/2026/B/1002"}

    boot_sandeep = service.get_bootstrap(sandeep_user)
    assert [c["id"] for c in boot_sandeep["companies"]] == ["hanmars"]
    assert [s["slug"] for s in boot_sandeep["subfolders"]] == ["sandeep"]
    assert boot_sandeep["companies"][0]["google_sheet_id"] == ""  # hidden in confidential mode
    assert set(boot_sandeep["assignments_by_bid"].keys()) == {"GEM/2026/B/1002"}
    assert boot_sandeep["assignments_by_bid"]["GEM/2026/B/1002"][0]["company_id"] == "hanmars"

    # Reject path traversal in disk_folder
    with pytest.raises(ValueError):
        service.create_or_update_company({"name": "Evil Co", "disk_folder": "../escape"})

    custom_co = service.create_or_update_company(
        {
            "name": "Apex Electro Optics",
            "type": "vendor",
            "disk_folder": "apex_eo",
            "category": "Electro Optics",
            "color": "#A7F3D0",
            "google_sheet_url": "https://docs.google.com/spreadsheets/d/1ApexSheetId999/edit",
        }
    )
    assert custom_co["id"] == "apex_electro_optics"
    assert custom_co["google_sheet_id"] == "1ApexSheetId999"

    thermal_sf = service.create_subfolder(custom_co["id"], "Thermal Sights")
    assert thermal_sf["slug"] == "thermal_sights"

    moved = service.move_tenders_to_folder(
        bid_nos=["GEM/2026/B/9999123"],
        company_id=custom_co["id"],
        subfolder_id=thermal_sf["id"],
        fetch_gem_annexures=False,
        sync_master_and_sheets=False,
    )
    tid = moved["tenders"][0]["id"]
    dec = service.update_decision(
        tid,
        admin_user,
        vendor_decision="CLARIFICATION",
        stage="UNDER DETAILED STUDY",
    )
    assert dec["vendor_decision"] == "CLARIFICATION"
    assert dec["tender"]["vendor_decision"] == "CLARIFICATION"
    assert len(service.list_tenders(admin_user, company_id=custom_co["id"], stage="UNDER DETAILED STUDY", vendor_decision="CLARIFICATION")) == 1

    # String user id lookup/upsert compatibility with CRMAuthService
    new_u = service.repo.upsert_user({"id": "alice", "username": "alice", "role": "vendor", "company_id": custom_co["id"]})
    assert isinstance(new_u["id"], int)
    assert service.repo.get_user_by_id("alice")["username"] == "alice"

    assert service.remove_tender_assignment(tid) is True
    assert service.repo.get_crm_tender(tid) is None


"""
Master Sheet and Google Sheets Integration Engine for GeMSentry.

Manages:
1. Sequential serial numbering (SL. NO) starting from existing master numbering (>= 1016).
2. Row generation and formatting matching 'TENDER MASTER SHEET(ETSPL) 2025- 26.xlsx'.
3. Bidirectional tracking and persistence in data/finalized_tenders.json.
4. Writing to local Excel master workbooks (Downloads and repo copies).
5. Real-time syncing with Google Sheet and Google Drive via Google Apps Script Webhook.
6. Lifecycle transitions to '(TENDER DETAILS (PARTICIPATED)' with Won/Lost status.
7. Row deletion for correcting accidental additions.
"""

import os
import re
import json
import base64
import shutil
import logging
import datetime
import threading
from typing import Any

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

import paths
from gemsentry.dateparse import parse_gem_date
from gemsentry.google_webhook import post_webhook
from gemsentry.storage import find_existing_pdf_file

logger = logging.getLogger("gemsentry.master_sheet")

CONFIG_PATH = os.path.join(paths.CONFIG_DIR, "google_sync_config.json")
FINALIZED_STORE_PATH = os.path.join(paths.DATA_DIR, "finalized_tenders.local.json")
# Falls back to the copy in the repo root rather than one developer's Downloads
# folder. Override with GEMSENTRY_MASTER_XLSX or local_master_excel_path.
DEFAULT_LOCAL_MASTER_PATH = os.environ.get(
    "GEMSENTRY_MASTER_XLSX",
    os.path.join(paths.ROOT, "TENDER MASTER SHEET(ETSPL) 2025- 26.xlsx"),
)
WORKSPACE_MASTER_PATH = os.path.join(paths.ROOT, "TENDER MASTER SHEET(ETSPL) 2025- 26.xlsx")

# Baseline serial number if no previous records exist
BASELINE_SERIAL_NO = 1016

MASTER_COLUMNS = [
    "SL. NO", "DOWNLOAD FROM", "WORK CATEGORY", "DOWNLOAD DATE", "MONTH",
    "ORGANISATION", "LOCATION/SITE", "TENDER ID", "REFERENCE NO.", "DESCRIPTION",
    "BID SUBMISSION (END DATE)", "BID SUBMISSION (END TIME)", "EXPERIENCE EXEMPTION\nYES/ NO",
    "TURNOVER EXEMPTION\nYES/ NO", "EMD/ TENDER FEES", "OEM AUTHORIZATION", "RFP LINK",
    "APPROVAL", "REMARKS"
]

PARTICIPATED_COLUMNS = [
    "SL. NO", "STATUS", "DOWNLOAD FROM", "WORK CATEGORY", "DOWNLOAD DATE", "MONTH",
    "ORGANISATION", "LOCATIOIN/SITE", "TENDER ID", "REFERENCENO.", "DESCRIPTION",
    "BID SUBMISSION (END DATE)", "BID SUBMISSION (END TIME)", "BID OPENING DATE",
    "SUBMISSION STATUS", "SUBMITTED BY", "REMARKS", "JOB ALIGNED TO", "ETSPL CTC",
    "TENDER VALUE", "EMD/ TRANSACTION/ DOCUMENT", "TECHNICAL STATUS", "FINANCIAL STATUS",
    "RESULT\nWON/LOST", "SO/ DO  STATUS", "SO LINK", "REMARKS"
]

# Vendor definitions with color coding:
# - Drone -> Drone vendor (Red)
# - Power Supply & Electrical -> Power supply vendor (Yellow)
# - Face Rec & Biometrics -> Biometrics vendor (Blue)
VENDOR_DEFINITIONS = {
    "drone": {
        "id": "drone",
        "name": "Drone vendor",
        "category_label": "Drone / UAV",
        "hex_color": "FEE2E2",  # Soft Red
        "web_color": "#FEE2E2",
        "keywords": [
            "drone", "drones", "uav", "unmanned aerial", "quadcopter", "multirotor",
            "aerostat", "gis", "mapping", "surveillance drone"
        ]
    },
    "power_supply": {
        "id": "power_supply",
        "name": "Power supply vendor",
        "category_label": "Power Supply / Electrical",
        "hex_color": "FEF08A",  # Soft Yellow
        "web_color": "#FEF08A",
        "keywords": [
            "power supply", "rectifier", "lvpsu", "hvpsu", "static convertor",
            "battery charger", "solid state power amplifier", "voltage regulator",
            "ups", "psu", "smps", "transformer", "alternator", "electrical", "inverter"
        ]
    },
    "biometrics": {
        "id": "biometrics",
        "name": "Biometrics vendor",
        "category_label": "Biometrics & Facial Recognition",
        "hex_color": "BFDBFE",  # Soft Blue
        "web_color": "#BFDBFE",
        "keywords": [
            "facial recognition", "face recognition", "facial based", "face based",
            "biometric", "biometrics", "frs", "iris scanner", "iris recognition",
            "fingerprint", "access control", "aadhaar authentication", "e-kyc"
        ]
    }
}


def detect_vendor(tender: dict[str, Any] | None, custom_vendor: str | None = None,
                  vendor_sheets: dict | None = None) -> dict[str, Any]:
    """Detects the assigned vendor and master sheet color for a tender.

    Vendors:
    - Drone vendor (Drone / UAV) -> Red (FEE2E2)
    - Power supply vendor (Power Supply / Electrical) -> Yellow (FEF08A)
    - Biometrics vendor (Biometrics & Face Recognition) -> Blue (BFDBFE)
    """
    tender = tender or {}
    definitions = {key: {**value, "name": ((vendor_sheets or {}).get(key) or {}).get("name") or value["name"]}
                   for key, value in VENDOR_DEFINITIONS.items()}
    override = str(custom_vendor or tender.get("assigned_vendor") or "").strip().lower()
    if override:
        for vid, vinfo in definitions.items():
            if override in (vid, vinfo["name"].lower(), vinfo["category_label"].lower()) or vid in override:
                return vinfo
        if override in ("none", "unassigned", "default", "general"):
            return {"id": None, "name": None, "category_label": "General", "hex_color": None, "web_color": None}

    analysis = tender.get("analysis") or {}
    bl = analysis.get("business_line") or {}
    bl_id = str(bl.get("id") or "").lower()
    bl_label = str(bl.get("label") or "").lower()

    if bl_id == "drone" or "drone" in bl_label or "uav" in bl_label:
        return definitions["drone"]
    if bl_id in ("power_supply", "components") or "power" in bl_label or "electrical" in bl_label:
        return definitions["power_supply"]
    if bl_id == "biometrics" or "biometric" in bl_label or "face" in bl_label:
        return definitions["biometrics"]

    # No keyword heuristic guessing — only route if explicitly chosen by user
    return {"id": None, "name": None, "category_label": "General", "hex_color": None, "web_color": None}


class MasterSheetManager:
    """Coordinates finalized tenders between GeMSentry, Excel master sheets, and Google Sheets."""

    def __init__(self):
        self.lock = threading.RLock()
        self.config = self._load_config()
        self.finalized_records: list[dict[str, Any]] = self._load_store()
        self._ensure_serial_baseline()

    def _load_config(self) -> dict[str, Any]:
        default_cfg = {
            # Private IDs, URLs and the shared secret belong in ignored local
            # configuration or environment variables, never tracked defaults.
            "spreadsheet_id": "",
            "spreadsheet_url": "",
            "apps_script_url": "",
            "webhook_secret": "",
            "google_drive_mount_path": "",
            "local_master_excel_path": DEFAULT_LOCAL_MASTER_PATH,
            "sync_to_local_excel": True,
            "sync_to_google_sheet": True,
            "default_sheet": "UNDER DETAILED STUDY",
            "vendor_sheets": {
                "drone": {
                    "name": "Drone vendor",
                    "category": "Drone / UAV",
                    "spreadsheet_id": "",
                    "spreadsheet_url": "",
                    "color": "#FEE2E2"
                },
                "power_supply": {
                    "name": "Power supply vendor",
                    "category": "Power Supply / Electrical",
                    "spreadsheet_id": "",
                    "spreadsheet_url": "",
                    "color": "#FEF08A"
                },
                "biometrics": {
                    "name": "Biometrics vendor",
                    "category": "Biometrics & Facial Recognition",
                    "spreadsheet_id": "",
                    "spreadsheet_url": "",
                    "color": "#BFDBFE"
                }
            }
        }
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    user_cfg = json.load(f)
                    default_cfg.update(user_cfg)
            except Exception as e:
                logger.warning("Could not read google_sync_config.json: %s", e)

        # Environment wins over the file, so a deployment can inject secrets
        # without ever writing them to disk.
        for env_name, key in (
            ("GEMSENTRY_SHEET_ID", "spreadsheet_id"),
            ("GEMSENTRY_SHEET_URL", "spreadsheet_url"),
            ("GEMSENTRY_APPS_SCRIPT_URL", "apps_script_url"),
            ("GEMSENTRY_WEBHOOK_SECRET", "webhook_secret"),
            ("GEMSENTRY_MASTER_XLSX", "local_master_excel_path"),
        ):
            if os.environ.get(env_name):
                default_cfg[key] = os.environ[env_name]

        for env_name, vid in (
            ("GEMSENTRY_DRONE_SHEET_ID", "drone"),
            ("GEMSENTRY_POWER_SHEET_ID", "power_supply"),
            ("GEMSENTRY_BIOMETRICS_SHEET_ID", "biometrics"),
        ):
            if os.environ.get(env_name) and "vendor_sheets" in default_cfg and vid in default_cfg["vendor_sheets"]:
                default_cfg["vendor_sheets"][vid]["spreadsheet_id"] = os.environ[env_name]

        return default_cfg

    def load_config(self) -> dict[str, Any]:
        """Reloads and returns the latest config from disk."""
        with self.lock:
            self.config = self._load_config()
            return self.config

    def save_config(self, new_config: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if not isinstance(new_config, dict):
                raise ValueError("Configuration must be an object.")
            if "webhook_secret" in new_config and not isinstance(new_config["webhook_secret"], str):
                raise ValueError("Webhook secret must be text.")
            new_config = dict(new_config)
            if "webhook_secret" in new_config:
                new_config["webhook_secret"] = new_config["webhook_secret"].strip()
            new_config.pop("webhook_secret_configured", None)
            # A blank password field means keep the existing secret.
            if not new_config.get("webhook_secret"):
                new_config.pop("webhook_secret", None)
            if "vendor_sheets" in new_config and isinstance(new_config["vendor_sheets"], dict):
                current_vs = self.config.get("vendor_sheets") or {}
                for k, v in new_config["vendor_sheets"].items():
                    if k in current_vs and isinstance(v, dict):
                        current_vs[k].update(v)
                    else:
                        current_vs[k] = v
                new_config["vendor_sheets"] = current_vs
            self.config.update(new_config)
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            logger.info("Updated Google sync configuration.")
            return self.config

    def _load_store(self) -> list[dict[str, Any]]:
        if os.path.exists(FINALIZED_STORE_PATH):
            try:
                with open(FINALIZED_STORE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception as e:
                logger.warning("Failed loading finalized_tenders.json: %s", e)
        return []

    def _save_store(self):
        os.makedirs(os.path.dirname(FINALIZED_STORE_PATH), exist_ok=True)
        try:
            with open(FINALIZED_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.finalized_records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed persisting finalized_tenders.json: %s", e)

    def _get_active_master_paths(self) -> list[str]:
        paths_to_update = []
        configured_path = self.config.get("local_master_excel_path") or DEFAULT_LOCAL_MASTER_PATH
        if os.path.exists(configured_path):
            paths_to_update.append(configured_path)
        if os.path.exists(WORKSPACE_MASTER_PATH) and WORKSPACE_MASTER_PATH not in paths_to_update:
            paths_to_update.append(WORKSPACE_MASTER_PATH)
        return paths_to_update

    def _ensure_serial_baseline(self):
        """Scans local excel files to determine the current highest serial number from actual tenders."""
        max_sl = BASELINE_SERIAL_NO
        for rec in self.finalized_records:
            sl = rec.get("sl_no")
            if isinstance(sl, (int, float)) and sl > max_sl:
                max_sl = int(sl)

        for path in self._get_active_master_paths():
            try:
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                for sname in ["MASTER", "UNDER DETAILED STUDY", "(TENDER DETAILS (PARTICIPATED)"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        for row in sheet.iter_rows(values_only=True):
                            if not row or len(row) < 2:
                                continue
                            val_sl = row[0]
                            if val_sl is None:
                                continue
                            # Check if the row has any non-empty data in columns after col 1
                            has_data = any(c is not None and str(c).strip() != "" for c in row[1:])
                            if not has_data:
                                continue
                            # Skip header rows
                            str_sl = str(val_sl).strip()
                            if str_sl.upper() in ("SL. NO", "SL NO", "S.NO", "SL", "SERIAL NO"):
                                continue
                            try:
                                val = int(float(str_sl))
                                if BASELINE_SERIAL_NO <= val < 50000 and val > max_sl:
                                    max_sl = val
                            except (ValueError, TypeError):
                                pass
                wb.close()
            except Exception as e:
                logger.debug("Error checking baseline serial from %s: %s", path, e)

        self.highest_serial_no = max_sl

    def get_highest_serial_number(self, refresh: bool = False) -> int:
        with self.lock:
            if refresh:
                self._ensure_serial_baseline()
            max_sl = getattr(self, "highest_serial_no", BASELINE_SERIAL_NO)
            for rec in self.finalized_records:
                sl = rec.get("sl_no")
                if isinstance(sl, (int, float)) and sl > max_sl:
                    max_sl = int(sl)
            return max_sl

    def is_tender_finalized(self, bid_no: str) -> bool:
        norm_bid = str(bid_no).strip().lower()
        return any(str(r.get("bid_no")).strip().lower() == norm_bid for r in self.finalized_records)

    def get_record(self, bid_no: str) -> dict[str, Any] | None:
        norm_bid = str(bid_no).strip().lower()
        for r in self.finalized_records:
            if str(r.get("bid_no")).strip().lower() == norm_bid:
                return r
        return None

    def _format_date_parts(self, date_str: str | None) -> tuple[str, str]:
        if not date_str:
            return "N/A", "15:00"
        dt = parse_gem_date(date_str)
        if dt:
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        return str(date_str)[:10], "15:00"

    def _handle_google_drive(self, tender: dict[str, Any], bid_no: str) -> str:
        """Handles PDF copy to mounted Google Drive or cloud upload."""
        # 1. Reuse existing Google Drive link if already present
        existing_drive = tender.get("rfp_link") or tender.get("drive_link")
        if existing_drive and "drive.google.com" in str(existing_drive):
            return str(existing_drive)

        local_pdf = tender.get("local_pdf_path")
        abs_pdf = ""
        if local_pdf:
            abs_pdf = local_pdf if os.path.isabs(local_pdf) else os.path.join(paths.ROOT, local_pdf)
            if not os.path.exists(abs_pdf):
                abs_pdf = ""

        # Auto-search disk for PDF if not specified
        if not abs_pdf:
            sanitized = re.sub(r'[\\/*?:"<>|]', "_", bid_no)
            found = find_existing_pdf_file(sanitized)
            if found:
                abs_pdf = os.path.join(paths.ROOT, found) if not os.path.isabs(found) else found

        # 2. Check local mounted drive folder (e.g. G:\My Drive\...)
        mount_path = (self.config.get("google_drive_mount_path") or "").strip()
        if mount_path and os.path.exists(mount_path) and os.path.isdir(mount_path) and abs_pdf and os.path.exists(abs_pdf):
            try:
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", bid_no) + ".pdf"
                dest = os.path.join(mount_path, safe_name)
                shutil.copy2(abs_pdf, dest)
                logger.info("Copied tender PDF to mounted Google Drive: %s", dest)
                return dest
            except Exception as e:
                logger.warning("Failed copying PDF to mounted Google Drive: %s", e)

        # 3. Check Apps Script direct drive uploader
        apps_script_url = (self.config.get("apps_script_url") or "").strip()
        if apps_script_url and abs_pdf and os.path.exists(abs_pdf):
            try:
                with open(abs_pdf, "rb") as f:
                    pdf_b64 = base64.b64encode(f.read()).decode("utf-8")
                data = post_webhook(
                    self.config,
                    {
                        "action": "upload_pdf_to_drive",
                        "filename": f"{re.sub(r'[\\\\/*?:\"<>|]', '_', bid_no)}.pdf",
                        "base64_data": pdf_b64
                    },
                    timeout=20
                )
                if data.get("drive_link"):
                    logger.info("Uploaded tender PDF to Google Drive via Apps Script: %s", data["drive_link"])
                    return data["drive_link"]
            except Exception as e:
                logger.warning("Google Drive upload failed (%s).", type(e).__name__)

        # 4. Fallback: GeM PDF link or local relative path
        return tender.get("pdf_url") or local_pdf or ""

    def _sync_to_local_excel(self, record: dict[str, Any], target_sheet: str) -> bool:
        """Writes row to local Excel files, respecting empty rows, header offsets, and styling neatly."""
        success = True
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        for path in self._get_active_master_paths():
            try:
                wb = openpyxl.load_workbook(path)
                if target_sheet not in wb.sheetnames:
                    logger.warning("Sheet %s not found in %s", target_sheet, path)
                    wb.close()
                    continue
                ws = wb[target_sheet]

                header_row = 2 if "PARTICIPATED" in target_sheet else 4

                # 1. Search if this exact tender already exists in sheet to update in-place
                target_row = None
                norm_bid = str(record.get("bid_no") or "").strip().lower()
                target_sl = str(record.get("sl_no")).strip() if record.get("sl_no") is not None else None
                col_bid = 9 if "PARTICIPATED" in target_sheet else 8
                col_ref = 10 if "PARTICIPATED" in target_sheet else 9

                for r in range(header_row + 1, ws.max_row + 1):
                    cell_id = ws.cell(row=r, column=col_bid).value
                    cell_ref = ws.cell(row=r, column=col_ref).value

                    id_match = norm_bid and (
                        (cell_id and str(cell_id).strip().lower() == norm_bid) or
                        (cell_ref and str(cell_ref).strip().lower() == norm_bid)
                    )
                    if id_match:
                        target_row = r
                        break

                # 2. If tender does not already exist, check if there is an UNOCCUPIED row matching target_sl
                # (e.g. pre-numbered template rows like 1023..1200 that are completely empty in columns 2..19).
                # NEVER overwrite an already occupied row to prevent erasing/superimposing existing data!
                if not target_row and target_sl:
                    for r in range(header_row + 1, ws.max_row + 1):
                        cell_sl = ws.cell(row=r, column=1).value
                        if cell_sl is not None and str(cell_sl).strip() == target_sl:
                            max_check = max(ws.max_column, 20)
                            row_has_data = any(
                                ws.cell(row=r, column=c).value is not None and
                                str(ws.cell(row=r, column=c).value).strip() != ""
                                for c in range(2, max_check + 1)
                            )
                            if not row_has_data:
                                target_row = r
                                break
                            else:
                                logger.warning(
                                    "Row %d in %s has SL %s but already contains data for another entry. "
                                    "Will not overwrite/erase; searching for next available empty row.",
                                    r, target_sheet, target_sl
                                )

                # 3. If no matching slot found, find first genuinely empty row (all columns empty)
                if not target_row:
                    for r in range(header_row + 1, ws.max_row + 2):
                        max_check = max(ws.max_column, 20)
                        row_has_any_data = any(
                            ws.cell(row=r, column=c).value is not None and
                            str(ws.cell(row=r, column=c).value).strip() != ""
                            for c in range(1, max_check + 1)
                        )
                        if not row_has_any_data:
                            target_row = r
                            break

                if not target_row:
                    target_row = ws.max_row + 1

                # Set generous 36pt row height for neat visibility
                ws.row_dimensions[target_row].height = 36.0

                # Populate row cells
                if "PARTICIPATED" in target_sheet:
                    row_data = [
                        record.get("sl_no"),
                        record.get("tender_type", "RFP"),
                        record.get("download_from", "GEM"),
                        record.get("work_category", "SUPPLY"),
                        record.get("download_date"),
                        record.get("month"),
                        record.get("organisation"),
                        record.get("location"),
                        record.get("bid_no"),
                        record.get("bid_no"),
                        record.get("title"),
                        record.get("end_date"),
                        record.get("end_time"),
                        record.get("drive_link") or record.get("rfp_link") or "",
                        record.get("submission_status", "SUBMITTED"),
                        record.get("submitted_by", "SUBMITTED BY ETSPL"),
                        record.get("remarks", ""),
                        record.get("job_aligned_to", ""),
                        record.get("etspl_ctc", ""),
                        record.get("tender_value", "N/A"),
                        record.get("emd_doc", "EXEMPTED"),
                        record.get("technical_status", "QUALIFIED"),
                        record.get("financial_status", "QUALIFIED"),
                        record.get("won_lost_result", "WON L - 1"),
                        record.get("so_status", "SO RECEIVED"),
                        record.get("so_link", ""),
                        record.get("final_remarks", "")
                    ]
                else:
                    row_data = [
                        record.get("sl_no"),
                        record.get("download_from", "GEM"),
                        record.get("work_category", "SUPPLY"),
                        record.get("download_date"),
                        record.get("month"),
                        record.get("organisation"),
                        record.get("location"),
                        record.get("bid_no"),
                        record.get("bid_no"),
                        record.get("title"),
                        record.get("end_date"),
                        record.get("end_time"),
                        record.get("experience_exemption", "YES"),
                        record.get("turnover_exemption", "YES"),
                        record.get("emd", 0.0),
                        record.get("oem_authorization", "YES"),
                        record.get("rfp_link", ""),
                        record.get("approval", "TO BE SUBMIT"),
                        record.get("remarks", "")
                    ]

                is_part = "PARTICIPATED" in target_sheet
                vendor_hex = str(record.get("vendor_color") or "").lstrip("#").upper()
                vendor_fill = PatternFill(start_color=vendor_hex, end_color=vendor_hex, fill_type="solid") if len(vendor_hex) == 6 else None

                for col_idx, val in enumerate(row_data, 1):
                    cell = ws.cell(row=target_row, column=col_idx, value=val)
                    cell.font = Font(name="Arial", size=11)
                    cell.border = thin_border
                    if vendor_fill:
                        cell.fill = vendor_fill

                    # Alignments & formatting
                    if not is_part:
                        if col_idx in (1, 2, 3, 4, 5, 8, 9, 11, 12, 13, 14, 16, 17, 18):
                            cell.alignment = Alignment(vertical="center", horizontal="center")
                        elif col_idx in (6, 10, 19):
                            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
                        elif col_idx == 15:
                            cell.alignment = Alignment(vertical="center", horizontal="right")
                            cell.number_format = '#,##0'

                        if col_idx in (1, 8):
                            cell.font = Font(name="Arial", size=11, bold=True)
                        elif col_idx == 17 and val and str(val).startswith("http"):
                            cell.hyperlink = str(val)
                            cell.value = "Google Drive RFP ↗" if "drive.google.com" in str(val) else "Open RFP ↗"
                            cell.font = Font(name="Arial", size=11, color="1D4ED8", underline="single", bold=True)
                        elif col_idx == 18:
                            cell.font = Font(name="Arial", size=11, color="047857", bold=True)
                    else:
                        if col_idx in (1, 2, 3, 4, 5, 9, 10, 12, 13, 14, 15, 24, 25, 26):
                            cell.alignment = Alignment(vertical="center", horizontal="center")
                        elif col_idx in (7, 8, 11, 17, 27):
                            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
                        elif col_idx in (20, 21):
                            cell.alignment = Alignment(vertical="center", horizontal="right")
                            cell.number_format = '#,##0'

                        if col_idx in (1, 9):
                            cell.font = Font(name="Arial", size=11, bold=True)
                        elif col_idx == 14 and val and str(val).startswith("http"):
                            cell.hyperlink = str(val)
                            cell.value = "Google Drive RFP ↗" if "drive.google.com" in str(val) else "Open RFP ↗"
                            cell.font = Font(name="Arial", size=11, color="1D4ED8", underline="single", bold=True)
                        elif col_idx == 24:
                            is_won = "WON" in str(val).upper()
                            cell.font = Font(name="Arial", size=11, color="047857" if is_won else "DC2626", bold=True)
                        elif col_idx == 26 and val and str(val).startswith("http"):
                            cell.hyperlink = str(val)
                            cell.value = "Open SO Doc ↗"
                            cell.font = Font(name="Arial", size=11, color="1D4ED8", underline="single", bold=True)

                wb.save(path)
                wb.close()
                logger.info("Successfully wrote row %d to sheet %s in %s", target_row, target_sheet, path)
            except PermissionError:
                logger.warning("Could not write to %s because it is open in another program.", path)
                success = False
            except Exception as e:
                logger.error("Error writing to local Excel workbook %s: %s", path, e)
                success = False
        return success

    def _delete_from_local_excel(self, bid_no: str, sl_no: int | None = None) -> int:
        deleted_count = 0
        norm_bid = str(bid_no).strip().lower()
        for path in self._get_active_master_paths():
            try:
                wb = openpyxl.load_workbook(path)
                for sname in ["UNDER DETAILED STUDY", "MASTER", "(TENDER DETAILS (PARTICIPATED)"]:
                    if sname not in wb.sheetnames:
                        continue
                    ws = wb[sname]
                    header_row = 2 if "PARTICIPATED" in sname else 4
                    # Iterate backwards from bottom to header
                    for r in range(ws.max_row, header_row, -1):
                        cell_id = ws.cell(row=r, column=8).value
                        cell_ref = ws.cell(row=r, column=9).value
                        cell_sl = ws.cell(row=r, column=1).value

                        id_match = norm_bid and (
                            (cell_id and str(cell_id).strip().lower() == norm_bid) or
                            (cell_ref and str(cell_ref).strip().lower() == norm_bid)
                        )
                        sl_match = sl_no and cell_sl and int(float(str(cell_sl))) == sl_no

                        if id_match or sl_match:
                            ws.delete_rows(r, 1)
                            deleted_count += 1
                            logger.info("Deleted row %d from %s in %s", r, sname, path)

                wb.save(path)
                wb.close()
            except Exception as e:
                logger.error("Error deleting tender from local Excel %s: %s", path, e)
        return deleted_count

    def _sync_to_google_sheet(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sends action payload to Google Apps Script Webhook."""
        self.load_config()
        apps_script_url = (self.config.get("apps_script_url") or "").strip()
        if not apps_script_url:
            return {"status": "skipped", "message": "No Google Apps Script Webhook URL configured."}
        try:
            data = post_webhook(self.config, payload)
            logger.info("Google Sheet sync status: %s", data.get("status", "unknown"))
            return data
        except Exception as e:
            logger.warning("Google Sheet webhook request failed (%s).", type(e).__name__)
            message = str(e) if isinstance(e, ValueError) else "Google Sheet webhook request failed."
            return {"status": "error", "message": message}

    def _build_gsheet_payload(
        self,
        record: dict[str, Any],
        target_sheet: str = "MASTER",
        secondary_sheet: str | None = None
    ) -> dict[str, Any]:
        """Builds a comprehensive payload for Google Apps Script with both flat fields and nested tender."""
        rfp = record.get("rfp_link") or record.get("drive_link") or record.get("pdf_url") or ""
        vendor_sheets = self.config.get("vendor_sheets") or {}
        return {
            "action": "append_tender",
            "target_sheet": target_sheet,
            "secondary_sheet": secondary_sheet,
            "sl_no": record.get("sl_no"),
            "download_from": record.get("download_from", "GEM"),
            "work_category": record.get("work_category", "SUPPLY"),
            "download_date": record.get("download_date"),
            "month": record.get("month"),
            "organisation": record.get("organisation"),
            "location": record.get("location"),
            "tender_id": record.get("bid_no"),
            "reference_no": record.get("bid_no"),
            "description": record.get("title"),
            "end_date": record.get("end_date"),
            "end_time": record.get("end_time"),
            "experience_exemption": record.get("experience_exemption", "YES"),
            "turnover_exemption": record.get("turnover_exemption", "YES"),
            "emd": record.get("emd", 0.0),
            "oem_authorization": record.get("oem_authorization", "YES"),
            "rfp_link": rfp,
            "approval": record.get("approval", "TO BE SUBMIT"),
            "remarks": record.get("remarks", ""),
            "tech_spec_url": record.get("tech_spec_url") or "",
            "tech_spec_filename": record.get("tech_spec_filename") or "",
            "vendor_id": record.get("vendor_id"),
            "vendor_name": record.get("vendor_name"),
            "vendor_color": record.get("vendor_color"),
            "vendor_web_color": record.get("vendor_web_color"),
            "vendor_category": record.get("vendor_category"),
            "vendor_sheets": vendor_sheets,
            "tender": record
        }

    def finalize_tender(
        self,
        tender: dict[str, Any],
        target_sheet: str = "UNDER DETAILED STUDY",
        custom_fields: dict[str, Any] | None = None,
        sl_no: int | None = None
    ) -> dict[str, Any]:
        """Finalizes a tender, assigns sequential SL. NO, updates Excel & Google Sheet."""
        with self.lock:
            bid_no = tender.get("bid_no") or "UNKNOWN"
            custom_fields = custom_fields or {}

            requested_sl = sl_no or custom_fields.get("sl_no")
            if requested_sl is not None:
                try:
                    requested_sl = int(requested_sl)
                except (ValueError, TypeError):
                    requested_sl = None

            # Check if already finalized
            existing = self.get_record(bid_no)
            if requested_sl is not None:
                sl_no = requested_sl
                self.highest_serial_no = max(getattr(self, "highest_serial_no", BASELINE_SERIAL_NO), sl_no)
            elif existing and existing.get("sl_no"):
                sl_no = existing.get("sl_no")
            else:
                sl_no = self.get_highest_serial_number(refresh=True) + 1
                self.highest_serial_no = sl_no

            analysis = tender.get("analysis") or {}
            now = datetime.datetime.now()
            end_date, end_time = self._format_date_parts(tender.get("end_date"))

            # Handle Google Drive
            rfp_link = self._handle_google_drive(tender, bid_no)

            # Exemptions & EMD
            exp_exempt = "YES" if "yes" in str(analysis.get("startup_exemption", "")).lower() else "NO"
            trn_exempt = "YES" if "yes" in str(analysis.get("mse_exemption", "")).lower() else "NO"
            emd_val = analysis.get("emd_amount") or 0.0
            if "no emd" in str(analysis.get("emd_status", "")).lower():
                emd_val = 0.0

            # Work Category
            work_cat = (
                custom_fields.get("work_category") or
                (analysis.get("business_line") or {}).get("label") or
                tender.get("nlp_category") or
                tender.get("item_category") or
                "SUPPLY"
            ).upper()

            # Vendor Detection & Assignment
            vendor_override = custom_fields.get("assigned_vendor")
            vendor_info = detect_vendor(tender, vendor_override, self.config.get("vendor_sheets"))

            record = {
                "sl_no": sl_no,
                "bid_no": bid_no,
                "download_from": (tender.get("source_name") or tender.get("source_id") or "GEM").upper(),
                "work_category": work_cat,
                "download_date": now.strftime("%Y-%m-%d"),
                "month": now.strftime("%B").upper(),
                "organisation": analysis.get("buyer_org") or tender.get("department") or "N/A",
                "location": analysis.get("consignee_state") or "N/A",
                "title": tender.get("title") or analysis.get("primary_item") or "N/A",
                "end_date": end_date,
                "end_time": end_time,
                "experience_exemption": exp_exempt,
                "turnover_exemption": trn_exempt,
                "emd": emd_val,
                "oem_authorization": custom_fields.get("oem_authorization") or "YES",
                "rfp_link": rfp_link,
                "approval": custom_fields.get("approval") or "TO BE SUBMIT",
                "remarks": custom_fields.get("remarks") or (f"Pre-Bid: {analysis.get('pre_bid_date')}" if analysis.get("pre_bid_date") else ""),
                "target_sheet": target_sheet,
                "finalized_at": now.isoformat(),
                "est_value_inr": analysis.get("est_value_inr") or tender.get("est_value_inr"),
                "vendor_id": vendor_info.get("id"),
                "vendor_name": vendor_info.get("name"),
                "vendor_color": vendor_info.get("hex_color"),
                "vendor_web_color": vendor_info.get("web_color"),
                "vendor_category": vendor_info.get("category_label"),
                "tech_spec_url": custom_fields.get("tech_spec_url") or (existing.get("tech_spec_url", "") if existing else ""),
                "tech_spec_filename": custom_fields.get("tech_spec_filename") or (existing.get("tech_spec_filename", "") if existing else ""),
                "tech_spec_updated_at": existing.get("tech_spec_updated_at", "") if existing else (now.isoformat() if custom_fields.get("tech_spec_url") else "")
            }

            # Update records
            if existing:
                existing.update(record)
            else:
                self.finalized_records.append(record)
            self._save_store()

            # Local Excel Sync - Compulsorily append to MASTER, and also to target_sheet if different
            excel_synced = self._sync_to_local_excel(record, "MASTER")
            if target_sheet and target_sheet != "MASTER":
                self._sync_to_local_excel(record, target_sheet)

            # Google Sheet Sync - Compulsorily append to MASTER
            gsheet_payload = self._build_gsheet_payload(
                record=record,
                target_sheet="MASTER",
                secondary_sheet=target_sheet if target_sheet != "MASTER" else None
            )
            gsheet_res = self._sync_to_google_sheet(gsheet_payload)

            return {
                "status": "ok",
                "sl_no": sl_no,
                "bid_no": bid_no,
                "record": record,
                "local_excel_synced": excel_synced,
                "google_sheet_synced": gsheet_res.get("status") == "ok",
                "google_response": gsheet_res
            }

    def delete_tender(self, bid_no_or_sl_no: Any) -> dict[str, Any]:
        """Deletes a finalized tender from JSON store, local Excel, and Google Sheet."""
        with self.lock:
            target_bid = None
            target_sl = None

            # Match in store
            matched_idx = -1
            for idx, r in enumerate(self.finalized_records):
                if str(r.get("bid_no")).strip().lower() == str(bid_no_or_sl_no).strip().lower():
                    matched_idx = idx
                    target_bid = r.get("bid_no")
                    target_sl = r.get("sl_no")
                    break
                if str(r.get("sl_no")) == str(bid_no_or_sl_no):
                    matched_idx = idx
                    target_bid = r.get("bid_no")
                    target_sl = r.get("sl_no")
                    break

            if matched_idx >= 0:
                self.finalized_records.pop(matched_idx)
                self._save_store()
            else:
                target_bid = str(bid_no_or_sl_no)

            # Delete from Excel
            excel_del = self._delete_from_local_excel(target_bid, target_sl)

            # Delete from Google Sheet
            gsheet_res = self._sync_to_google_sheet({
                "action": "delete_tender",
                "bid_no": target_bid,
                "sl_no": target_sl
            })

            return {
                "status": "ok",
                "deleted_bid": target_bid,
                "deleted_sl": target_sl,
                "excel_rows_deleted": excel_del,
                "google_response": gsheet_res
            }

    def move_to_participated(
        self,
        bid_no: str,
        won_lost_result: str = "WON L - 1",
        tender_value: Any | None = None,
        so_link: str | None = None,
        submission_status: str = "SUBMITTED",
        final_remarks: str | None = None
    ) -> dict[str, Any]:
        """Transitions a finalized tender to '(TENDER DETAILS (PARTICIPATED)'."""
        with self.lock:
            record = self.get_record(bid_no)
            if not record:
                return {"status": "error", "message": f"Tender {bid_no} is not finalized yet."}

            record["tender_type"] = "RFP"
            record["won_lost_result"] = won_lost_result
            record["submission_status"] = submission_status
            record["submitted_by"] = "SUBMITTED BY ETSPL"
            record["tender_value"] = tender_value or record.get("est_value_inr") or "N/A"
            record["so_status"] = "SO RECEIVED" if "won" in won_lost_result.lower() else "SUBMITTED"
            record["so_link"] = so_link or ""
            record["final_remarks"] = final_remarks or ""
            record["target_sheet"] = "(TENDER DETAILS (PARTICIPATED)"
            self._save_store()

            # Append to Participated sheet in Excel
            excel_synced = self._sync_to_local_excel(record, "(TENDER DETAILS (PARTICIPATED)")

            # Send to Google Sheet
            gsheet_res = self._sync_to_google_sheet({
                "action": "move_to_participated",
                "tender_id": bid_no,
                "sl_no": record.get("sl_no"),
                "won_lost_result": won_lost_result,
                "tender_value": record.get("tender_value"),
                "so_link": record.get("so_link"),
                "tender": record
            })

            return {
                "status": "ok",
                "bid_no": bid_no,
                "sl_no": record.get("sl_no"),
                "result": won_lost_result,
                "local_excel_synced": excel_synced,
                "google_response": gsheet_res
            }

    def update_tech_spec(
        self,
        bid_no: str,
        tech_spec_url: str | None = None,
        filename: str | None = None,
        file_bytes: bytes | None = None,
        assigned_vendor: str | None = None
    ) -> dict[str, Any]:
        """Updates or attaches technical specification sheet for a specific tender."""
        with self.lock:
            record = self.get_record(bid_no)
            if not record:
                return {"status": "error", "message": f"Tender {bid_no} is not finalized yet. Please finalize it first."}

            now_iso = datetime.datetime.now().isoformat()
            local_file_path = None

            # If user selected / updated the destination vendor
            if assigned_vendor is not None:
                vinfo = detect_vendor(record, custom_vendor=assigned_vendor, vendor_sheets=self.config.get("vendor_sheets"))
                record["vendor_id"] = vinfo.get("id")
                record["vendor_name"] = vinfo.get("name")
                record["vendor_color"] = vinfo.get("hex_color")
                record["vendor_web_color"] = vinfo.get("web_color")
                record["job_aligned_to"] = vinfo.get("name")

            # Handle local file storage if file_bytes provided
            if file_bytes and filename:
                safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", bid_no)
                spec_dir = os.path.join(paths.TECH_SPECS_DIR, safe_slug)
                os.makedirs(spec_dir, exist_ok=True)
                clean_name = os.path.basename(filename)
                local_file_path = os.path.join(spec_dir, clean_name)
                with open(local_file_path, "wb") as f:
                    f.write(file_bytes)
                logger.info("Saved local tech spec sheet for %s at %s", bid_no, local_file_path)

                # If no URL explicitly passed, attempt upload to Google Drive via Apps Script Webhook
                if not tech_spec_url and self.config.get("apps_script_url"):
                    b64 = base64.b64encode(file_bytes).decode("utf-8")
                    drive_res = self._sync_to_google_sheet({
                        "action": "upload_tech_spec_to_drive",
                        "bid_no": bid_no,
                        "filename": clean_name,
                        "base64_data": b64,
                        "vendor_id": record.get("vendor_id")
                    })
                    if drive_res.get("status") == "ok" and drive_res.get("drive_link"):
                        tech_spec_url = drive_res["drive_link"]
                        logger.info("Uploaded tech spec to Google Drive: %s", tech_spec_url)

                if not tech_spec_url:
                    tech_spec_url = f"/api/finalized/spec-sheet/{safe_slug}"

            if tech_spec_url:
                record["tech_spec_url"] = tech_spec_url
            if filename:
                record["tech_spec_filename"] = filename
            record["tech_spec_updated_at"] = now_iso
            self._save_store()

            # Push live update to Google Apps Script Webhook
            gsheet_res = self._sync_to_google_sheet({
                "action": "update_tech_spec",
                "bid_no": bid_no,
                "sl_no": record.get("sl_no"),
                "title": record.get("title"),
                "tech_spec_url": record.get("tech_spec_url"),
                "vendor_id": record.get("vendor_id"),
                "vendor_sheets": self.config.get("vendor_sheets", {})
            })

            return {
                "status": "ok",
                "bid_no": bid_no,
                "sl_no": record.get("sl_no"),
                "tech_spec_url": record.get("tech_spec_url"),
                "tech_spec_filename": record.get("tech_spec_filename"),
                "vendor_id": record.get("vendor_id"),
                "vendor_name": record.get("vendor_name"),
                "google_response": gsheet_res
            }

    def sync_all_to_google_sheet(self) -> dict[str, Any]:
        """Pushes all finalized tenders from local store into Google Sheet."""
        with self.lock:
            apps_script_url = (self.config.get("apps_script_url") or "").strip()
            if not apps_script_url:
                return {
                    "status": "error",
                    "message": "No Google Apps Script Webhook URL configured in Settings. Please deploy the Apps Script as a Web App and paste its URL."
                }

            if not self.finalized_records:
                return {
                    "status": "ok",
                    "message": "No finalized tenders to sync.",
                    "synced_count": 0
                }

            synced = []
            errors = []
            for record in self.finalized_records:
                target_sheet = record.get("target_sheet") or "UNDER DETAILED STUDY"
                # Ensure local Excel has it in MASTER
                self._sync_to_local_excel(record, "MASTER")
                if target_sheet and target_sheet != "MASTER":
                    self._sync_to_local_excel(record, target_sheet)

                payload = self._build_gsheet_payload(
                    record=record,
                    target_sheet="MASTER",
                    secondary_sheet=target_sheet if target_sheet != "MASTER" else None
                )
                res = self._sync_to_google_sheet(payload)
                if res.get("status") == "ok":
                    synced.append(record.get("sl_no"))
                else:
                    errors.append({
                        "sl_no": record.get("sl_no"),
                        "bid_no": record.get("bid_no"),
                        "error": res.get("message") or res.get("error") or str(res)
                    })

            return {
                "status": "ok",
                "synced_count": len(synced),
                "synced_sl_nos": synced,
                "errors": errors,
                "message": f"Successfully synced {len(synced)} of {len(self.finalized_records)} tender(s) to Google Sheet."
            }

    def get_all_finalized(self) -> dict[str, Any]:
        with self.lock:
            sorted_records = sorted(
                self.finalized_records,
                key=lambda r: int(r.get("sl_no") or 0),
                reverse=True
            )
            sheets_count = {}
            for r in self.finalized_records:
                s = r.get("target_sheet", "UNDER DETAILED STUDY")
                sheets_count[s] = sheets_count.get(s, 0) + 1
                # Auto-backfill vendor metadata for older records if missing
                if not r.get("vendor_id") and (r.get("work_category") or r.get("title")):
                    v = detect_vendor(r, custom_vendor=r.get("vendor_name"), vendor_sheets=self.config.get("vendor_sheets"))
                    if v.get("id"):
                        r["vendor_id"] = v.get("id")
                        r["vendor_name"] = v.get("name")
                        r["vendor_color"] = v.get("hex_color")
                        r["vendor_web_color"] = v.get("web_color")
                        r["vendor_category"] = v.get("category_label")
                if "tech_spec_url" not in r:
                    r["tech_spec_url"] = ""
                if "tech_spec_filename" not in r:
                    r["tech_spec_filename"] = ""

            return {
                "total_count": len(self.finalized_records),
                "highest_serial_no": self.get_highest_serial_number(refresh=True),
                "records": sorted_records,
                "counts_by_sheet": sheets_count,
                "spreadsheet_url": self.config.get("spreadsheet_url"),
                "spreadsheet_id": self.config.get("spreadsheet_id"),
                "has_webhook": bool(self.config.get("apps_script_url")),
                "has_gdrive_mount": bool(self.config.get("google_drive_mount_path")),
                "vendor_definitions": VENDOR_DEFINITIONS,
                "vendor_sheets": self.config.get("vendor_sheets", {})
            }


master_sheet_manager = MasterSheetManager()


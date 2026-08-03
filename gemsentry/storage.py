"""Tender metadata persistence, PDF index and workspace teardown."""

import csv
import datetime
import json
import os
import paths
import shutil

from gemsentry.constants import TENDERS_DIR, logger
from gemsentry.profile import workspace_label, workspace_paths


def build_pdf_index(downloads_dir):
    """
    One-pass {filename: repo-relative path} index of downloaded PDFs (BE-27).
    Replaces the per-tender os.walk in scrape() that went O(N²) as the
    inventory grew. Skips complete_summary (flattened copies) and backups.
    """
    index = {}
    if not os.path.exists(downloads_dir):
        return index
    for root, dirs, files in os.walk(downloads_dir):
        dirs[:] = [d for d in dirs if d not in ("complete_summary", "backups")]
        for fname in files:
            if fname.lower().endswith(".pdf") and fname not in index:
                full = os.path.join(root, fname)
                try:
                    if os.path.getsize(full) > 0:
                        index[fname] = paths.repo_relative(full)
                except OSError:
                    continue
    return index


def find_existing_pdf_file(sanitized_bid, downloads_dir=None):
    downloads_dir = downloads_dir if downloads_dir is not None else workspace_paths()[1]
    if os.path.exists(downloads_dir):
        for root, dirs, files in os.walk(downloads_dir):
            expected_filename = f"{sanitized_bid}.pdf"
            if expected_filename in files:
                full_path = os.path.join(root, expected_filename)
                if os.path.getsize(full_path) > 0:
                    return paths.repo_relative(full_path)
    return None


def load_existing_metadata(tenders_dir=None):
    """Load the workspace's tender records keyed by bid number.

    ``metadata.json`` is the complete record and is preferred. The CSV loader
    below is a legacy fallback that can only rebuild a fixed set of columns --
    it silently drops everything else (source_id, source_name, est_value_inr,
    domain, score), so it is used only when the JSON is missing or unreadable.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]

    json_path = os.path.join(tenders_dir, "metadata.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list):
                loaded = {
                    t["bid_no"]: t for t in records
                    if isinstance(t, dict) and t.get("bid_no")
                }
                logger.info("Loaded %d existing records from metadata.json", len(loaded))
                return loaded
            logger.error("metadata.json is not a list; falling back to CSV.")
        except (OSError, ValueError) as e:
            logger.error("Error reading metadata.json (%s); falling back to CSV.", e)

    return _load_metadata_csv(tenders_dir)


def _load_metadata_csv(tenders_dir):
    """Legacy reader for workspaces predating metadata.json."""
    existing_tenders = {}
    csv_path = os.path.join(tenders_dir, "metadata.csv")
    if os.path.exists(csv_path):
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bid_no = row.get("Bid Number")
                    if bid_no:
                        # Load parsed analysis JSON if it exists
                        analysis = None
                        analysis_str = row.get("Analysis")
                        if analysis_str:
                            try:
                                analysis = json.loads(analysis_str)
                            except:
                                pass
                                
                        existing_tenders[bid_no] = {
                            "bid_no": bid_no,
                            "title": row.get("Title"),
                            "quantity": row.get("Quantity"),
                            "department": row.get("Department"),
                            "start_date": row.get("Start Date"),
                            "end_date": row.get("End Date"),
                            "keyword": row.get("Keyword"),
                            "downloaded": row.get("Downloaded") == "True",
                            "local_pdf_path": row.get("Local PDF Path"),
                            "pdf_url": row.get("PDF URL"),
                            "first_seen": row.get("First Seen") or None,
                            "status": row.get("Status", "Pending Review"),
                            "status_source": row.get("Status Source") or None,
                            "source_id": row.get("Source ID") or None,
                            "source_name": row.get("Source Name") or None,
                            "analysis": analysis
                        }
            logger.info(f"Loaded {len(existing_tenders)} existing records from metadata.csv")
        except Exception as e:
            logger.error(f"Error reading existing CSV metadata: {e}")
    return existing_tenders


def save_metadata(tenders_list, tenders_dir=None):
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]
    os.makedirs(tenders_dir, exist_ok=True)
    # Save JSON
    json_path = os.path.join(tenders_dir, "metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tenders_list, f, indent=2, ensure_ascii=False)

    # Save JS (for dashboard)
    js_path = os.path.join(tenders_dir, "metadata.js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("// GeM Scraper Output Metadata\n")
        f.write(f"const TENDER_DATA = {json.dumps(tenders_list, indent=2, ensure_ascii=False)};\n")

    # Save CSV
    csv_path = os.path.join(tenders_dir, "metadata.csv")
    try:
        with open(csv_path, mode="w", encoding="utf-8", newline="") as f:
            fieldnames = ["Bid Number", "Title", "Quantity", "Department", "Start Date", "End Date", "Keyword", "Downloaded", "Local PDF Path", "PDF URL", "First Seen", "Status", "Status Source", "Source ID", "Source Name", "Analysis"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in tenders_list:
                writer.writerow({
                    "Bid Number": t["bid_no"],
                    "Title": t["title"],
                    "Quantity": t["quantity"],
                    "Department": t["department"],
                    "Start Date": t["start_date"],
                    "End Date": t["end_date"],
                    "Keyword": t["keyword"],
                    "Downloaded": str(t["downloaded"]),
                    "Local PDF Path": t["local_pdf_path"],
                    "PDF URL": t["pdf_url"],
                    "First Seen": t.get("first_seen") or "",
                    "Status": t.get("status", "Pending Review"),
                    "Status Source": t.get("status_source") or "",
                    "Source ID": t.get("source_id") or "gem",
                    "Source Name": t.get("source_name") or "Government e-Marketplace (GeM)",
                    "Analysis": json.dumps(t.get("analysis")) if t.get("analysis") else ""
                })
        logger.info(f"Saved metadata CSV: {csv_path}")
    except Exception as e:
        logger.error(f"Error saving CSV metadata: {e}")


def auto_export_summary(tenders_dir, downloads_dir=None):
    """
    Refresh <workspace>/reports/tender_summary.xlsx after metadata changes so
    the Excel always mirrors the latest verdicts. Never fails the caller —
    e.g. the workbook being open in Excel (file lock) only logs a warning.
    """
    try:
        from tools.export_summary import export_workbook
        summary = export_workbook(tenders_dir)
        if summary:
            logger.info(
                "Excel summary refreshed: %s (%d tenders — Pursue %d / Review %d / Drop %d%s)",
                paths.repo_relative(summary["output"]), summary["total"],
                summary["pursue"], summary["review"], summary["drop"],
                f" / {summary['new']} new" if summary.get("new") else "",
            )
    except Exception as e:
        logger.warning("Excel summary export skipped: %s "
                       "(close tender_summary.xlsx if it is open and re-run "
                       "tools/export_summary.py)", e)


def clear_workspace(tenders_dir=None, downloads_dir=None):
    """
    Reset one workspace for a clean run (per-profile "Clear All"):
      1. Back up metadata.{json,csv,js} to <workspace>/backups/<stamp>/
      2. Delete every downloaded PDF folder under <workspace>/downloads/
      3. Empty the metadata DB (all three formats)
      4. Remove the generated report + export state so New_Since_Last restarts
    Never touches other workspaces or the backups/ folder itself.
    Returns a summary dict.
    """
    if tenders_dir is None or downloads_dir is None:
        tenders_dir, downloads_dir = workspace_paths()

    tenders = load_existing_metadata(tenders_dir)
    record_count = len(tenders)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(tenders_dir, "backups", stamp)
    backed_up = False
    for name in ("metadata.json", "metadata.csv", "metadata.js"):
        src = os.path.join(tenders_dir, name)
        if os.path.exists(src):
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(backup_dir, name))
            backed_up = True

    pdf_count = 0
    if os.path.exists(downloads_dir):
        for entry in os.listdir(downloads_dir):
            full = os.path.join(downloads_dir, entry)
            if not os.path.isdir(full):
                continue
            pdf_count += sum(
                1 for _root, _dirs, files in os.walk(full)
                for f in files if f.lower().endswith(".pdf")
            )
            shutil.rmtree(full, ignore_errors=True)

    save_metadata([], tenders_dir)

    # Remove only THIS profile's workbook from the central reports folder
    label = workspace_label(tenders_dir)
    reports_dir = os.path.join(TENDERS_DIR, "reports")
    for name in (f"tender_summary_{label}.xlsx", f".export_state_{label}.json"):
        try:
            target = os.path.join(reports_dir, name)
            if os.path.exists(target):
                os.remove(target)
        except OSError as e:
            logger.warning("Could not remove %s during clear: %s", name, e)

    logger.info(
        "Workspace cleared: %d records and %d PDFs removed (metadata backed up to %s).",
        record_count, pdf_count,
        paths.repo_relative(backup_dir) if backed_up else "n/a",
    )
    return {
        "records_removed": record_count,
        "pdfs_removed": pdf_count,
        "backup": paths.repo_relative(backup_dir) if backed_up else None,
    }

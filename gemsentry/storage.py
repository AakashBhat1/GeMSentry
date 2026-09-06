"""Tender metadata persistence, PDF index and workspace teardown."""

import csv
import datetime
import io
import sqlite3
import json
import os
import paths
import shutil
import threading

from gemsentry import db
from gemsentry.atomicio import atomic_write_text
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
        for root, _dirs, files in os.walk(downloads_dir):
            expected_filename = f"{sanitized_bid}.pdf"
            if expected_filename in files:
                full_path = os.path.join(root, expected_filename)
                if os.path.getsize(full_path) > 0:
                    return paths.repo_relative(full_path)
    return None


def load_existing_metadata(tenders_dir=None):
    """Load the workspace's tender records keyed by bid number.

    SQLite is the store of record; a workspace that predates it is migrated
    from ``metadata.json`` on first access. The JSON and CSV readers survive
    only as fallbacks -- note that the CSV one can rebuild a fixed set of
    columns and silently drops everything else (source_id, source_name,
    est_value_inr, domain, score), so it is genuinely last-resort.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]

    try:
        conn = db.ensure_migrated(tenders_dir)
        try:
            loaded = db.load_all(conn)
        finally:
            conn.close()
        if loaded:
            logger.info("Loaded %d existing records from %s", len(loaded), db.DB_FILENAME)
            return loaded
    except sqlite3.Error as e:
        logger.error("Tender database unavailable (%s); falling back to JSON.", e)

    return _load_metadata_json(tenders_dir)


def _load_metadata_json(tenders_dir):
    """Fallback reader for the JSON export."""
    json_path = os.path.join(tenders_dir, "metadata.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
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
            with open(csv_path, encoding="utf-8") as f:
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
                            except (ValueError, TypeError):
                                # Legacy rows can carry a truncated blob; the
                                # record is still usable without its analysis.
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


# metadata.json is rewritten wholesale by several threads (the scrape worker,
# the rescore worker, and /api/tenders/status). Serialise them so two writers
# can never interleave, and so a reader never races a swap.
_save_lock = threading.RLock()

METADATA_CSV_FIELDS = [
    "Bid Number", "Title", "Quantity", "Department", "Start Date", "End Date",
    "Keyword", "Downloaded", "Local PDF Path", "PDF URL", "First Seen",
    "Status", "Status Source", "Source ID", "Source Name", "Analysis",
]


def _csv_row(t):
    return {
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
        "Analysis": json.dumps(t.get("analysis")) if t.get("analysis") else "",
    }


def save_metadata(tenders_list, tenders_dir=None):
    """Persist the whole workspace: SQLite (store of record) + JSON/CSV exports.

    The exports keep tools/ and the workspace-discovery checks working. Both
    files are written atomically under a process-wide lock, so an interrupted
    save leaves the previous good copy intact.

    For changing a single record, prefer ``update_record`` -- this function
    rewrites everything by design.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]
    os.makedirs(tenders_dir, exist_ok=True)

    with _save_lock:
        try:
            conn = db.connect(tenders_dir)
            try:
                db.replace_all(conn, tenders_list)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.error("Could not write the tender database: %s", e)

        write_exports(tenders_list, tenders_dir)


def write_exports(tenders_list, tenders_dir):
    """Refresh the JSON and CSV exports of the store, atomically."""
    os.makedirs(tenders_dir, exist_ok=True)
    with _save_lock:
        json_path = os.path.join(tenders_dir, "metadata.json")
        atomic_write_text(
            json_path, json.dumps(tenders_list, indent=2, ensure_ascii=False)
        )

        # metadata.js used to be written here as a third copy of the same
        # payload. Nothing reads it -- the dashboard fetches /api/tenders --
        # so it was pure write amplification (~6 MB per save). Drop any stale
        # copy left over from before this change.
        stale_js = os.path.join(tenders_dir, "metadata.js")
        if os.path.exists(stale_js):
            try:
                os.remove(stale_js)
                logger.info("Removed unused metadata.js (superseded by /api/tenders).")
            except OSError as e:
                logger.warning("Could not remove stale metadata.js: %s", e)

        csv_path = os.path.join(tenders_dir, "metadata.csv")
        try:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=METADATA_CSV_FIELDS)
            writer.writeheader()
            for t in tenders_list:
                writer.writerow(_csv_row(t))
            atomic_write_text(csv_path, buf.getvalue(), newline="")
            logger.info("Saved metadata CSV: %s", csv_path)
        except Exception as e:
            logger.error("Error saving CSV metadata: %s", e)


def update_record(bid_no, changes, tenders_dir=None):
    """Apply ``changes`` to one tender. Returns the updated record, or None.

    A single-row UPDATE instead of rewriting the whole corpus, which is what
    pinning a status used to cost. The JSON/CSV exports are refreshed in the
    background so the caller is not made to wait for them.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]
    conn = db.ensure_migrated(tenders_dir)
    try:
        record = db.update_one(conn, bid_no, changes)
    finally:
        conn.close()

    if record is not None:
        schedule_export(tenders_dir)
    return record


def store_revision(tenders_dir=None):
    """Monotonic version of the store, for HTTP cache keys.

    A file mtime cannot serve here: single-row updates do not touch the JSON
    export, so its mtime would go stale while the data had in fact changed.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]
    try:
        conn = db.connect(tenders_dir)
        try:
            return db.revision(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None


# Coalesces bursts of single-row updates (a user clicking through a list) into
# one export rather than one per click.
EXPORT_DEBOUNCE_SECONDS = 2.0
_export_timers = {}
_export_timer_lock = threading.Lock()


def schedule_export(tenders_dir, delay=EXPORT_DEBOUNCE_SECONDS):
    """Refresh the JSON/CSV exports shortly, collapsing repeated calls."""
    with _export_timer_lock:
        existing = _export_timers.get(tenders_dir)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(delay, _run_export, args=(tenders_dir,))
        timer.daemon = True
        _export_timers[tenders_dir] = timer
        timer.start()


def _run_export(tenders_dir):
    with _export_timer_lock:
        _export_timers.pop(tenders_dir, None)
    try:
        conn = db.connect(tenders_dir)
        try:
            records = list(db.load_all(conn).values())
        finally:
            conn.close()
        write_exports(records, tenders_dir)
        # Regenerating the workbook is the slowest part by far; keeping it here
        # means a status click never waits on it.
        auto_export_summary(tenders_dir)
    except Exception as e:
        logger.error("Background metadata export failed: %s", e)


def flush_exports(timeout=10.0):
    """Run any pending export now. For tests and clean shutdown."""
    with _export_timer_lock:
        pending = list(_export_timers.items())
        for _dir, timer in pending:
            timer.cancel()
        _export_timers.clear()
    for tenders_dir, _timer in pending:
        _run_export(tenders_dir)


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


# Each "Clear All" snapshots the full metadata set. Unbounded, that directory
# grows without limit (it reached 235 MB / 16 snapshots in practice).
BACKUP_RETENTION = 5


def prune_backups(backups_dir, keep=BACKUP_RETENTION):
    """Keep only the newest ``keep`` timestamped snapshots. Returns count removed."""
    if not os.path.isdir(backups_dir):
        return 0
    try:
        stamps = sorted(
            d for d in os.listdir(backups_dir)
            if os.path.isdir(os.path.join(backups_dir, d))
        )
    except OSError as e:
        logger.warning("Could not list backups for pruning: %s", e)
        return 0

    removed = 0
    for stale in stamps[:-keep] if keep > 0 else stamps:
        try:
            shutil.rmtree(os.path.join(backups_dir, stale))
            removed += 1
        except OSError as e:
            logger.warning("Could not prune backup %s: %s", stale, e)
    if removed:
        logger.info("Pruned %d old backup snapshot(s), keeping the newest %d.", removed, keep)
    return removed


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

    prune_backups(os.path.join(tenders_dir, "backups"))

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

    # Drop the SQLite store as well; leaving it would repopulate on next load.
    for suffix in ("", "-wal", "-shm"):
        stale_db = db.db_path(tenders_dir) + suffix
        if os.path.exists(stale_db):
            try:
                os.remove(stale_db)
            except OSError as e:
                logger.warning("Could not remove %s during clear: %s", stale_db, e)

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

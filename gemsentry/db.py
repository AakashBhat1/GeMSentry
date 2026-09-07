"""SQLite-backed tender store.

Why: ``metadata.json`` is a single ~8 MB document, so *every* write rewrote all
of it. Pinning one tender's status -- a single field on a single row -- cost a
full parse, a full re-serialise and a full disk write, and two threads doing it
at once could only be made safe by serialising the whole operation.

SQLite gives per-row writes, real transactions and concurrent readers (WAL),
from the standard library, with no server to run.

Record fidelity is the other requirement: the old CSV fallback silently dropped
source_id/est_value_inr/domain/score. Here the complete record is stored as
JSON in ``data`` and returned verbatim; the sibling columns exist only so the
common filters and sorts can use an index, and are always derived from ``data``.

``metadata.json`` is still written, as an *export*, because tools/ and the
workspace-discovery checks read it. It is refreshed on every bulk save and,
after a single-row update, by a debounced background writer -- so the click
returns immediately and the export catches up.
"""

import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger("gemsentry.db")

DB_FILENAME = "metadata.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    bid_no       TEXT PRIMARY KEY,
    data         TEXT NOT NULL,
    status       TEXT,
    status_source TEXT,
    source_id    TEXT,
    end_date     TEXT,
    first_seen   TEXT,
    score        REAL
);
CREATE INDEX IF NOT EXISTS idx_tenders_status    ON tenders(status);
CREATE INDEX IF NOT EXISTS idx_tenders_source    ON tenders(source_id);
CREATE INDEX IF NOT EXISTS idx_tenders_end_date  ON tenders(end_date);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db_path(tenders_dir):
    return os.path.join(tenders_dir, DB_FILENAME)


def connect(tenders_dir):
    """Open (creating if needed) the workspace database."""
    os.makedirs(tenders_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(tenders_dir), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while a scrape writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _ensure_status_source_column(conn)
    return conn


def _ensure_status_source_column(conn):
    """Add and backfill ``status_source`` on databases created before it existed.

    Manual pins are protected by reading this column inside the write
    transaction, so a workspace opened from an older build has to grow it
    before the first save, not on the next full rebuild.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tenders)")}
    if "status_source" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE tenders ADD COLUMN status_source TEXT")
        for row in conn.execute("SELECT bid_no, data FROM tenders").fetchall():
            try:
                record = _row_to_record(row)
            except ValueError:
                continue
            conn.execute(
                "UPDATE tenders SET status_source = ? WHERE bid_no = ?",
                (record.get("status_source"), row["bid_no"]),
            )
    logger.info("Added status_source column to %s (manual pins now protected).",
                DB_FILENAME)


def _columns(record):
    """Indexed columns, always derived from the record itself."""
    analysis = record.get("analysis")
    score = analysis.get("score") if isinstance(analysis, dict) else None
    if not isinstance(score, (int, float)):
        score = None
    return (
        record.get("status") or "Pending Review",
        record.get("status_source"),
        record.get("source_id") or "gem",
        record.get("end_date") or "",
        record.get("first_seen") or "",
        score,
    )


def _row_to_record(row):
    return json.loads(row["data"])


def _rows_for(records):
    """Storable rows. Non-dicts and records without a bid_no are skipped --
    callers upstream can pass partially built entries."""
    rows = []
    for r in records:
        if not isinstance(r, dict) or not r.get("bid_no"):
            continue
        rows.append((r["bid_no"], json.dumps(r, ensure_ascii=False), *_columns(r)))
    return rows


def bump_revision(conn):
    """Increment and return the store revision.

    Gives the HTTP layer a cheap, monotonic cache key that -- unlike a file
    mtime -- changes on single-row writes too.
    """
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('revision', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
    )
    return revision(conn)


def revision(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
    return int(row["value"]) if row else 0


def load_all(conn):
    """Every record, keyed by bid number, exactly as stored."""
    rows = conn.execute("SELECT data FROM tenders").fetchall()
    out = {}
    for row in rows:
        try:
            record = _row_to_record(row)
        except ValueError:
            continue
        if record.get("bid_no"):
            out[record["bid_no"]] = record
    return out


def get(conn, bid_no):
    row = conn.execute(
        "SELECT data FROM tenders WHERE bid_no = ?", (bid_no,)
    ).fetchone()
    return _row_to_record(row) if row else None


MANUAL_STATUS_SOURCE = "manual"


def manual_pins(conn):
    """``{bid_no: status}`` for every tender a user has decided by hand."""
    return {
        row["bid_no"]: row["status"]
        for row in conn.execute(
            "SELECT bid_no, status FROM tenders WHERE status_source = ?",
            (MANUAL_STATUS_SOURCE,),
        )
        if row["status"]
    }


def apply_manual_pins(conn, records):
    """Return ``records`` with any manual decision already in the store folded in.

    A scrape reads the workspace, works for minutes, then writes its snapshot
    back. A tender the user shortlisted in that gap would be overwritten by the
    stale copy and silently revert to Pending Review. Re-reading the pins here
    -- inside the write transaction, from the store of record -- closes the gap.
    Records are copied rather than mutated so the caller's snapshot is untouched.
    """
    pins = manual_pins(conn)
    if not pins:
        return list(records)

    merged = []
    for record in records:
        if isinstance(record, dict):
            pinned = pins.get(record.get("bid_no"))
            if pinned and (record.get("status") != pinned
                           or record.get("status_source") != MANUAL_STATUS_SOURCE):
                record = {**record, "status": pinned,
                          "status_source": MANUAL_STATUS_SOURCE}
        merged.append(record)
    return merged


def upsert_many(conn, records):
    """Insert or replace ``records`` in one transaction. Returns the count."""
    rows = _rows_for(records)
    with conn:
        conn.executemany(
            "INSERT INTO tenders(bid_no, data, status, status_source, "
            "source_id, end_date, first_seen, score) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(bid_no) DO UPDATE SET "
            "data=excluded.data, status=excluded.status, "
            "status_source=excluded.status_source, "
            "source_id=excluded.source_id, end_date=excluded.end_date, "
            "first_seen=excluded.first_seen, score=excluded.score",
            rows,
        )
        bump_revision(conn)
    return len(rows)


def replace_all(conn, records, preserve_manual=True):
    """Make the store contain exactly ``records``. Atomic: all or nothing.

    Returns the records as actually stored, which is what the JSON/CSV exports
    must be written from -- with ``preserve_manual`` they can differ from the
    caller's list by a status the user pinned while the caller was working.
    """
    with conn:
        # BEGIN IMMEDIATE takes the write lock *before* the pins are read.
        # Without it the read runs outside the transaction sqlite3 opens on the
        # DELETE, leaving a window in which a pin could still be lost.
        conn.execute("BEGIN IMMEDIATE")
        stored = apply_manual_pins(conn, records) if preserve_manual else list(records)
        rows = _rows_for(stored)
        conn.execute("DELETE FROM tenders")
        conn.executemany(
            "INSERT INTO tenders(bid_no, data, status, status_source, "
            "source_id, end_date, first_seen, score) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        bump_revision(conn)
    return stored


def update_one(conn, bid_no, changes):
    """Apply ``changes`` to one record. Returns the updated record, or None.

    This is the path that used to rewrite the entire corpus to pin a status.
    """
    with conn:
        # Read-modify-write: hold the write lock across both halves so two
        # concurrent updates to one tender cannot clobber each other.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM tenders WHERE bid_no = ?", (bid_no,)
        ).fetchone()
        if row is None:
            return None
        record = {**_row_to_record(row), **changes}
        conn.execute(
            "UPDATE tenders SET data=?, status=?, status_source=?, "
            "source_id=?, end_date=?, first_seen=?, score=? WHERE bid_no=?",
            (json.dumps(record, ensure_ascii=False), *_columns(record), bid_no),
        )
        bump_revision(conn)
    return record


def count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM tenders").fetchone()["n"]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

_migration_lock = threading.Lock()


def ensure_migrated(tenders_dir, json_records=None):
    """Seed an empty database from the workspace's existing metadata.json.

    Idempotent, and a no-op once the store holds anything.
    """
    with _migration_lock:
        conn = connect(tenders_dir)
        try:
            if count(conn) > 0:
                return conn

            if json_records is None:
                json_records = _read_legacy_json(tenders_dir)
            if json_records:
                replace_all(conn, json_records)
                logger.info(
                    "Migrated %d record(s) from metadata.json into %s",
                    len(json_records), DB_FILENAME,
                )
            return conn
        except Exception:
            conn.close()
            raise


def _read_legacy_json(tenders_dir):
    path = os.path.join(tenders_dir, "metadata.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        return records if isinstance(records, list) else []
    except (OSError, ValueError) as e:
        logger.error("Could not read metadata.json for migration: %s", e)
        return []

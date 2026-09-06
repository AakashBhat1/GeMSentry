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
    return conn


def _columns(record):
    """Indexed columns, always derived from the record itself."""
    analysis = record.get("analysis")
    score = analysis.get("score") if isinstance(analysis, dict) else None
    if not isinstance(score, (int, float)):
        score = None
    return (
        record.get("status") or "Pending Review",
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


def upsert_many(conn, records):
    """Insert or replace ``records`` in one transaction. Returns the count."""
    rows = _rows_for(records)
    with conn:
        conn.executemany(
            "INSERT INTO tenders(bid_no, data, status, source_id, end_date, "
            "first_seen, score) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(bid_no) DO UPDATE SET "
            "data=excluded.data, status=excluded.status, "
            "source_id=excluded.source_id, end_date=excluded.end_date, "
            "first_seen=excluded.first_seen, score=excluded.score",
            rows,
        )
        bump_revision(conn)
    return len(rows)


def replace_all(conn, records):
    """Make the store contain exactly ``records``. Atomic: all or nothing."""
    rows = _rows_for(records)
    with conn:
        conn.execute("DELETE FROM tenders")
        conn.executemany(
            "INSERT INTO tenders(bid_no, data, status, source_id, end_date, "
            "first_seen, score) VALUES(?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        bump_revision(conn)
    return len(rows)


def update_one(conn, bid_no, changes):
    """Apply ``changes`` to one record. Returns the updated record, or None.

    This is the path that used to rewrite the entire corpus to pin a status.
    """
    with conn:
        row = conn.execute(
            "SELECT data FROM tenders WHERE bid_no = ?", (bid_no,)
        ).fetchone()
        if row is None:
            return None
        record = {**_row_to_record(row), **changes}
        conn.execute(
            "UPDATE tenders SET data=?, status=?, source_id=?, end_date=?, "
            "first_seen=?, score=? WHERE bid_no=?",
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

"""SQLite tender store: fidelity, per-row writes, revisions and migration."""

import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry import db, storage  # noqa: E402

RECORD = {
    "bid_no": "GEM/2026/B/5001",
    "title": "Thermal imaging cameras",
    "quantity": "4",
    "department": "Ministry of Home Affairs",
    "start_date": "01-09-2026",
    "end_date": "25-09-2026",
    "keyword": "thermal",
    "downloaded": True,
    "local_pdf_path": "tenders/downloads/a.pdf",
    "pdf_url": "https://example.gov.in/a.pdf",
    "first_seen": "2026-09-01",
    "status": "Pending Review",
    "source_id": "defproc",
    "source_name": "Defence eProcurement Portal",
    "est_value_inr": 4500000,
    "domain": "biometrics_and_surveillance",
    "analysis": {"score": 7.5, "breakdown": [{"criterion": "EMD", "points": 2}]},
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path))
    yield c
    c.close()


# --------------------------------------------------------------------------
# Fidelity -- the failure the CSV fallback used to cause
# --------------------------------------------------------------------------

def test_every_field_survives_a_round_trip(conn):
    db.replace_all(conn, [RECORD])
    assert db.load_all(conn)[RECORD["bid_no"]] == RECORD


def test_fields_the_csv_reader_dropped_are_preserved(conn):
    db.replace_all(conn, [RECORD])
    loaded = db.get(conn, RECORD["bid_no"])
    for field in ("source_id", "source_name", "est_value_inr", "domain"):
        assert loaded[field] == RECORD[field], field
    assert loaded["analysis"]["score"] == 7.5


def test_non_ascii_titles_survive(conn):
    record = {**RECORD, "bid_no": "X/1", "title": "Solar plant सौर"}
    db.replace_all(conn, [record])
    assert db.get(conn, "X/1")["title"] == "Solar plant सौर"


# --------------------------------------------------------------------------
# Write semantics
# --------------------------------------------------------------------------

def test_replace_all_makes_the_store_exact(conn):
    db.replace_all(conn, [RECORD])
    db.replace_all(conn, [{**RECORD, "bid_no": "OTHER/1"}])
    assert set(db.load_all(conn)) == {"OTHER/1"}


def test_upsert_many_keeps_untouched_rows(conn):
    db.replace_all(conn, [RECORD])
    db.upsert_many(conn, [{**RECORD, "bid_no": "OTHER/1"}])
    assert set(db.load_all(conn)) == {RECORD["bid_no"], "OTHER/1"}


def test_upsert_overwrites_an_existing_row(conn):
    db.replace_all(conn, [RECORD])
    db.upsert_many(conn, [{**RECORD, "title": "Revised"}])
    assert db.count(conn) == 1
    assert db.get(conn, RECORD["bid_no"])["title"] == "Revised"


def test_update_one_merges_and_leaves_the_rest_alone(conn):
    db.replace_all(conn, [RECORD])
    updated = db.update_one(conn, RECORD["bid_no"], {"status": "Shortlisted"})
    assert updated["status"] == "Shortlisted"
    assert updated["title"] == RECORD["title"]
    assert updated["analysis"] == RECORD["analysis"]


def test_update_one_returns_none_for_an_unknown_bid(conn):
    assert db.update_one(conn, "NOPE", {"status": "Shortlisted"}) is None


def test_update_one_refreshes_the_indexed_column(conn):
    db.replace_all(conn, [RECORD])
    db.update_one(conn, RECORD["bid_no"], {"status": "Rejected"})
    row = conn.execute("SELECT status FROM tenders").fetchone()
    assert row["status"] == "Rejected"


def test_records_without_a_bid_no_are_skipped(conn):
    db.replace_all(conn, [RECORD, {"title": "orphan"}, "not-a-dict", None])
    assert db.count(conn) == 1


# --------------------------------------------------------------------------
# Revision counter -- the HTTP cache key
# --------------------------------------------------------------------------

def test_revision_advances_on_a_bulk_write(conn):
    before = db.revision(conn)
    db.replace_all(conn, [RECORD])
    assert db.revision(conn) > before


def test_revision_advances_on_a_single_row_update(conn):
    db.replace_all(conn, [RECORD])
    before = db.revision(conn)
    db.update_one(conn, RECORD["bid_no"], {"status": "Shortlisted"})
    assert db.revision(conn) > before


def test_revision_is_stable_without_writes(conn):
    db.replace_all(conn, [RECORD])
    assert db.revision(conn) == db.revision(conn)


# --------------------------------------------------------------------------
# Migration from the legacy JSON
# --------------------------------------------------------------------------

def test_migrates_an_existing_metadata_json(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps([RECORD]), encoding="utf-8")
    c = db.ensure_migrated(str(tmp_path))
    try:
        assert db.load_all(c)[RECORD["bid_no"]] == RECORD
    finally:
        c.close()


def test_migration_is_idempotent(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps([RECORD]), encoding="utf-8")
    for _ in range(3):
        db.ensure_migrated(str(tmp_path)).close()
    c = db.connect(str(tmp_path))
    try:
        assert db.count(c) == 1
    finally:
        c.close()


def test_migration_does_not_clobber_newer_data(tmp_path):
    """A stale JSON export must never overwrite the live store."""
    (tmp_path / "metadata.json").write_text(json.dumps([RECORD]), encoding="utf-8")
    c = db.ensure_migrated(str(tmp_path))
    db.update_one(c, RECORD["bid_no"], {"status": "Shortlisted"})
    c.close()

    c = db.ensure_migrated(str(tmp_path))
    try:
        assert db.get(c, RECORD["bid_no"])["status"] == "Shortlisted"
    finally:
        c.close()


def test_empty_workspace_migrates_to_an_empty_store(tmp_path):
    c = db.ensure_migrated(str(tmp_path))
    try:
        assert db.count(c) == 0
    finally:
        c.close()


# --------------------------------------------------------------------------
# storage.py integration
# --------------------------------------------------------------------------

def test_save_then_load_round_trips_through_the_store(tmp_path):
    storage.save_metadata([RECORD], str(tmp_path))
    assert storage.load_existing_metadata(str(tmp_path))[RECORD["bid_no"]] == RECORD


def test_save_still_writes_the_json_export(tmp_path):
    storage.save_metadata([RECORD], str(tmp_path))
    exported = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert exported == [RECORD]


def test_update_record_changes_one_field(tmp_path):
    storage.save_metadata([RECORD, {**RECORD, "bid_no": "OTHER/1"}], str(tmp_path))
    storage.update_record(RECORD["bid_no"], {"status": "Rejected"}, str(tmp_path))
    loaded = storage.load_existing_metadata(str(tmp_path))
    assert loaded[RECORD["bid_no"]]["status"] == "Rejected"
    assert loaded["OTHER/1"]["status"] == "Pending Review"


def test_update_record_returns_none_for_an_unknown_bid(tmp_path):
    storage.save_metadata([RECORD], str(tmp_path))
    assert storage.update_record("NOPE", {"status": "Rejected"}, str(tmp_path)) is None


def test_update_record_eventually_refreshes_the_export(tmp_path):
    storage.save_metadata([RECORD], str(tmp_path))
    storage.update_record(RECORD["bid_no"], {"status": "Rejected"}, str(tmp_path))
    storage.flush_exports()
    exported = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert exported[0]["status"] == "Rejected"


def test_store_revision_advances_after_an_update(tmp_path):
    storage.save_metadata([RECORD], str(tmp_path))
    before = storage.store_revision(str(tmp_path))
    storage.update_record(RECORD["bid_no"], {"status": "Rejected"}, str(tmp_path))
    assert storage.store_revision(str(tmp_path)) > before


def test_concurrent_updates_all_land(tmp_path):
    """Twenty threads pinning twenty statuses; none may be lost."""
    records = [{**RECORD, "bid_no": f"B/{i}"} for i in range(20)]
    storage.save_metadata(records, str(tmp_path))

    def worker(i):
        storage.update_record(f"B/{i}", {"status": "Shortlisted"}, str(tmp_path))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    loaded = storage.load_existing_metadata(str(tmp_path))
    assert all(loaded[f"B/{i}"]["status"] == "Shortlisted" for i in range(20))

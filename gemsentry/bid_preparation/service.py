"""Workspace-scoped SQLite store for offline bid preparation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

from gemsentry.profile import workspace_paths

BID_RE = re.compile(r"^[A-Za-z0-9_./-]{1,128}$")
STATUSES = {"draft", "in_review", "ready_for_human_submission", "not_pursuing"}


def _paths():
    tender_dir, _ = workspace_paths()
    root = os.path.join(tender_dir, "bid_preparation")
    docs = os.path.join(root, "documents")
    os.makedirs(docs, exist_ok=True)
    return os.path.join(root, "bid_preparation.db"), docs


def connect():
    db_path, _ = _paths()
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS preparations (
      bid_no TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'draft',
      quoted_amount REAL, currency TEXT NOT NULL DEFAULT 'INR',
      notes TEXT NOT NULL DEFAULT '', checklist TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
      id INTEGER PRIMARY KEY AUTOINCREMENT, bid_no TEXT NOT NULL,
      category TEXT NOT NULL, filename TEXT NOT NULL, stored_name TEXT NOT NULL,
      size INTEGER NOT NULL, sha256 TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_bid_prep_docs_bid ON documents(bid_no);
    CREATE TABLE IF NOT EXISTS audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT, bid_no TEXT NOT NULL,
      actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_bid(bid_no):
    bid = str(bid_no or "").strip()
    if not BID_RE.fullmatch(bid) or ".." in bid:
        raise ValueError("Invalid bid number.")
    return bid


def _audit(conn, bid_no, actor, action, details=None):
    conn.execute("INSERT INTO audit(bid_no,actor,action,details,created_at) VALUES(?,?,?,?,?)",
                 (bid_no, str(actor or "admin")[:120], action, json.dumps(details or {}, ensure_ascii=False), _now()))


def get(bid_no):
    bid_no = validate_bid(bid_no)
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM preparations WHERE bid_no=?", (bid_no,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["checklist"] = json.loads(result["checklist"] or "{}")
        result["documents"] = [dict(x) for x in conn.execute(
            "SELECT id,category,filename,size,sha256,verified,created_at FROM documents WHERE bid_no=? ORDER BY id DESC", (bid_no,))]
        result["audit"] = [dict(x) for x in conn.execute(
            "SELECT actor,action,details,created_at FROM audit WHERE bid_no=? ORDER BY id DESC LIMIT 50", (bid_no,))]
        for item in result["audit"]:
            item["details"] = json.loads(item["details"] or "{}")
        return result
    finally:
        conn.close()


def list_all():
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM preparations ORDER BY updated_at DESC").fetchall()
        return [{**dict(r), "checklist": json.loads(r["checklist"] or "{}"),
                 "document_count": conn.execute("SELECT COUNT(*) FROM documents WHERE bid_no=?", (r["bid_no"],)).fetchone()[0]}
                for r in rows]
    finally:
        conn.close()


def save(bid_no, payload, actor):
    bid_no = validate_bid(bid_no)
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object.")
    status = str(payload.get("status", "draft"))
    if status not in STATUSES:
        raise ValueError("Unsupported preparation status.")
    amount = payload.get("quoted_amount")
    if amount in (None, ""):
        amount = None
    else:
        if isinstance(amount, bool):
            raise ValueError("quoted_amount must be a positive number.")
        amount = float(amount)
        if amount < 0 or amount > 1e13:
            raise ValueError("quoted_amount is outside the allowed range.")
    currency = str(payload.get("currency") or "INR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a 3-letter code.")
    notes = str(payload.get("notes") or "")
    if len(notes) > 10000:
        raise ValueError("notes may not exceed 10,000 characters.")
    checklist = payload.get("checklist", {})
    if not isinstance(checklist, dict) or len(checklist) > 100:
        raise ValueError("checklist must be an object with at most 100 items.")
    clean_checklist = {str(k)[:100]: bool(v) for k, v in checklist.items()}
    now = _now()
    conn = connect()
    try:
        with conn:
            existed = conn.execute("SELECT 1 FROM preparations WHERE bid_no=?", (bid_no,)).fetchone()
            conn.execute("""INSERT INTO preparations(bid_no,status,quoted_amount,currency,notes,checklist,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(bid_no) DO UPDATE SET status=excluded.status,
              quoted_amount=excluded.quoted_amount,currency=excluded.currency,notes=excluded.notes,
              checklist=excluded.checklist,updated_at=excluded.updated_at""",
              (bid_no,status,amount,currency,notes,json.dumps(clean_checklist),now,now))
            _audit(conn,bid_no,actor,"updated" if existed else "created",{"status":status,"quoted_amount":amount})
    finally:
        conn.close()
    return get(bid_no)


def add_document(bid_no, category, filename, data, actor):
    bid_no = validate_bid(bid_no)
    category = str(category or "other").strip().lower()
    if not re.fullmatch(r"[a-z_]{2,40}", category):
        raise ValueError("Invalid document category.")
    if not data or len(data) > 25 * 1024 * 1024:
        raise ValueError("PDF must be between 1 byte and 25 MB.")
    if not str(filename or "").lower().endswith(".pdf"):
        raise ValueError("Only PDF documents are accepted in this version.")
    if not data.startswith(b"%PDF-"):
        raise ValueError("The uploaded file does not appear to be a PDF.")
    _, docs_dir = _paths()
    digest = hashlib.sha256(data).hexdigest()
    stored = f"{digest}.pdf"
    target = os.path.join(docs_dir, stored)
    if not os.path.exists(target):
        with open(target, "xb") as f:
            f.write(data)
    safe_name = os.path.basename(str(filename).replace("\\", "/"))[:180]
    conn = connect()
    try:
        with conn:
            row = conn.execute("SELECT 1 FROM preparations WHERE bid_no=?", (bid_no,)).fetchone()
            if not row:
                raise LookupError("Create the bid preparation record before adding documents.")
            conn.execute("INSERT INTO documents(bid_no,category,filename,stored_name,size,sha256,created_at) VALUES(?,?,?,?,?,?,?)",
                         (bid_no,category,safe_name,stored,len(data),digest,_now()))
            _audit(conn,bid_no,actor,"document_added",{"filename":safe_name,"category":category,"sha256":digest})
    finally:
        conn.close()
    return get(bid_no)


def verify_document(document_id, verified, actor):
    conn = connect()
    try:
        with conn:
            row = conn.execute("SELECT bid_no FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row:
                raise LookupError("Document not found.")
            conn.execute("UPDATE documents SET verified=? WHERE id=?", (1 if verified else 0, document_id))
            _audit(conn,row["bid_no"],actor,"document_verified" if verified else "document_unverified",{"document_id":document_id})
        return get(row["bid_no"])
    finally:
        conn.close()


def document_path(document_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            return None
        _, docs_dir = _paths()
        path = os.path.realpath(os.path.join(docs_dir, row["stored_name"]))
        if os.path.commonpath([os.path.realpath(docs_dir), path]) != os.path.realpath(docs_dir):
            return None
        return path, row["filename"]
    finally:
        conn.close()

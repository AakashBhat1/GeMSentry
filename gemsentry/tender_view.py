"""List/detail projections and a stat-keyed response cache for /api/tenders.

The dashboard used to receive every stored field for every tender on each
load. ``analysis`` alone accounted for ~6.6 MB of an 11.7 MB corpus, and its
three heaviest members (``fit_breakdown``, ``breakdown``, ``reasons``) are only
ever rendered in the per-tender detail panel -- never in the list.

So the list response drops those, and the detail panel fetches the full record
for one bid on demand. Responses are cached against the metadata file's
(mtime_ns, size), which also yields a strong ETag for conditional GETs.
"""

import hashlib
import json
import os
import threading

# Rendered only inside the score accordion, so they are stripped from list
# payloads and refetched per-bid when a user opens one. `eligibility` is
# deliberately NOT here: the collapsed card renders its verdict badge.
HEAVY_ANALYSIS_FIELDS = (
    "fit_breakdown",
    "breakdown",
    "reasons",
    "field_status",
)


def project_for_list(record):
    """Return a copy of ``record`` without the detail-only analysis members."""
    analysis = record.get("analysis")
    if not isinstance(analysis, dict):
        return record
    trimmed = {k: v for k, v in analysis.items() if k not in HEAVY_ANALYSIS_FIELDS}
    # Let the client know a fuller record exists behind /api/tenders/<bid_no>.
    trimmed["_truncated"] = True
    return {**record, "analysis": trimmed}


def stat_key(path):
    """Identity of the metadata file, or None when it does not exist yet."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class TenderResponseCache:
    """Caches the serialized list payload + ETag against the file's stat key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key = None
        self._body = None
        self._etag = None

    def get(self, key):
        with self._lock:
            if key is not None and key == self._key:
                return self._body, self._etag
        return None, None

    def put(self, key, body):
        etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
        with self._lock:
            self._key, self._body, self._etag = key, body, etag
        return etag

    def invalidate(self):
        with self._lock:
            self._key = self._body = self._etag = None


def serialize(payload):
    """Compact JSON bytes -- no indent, the wire does not need pretty-printing."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

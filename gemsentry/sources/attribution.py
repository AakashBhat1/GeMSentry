"""Source attribution for tender records.

Records written before the multi-source refactor carry no ``source_id``: the
GeM pipeline was the only writer, so the portal was implied by the file itself.
The dashboard's portal filter reads that field, so every bucket counted zero
even though the records were there.

This module derives the field at read time -- from the document host, falling
back to the bid-number prefix -- so history becomes filterable without
rewriting ``metadata.json``. New records get the field stamped at write time
(see :func:`gemsentry.sources.gem.client.doc_to_tender`); this is the safety
net for everything already on disk.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from gemsentry.constants import logger

# Records whose portal cannot be established at all. Kept as an explicit
# bucket rather than silently folded into GeM, so a mis-attributed portal is
# visible in the UI instead of inflating the marketplace count.
UNKNOWN_SOURCE_ID = "unknown"
UNKNOWN_SOURCE_NAME = "Unattributed"

# GeM bid numbers are the one identifier stable enough to attribute on their
# own: every marketplace bid is "GEM/<year>/<type>/<serial>".
_GEM_BID_PREFIX = "GEM/"
_GEM_SOURCE_ID = "gem"
_GEM_SOURCE_NAME = "Government e-Marketplace (GeM)"


def normalize_host(url: str) -> str:
    """Return the bare lowercase host of ``url`` ('' when unparseable)."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def build_host_index(sources: Iterable[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """Map portal host -> (source_id, source_name) from the sources config.

    Two portals can share a host (CPPP publishes two apps under
    ``eprocure.gov.in``); the first configured entry wins, which is accurate at
    the host level and only loses the sub-app distinction.
    """
    index: Dict[str, Tuple[str, str]] = {}
    for source in sources or []:
        source_id = source.get("id")
        host = normalize_host(source.get("url", ""))
        if not source_id or not host or host in index:
            continue
        index[host] = (source_id, source.get("name") or source_id)
    return index


def _match_host(host: str, index: Dict[str, Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """Resolve ``host`` against the index, allowing subdomains.

    GeM serves bid documents from ``bidplus.gem.gov.in`` while the config lists
    ``gem.gov.in``, so an exact-match-only lookup would miss every GeM record.
    """
    if not host:
        return None
    if host in index:
        return index[host]
    for base, attribution in index.items():
        if host.endswith("." + base):
            return attribution
    return None


def derive_source(
    record: Dict[str, Any],
    host_index: Dict[str, Tuple[str, str]],
) -> Tuple[str, str]:
    """Return the (source_id, source_name) a tender record belongs to.

    An explicit ``source_id`` on the record always wins; it is written by the
    adapters and is authoritative.
    """
    explicit_id = record.get("source_id")
    if explicit_id:
        return explicit_id, record.get("source_name") or explicit_id

    for url_field in ("pdf_url", "url"):
        matched = _match_host(normalize_host(record.get(url_field) or ""), host_index)
        if matched:
            return matched

    bid_no = str(record.get("bid_no") or "")
    if bid_no.upper().startswith(_GEM_BID_PREFIX):
        known = host_index.get("gem.gov.in")
        return known if known else (_GEM_SOURCE_ID, _GEM_SOURCE_NAME)

    return UNKNOWN_SOURCE_ID, UNKNOWN_SOURCE_NAME


def annotate_sources(
    records: Iterable[Dict[str, Any]],
    sources: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return copies of ``records`` with source_id / source_name filled in.

    The inputs are never mutated: the caller's in-memory metadata stays exactly
    as it was loaded from disk.
    """
    host_index = build_host_index(sources)
    annotated: List[Dict[str, Any]] = []
    derived_count = 0

    for record in records:
        source_id, source_name = derive_source(record, host_index)
        if not record.get("source_id"):
            derived_count += 1
        annotated.append({**record, "source_id": source_id, "source_name": source_name})

    if derived_count:
        logger.debug("Derived portal attribution for %d record(s)", derived_count)
    return annotated

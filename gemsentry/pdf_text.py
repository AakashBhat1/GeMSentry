"""Cached PDF text extraction.

Profiling the analysis pipeline showed ~99% of the wall time was
``pypdf.extract_text`` -- roughly 1.9s per tender PDF against ~20ms for all the
regex parsing and scoring combined. Every re-analysis (a parser fix, a
``--reparse`` rescore, a re-run over a workspace) paid that cost again for
bytes that had not changed.

So extraction results are cached on the SHA-256 of the file's *contents*:

* path-independent -- the same PDF filed under two dates hits one entry;
* auto-invalidating -- edited bytes produce a different key, no staleness;
* self-versioning   -- ``CACHE_VERSION`` bumps evict everything when the
  extraction rules themselves change.

The cache is strictly an accelerator: any read/write failure falls through to
a direct extraction, so a missing or unwritable cache directory only costs
speed, never correctness.
"""

import hashlib
import os
import re

from pypdf import PdfReader

import paths
from gemsentry.constants import MAX_PDF_PAGES, logger

# Bump whenever extraction or normalisation changes so old entries are ignored.
CACHE_VERSION = 1

CACHE_DIR = os.path.join(paths.DATA_DIR, "pdf_text_cache")

_HASH_CHUNK = 1 << 20  # 1 MiB
_WHITESPACE_RX = re.compile(r"\s+")


def normalize(text):
    """Collapse every whitespace run to a single space, as parsers expect."""
    return _WHITESPACE_RX.sub(" ", text or "")


def file_digest(pdf_path):
    """SHA-256 of the file contents, or ``None`` if it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(pdf_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.debug("Could not hash %s: %s", pdf_path, exc)
        return None
    return digest.hexdigest()


def _cache_path(digest, max_pages):
    key = f"{digest}-v{CACHE_VERSION}-p{max_pages}"
    # Shard on the first two hex chars: 1k+ PDFs in one flat directory is
    # slow to enumerate on Windows.
    return os.path.join(CACHE_DIR, key[:2], f"{key}.txt")


def _read_cache(cache_file):
    try:
        with open(cache_file, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _write_cache(cache_file, text):
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        # Write-then-rename so a crash mid-write cannot leave a truncated
        # entry that would later be served as if it were complete.
        tmp_file = f"{cache_file}.{os.getpid()}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_file, cache_file)
    except OSError as exc:
        logger.debug("Could not cache text for %s: %s", cache_file, exc)


def extract_raw_text(pdf_path, max_pages=MAX_PDF_PAGES):
    """Extract text from the first ``max_pages`` pages. No caching."""
    reader = PdfReader(pdf_path)
    pages = reader.pages[:max_pages]
    return "\n".join((page.extract_text() or "") for page in pages) + "\n"


def extract_text(pdf_path, max_pages=MAX_PDF_PAGES, use_cache=True):
    """Return whitespace-normalised text for ``pdf_path``.

    Raises whatever :class:`~pypdf.PdfReader` raises for an unreadable PDF;
    callers already handle that as an analysis failure.
    """
    if not use_cache:
        return normalize(extract_raw_text(pdf_path, max_pages))

    digest = file_digest(pdf_path)
    if digest is None:
        return normalize(extract_raw_text(pdf_path, max_pages))

    cache_file = _cache_path(digest, max_pages)
    cached = _read_cache(cache_file)
    if cached is not None:
        return cached

    text = normalize(extract_raw_text(pdf_path, max_pages))
    _write_cache(cache_file, text)
    return text


def cache_stats():
    """Return ``(entry_count, total_bytes)`` for the on-disk cache."""
    entries = 0
    total = 0
    for root, _dirs, files in os.walk(CACHE_DIR):
        for name in files:
            if not name.endswith(".txt"):
                continue
            entries += 1
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return entries, total


def clear_cache():
    """Delete every cached extraction. Returns the number of entries removed.

    Only ``.txt`` entries are touched, matching :func:`cache_stats` -- a
    misconfigured ``CACHE_DIR`` must not turn this into a directory wipe.
    """
    removed = 0
    for root, _dirs, files in os.walk(CACHE_DIR):
        for name in files:
            if not name.endswith(".txt"):
                continue
            try:
                os.remove(os.path.join(root, name))
                removed += 1
            except OSError:
                pass
    logger.info("Cleared %d cached PDF text entr(ies).", removed)
    return removed

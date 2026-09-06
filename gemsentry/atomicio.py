"""Crash-safe file writes.

Every writer in the app used ``open(path, "w")``, which truncates the target
before the new bytes land. A crash (or Ctrl-C) mid-write left a truncated or
empty file; for ``metadata.json`` that meant falling back to the lossy CSV
reader, which silently drops source_id/est_value_inr/domain/score.

``atomic_write_text`` writes a sibling ``.tmp`` and ``os.replace``s it over the
target. ``os.replace`` is atomic on POSIX and on Windows (MoveFileEx with
MOVEFILE_REPLACE_EXISTING), so a reader sees either the whole old file or the
whole new one -- never a partial write.
"""

import os
import tempfile


def atomic_write_text(path, text, encoding="utf-8", newline=None):
    """Write ``text`` to ``path`` atomically, flushing to disk before swapping."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    # Same directory as the target: os.replace cannot cross filesystems.
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

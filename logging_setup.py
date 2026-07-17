"""
Stdlib logging setup for GeMSentry (Phase 4 / BE-22, BE-24).

Console + rotating app log + optional per-scrape session handler +
callback handler for the live dashboard feed.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Callable, Deque, List, Optional

import paths

_CONFIGURED = False
LOGGER_NAME = "gemsentry"

# Bounded live buffer shared with app.py /api/status (BE-24)
LOG_BUFFER_MAX = 500
log_buffer: Deque[str] = deque(maxlen=LOG_BUFFER_MAX)

# Current / last scrape session file path (absolute)
_current_session_path: Optional[str] = None
_current_session_handler: Optional[logging.Handler] = None


class CallbackHandler(logging.Handler):
    """Invoke a callback with formatted log lines (dashboard live feed)."""

    def __init__(self, callback: Callable[[str], None], level: int = logging.INFO):
        super().__init__(level=level)
        self.callback = callback
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.callback(msg)
        except Exception:
            self.handleError(record)


class BufferHandler(logging.Handler):
    """Append formatted lines to the bounded in-memory deque."""

    def __init__(self, buffer: Deque[str], level: int = logging.INFO):
        super().__init__(level=level)
        self.buffer = buffer
        self.setFormatter(
            logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure gemsentry logger once (console + rotating file). Idempotent."""
    global _CONFIGURED
    logger = get_logger()
    if _CONFIGURED:
        return logger

    paths.ensure_dirs()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console: human-readable INFO+
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(console)

    # Rotating file: keep more detail (DEBUG)
    file_handler = RotatingFileHandler(
        paths.APP_LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    # Bounded in-memory buffer for /api/status live feed
    buffer_handler = BufferHandler(log_buffer, level=level)
    logger.addHandler(buffer_handler)

    # Quiet noisy third-party loggers
    for noisy in ("playwright", "urllib3", "asyncio", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logger.debug("Logging configured; app log at %s", paths.APP_LOG_PATH)
    return logger


def clear_log_buffer() -> None:
    log_buffer.clear()


def get_log_buffer_lines() -> List[str]:
    return list(log_buffer)


def get_session_path() -> Optional[str]:
    return _current_session_path


def start_scrape_session() -> str:
    """
    Open a per-scrape session log under logs/scrapes/.
    Returns absolute path to the session file.
    """
    global _current_session_path, _current_session_handler
    # Detach any leftover handler first
    end_scrape_session()

    paths.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_path = os.path.join(paths.SCRAPE_LOGS_DIR, f"scrape-{stamp}.log")
    handler = logging.FileHandler(session_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    get_logger().addHandler(handler)
    _current_session_handler = handler
    _current_session_path = session_path
    get_logger().info("Scrape session log: %s", paths.repo_relative(session_path))
    return session_path


def end_scrape_session() -> None:
    """Detach and close the current scrape session FileHandler."""
    global _current_session_handler
    if _current_session_handler is None:
        return
    logger = get_logger()
    try:
        logger.removeHandler(_current_session_handler)
        _current_session_handler.close()
    except Exception:
        pass
    _current_session_handler = None


def attach_callback(callback: Callable[[str], None]) -> CallbackHandler:
    """Attach a temporary callback handler; caller must detach in finally."""
    handler = CallbackHandler(callback)
    get_logger().addHandler(handler)
    return handler


def detach_handler(handler: Optional[logging.Handler]) -> None:
    if handler is None:
        return
    logger = get_logger()
    try:
        logger.removeHandler(handler)
        handler.close()
    except Exception:
        pass


def list_session_logs(limit: int = 20) -> List[dict]:
    """Newest session log files under logs/scrapes/ (max `limit`)."""
    paths.ensure_dirs()
    entries = []
    try:
        names = [
            n
            for n in os.listdir(paths.SCRAPE_LOGS_DIR)
            if n.startswith("scrape-") and n.endswith(".log")
        ]
    except OSError:
        return []

    for name in names:
        full = os.path.join(paths.SCRAPE_LOGS_DIR, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "path": paths.repo_relative(full),
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "size": st.st_size,
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries[:limit]


def tail_file(abs_path: str, lines: int = 100) -> List[str]:
    """Return last `lines` of a text file (UTF-8, best-effort)."""
    if not abs_path or not os.path.isfile(abs_path):
        return []
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            # Efficient enough for log tails under a few MB
            content = f.readlines()
        return [ln.rstrip("\n\r") for ln in content[-lines:]]
    except OSError:
        return []


def safe_logs_path(candidate: str) -> Optional[str]:
    """Resolve path only if it stays under LOGS_DIR."""
    if not candidate:
        return None
    abs_cand = os.path.abspath(candidate)
    logs_root = os.path.abspath(paths.LOGS_DIR)
    try:
        if os.path.commonpath([abs_cand, logs_root]) != logs_root:
            return None
    except ValueError:
        return None
    return abs_cand if os.path.isfile(abs_cand) else None

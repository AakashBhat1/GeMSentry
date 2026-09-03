"""
Central path map for GeMSentry (Phase 4 / BE-21).

All absolute paths derived from repo ROOT or server_config.json overrides.
Call ensure_dirs() at startup.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
DATA_SOURCE_DIR = os.path.join(DATA_DIR, "source")
DASHBOARD_PATH = os.path.join(ROOT, "dashboard.html")

KEYWORDS_PATH = os.path.join(CONFIG_DIR, "keywords.csv")
SCORING_CONFIG_PATH = os.path.join(CONFIG_DIR, "scoring_config.json")
COMPANY_PROFILE_PATH = os.path.join(CONFIG_DIR, "company_profile.json")
SOURCES_PATH = os.path.join(CONFIG_DIR, "sources.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
SERVER_CONFIG_PATH = os.path.join(CONFIG_DIR, "server_config.json")

# Legacy root-level paths (pre-Phase-4) for resilient loaders
LEGACY_KEYWORDS_PATH = os.path.join(ROOT, "keywords.csv")
LEGACY_SCORING_CONFIG_PATH = os.path.join(ROOT, "scoring_config.json")
LEGACY_COMPANY_PROFILE_PATH = os.path.join(ROOT, "company_profile.json")
LEGACY_HISTORY_PATH = os.path.join(ROOT, "history.json")


def load_server_config() -> dict:
    """Load config/server_config.json with environment variable overrides."""
    cfg = {
        "auth_token": "",
        "host": "0.0.0.0",
        "port": 5000,
        "tenders_dir": "",
        "logs_dir": "",
    }
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
        except Exception:
            pass

    # Environment variables take precedence if set
    if "GEMSENTRY_AUTH_TOKEN" in os.environ:
        cfg["auth_token"] = os.environ["GEMSENTRY_AUTH_TOKEN"]
    if "GEMSENTRY_HOST" in os.environ:
        cfg["host"] = os.environ["GEMSENTRY_HOST"]
    if "GEMSENTRY_PORT" in os.environ:
        try:
            cfg["port"] = int(os.environ["GEMSENTRY_PORT"])
        except ValueError:
            pass
    if "GEMSENTRY_TENDERS_DIR" in os.environ:
        cfg["tenders_dir"] = os.environ["GEMSENTRY_TENDERS_DIR"]
    if "GEMSENTRY_LOGS_DIR" in os.environ:
        cfg["logs_dir"] = os.environ["GEMSENTRY_LOGS_DIR"]

    return cfg


_cfg = load_server_config()

# Dynamic folder resolution: if configured, use override; otherwise use default under ROOT
_tenders_override = _cfg.get("tenders_dir", "").strip()
if _tenders_override:
    TENDERS_DIR = _tenders_override if os.path.isabs(_tenders_override) else os.path.join(ROOT, _tenders_override)
else:
    TENDERS_DIR = os.path.join(ROOT, "tenders")

DOWNLOADS_DIR = os.path.join(TENDERS_DIR, "downloads")

_logs_override = _cfg.get("logs_dir", "").strip()
if _logs_override:
    LOGS_DIR = _logs_override if os.path.isabs(_logs_override) else os.path.join(ROOT, _logs_override)
else:
    LOGS_DIR = os.path.join(ROOT, "logs")

SCRAPE_LOGS_DIR = os.path.join(LOGS_DIR, "scrapes")
APP_LOG_PATH = os.path.join(LOGS_DIR, "gemsentry.log")


def ensure_dirs() -> None:
    """Create standard directories if missing (idempotent)."""
    for d in (
        CONFIG_DIR,
        DATA_DIR,
        DATA_SOURCE_DIR,
        LOGS_DIR,
        SCRAPE_LOGS_DIR,
        TENDERS_DIR,
        DOWNLOADS_DIR,
    ):
        os.makedirs(d, exist_ok=True)


def repo_relative(abs_path: str) -> str:
    """Return a POSIX-style path relative to ROOT when possible."""
    try:
        rel = os.path.relpath(abs_path, ROOT)
    except ValueError:
        return abs_path.replace("\\", "/")
    return rel.replace("\\", "/")

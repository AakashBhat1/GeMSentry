"""
Central path map for GeMSentry (Phase 4 / BE-21).

All absolute paths derived from repo ROOT. Call ensure_dirs() at startup.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))

CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
DATA_SOURCE_DIR = os.path.join(DATA_DIR, "source")
LOGS_DIR = os.path.join(ROOT, "logs")
SCRAPE_LOGS_DIR = os.path.join(LOGS_DIR, "scrapes")
TENDERS_DIR = os.path.join(ROOT, "tenders")
DOWNLOADS_DIR = os.path.join(TENDERS_DIR, "downloads")

KEYWORDS_PATH = os.path.join(CONFIG_DIR, "keywords.csv")
SCORING_CONFIG_PATH = os.path.join(CONFIG_DIR, "scoring_config.json")
COMPANY_PROFILE_PATH = os.path.join(CONFIG_DIR, "company_profile.json")
SOURCES_PATH = os.path.join(CONFIG_DIR, "sources.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
APP_LOG_PATH = os.path.join(LOGS_DIR, "gemsentry.log")
DASHBOARD_PATH = os.path.join(ROOT, "dashboard.html")

# Legacy root-level paths (pre-Phase-4) for resilient loaders
LEGACY_KEYWORDS_PATH = os.path.join(ROOT, "keywords.csv")
LEGACY_SCORING_CONFIG_PATH = os.path.join(ROOT, "scoring_config.json")
LEGACY_COMPANY_PROFILE_PATH = os.path.join(ROOT, "company_profile.json")
LEGACY_HISTORY_PATH = os.path.join(ROOT, "history.json")


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

"""Shared request-handling state and helpers for the web layer.

Lives outside app.py so blueprints can import it without importing the
application object -- app.py imports the blueprints, so the reverse direction
would be circular.
"""

import datetime
import gzip
import hmac
import logging
import os
import threading
from urllib.parse import urlparse

from flask import jsonify, request

import logging_setup
import paths
import scraper
from gemsentry import tender_view
from gemsentry.live_excel import live_excel_manager
from gemsentry.search import expand_keywords
from gemsentry.sources import SourceRegistry

logger = logging.getLogger("gemsentry")

# One registry for the process; config/sources.json is re-read on demand.
source_registry = SourceRegistry()

# Below this size gzip costs more CPU than it saves on the wire.
GZIP_MIN_BYTES = 1024

# Keyed on the SQLite store's revision counter, which advances on every write
# including single-row updates that never touch the JSON export.
tenders_cache = tender_view.TenderResponseCache()


def fail(exc, status=500):
    """Log the real error server-side; return a generic message to the client.

    Every route used to do `jsonify({"error": str(e)}), 500`, which leaked
    internal exception text (paths, driver errors) to whoever called the API.
    """
    logger.exception("Unhandled error in %s: %s", request.path, exc)
    return jsonify({"error": "Internal server error. See the server log for details."}), status


def is_auth_enabled() -> bool:
    cfg = paths.load_server_config()
    return bool(cfg.get("auth_token", "").strip())


def check_auth(token_to_test: str | None) -> bool:
    if not is_auth_enabled():
        return True
    expected = paths.load_server_config().get("auth_token", "").strip()
    if not token_to_test:
        return False
    # Constant-time: a plain `==` leaks the shared secret one byte at a time
    # through response timing.
    return hmac.compare_digest(token_to_test.strip(), expected)


# Methods that change server state. These accept the bearer header only.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Reachable before the user has entered the access key.
# Logout is public: it only expires the caller's own cookie, and it must work
# even once the page has already dropped its bearer token.
PUBLIC_PATHS = {"/", "/favicon.ico", "/api/auth/status", "/api/auth/verify",
                "/api/auth/logout"}


APPS_SCRIPT_HOSTS = {"script.google.com", "script.googleusercontent.com"}


def is_apps_script_url(url: str) -> bool:
    """True only for an https Google Apps Script endpoint."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in APPS_SCRIPT_HOSTS


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return None


def enforce_auth():
    if not is_auth_enabled():
        return None
    if request.path in PUBLIC_PATHS or request.path.endswith(".png"):
        return None
    if request.path.startswith("/static/"):
        return None

    token = _bearer_token()

    # The cookie exists so ordinary browser navigations (opening a tender PDF,
    # downloading the summary workbook) carry credentials -- the browser will
    # not attach an Authorization header to those. It is accepted for safe
    # methods ONLY: honouring it on POST would make every mutating endpoint
    # forgeable by any other site the user has open (CSRF). A cross-origin
    # page cannot set an Authorization header without a CORS preflight that
    # this server never grants, so requiring the header is the CSRF defence.
    if token is None and request.method not in UNSAFE_METHODS:
        token = request.cookies.get("gemsentry_token")

    if not check_auth(token):
        return jsonify({
            "error": "Unauthorized: Authentication required.",
            "auth_required": True
        }), 401

# Threading and status control.
#
# "status" answers only "is a job running right now?". How the last job *ended*
# is a separate fact, in "outcome" -- conflating the two is what let a crashed
# scrape return to idle and be reported to the user as a success.
JOB_RUNNING = "running"
JOB_IDLE = "idle"

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"

status_lock = threading.Lock()
scrape_status = {
    "status": JOB_IDLE,
    "job": None,          # scrape | single_bid | rescore
    "outcome": None,      # succeeded | partial | failed, for the last finished job
    "error": None,        # user-facing summary when the job failed
    "warnings": [],       # non-fatal problems worth surfacing on a partial run
    "current_keyword": "",
    "new_count": 0,
    "start_time": None,
    "log_session_path": None,
}


def begin_job(job):
    """Mark ``job`` as started, clearing the previous run's outcome.

    The caller must already hold ``status_lock`` and have checked that no job
    is running.
    """
    scrape_status.update({
        "status": JOB_RUNNING,
        "job": job,
        "outcome": None,
        "error": None,
        "warnings": [],
        "new_count": 0,
        "start_time": datetime.datetime.now().isoformat(),
        "log_session_path": None,
    })
    logging_setup.clear_log_buffer()


def finish_job(outcome, error=None, warnings=None):
    """Record how a background job ended and release the running flag."""
    with status_lock:
        scrape_status["status"] = JOB_IDLE
        scrape_status["outcome"] = outcome
        scrape_status["error"] = error
        scrape_status["warnings"] = list(warnings or [])
        sess = logging_setup.get_session_path()
        if sess:
            scrape_status["log_session_path"] = paths.repo_relative(sess)

# Bounded live buffer (also mirrored via logging_setup.log_buffer)
LOG_BUFFER_MAX = logging_setup.LOG_BUFFER_MAX
scrape_logs = logging_setup.log_buffer  # deque, maxlen=500

ALLOWED_SCRAPE_SORTS = {
    "Bid-End-Date-Latest",
    "Bid-End-Date-Oldest",
    "Bid-Start-Date-Latest",
    "Bid-Start-Date-Oldest",
}


def _number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if integer and not parsed.is_integer():
        raise ValueError(f"{name} must be a whole number")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return int(parsed) if integer else parsed


def normalize_scrape_payload(data):
    """Validate and normalize the background scrape request."""
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")

    raw_keywords = data.get("keywords")
    if not isinstance(raw_keywords, list):
        raise ValueError("keywords must be a non-empty list")
    keywords = []
    seen = set()
    for raw in raw_keywords:
        clean = " ".join(str(raw or "").split()).strip()
        key = clean.casefold()
        if clean and key not in seen:
            keywords.append(clean)
            seen.add(key)
    if not keywords:
        raise ValueError("keywords must contain at least one non-empty value")
    if len(keywords) > 1000:
        raise ValueError("keywords may contain at most 1000 values")

    sort_order = data.get("sort_order", "Bid-Start-Date-Latest")
    if sort_order not in ALLOWED_SCRAPE_SORTS:
        raise ValueError("sort_order is not supported")

    cfg = scraper.load_scoring_config()
    default_min_days = float(
        (cfg.get("date_window") or {}).get("min_days_to_bid", 5)
    )
    min_days_left = _number(
        data.get("min_days_left", default_min_days),
        "min_days_left", 0, 365,
    )
    raw_max_days = data.get("max_days_left")
    max_days_left = (
        None if raw_max_days is None
        else _number(raw_max_days, "max_days_left", 0, 365)
    )
    if max_days_left is not None and max_days_left < min_days_left:
        raise ValueError("max_days_left must be greater than or equal to min_days_left")

    raw_target = data.get("target_count")
    target_count = (
        None if raw_target is None
        else _number(raw_target, "target_count", 1, 1000, integer=True)
    )
    return {
        "keywords": keywords,
        "max_pages": (None if data.get("max_pages") is None else
                      _number(data["max_pages"], "max_pages", 1, 1000, integer=True)),
        "sort_order": sort_order,
        "target_count": target_count,
        "min_days_left": min_days_left,
        "max_days_left": max_days_left,
    }


def add_log(message):
    """Log to gemsentry logger (console + file + bounded buffer + session)."""
    # Strip leading timestamps if callers already formatted; logger formats again
    logger.info("%s", message)


def run_scraper_thread(keywords, max_pages, sort_order, target_count=None, min_days_left=None, max_days_left=None):
    global scrape_status
    warnings = []
    try:
        if target_count:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) with target goal: {target_count} tenders per keyword in [{min_days_left}-{max_days_left}] days window...")
        elif min_days_left is not None:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) sorted by '{sort_order}' (filtering tenders closing in < {min_days_left} days)...")
        else:
            add_log(f"Starting background scrape for {len(keywords)} keyword(s) sorted by '{sort_order}'...")

        # Scraper logs via logger; BufferHandler fills live buffer.
        # Optional callback reserved for future UI hooks (no re-log).
        def scraper_log_callback(msg):
            pass

        tenders_list, new_count = scraper.scrape(
            selected_keywords=keywords,
            max_pages=max_pages,
            sort_order=sort_order,
            log_callback=scraper_log_callback,
            target_count=target_count,
            min_days_left=min_days_left,
            max_days_left=max_days_left,
        )

        # Fan out to the non-GeM portals (DefProc, BEL, CPPP, NTPC, states...).
        # A portal being down is a partial result, not a failed scrape: the GeM
        # half already landed, and the user needs to know which half is missing.
        try:
            source_registry.reload_sources()
            runnable = source_registry.runnable_adapters()
            if runnable:
                add_log(f"Querying {len(runnable)} external portal(s) in parallel...")
                external_keywords = expand_keywords(keywords)
                extra_tenders = source_registry.fetch_from_all_active(
                    external_keywords, max_pages=max_pages or 30
                )
                if extra_tenders:
                    add_log(f"Multi-source portals returned {len(extra_tenders)} unique tenders.")
                    new_count += scraper.ingest_external_tenders(extra_tenders)
        except Exception as ms_err:
            add_log(f"Multi-source fetch error: {ms_err}")
            warnings.append(f"External portal search did not complete: {ms_err}")

        with status_lock:
            # Capture session path before scraper's finally clears handler
            # (scrape ends session in finally after return — path still set)
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)
            scrape_status["new_count"] = new_count

        add_log(f"Scraping completed. Discovered {new_count} total new tenders across all portals.")
        try:
            live_excel_manager.on_scrape_completed()
            add_log("Live Excel session started after scrape (10-minute curation window open).")
        except Exception as le_err:
            logger.warning("Could not initiate live excel session after scrape: %s", le_err)
            warnings.append(f"Live Excel curation session did not start: {le_err}")

        finish_job(
            OUTCOME_PARTIAL if warnings else OUTCOME_SUCCEEDED,
            warnings=warnings,
        )
    except Exception as e:
        add_log(f"Scraping thread crashed: {e}")
        logger.exception("Background scrape failed")
        finish_job(OUTCOME_FAILED, error=str(e), warnings=warnings)


def run_scraper_id_thread(bid_id):
    global scrape_status
    try:
        add_log(f"Starting background single bid acquisition for ID: '{bid_id}'...")

        def scraper_log_callback(msg):
            pass

        tender = scraper.scrape_single_bid(
            bid_id=bid_id,
            log_callback=scraper_log_callback,
        )

        if tender:
            add_log(f"Acquisition completed. Tender {tender['bid_no']} successfully imported.")
            with status_lock:
                scrape_status["new_count"] = 1
            warnings = []
            try:
                live_excel_manager.on_scrape_completed()
            except Exception as le_err:
                logger.warning("Could not initiate live excel session: %s", le_err)
                warnings.append(f"Live Excel curation session did not start: {le_err}")
            finish_job(
                OUTCOME_PARTIAL if warnings else OUTCOME_SUCCEEDED,
                warnings=warnings,
            )
        else:
            # Nothing was imported. The job did not crash, but calling that a
            # success is exactly the lie this outcome field exists to prevent.
            add_log(f"Acquisition failed. No tender was imported for ID: '{bid_id}'.")
            with status_lock:
                scrape_status["new_count"] = 0
            finish_job(
                OUTCOME_FAILED,
                error=f"No tender on GeM matched the ID '{bid_id}'.",
            )
    except Exception as e:
        add_log(f"Single bid scraping thread crashed: {e}")
        logger.exception("Single bid acquisition failed")
        finish_job(OUTCOME_FAILED, error=str(e))

def _metadata_path():
    return os.path.join(scraper.workspace_paths()[0], "metadata.json")


def _gzip_if_accepted(body):
    """Gzip the payload when the client advertises support. Returns (body, encoding)."""
    if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return body, None
    if len(body) < GZIP_MIN_BYTES:
        return body, None
    return gzip.compress(body, compresslevel=6), "gzip"

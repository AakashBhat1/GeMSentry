"""GeMSentry HTTP application: composition root.

Routes live in blueprints under gemsentry/web/; shared request-handling state
and helpers live in gemsentry/web/context.py. This module only builds the app,
wires the pieces together and installs the cross-cutting handlers.
"""

import logging
import threading
import webbrowser

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

import paths
import logging_setup

# Ensure dirs + logging before scraper import side-effects matter
paths.ensure_dirs()
logging_setup.setup_logging()

import scraper  # noqa: E402,F401  (re-exported: tests patch app.scraper)
from gemsentry.web import context  # noqa: E402,F401
from gemsentry.web.auth import auth_bp  # noqa: E402

# Re-exported so `app.<name>` stays the stable handle for tests and any
# external caller that grew up against the single-module layout.
from gemsentry.web.context import (  # noqa: E402,F401
    check_auth, enforce_auth, fail, is_apps_script_url, is_auth_enabled,
    normalize_scrape_payload, scrape_status, source_registry, status_lock,
    tenders_cache,
)
from gemsentry.web.live_excel import live_excel_bp  # noqa: E402
from gemsentry.web.master_sheet import master_sheet_bp  # noqa: E402
from gemsentry.web.settings import settings_bp  # noqa: E402
from gemsentry.web.tenders import tenders_bp  # noqa: E402

logger = logging.getLogger("gemsentry")

app = Flask(__name__, static_folder=".", static_url_path="")

app.register_blueprint(auth_bp)
app.register_blueprint(tenders_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(live_excel_bp)
app.register_blueprint(master_sheet_bp)

# Runs before every request on every blueprint.
app.before_request(enforce_auth)


@app.errorhandler(Exception)
def handle_unexpected(exc):
    """Catch-all so an unguarded route cannot return an HTML traceback."""
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code
    return fail(exc)


if __name__ == "__main__":
    import time

    server_cfg = paths.load_server_config()
    paths.require_safe_bind(server_cfg)
    host = server_cfg.get("host", "127.0.0.1")
    port = int(server_cfg.get("port", 5000))

    logger.info("=" * 60)
    logger.info("GeMSentry RFP Acquisition Dashboard Running on Dedicated Server")
    logger.info("Host: %s | Port: %d | Auth: %s", host, port,
                "ENABLED" if is_auth_enabled() else "DISABLED")
    logger.info("Local access:   http://localhost:%s", port)
    logger.info("=" * 60)

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    if paths.is_loopback(host) or host == "0.0.0.0":
        threading.Thread(target=open_browser, daemon=True).start()

    from gemsentry.serve import serve
    serve(app, host, port)

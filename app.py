import os
import threading
import datetime
import webbrowser
import logging
from flask import Flask, jsonify, request, send_from_directory

import paths
import logging_setup

# Ensure dirs + logging before scraper import side-effects matter
paths.ensure_dirs()
logging_setup.setup_logging()

import scraper  # noqa: E402

logger = logging.getLogger("gemsentry")

app = Flask(__name__, static_folder=".", static_url_path="")

# Threading and status control
status_lock = threading.Lock()
scrape_status = {
    "status": "idle",
    "current_keyword": "",
    "new_count": 0,
    "start_time": None,
    "log_session_path": None,
}

# Bounded live buffer (also mirrored via logging_setup.log_buffer)
LOG_BUFFER_MAX = logging_setup.LOG_BUFFER_MAX
scrape_logs = logging_setup.log_buffer  # deque, maxlen=500


def add_log(message):
    """Log to gemsentry logger (console + file + bounded buffer + session)."""
    # Strip leading timestamps if callers already formatted; logger formats again
    logger.info("%s", message)


def run_scraper_thread(keywords, max_pages, sort_order):
    global scrape_status
    try:
        add_log(f"Starting background scrape for {len(keywords)} keyword(s)...")

        # Scraper logs via logger; BufferHandler fills live buffer.
        # Optional callback reserved for future UI hooks (no re-log).
        def scraper_log_callback(msg):
            pass

        tenders_list, new_count = scraper.scrape(
            selected_keywords=keywords,
            max_pages=max_pages,
            sort_order=sort_order,
            log_callback=scraper_log_callback,
        )

        with status_lock:
            # Capture session path before scraper's finally clears handler
            # (scrape ends session in finally after return — path still set)
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)

        add_log(f"Scraping completed. Discovered {new_count} new tenders.")
        with status_lock:
            scrape_status["status"] = "idle"
            scrape_status["new_count"] = new_count
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)
    except Exception as e:
        add_log(f"Scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"


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
        else:
            add_log(f"Acquisition failed. No tender was imported for ID: '{bid_id}'.")
            with status_lock:
                scrape_status["new_count"] = 0

        with status_lock:
            scrape_status["status"] = "idle"
            sess = logging_setup.get_session_path()
            if sess:
                scrape_status["log_session_path"] = paths.repo_relative(sess)
    except Exception as e:
        add_log(f"Single bid scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"


@app.route("/")
def serve_dashboard():
    return send_from_directory(paths.ROOT, "dashboard.html")


@app.route("/api/keywords", methods=["GET"])
def get_keywords():
    try:
        keywords = scraper.load_keywords()
        return jsonify({"keywords": keywords})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tenders", methods=["GET"])
def get_tenders():
    try:
        tenders_dict = scraper.load_existing_metadata()
        return jsonify({"tenders": list(tenders_dict.values())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status", methods=["GET"])
def get_status():
    with status_lock:
        logs = list(logging_setup.log_buffer)
        sess = logging_setup.get_session_path()
        session_rel = (
            paths.repo_relative(sess)
            if sess
            else scrape_status.get("log_session_path")
        )
        return jsonify({
            "status": scrape_status["status"],
            "current_keyword": scrape_status["current_keyword"],
            "new_count": scrape_status["new_count"],
            "logs": logs,
            "log_session_path": session_rel,
            "log_count": len(logs),
        })


@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    global scrape_status
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400

        data = request.json or {}
        selected_keywords = data.get("keywords", [])
        max_pages = data.get("max_pages", 2)
        sort_order = data.get("sort_order", "Bid-Start-Date-Latest")

        if not selected_keywords:
            return jsonify({"error": "No keywords selected."}), 400

        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        scrape_status["log_session_path"] = None
        logging_setup.clear_log_buffer()

    thread = threading.Thread(
        target=run_scraper_thread,
        args=(selected_keywords, max_pages, sort_order),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Scraper started successfully."})


@app.route("/api/scrape/id", methods=["POST"])
def trigger_scrape_id():
    global scrape_status
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400

        data = request.json or {}
        bid_id = data.get("bid_id")

        if not bid_id:
            return jsonify({"error": "No Bid ID provided."}), 400

        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        scrape_status["log_session_path"] = None
        logging_setup.clear_log_buffer()

    thread = threading.Thread(
        target=run_scraper_id_thread,
        args=(bid_id,),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Single bid scraper started successfully."})


@app.route("/api/tenders/status", methods=["POST"])
def update_tender_status():
    try:
        data = request.json or {}
        bid_no = data.get("bid_no")
        new_status = data.get("status")

        if not bid_no or not new_status:
            return jsonify({"error": "Missing bid_no or status in request."}), 400

        if new_status not in ["Shortlisted", "Rejected", "Pending Review"]:
            return jsonify({"error": "Invalid status value."}), 400

        tenders_dict = scraper.load_existing_metadata()
        if bid_no in tenders_dict:
            tenders_dict[bid_no]["status"] = new_status
            scraper.save_metadata(list(tenders_dict.values()))
            return jsonify({"message": f"Status for bid {bid_no} updated to {new_status}."})
        else:
            return jsonify({"error": f"Bid {bid_no} not found in database."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring-config", methods=["GET"])
def get_scoring_config():
    """Return current scoring config (defaults if file absent)."""
    try:
        cfg = scraper.load_scoring_config()
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring-config", methods=["POST"])
def update_scoring_config():
    """Validate and atomically persist scoring_config.json."""
    try:
        data = request.json
        if data is None:
            return jsonify({"error": "JSON body required."}), 400

        err = scraper.validate_scoring_config(data)
        if err:
            return jsonify({"error": err}), 400

        scraper.save_scoring_config(data)
        return jsonify({
            "message": "Scoring config updated successfully. Applies on next scrape.",
            "config": data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company-profile", methods=["GET"])
def get_company_profile():
    """Return current company profile (defaults if file absent)."""
    try:
        profile = scraper.load_company_profile()
        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company-profile", methods=["POST"])
def update_company_profile():
    """Validate and atomically persist company_profile.json."""
    try:
        data = request.json
        if data is None:
            return jsonify({"error": "JSON body required."}), 400

        err = scraper.validate_company_profile(data)
        if err:
            return jsonify({"error": err}), 400

        scraper.save_company_profile(data)
        return jsonify({
            "message": "Company profile updated successfully. Applies on next scrape.",
            "profile": data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def get_logs():
    """List app log + recent scrape sessions and return a tail (BE-24)."""
    try:
        sessions = logging_setup.list_session_logs(limit=20)
        latest = sessions[0] if sessions else None
        # Prefer latest session tail; else app log
        if latest:
            abs_latest = os.path.join(paths.SCRAPE_LOGS_DIR, latest["name"])
            safe = logging_setup.safe_logs_path(abs_latest)
            tail = logging_setup.tail_file(safe, lines=100) if safe else []
        else:
            safe = logging_setup.safe_logs_path(paths.APP_LOG_PATH)
            tail = logging_setup.tail_file(safe, lines=100) if safe else []

        return jsonify({
            "app_log": paths.repo_relative(paths.APP_LOG_PATH),
            "sessions": sessions,
            "latest_session": latest,
            "tail": tail,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tenders/downloads/<path:filename>")
def serve_pdf(filename):
    # Serve PDF files securely from the downloads directory
    safe_path = filename.replace("\\", "/")
    return send_from_directory(paths.DOWNLOADS_DIR, safe_path)


if __name__ == "__main__":
    port = 5000
    logger.info("=" * 58)
    logger.info("GeMSentry RFP Acquisition Dashboard Running on Local Server")
    logger.info("Access dashboard at: http://localhost:%s", port)
    logger.info("=" * 58)

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    import time
    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host="127.0.0.1", port=port, debug=False)

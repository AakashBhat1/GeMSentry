import os
import sys
import json
import threading
import datetime
import webbrowser
from flask import Flask, jsonify, request, send_from_directory

# Import the scraper module
import scraper

app = Flask(__name__, static_folder=".", static_url_path="")

# Threading and status control
status_lock = threading.Lock()
scrape_status = {
    "status": "idle",
    "current_keyword": "",
    "new_count": 0,
    "start_time": None
}
scrape_logs = []

def add_log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    with status_lock:
        scrape_logs.append(log_line)
    print(log_line)  # Also echo to python console

def run_scraper_thread(keywords, max_pages, sort_order):
    global scrape_status, scrape_logs
    try:
        add_log(f"Starting background scrape for {len(keywords)} keyword(s)...")
        
        # Scraper callback to push logs from scraper.py to Flask status log
        def scraper_log_callback(msg):
            add_log(msg)
            
        tenders_list, new_count = scraper.scrape(
            selected_keywords=keywords,
            max_pages=max_pages,
            sort_order=sort_order,
            log_callback=scraper_log_callback
        )
        
        add_log(f"Scraping completed. Discovered {new_count} new tenders.")
        with status_lock:
            scrape_status["status"] = "idle"
            scrape_status["new_count"] = new_count
    except Exception as e:
        add_log(f"Scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"

def run_scraper_id_thread(bid_id):
    global scrape_status, scrape_logs
    try:
        add_log(f"Starting background single bid acquisition for ID: '{bid_id}'...")
        
        def scraper_log_callback(msg):
            add_log(msg)
            
        tender = scraper.scrape_single_bid(
            bid_id=bid_id,
            log_callback=scraper_log_callback
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
    except Exception as e:
        add_log(f"Single bid scraping thread crashed: {e}")
        with status_lock:
            scrape_status["status"] = "idle"

@app.route("/")
def serve_dashboard():
    # Serve the dashboard page at the root URL
    return send_from_directory(".", "dashboard.html")

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
        return jsonify({
            "status": scrape_status["status"],
            "current_keyword": scrape_status["current_keyword"],
            "new_count": scrape_status["new_count"],
            "logs": scrape_logs
        })

@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    global scrape_status, scrape_logs
    with status_lock:
        if scrape_status["status"] == "running":
            return jsonify({"error": "Scraper is already running."}), 400
            
        data = request.json or {}
        selected_keywords = data.get("keywords", [])
        max_pages = data.get("max_pages", 2)
        sort_order = data.get("sort_order", "Bid-End-Date-Latest")
        
        if not selected_keywords:
            return jsonify({"error": "No keywords selected."}), 400
            
        # Initialize status and log list
        scrape_status["status"] = "running"
        scrape_status["new_count"] = 0
        scrape_status["start_time"] = datetime.datetime.now().isoformat()
        scrape_logs.clear()
        
    # Start thread
    thread = threading.Thread(
        target=run_scraper_thread,
        args=(selected_keywords, max_pages, sort_order),
        daemon=True
    )
    thread.start()
    
    return jsonify({"message": "Scraper started successfully."})

@app.route("/api/scrape/id", methods=["POST"])
def trigger_scrape_id():
    global scrape_status, scrape_logs
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
        scrape_logs.clear()
        
    thread = threading.Thread(
        target=run_scraper_id_thread,
        args=(bid_id,),
        daemon=True
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

@app.route("/tenders/downloads/<path:filename>")
def serve_pdf(filename):
    # Serve PDF files securely from the downloads directory
    # On Windows, path separators can be converted
    safe_path = filename.replace("\\", "/")
    directory = os.path.join("tenders", "downloads")
    return send_from_directory(directory, safe_path)

if __name__ == "__main__":
    port = 5000
    print(f"\n==========================================================")
    print(f"      GeMSentry RFP Acquisition Dashboard Running on Local Server")
    print(f"      Access dashboard at: http://localhost:{port}")
    print(f"==========================================================\n")
    
    # Automatically open the dashboard in browser after a short delay
    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")
        
    import time
    threading.Thread(target=open_browser, daemon=True).start()
    
    app.run(host="127.0.0.1", port=port, debug=False)

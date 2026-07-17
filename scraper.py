import os
import re
import sys
import json
import csv
import time
import random
import datetime
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

# Configurations
TENDERS_DIR = "tenders"
DOWNLOADS_DIR = os.path.join(TENDERS_DIR, "downloads")

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip().replace(" ", "_")

def sanitize_folder_name(name):
    sanitized = re.sub(r'[^a-zA-Z0-9_\-\s]', '_', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_').lower()

def get_date_folder_name():
    now = datetime.datetime.now()
    return f"{now.strftime('%d')} {now.strftime('%b').lower()}{now.strftime('%y')}"

def load_keywords():
    keywords = []
    csv_path = "keywords.csv"
    if os.path.exists(csv_path):
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if clean.startswith('\ufeff'):
                        clean = clean.replace('\ufeff', '')
                    if clean and not clean.lower().startswith("keyword") and clean not in keywords:
                        keywords.append(clean)
        except Exception as e:
            print(f"Error reading keywords.csv: {e}")
            
    cleaned_keywords = []
    for kw in keywords:
        kw_clean = kw.strip()
        if kw_clean and kw_clean.lower() not in [k.lower() for k in cleaned_keywords]:
            cleaned_keywords.append(kw_clean)
            
    if not cleaned_keywords:
        cleaned_keywords = ["artificial intelligence", "indigenous", "power supply"]
        
    print(f"Loaded {len(cleaned_keywords)} unique keywords from keywords.csv")
    return cleaned_keywords

def find_existing_pdf_file(sanitized_bid):
    if os.path.exists(DOWNLOADS_DIR):
        for root, dirs, files in os.walk(DOWNLOADS_DIR):
            expected_filename = f"{sanitized_bid}.pdf"
            if expected_filename in files:
                full_path = os.path.join(root, expected_filename)
                if os.path.getsize(full_path) > 0:
                    rel_path = os.path.relpath(full_path, start=".").replace("\\", "/")
                    return rel_path
    return None

def analyze_rfp_pdf(pdf_path):
    analysis = {
        "emd_amount": None,
        "emd_status": "Not Required",
        "startup_exemption": "No",
        "mse_exemption": "No",
        "pre_bid_required": "No",
        "pre_bid_date": None,
        "epbg_required": "No",
        "epbg_percentage": None,
        "score": 10,
        "reasons": []
    }
    
    if not os.path.exists(pdf_path):
        return None
        
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for i in range(min(3, len(reader.pages))):
            text += reader.pages[i].extract_text() + "\n"
            
        # Normalize spaces to single space for regex matching
        text_clean = re.sub(r'\s+', ' ', text)
        
        # 1. EMD Analysis
        # Check "EMD Required : Yes/No"
        emd_req_match = re.search(r'EMD\s+Required\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
        emd_req = emd_req_match.group(1) if emd_req_match else "No"
        
        if emd_req.lower() == "yes" or "EMD Amount" in text_clean:
            # Look for EMD Amount
            emd_amount_match = re.search(r'(?:EMD\s+Amount\s*(?:\(INR\))?|EMD\s*value)\s*:\s*([\d,]+)', text_clean, re.IGNORECASE)
            if emd_amount_match:
                amount_str = emd_amount_match.group(1).replace(",", "")
                try:
                    amount = int(amount_str)
                    analysis["emd_amount"] = amount
                    if amount > 1000000: # 10 Lakh INR
                        analysis["emd_status"] = f"Required ({amount:,} INR) - Exceeds 10 Lakh"
                        analysis["score"] -= 2
                        analysis["reasons"].append(f"EMD amount ({amount:,} INR) is high (exceeds 10 Lakh).")
                    else:
                        analysis["emd_status"] = f"Required ({amount:,} INR) - Within 10 Lakh"
                        analysis["reasons"].append(f"EMD amount ({amount:,} INR) is within acceptable limits.")
                except ValueError:
                    analysis["emd_status"] = "Required (Amount not parsed)"
            else:
                analysis["emd_status"] = "Required (Amount not parsed)"
        else:
            analysis["emd_status"] = "No EMD Required (OK)"
            analysis["reasons"].append("No EMD required.")
            
        # 2. Startup & MSE exemptions
        st_exp = None
        st_turn = None
        
        # Check Startup Exemption (Unified or Separate)
        startup_match = re.search(r'Startup\s+Exemption\s+for\s+Years\s+of\s+Experience\s+and\s+Turnover\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
        if startup_match:
            val = startup_match.group(1).lower()
            st_exp = val
            st_turn = val
        else:
            # Check separate fields
            st_exp_match = re.search(r'Startup\s+Exemption\s+for\s+(?:Years\s+of\s+)?Experience\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
            st_turn_match = re.search(r'Startup\s+Exemption\s+for\s+Turnover\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
            st_exp = st_exp_match.group(1).lower() if st_exp_match else None
            st_turn = st_turn_match.group(1).lower() if st_turn_match else None
            
        # Fallbacks for Startup
        if st_exp is None:
            st_exp = "yes" if re.search(r'Startup\s+Exemption.*?Yes', text_clean, re.IGNORECASE) else "no"
        if st_turn is None:
            st_turn = st_exp

        # Check MSE Exemption (Unified or Separate)
        mse_match = re.search(r'MSE\s+Exemption\s+for\s+Years\s+of\s+Experience\s+and\s+Turnover\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
        if mse_match:
            val = mse_match.group(1).lower()
            mse_exp = val
            mse_turn = val
        else:
            # Check separate fields
            mse_exp_match = re.search(r'MSE\s+Exemption\s+for\s+(?:Years\s+of\s+)?Experience\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
            mse_turn_match = re.search(r'MSE\s+Exemption\s+for\s+Turnover\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
            mse_exp = mse_exp_match.group(1).lower() if mse_exp_match else None
            mse_turn = mse_turn_match.group(1).lower() if mse_turn_match else None
            
        # Fallbacks for MSE
        if mse_exp is None:
            mse_exp = "yes" if re.search(r'MSE\s+Exemption.*?Yes', text_clean, re.IGNORECASE) else "no"
        if mse_turn is None:
            mse_turn = mse_exp

        # Map descriptive labels for UI
        def get_label(exp, turn):
            if exp == "yes" and turn == "yes":
                return "Yes (Full)"
            elif exp == "yes":
                return "Yes (Experience Only)"
            elif turn == "yes":
                return "Yes (Turnover Only)"
            else:
                return "No Exemption"

        analysis["startup_exemption"] = get_label(st_exp, st_turn)
        analysis["mse_exemption"] = get_label(mse_exp, mse_turn)

        # Exemption scoring combinations
        st_exempt_count = (1 if st_exp == "yes" else 0) + (1 if st_turn == "yes" else 0)
        mse_exempt_count = (1 if mse_exp == "yes" else 0) + (1 if mse_turn == "yes" else 0)
        total_exemptions = st_exempt_count + mse_exempt_count

        # Scoring deductions based on combinations
        deductions = 0
        if st_exp == "no":
            deductions += 1.0
            analysis["reasons"].append("Exemption Check: Startup Experience criteria is NOT relaxed (-1 mark).")
        if st_turn == "no":
            deductions += 1.0
            analysis["reasons"].append("Exemption Check: Startup Turnover criteria is NOT relaxed (-1 mark).")
        if mse_exp == "no":
            deductions += 1.0
            analysis["reasons"].append("Exemption Check: MSE Experience criteria is NOT relaxed (-1 mark).")
        if mse_turn == "no":
            deductions += 1.0
            analysis["reasons"].append("Exemption Check: MSE Turnover criteria is NOT relaxed (-1 mark).")

        # Additional penalty if absolutely no exemptions are granted
        if total_exemptions == 0:
            deductions += 1.5
            analysis["reasons"].append("Exemption Check: Strict requirements. Neither Startup nor MSE exemptions are granted (-1.5 marks penalty).")
        elif total_exemptions == 4:
            analysis["reasons"].append("Exemption Check: Full Startup & MSE exemptions are granted (Turnover and Experience).")
        else:
            analysis["reasons"].append(f"Exemption Check: Partial exemptions granted ({total_exemptions}/4 relaxed).")

        analysis["score"] -= deductions
            
        # 3. Pre-Bid Meeting
        # "Pre-Bid Meeting Required : Yes/No"
        prebid_req_match = re.search(r'Pre-Bid\s+Meeting\s+Required\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
        prebid_req = prebid_req_match.group(1) if prebid_req_match else "No"
        analysis["pre_bid_required"] = prebid_req
        
        if prebid_req.lower() == "yes":
            # Search for pre-bid date
            prebid_date_match = re.search(r'(?:Pre-Bid\s+Date\s+and\s+Time|Pre-Bid\s+Meeting\s+Date)\s*:\s*([\d\-\s\:\w\,]+?(?:AM|PM|hrs|GMT))', text_clean, re.IGNORECASE)
            if prebid_date_match:
                p_date = prebid_date_match.group(1).strip()
                analysis["pre_bid_date"] = p_date
                analysis["reasons"].append(f"Pre-Bid meeting scheduled on: {p_date}.")
            else:
                analysis["score"] -= 1
                analysis["reasons"].append("Pre-bid meeting is required, but date and time are not clearly specified.")
        else:
            analysis["reasons"].append("No Pre-bid meeting required.")
            
        # 4. ePBG Details
        # "ePBG Detail : ePBG Required : Yes/No"
        epbg_req_match = re.search(r'ePBG\s+Required\s*:\s*(Yes|No)', text_clean, re.IGNORECASE)
        epbg_req = epbg_req_match.group(1) if epbg_req_match else "No"
        
        # GeM document alternative: "ePBG Detail" table
        if "ePBG Detail" in text_clean:
            epbg_req = "Yes"
            
        analysis["epbg_required"] = epbg_req
        
        if epbg_req.lower() == "yes":
            epbg_pct_match = re.search(r'ePBG\s+Percentage\s*(?:\(%\))?\s*:\s*([\d\.]+)', text_clean, re.IGNORECASE)
            if epbg_pct_match:
                pct = epbg_pct_match.group(1)
                analysis["epbg_percentage"] = f"{pct}%"
                analysis["reasons"].append(f"ePBG / Performance Guarantee required: {pct}%.")
            else:
                analysis["reasons"].append("ePBG required (Percentage details not parsed).")
        else:
            analysis["reasons"].append("No ePBG required.")
            
        # Keep score bounded between 1 and 10
        analysis["score"] = max(1, min(10, analysis["score"]))
        
    except Exception as e:
        print(f"Error parsing PDF metadata: {e}")
        analysis["reasons"].append(f"PDF parsing error: {e}")
        analysis["score"] = 5
        
    return analysis

def load_existing_metadata():
    existing_tenders = {}
    csv_path = os.path.join(TENDERS_DIR, "metadata.csv")
    if os.path.exists(csv_path):
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bid_no = row.get("Bid Number")
                    if bid_no:
                        # Load parsed analysis JSON if it exists
                        analysis = None
                        analysis_str = row.get("Analysis")
                        if analysis_str:
                            try:
                                analysis = json.loads(analysis_str)
                            except:
                                pass
                                
                        existing_tenders[bid_no] = {
                            "bid_no": bid_no,
                            "title": row.get("Title"),
                            "quantity": row.get("Quantity"),
                            "department": row.get("Department"),
                            "start_date": row.get("Start Date"),
                            "end_date": row.get("End Date"),
                            "keyword": row.get("Keyword"),
                            "downloaded": row.get("Downloaded") == "True",
                            "local_pdf_path": row.get("Local PDF Path"),
                            "pdf_url": row.get("PDF URL"),
                            "status": row.get("Status", "Pending Review"),
                            "analysis": analysis
                        }
            print(f"Loaded {len(existing_tenders)} existing records from metadata.csv")
        except Exception as e:
            print(f"Error reading existing CSV metadata: {e}")
    return existing_tenders

def save_metadata(tenders_list):
    # Save JSON
    json_path = os.path.join(TENDERS_DIR, "metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tenders_list, f, indent=2, ensure_ascii=False)

    # Save JS (for dashboard)
    js_path = os.path.join(TENDERS_DIR, "metadata.js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("// GeM Scraper Output Metadata\n")
        f.write(f"const TENDER_DATA = {json.dumps(tenders_list, indent=2, ensure_ascii=False)};\n")

    # Save CSV
    csv_path = os.path.join(TENDERS_DIR, "metadata.csv")
    try:
        with open(csv_path, mode="w", encoding="utf-8", newline="") as f:
            fieldnames = ["Bid Number", "Title", "Quantity", "Department", "Start Date", "End Date", "Keyword", "Downloaded", "Local PDF Path", "PDF URL", "Status", "Analysis"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in tenders_list:
                writer.writerow({
                    "Bid Number": t["bid_no"],
                    "Title": t["title"],
                    "Quantity": t["quantity"],
                    "Department": t["department"],
                    "Start Date": t["start_date"],
                    "End Date": t["end_date"],
                    "Keyword": t["keyword"],
                    "Downloaded": str(t["downloaded"]),
                    "Local PDF Path": t["local_pdf_path"],
                    "PDF URL": t["pdf_url"],
                    "Status": t.get("status", "Pending Review"),
                    "Analysis": json.dumps(t.get("analysis")) if t.get("analysis") else ""
                })
        print(f"Saved metadata CSV: {csv_path}")
    except Exception as e:
        print(f"Error saving CSV metadata: {e}")

def parse_cards(html, keyword):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#bidCard") or soup
    cards = container.select("div.card")
    results = []

    for card in cards:
        try:
            bid_link = card.select_one("p.bid_no a.bid_no_hover, p.bid_no a, a.bid_no_hover")
            if not bid_link:
                continue
            bid_no = bid_link.get_text(strip=True)
            if not bid_no or len(bid_no) < 5:
                continue

            pdf_href = bid_link.get("href", "")
            if pdf_href.startswith("/"):
                pdf_url = "https://bidplus.gem.gov.in" + pdf_href
            elif pdf_href.startswith("http"):
                pdf_url = pdf_href
            else:
                pdf_url = f"https://bidplus.gem.gov.in/showbidDocument/{bid_no}"

            title = ""
            col4 = card.select_one("div.col-md-4")
            if col4:
                popover = col4.select_one("a[data-toggle='popover']")
                if popover:
                    title = popover.get("data-content") or popover.get("title") or popover.get_text(strip=True)
                else:
                    rows = col4.select("div.row")
                    if rows:
                        title = rows[0].get_text(strip=True).replace("Items:", "").strip()

            if not title:
                continue

            quantity = "N/A"
            if col4:
                rows = col4.select("div.row")
                for r in rows:
                    txt = r.get_text(strip=True)
                    if "Quantity:" in txt:
                        quantity = txt.replace("Quantity:", "").strip()
                        break

            department = "N/A"
            col5 = card.select_one("div.col-md-5")
            if col5:
                rows = col5.select("div.row")
                if len(rows) >= 2:
                    department = rows[1].get_text(separator=" | ", strip=True).replace("Department Name And Address:", "").strip()
                elif rows:
                    department = rows[0].get_text(separator=" | ", strip=True).replace("Department Name And Address:", "").strip()
            
            department = re.sub(r'\s+', ' ', department)

            start_date_el = card.select_one(".start_date")
            start_date = start_date_el.get_text(strip=True) if start_date_el else "N/A"

            end_date_el = card.select_one(".end_date, span.end_date")
            end_date = end_date_el.get_text(strip=True) if end_date_el else "N/A"

            results.append({
                "bid_no": bid_no,
                "title": title,
                "quantity": quantity,
                "department": department,
                "start_date": start_date,
                "end_date": end_date,
                "pdf_url": pdf_url,
                "keyword": keyword,
                "downloaded": False,
                "local_pdf_path": "",
                "status": "Pending Review",
                "analysis": None
            })
        except Exception as e:
            print(f"Error parsing card: {e}")
            continue

    return results

def select_sort_order(page, sort_order="Bid-End-Date-Latest"):
    sort_map = {
        "Bid-Start-Date-Latest": ("Bid Start Date: Latest First", "#Bid-Start-Date-Latest"),
        "Bid-Start-Date-Oldest": ("Bid Start Date: Oldest First", "#Bid-Start-Date-Oldest"),
        "Bid-End-Date-Latest": ("Bid End Date: Latest First", "#Bid-End-Date-Latest"),
        "Bid-End-Date-Oldest": ("Bid End Date: Oldest First", "#Bid-End-Date-Oldest")
    }
    
    label, selector_id = sort_map.get(sort_order, ("Bid End Date: Latest First", "#Bid-End-Date-Latest"))
    print(f"Setting sorting to '{label}'...")
    try:
        sort_button = page.locator("#currentSort")
        if sort_button.count() > 0:
            sort_button.click()
            page.wait_for_timeout(800)
            
            option = page.locator(selector_id)
            if option.count() > 0:
                option.click()
                page.wait_for_timeout(3000)  # Wait for AJAX refresh
                print(f"Successfully set sort order to '{label}'")
                return True
        print(f"Could not find the sort button (#currentSort) or target option ({selector_id}) on the page.")
    except Exception as e:
        print(f"Failed to set sorting option: {e}")
    return False

def download_rfp_pdf(context, pdf_url, save_path):
    page = None
    try:
        page = context.new_page()
        
        # Listen for download event
        download_container = []
        page.on("download", lambda d: download_container.append(d))
        
        # Navigate to the PDF URL
        try:
            response = page.goto(pdf_url, wait_until="commit", timeout=40000)
        except Exception as e:
            if "download" in str(e).lower() or "navigated to a download" in str(e).lower():
                response = None
            else:
                raise e
        
        page.wait_for_timeout(2000)
        
        # Scenario A: Download triggered
        if download_container:
            download = download_container[0]
            download.save_as(save_path)
            print(f"Successfully saved PDF via page download event: {os.path.basename(save_path)}")
            return True
            
        # Scenario B: Loaded inline
        if response and response.status == 200:
            body = response.body()
            if body.startswith(b"%PDF") or "pdf" in response.headers.get("content-type", "").lower():
                with open(save_path, "wb") as f:
                    f.write(body)
                print(f"Successfully saved PDF via page response body: {os.path.basename(save_path)}")
                return True
            else:
                print(f"Response was not a PDF (Content-Type: {response.headers.get('content-type')}).")
                
    except Exception as e:
        print(f"Download failed for {pdf_url}: {e}")
    finally:
        if page:
            try:
                page.close()
            except:
                pass
    return False

def parse_gem_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    for fmt in ("%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    match = re.search(r'(\d{2})-(\d{2})-(\d{4})', date_str)
    if match:
        try:
            return datetime.datetime.strptime(match.group(0), "%d-%m-%Y")
        except ValueError:
            pass
    return None

def check_date_policy(start_date_str, end_date_str):
    start_date_obj = parse_gem_date(start_date_str)
    end_date_obj = parse_gem_date(end_date_str)
    current_date = datetime.datetime.now()
    
    reasons = []
    
    if not start_date_obj or not end_date_obj:
        return True, []
        
    # 1. Start date must be in current month & year
    if start_date_obj.month != current_date.month or start_date_obj.year != current_date.year:
        reasons.append(f"Start date ({start_date_str}) is not in the current month")
    
    # 2. End date must be at least 7 days (1 week) after start date
    if (end_date_obj - start_date_obj).days < 7:
        reasons.append(f"Bid duration is less than 7 days (Start: {start_date_str}, End: {end_date_str})")
        
    # 3. End date must be at least 7 days (1 week) after current date
    if (end_date_obj - current_date).days < 7:
        reasons.append(f"Remaining bid time is less than 7 days (End: {end_date_str}, Today: {current_date.strftime('%d-%m-%Y')})")
        
    return len(reasons) == 0, reasons

def scrape(selected_keywords=None, max_pages=2, sort_order="Bid-End-Date-Latest", log_callback=None):
    class LogStream:
        def __init__(self, callback):
            self.callback = callback
            self.buffer = ""
            self.is_writing = False
        def write(self, buf):
            sys.__stdout__.write(buf)
            if self.is_writing:
                return
            self.is_writing = True
            try:
                self.buffer += buf
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    self.callback(line)
            finally:
                self.is_writing = False
        def flush(self):
            sys.__stdout__.flush()
            
    original_stdout = sys.stdout
    if log_callback:
        sys.stdout = LogStream(log_callback)

    try:
        print("Initializing directories...")
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

        # 1. Load dynamic keywords
        if selected_keywords:
            KEYWORDS = selected_keywords
            print(f"Scraping {len(KEYWORDS)} selected keyword(s) for search.")
        else:
            KEYWORDS = load_keywords()
        
        # 2. Load existing metadata records
        all_tenders = load_existing_metadata()
        
        new_tenders_count = 0
        
        with sync_playwright() as p:
            print("Launching browser with stealth settings...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                accept_downloads=True
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = context.new_page()

            # Scrape keyword listings
            for keyword in KEYWORDS:
                print(f"\n--- Searching for keyword: '{keyword}' ---")
                encoded_term = urllib.parse.quote(keyword)
                search_url = f"https://bidplus.gem.gov.in/all-bids?bid_number=&items_per_page=&search_under=&search={encoded_term}"
                
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    
                    has_cards = True
                    try:
                        page.wait_for_selector("div.card, #bidCard", timeout=10000)
                    except Exception:
                        print(f"No bid cards displayed for '{keyword}' on page 1.")
                        has_cards = False
                    
                    if not has_cards:
                        continue

                    # Set Sorting Order
                    select_sort_order(page, sort_order)

                    # Page 1 parsing
                    tenders = parse_cards(page.content(), keyword)
                    print(f"Page 1: Found {len(tenders)} tenders")
                    for t in tenders:
                        date_ok, reasons = check_date_policy(t.get("start_date"), t.get("end_date"))
                        if not date_ok:
                            print(f"  [Skipped - Date Policy] {t['bid_no']}: {', '.join(reasons)}")
                            continue

                        if t["bid_no"] not in all_tenders:
                            all_tenders[t["bid_no"]] = t
                            new_tenders_count += 1
                            print(f"  [New Tender Discovered] {t['bid_no']}")
                        else:
                            existing = all_tenders[t["bid_no"]]
                            if keyword not in existing["keyword"]:
                                existing["keyword"] += f", {keyword}"

                    # Paginate pages up to max_pages
                    for page_num in range(2, max_pages + 1):
                        next_selector = f'a[href="#page-{page_num}"].page-link'
                        next_btn = page.query_selector(next_selector)
                        if not next_btn:
                            break
                        
                        print(f"Navigating to page {page_num}...")
                        next_btn.click()
                        page.wait_for_timeout(2500)
                        
                        page_tenders = parse_cards(page.content(), keyword)
                        print(f"Page {page_num}: Found {len(page_tenders)} tenders")
                        if not page_tenders:
                            break
                            
                        for t in page_tenders:
                            date_ok, reasons = check_date_policy(t.get("start_date"), t.get("end_date"))
                            if not date_ok:
                                print(f"  [Skipped - Date Policy] {t['bid_no']}: {', '.join(reasons)}")
                                continue

                            if t["bid_no"] not in all_tenders:
                                all_tenders[t["bid_no"]] = t
                                new_tenders_count += 1
                                print(f"  [New Tender Discovered] {t['bid_no']}")
                            else:
                                existing = all_tenders[t["bid_no"]]
                                if keyword not in existing["keyword"]:
                                    existing["keyword"] += f", {keyword}"

                except Exception as e:
                    print(f"Error searching for '{keyword}': {e}")
                
                time.sleep(random.uniform(2.0, 4.0))

            if new_tenders_count == 0:
                print(f"\nFor today ({get_date_folder_name()}), no new tenders could be found.")

            # Download RFP documents
            print(f"\n--- Checking RFP Downloads for {len(all_tenders)} total tenders ---")
            tenders_list = list(all_tenders.values())
            
            for idx, tender in enumerate(tenders_list):
                bid_no = tender["bid_no"]
                
                # 1. Skip already rejected or successfully processed tenders
                if tender.get("status") == "Rejected":
                    continue
                if tender.get("downloaded") and tender.get("analysis") and tender.get("local_pdf_path"):
                    if os.path.exists(tender["local_pdf_path"]):
                        continue
                        
                pdf_url = tender["pdf_url"]
                keyword = tender["keyword"].split(",")[0].strip()
                
                sanitized_bid = sanitize_filename(bid_no)
                sanitized_keyword = sanitize_folder_name(keyword)
                date_folder = get_date_folder_name()
                
                # 2. Date Validation Gate (only for unprocessed tenders)
                date_ok, date_reasons = check_date_policy(tender.get("start_date"), tender.get("end_date"))
                if not date_ok and tender.get("status") != "Shortlisted":
                    reason_msg = " | ".join(date_reasons)
                    print(f"[{idx+1}/{len(tenders_list)}] Skipping Bid {bid_no} - Rejected by Date Policy: {reason_msg}")
                    tender["downloaded"] = False
                    tender["status"] = "Rejected"
                    tender["analysis"] = {
                        "emd_amount": None,
                        "emd_status": "Not Analyzed",
                        "startup_exemption": "Unknown",
                        "mse_exemption": "Unknown",
                        "pre_bid_required": "Unknown",
                        "pre_bid_date": None,
                        "epbg_required": "Unknown",
                        "epbg_percentage": None,
                        "score": 1,
                        "reasons": [f"Auto-Rejected by Date Policy: {r}" for r in date_reasons]
                    }
                    continue
                
                existing_path = find_existing_pdf_file(sanitized_bid)
                
                target_dir = os.path.join(DOWNLOADS_DIR, sanitized_keyword, date_folder, sanitized_bid)
                save_path = os.path.join(target_dir, f"{sanitized_bid}.pdf")
                
                pdf_location = None
                if existing_path:
                    tender["downloaded"] = True
                    tender["local_pdf_path"] = existing_path
                    pdf_location = existing_path
                else:
                    os.makedirs(target_dir, exist_ok=True)
                    print(f"[{idx+1}/{len(tenders_list)}] Downloading new RFP for Bid: {bid_no}...")
                    success = download_rfp_pdf(context, pdf_url, save_path)
                    if success:
                        tender["downloaded"] = True
                        tender["local_pdf_path"] = save_path.replace("\\", "/")
                        pdf_location = save_path
                    else:
                        tender["downloaded"] = False

                # Scan and analyze RFP PDF if it is downloaded
                if tender["downloaded"] and pdf_location and os.path.exists(pdf_location):
                    # Perform scoring analysis
                    analysis = analyze_rfp_pdf(pdf_location)
                    if analysis:
                        tender["analysis"] = analysis
                        # Apply automated status if not already manually overridden
                        if tender.get("status") not in ["Shortlisted", "Rejected"]:
                            if analysis["score"] >= 7:
                                tender["status"] = "Shortlisted"
                            elif analysis["score"] <= 4:
                                tender["status"] = "Rejected"
                            else:
                                tender["status"] = "Pending Review"
                else:
                    if tender.get("status") not in ["Shortlisted", "Rejected"]:
                        tender["status"] = "Pending Review"
                    tender["analysis"] = {
                        "emd_amount": None,
                        "emd_status": "No PDF Available",
                        "startup_exemption": "Unknown",
                        "mse_exemption": "Unknown",
                        "pre_bid_required": "Unknown",
                        "pre_bid_date": None,
                        "epbg_required": "Unknown",
                        "epbg_percentage": None,
                        "score": 5,
                        "reasons": ["RFP PDF document is not available for analysis."]
                    }

                time.sleep(random.uniform(1.5, 3.0))

            browser.close()

        save_metadata(tenders_list)
        return tenders_list, new_tenders_count
    finally:
        sys.stdout = original_stdout

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GeM RFP Acquisition CLI Scraper")
    parser.add_argument("--keywords", nargs="+", help="Keywords list to search")
    parser.add_argument("--pages", type=int, default=2, help="Max pages limit per keyword")
    parser.add_argument("--sort", default="Bid-End-Date-Latest", 
                        choices=["Bid-End-Date-Latest", "Bid-End-Date-Oldest", "Bid-Start-Date-Latest", "Bid-Start-Date-Oldest"], 
                        help="Sort order option")
    
    args = parser.parse_args()
    scrape(selected_keywords=args.keywords, max_pages=args.pages, sort_order=args.sort)

"""GeM bidplus HTTP/Playwright client and card parsing."""

import datetime
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from bs4 import BeautifulSoup

from gemsentry.constants import logger
from gemsentry.dateparse import parse_gem_date, parse_iso_date_to_gem
from gemsentry.textutils import _parse_inr_amount, today_iso


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
            if pdf_href.startswith("http"):
                pdf_url = pdf_href
            elif pdf_href:
                pdf_url = urllib.parse.urljoin("https://bidplus.gem.gov.in/all-bids", pdf_href)
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
            est_value_inr = None
            if col4:
                rows = col4.select("div.row")
                for r in rows:
                    txt = r.get_text(" ", strip=True)
                    if "Quantity:" in txt or "Quantity" in txt:
                        quantity = re.sub(r'(?i)Quantity\s*:', "", txt).strip()
                    # BE-16: estimated / contract / bid value on the card
                    if est_value_inr is None and re.search(
                        r'(?i)(?:Estimated\s*(?:Value|Bid\s*Value)|Bid\s*Value|Contract\s*Value|Value\s*:)',
                        txt
                    ):
                        amt = _parse_inr_amount(txt)
                        if amt is not None and amt >= 1000:
                            est_value_inr = amt

            # Also scan whole card text for value labels if not found in col4 rows
            if est_value_inr is None:
                card_text = card.get_text(" ", strip=True)
                vm = re.search(
                    r'(?i)(?:Estimated\s*(?:Bid\s*)?Value|Bid\s*Value|Contract\s*Value)\s*:?\s*'
                    r'(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)',
                    card_text
                )
                if vm:
                    amt = _parse_inr_amount(vm.group(1))
                    if amt is not None and amt >= 1000:
                        est_value_inr = amt

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
                "est_value_inr": est_value_inr,
                "start_date": start_date,
                "end_date": end_date,
                "pdf_url": pdf_url,
                "keyword": keyword,
                "downloaded": False,
                "local_pdf_path": "",
                "first_seen": today_iso(),
                "status": "Pending Review",
                "analysis": None
            })
        except Exception as e:
            logger.error(f"Error parsing card: {e}")
            continue

    return results


def select_sort_order(page, sort_order="Bid-Start-Date-Latest"):
    sort_map = {
        "Bid-Start-Date-Latest": ("Bid Start Date: Latest First", "#Bid-Start-Date-Latest"),
        "Bid-Start-Date-Oldest": ("Bid Start Date: Oldest First", "#Bid-Start-Date-Oldest"),
        "Bid-End-Date-Latest": ("Bid End Date: Latest First", "#Bid-End-Date-Latest"),
        "Bid-End-Date-Oldest": ("Bid End Date: Oldest First", "#Bid-End-Date-Oldest")
    }
    
    label, selector_id = sort_map.get(sort_order, ("Bid Start Date: Latest First", "#Bid-Start-Date-Latest"))
    logger.info(f"Setting sorting to '{label}'...")
    try:
        sort_button = page.locator("#currentSort")
        if sort_button.count() > 0:
            sort_button.click()
            page.wait_for_timeout(800)
            
            option = page.locator(selector_id)
            if option.count() > 0:
                option.click()
                page.wait_for_timeout(3000)  # Wait for AJAX refresh
                logger.info(f"Successfully set sort order to '{label}'")
                return True
        logger.warning(f"Could not find the sort button (#currentSort) or target option ({selector_id}) on the page.")
    except Exception as e:
        logger.error(f"Failed to set sorting option: {e}")
    return False


def download_pdf_http(pdf_url, save_path, cookie_header,
                      referer="https://bidplus.gem.gov.in/all-bids"):
    """
    Fast raw-HTTP PDF download using the harvested browser session cookies
    (BE-27) — ~0.3–1s vs 2–4s for a full Playwright page. Thread-safe.
    Returns True only when the body is a real PDF; callers fall back to
    download_rfp_pdf (browser) on False.
    """
    if not pdf_url:
        return False
    try:
        req = urllib.request.Request(
            pdf_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/pdf,application/octet-stream,*/*",
                "Referer": referer,
                "Cookie": cookie_header or "",
            },
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=25) as resp:
            body = resp.read()
        if body.startswith(b"%PDF"):
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(body)
            return True
        logger.debug("HTTP download for %s returned non-PDF body (%d bytes).",
                     pdf_url, len(body))
    except Exception as e:
        logger.debug("HTTP download failed for %s: %s", pdf_url, e)
    return False


def download_rfp_pdf(context, pdf_url, save_path):
    page = None
    try:
        page = context.new_page()
        download_container = []
        page.on("download", lambda d: download_container.append(d))
        
        response = None
        try:
            response = page.goto(pdf_url, wait_until="commit", timeout=15000)
        except Exception as e:
            if "download" in str(e).lower() or "navigated to a download" in str(e).lower():
                response = None
            else:
                raise e
        
        # Give up to 1s for download object event to register if committed
        if not download_container and not response:
            page.wait_for_timeout(500)
        
        # Scenario A: Download event triggered
        if download_container:
            download = download_container[0]
            download.save_as(save_path)
            logger.info(f"Successfully saved PDF via download event: {os.path.basename(save_path)}")
            return True
            
        # Scenario B: Loaded inline
        if response and response.status == 200:
            body = response.body()
            if body.startswith(b"%PDF") or "pdf" in response.headers.get("content-type", "").lower():
                with open(save_path, "wb") as f:
                    f.write(body)
                logger.info(f"Successfully saved PDF via response body: {os.path.basename(save_path)}")
                return True
            else:
                logger.warning(f"Response was not a PDF (Content-Type: {response.headers.get('content-type')}).")
                
    except Exception as e:
        logger.error(f"Download failed for {pdf_url}: {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
    return False


def doc_to_tender(doc, keyword):
    b_id = doc.get("id") or (doc.get("b_id", [None])[0])
    bid_no = doc.get("b_bid_number", ["N/A"])[0] if isinstance(doc.get("b_bid_number"), list) else doc.get("b_bid_number", "N/A")
    
    title_list = doc.get("bd_category_name") or doc.get("b_category_name") or [""]
    title = title_list[0] if isinstance(title_list, list) else str(title_list)
    
    qty_list = doc.get("b_total_quantity") or ["N/A"]
    quantity = str(qty_list[0]) if isinstance(qty_list, list) else str(qty_list)
    
    dept_min = doc.get("ba_official_details_minName", [""])[0] if isinstance(doc.get("ba_official_details_minName"), list) else ""
    dept_name = doc.get("ba_official_details_deptName", [""])[0] if isinstance(doc.get("ba_official_details_deptName"), list) else ""
    department = " | ".join(filter(None, [dept_min, dept_name])) or "N/A"
    
    start_iso = doc.get("final_start_date_sort", [""])[0] if isinstance(doc.get("final_start_date_sort"), list) else ""
    end_iso = doc.get("final_end_date_sort", [""])[0] if isinstance(doc.get("final_end_date_sort"), list) else ""
    
    start_date = parse_iso_date_to_gem(start_iso)
    end_date = parse_iso_date_to_gem(end_iso)
    
    pdf_url = f"https://bidplus.gem.gov.in/showbidDocument/{b_id}"
    
    return {
        "bid_no": bid_no,
        "title": title,
        "quantity": quantity,
        "department": department,
        "est_value_inr": None,
        "start_date": start_date,
        "end_date": end_date,
        "pdf_url": pdf_url,
        "keyword": keyword,
        "downloaded": False,
        "local_pdf_path": "",
        "first_seen": today_iso(),
        "status": "Pending Review",
        "analysis": None
    }


_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch_keyword_bids_api(
    keyword,
    cookie_header,
    csrf_token,
    max_pages=2,
    sort_order="Bid-Start-Date-Latest",
    target_count=None,
    min_days_left=None,
    max_days_left=None,
):
    tenders = []
    url = "https://bidplus.gem.gov.in/all-bids-data"
    safety_max_pages = 30 if target_count else max_pages
    matching_target_count = 0
    now = datetime.datetime.now()

    for page_num in range(1, safety_max_pages + 1):
        try:
            payload_dict = {
                "param": {"searchBid": keyword, "searchType": "fullText"},
                "filter": {
                    "bidStatusType": "ongoing_bids",
                    "byType": "all",
                    "highBidValue": "",
                    "byEndDate": {"from": "", "to": ""},
                    "sort": sort_order
                }
            }
            if page_num > 1:
                payload_dict["param"]["page"] = page_num

            data = urllib.parse.urlencode({
                "payload": json.dumps(payload_dict),
                "csrf_bd_gem_nk": csrf_token or ""
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://bidplus.gem.gov.in/all-bids",
                    "Cookie": cookie_header or ""
                },
                method="POST"
            )

            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=12) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                docs = (
                    res_json.get("response", {})
                    .get("response", {})
                    .get("docs", [])
                )
                if not docs:
                    break
                for doc in docs:
                    t = doc_to_tender(doc, keyword)

                    if min_days_left is not None or max_days_left is not None:
                        end_dt = parse_gem_date(t.get("end_date"))
                        if end_dt:
                            rem_days = (end_dt - now).days
                            min_d = min_days_left if min_days_left is not None else 0
                            max_d = max_days_left if max_days_left is not None else 9999
                            if not (min_d <= rem_days <= max_d):
                                logger.debug(
                                    f"Skipping tender {t['bid_no']} ({rem_days} days left) - outside filter window [{min_d}-{max_d}] days."
                                )
                                continue
                            matching_target_count += 1

                    tenders.append(t)

            if target_count and matching_target_count >= target_count:
                logger.info(
                    f"Keyword '{keyword}': Target goal reached ({matching_target_count}/{target_count} tenders in window) on page {page_num}."
                )
                break
        except Exception as e:
            logger.warning(f"API request failed for keyword '{keyword}' page {page_num}: {e}")
            break
    return tenders

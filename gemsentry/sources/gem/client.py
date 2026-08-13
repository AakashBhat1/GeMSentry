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
from gemsentry.search import build_search_plan, matches_search_result
from gemsentry.sources.attribution import normalize_host
from gemsentry.textutils import _parse_inr_amount, today_iso

# Every GeM property (bidplus, mkp, ...) is a subdomain of this.
GEM_HOST = "gem.gov.in"

# Per-request ceiling for a raw-HTTP PDF fetch. A host that never completes
# the TCP handshake burns this whole budget, so it stays deliberately short;
# callers override it from download_policy.download_timeout.
DEFAULT_DOWNLOAD_TIMEOUT = 15


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
                "primary_item": title.split(",", 1)[0].strip(),
                "item_category": title,
                "quantity": quantity,
                "department": department,
                "est_value_inr": est_value_inr,
                "start_date": start_date,
                "end_date": end_date,
                "pdf_url": pdf_url,
                "source_id": "gem",
                "source_name": "Government e-Marketplace (GeM)",
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


def is_gem_url(url):
    """True when ``url`` points at GeM itself (bid documents live on a subdomain).

    Suffix matching is anchored on a dot so a lookalike host such as
    ``notgem.gov.in`` cannot pass as GeM.
    """
    host = normalize_host(url)
    return host == GEM_HOST or host.endswith("." + GEM_HOST)


def download_pdf_http(pdf_url, save_path, cookie_header,
                      referer="https://bidplus.gem.gov.in/all-bids",
                      timeout=DEFAULT_DOWNLOAD_TIMEOUT):
    """
    Fast raw-HTTP PDF download using the harvested browser session cookies
    (BE-27) — ~0.3–1s vs 2–4s for a full Playwright page. Thread-safe.
    Returns True only when the body is a real PDF; callers fall back to
    download_rfp_pdf (browser) on False.

    The session cookie is only ever sent to GeM. A tender record can carry any
    portal's document URL, and the bidplus session token must not travel to a
    third-party host just because the record ended up in this code path.
    """
    if not pdf_url:
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Referer": referer,
    }
    if cookie_header:
        if is_gem_url(pdf_url):
            headers["Cookie"] = cookie_header
        else:
            logger.debug("Withholding GeM session cookie from non-GeM host: %s", pdf_url)
    try:
        req = urllib.request.Request(pdf_url, headers=headers)
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
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


def _first_doc_value(doc, *keys, default=None):
    """Return the first non-empty scalar from a Solr field's list/scalar shapes."""
    for key in keys:
        value = doc.get(key)
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if item not in (None, "")), None)
        if value not in (None, ""):
            return value
    return default


def _doc_bool(doc, *keys):
    value = _first_doc_value(doc, *keys)
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _doc_estimated_value(doc):
    """Accept value fields seen across old/new GeM response variants."""
    value = _first_doc_value(
        doc,
        "b_estimated_value",
        "b_estimated_bid_value",
        "estimated_bid_value",
        "b_total_value",
        "b_bid_value",
    )
    amount = _parse_inr_amount(value) if value not in (None, "") else None
    return amount if amount is not None and amount >= 1000 else None


def doc_to_tender(doc, keyword):
    """Normalize one current GeM/Solr listing document into a scored record."""
    result_bid_no = str(_first_doc_value(doc, "b_bid_number", default="N/A"))
    parent_bid_no = str(_first_doc_value(doc, "b_bid_number_parent", default=""))
    is_reverse_auction = "/R/" in result_bid_no.upper() and bool(parent_bid_no)
    bid_no = parent_bid_no if is_reverse_auction else result_bid_no

    result_id = _first_doc_value(doc, "id", "b_id")
    parent_id = _first_doc_value(doc, "b_id_parent")
    document_id = parent_id if is_reverse_auction and parent_id is not None else result_id

    summary_category = str(_first_doc_value(
        doc, "b_category_name", "bd_category_name", default=""
    )).strip()
    item_category = str(_first_doc_value(
        doc, "bd_category_name", "b_category_name", default=summary_category
    )).strip()
    title = summary_category or item_category or "N/A"
    primary_item = (item_category or title).split(",", 1)[0].strip()

    quantity = str(_first_doc_value(doc, "b_total_quantity", default="N/A"))
    dept_min = str(_first_doc_value(doc, "ba_official_details_minName", default="")).strip()
    dept_name = str(_first_doc_value(doc, "ba_official_details_deptName", default="")).strip()
    department = " | ".join(filter(None, [dept_min, dept_name])) or "N/A"

    start_iso = _first_doc_value(doc, "final_start_date_sort", default="")
    end_iso = _first_doc_value(doc, "final_end_date_sort", default="")
    start_date = parse_iso_date_to_gem(str(start_iso))
    end_date = parse_iso_date_to_gem(str(end_iso))

    pdf_url = (
        f"https://bidplus.gem.gov.in/showbidDocument/{document_id}"
        if document_id not in (None, "") else ""
    )

    return {
        "bid_no": bid_no,
        "gem_result_bid_no": result_bid_no,
        "gem_parent_bid_no": parent_bid_no or None,
        "gem_document_id": str(document_id) if document_id not in (None, "") else None,
        "title": title,
        "primary_item": primary_item,
        "item_category": item_category,
        "quantity": quantity,
        "department": department,
        "est_value_inr": _doc_estimated_value(doc),
        "start_date": start_date,
        "end_date": end_date,
        "pdf_url": pdf_url,
        "is_reverse_auction": is_reverse_auction,
        "is_custom_bid": _doc_bool(doc, "b_is_custom_item"),
        "is_boq": _doc_bool(doc, "bd_details_is_boq", "bd_details_new_boq"),
        "is_global_tendering": _doc_bool(doc, "ba_is_global_tendering"),
        "is_single_packet": _doc_bool(doc, "ba_is_single_packet"),
        "is_high_value": _doc_bool(doc, "is_high_value"),
        "gem_bid_type": _first_doc_value(doc, "b_bid_type", "b_type"),
        "gem_status": _first_doc_value(doc, "b_status", "ra_b_status"),
        # Stamped so the marketplace is one portal among many in the dashboard
        # filter, rather than the unlabelled default everything else is not.
        "source_id": "gem",
        "source_name": "Government e-Marketplace (GeM)",
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
    seen_bid_nos = set()
    url = "https://bidplus.gem.gov.in/all-bids-data"
    safety_max_pages = 30 if target_count else max_pages
    matching_target_count = 0
    now = datetime.datetime.now()
    plan = build_search_plan(keyword)

    if len(plan.queries) > 1:
        logger.info(
            "Keyword '%s' expanded to %d focused portal queries: %s",
            keyword, len(plan.queries), ", ".join(plan.queries),
        )

    for search_query in plan.queries:
        if target_count and matching_target_count >= target_count:
            break
        for page_num in range(1, safety_max_pages + 1):
            try:
                payload_dict = {
                    "param": {"searchBid": search_query, "searchType": "fullText"},
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
                        t = doc_to_tender(doc, plan.canonical_keyword)
                        bid_no = t.get("bid_no")
                        if not bid_no or bid_no in seen_bid_nos:
                            continue
                        if not matches_search_result(t, plan):
                            logger.debug(
                                "Discarding %s: card does not verify search concept '%s'",
                                bid_no, plan.concept_id,
                            )
                            continue

                        if min_days_left is not None or max_days_left is not None:
                            end_dt = parse_gem_date(t.get("end_date"))
                            if not end_dt:
                                logger.debug(
                                    "Skipping tender %s: deadline is not parseable.", bid_no
                                )
                                continue
                            remaining = (end_dt - now).total_seconds() / 86400.0
                            min_d = float(min_days_left) if min_days_left is not None else 0.0
                            max_d = float(max_days_left) if max_days_left is not None else 9999.0
                            if not (min_d <= remaining <= max_d):
                                logger.debug(
                                    "Skipping tender %s (%.1f days left) - outside "
                                    "filter window [%.1f-%.1f] days.",
                                    bid_no, remaining, min_d, max_d,
                                )
                                continue

                        seen_bid_nos.add(bid_no)
                        tenders.append(t)
                        matching_target_count += 1

                if target_count and matching_target_count >= target_count:
                    logger.info(
                        "Keyword '%s': Target goal reached (%d/%d unique tenders) "
                        "with query '%s' on page %d.",
                        keyword, matching_target_count, target_count, search_query, page_num,
                    )
                    break
            except Exception as e:
                logger.warning(
                    "API request failed for keyword '%s' (query '%s') page %d: %s",
                    keyword, search_query, page_num, e,
                )
                break
    return tenders

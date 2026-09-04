"""GeM bidplus HTTP/Playwright client and card parsing."""

import datetime
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from bs4 import BeautifulSoup

from gemsentry.constants import logger
from gemsentry.dateparse import parse_gem_date, parse_iso_date_to_gem
from gemsentry.textutils import _parse_inr_amount, today_iso

# Per-request ceiling for a raw-HTTP PDF fetch. A host that never completes
# the TCP handshake burns this whole budget, so it stays deliberately short;
# callers override it from download_policy.download_timeout.
DEFAULT_DOWNLOAD_TIMEOUT = 15

# GeM's Solr-backed all-bids-data endpoint is slow on popular phrases
# ("IOT ENERGY METER") and can hang with no body. Each page is bounded;
# the whole keyword job is bounded separately so pagination cannot stall.
DEFAULT_SEARCH_TIMEOUT = 20
DEFAULT_SEARCH_RETRIES = 1
DEFAULT_KEYWORD_DEADLINE = 60

GEM_BIDPLUS = "https://bidplus.gem.gov.in"
GEM_ALL_BIDS_DATA = f"{GEM_BIDPLUS}/all-bids-data"

# Patch point for unit tests (never call urllib.request.urlopen directly below).
_urlopen = urllib.request.urlopen

# Product-name hyphens (LEAD-ACID, NB-IOT, NI-CD, E-GOVERNANCE). GeM's query
# parser treats '-' as Lucene NOT, and all-bids-data then 404s.
_HYPHEN_AS_SEPARATOR = re.compile(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])")
_PDF_MAGIC = b"%PDF"


def normalize_search_keyword(keyword):
    """Turn a user keyword into a GeM full-text query string.

    Hyphens between alphanumerics are word separators in these product names,
    not Lucene operators. Spaces are collapsed. The result is safe to embed
    in the JSON ``searchBid`` field (the form body is then urlencoded).
    """
    text = " ".join(str(keyword or "").split())
    return _HYPHEN_AS_SEPARATOR.sub(" ", text).strip()


def gem_document_url(document_id):
    """Build ``showbidDocument`` URL without turning slashes into extra path segments.

    Bid numbers look like ``GEM/2026/B/7553726``. Interpolating that raw value
    produces ``/showbidDocument/GEM/2026/B/7553726``, which GeM routes as nested
    paths and 404s. Numeric Solr ids stay unquoted.
    """
    if document_id in (None, ""):
        return ""
    token = str(document_id).strip()
    if not token:
        return ""
    if re.fullmatch(r"\d+", token):
        encoded = token
    else:
        encoded = urllib.parse.quote(token, safe="")
    return f"{GEM_BIDPLUS}/showbidDocument/{encoded}"


def looks_like_pdf_url(url):
    """True when ``url`` is a document endpoint, not an HTML listing page.

    BHEL (and other table adapters) store the listing-row href as ``url``.
    That page is Drupal HTML; fetching it as a PDF corrupts the downloads
    tree. GeM bid documents live on bidplus ``showbidDocument`` /
    ``showradocumentPdf`` paths, or a ``.pdf`` link.
    """
    if not url:
        return False
    lower = str(url).strip().lower()
    if not lower.startswith("http://") and not lower.startswith("https://"):
        return False
    path = urllib.parse.urlparse(lower).path
    if path.endswith(".pdf"):
        return True
    if "showbiddocument" in path or "showradocument" in path:
        return True
    return False


def classify_document_body(body, content_type=""):
    """Return ``pdf``, ``html``, or ``unknown`` from magic bytes then content-type.

    Login/error interstitials are often served with ``Content-Type: application/pdf``
    because the URL looks like a document. Magic bytes win; an HTML signature
    in the first 2 KiB still counts as HTML even when the header says PDF.
    """
    raw = body or b""
    stripped = raw.lstrip()
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    head = stripped[:2048].lower()

    if stripped.startswith(_PDF_MAGIC) or _PDF_MAGIC + b"-" in stripped[:1024]:
        if b"<html" in head or b"<!doctype html" in head:
            return "html"
        return "pdf"
    if (
        stripped.lower().startswith(b"<!doctype html")
        or stripped.lower().startswith(b"<html")
        or b"<html" in head
        or b"<!doctype html" in head
    ):
        return "html"
    if "html" in ctype or ctype == "text/html":
        return "html"
    if ctype == "application/pdf" or ctype.endswith("/pdf"):
        # Header claimed PDF but the body did not start with %PDF.
        return "unknown"
    return "unknown"


def is_pdf_file(path):
    """True when ``path`` exists and begins with PDF magic bytes."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(2048)
    except OSError:
        return False
    return classify_document_body(head) == "pdf"


def build_search_payload(keyword, page_num=1, sort_order="Bid-Start-Date-Latest"):
    """JSON object GeM expects inside the ``payload`` form field."""
    param = {
        "searchBid": normalize_search_keyword(keyword),
        "searchType": "fullText",
    }
    if page_num > 1:
        param["page"] = page_num
    return {
        "param": param,
        "filter": {
            "bidStatusType": "ongoing_bids",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": sort_order,
        },
    }


def encode_search_form(payload_dict, csrf_token=""):
    """URL-encode the GeM search form.

    Compact JSON avoids ``+`` (from ``quote_plus``) replacing the spaces that
    ``json.dumps`` would otherwise insert around ``:`` and ``,``. ``quote``
    (percent-encoding, including spaces as ``%20``) is used so a server that
    does not treat ``+`` as space cannot corrupt the JSON.
    """
    payload = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=True)
    return urllib.parse.urlencode(
        {"payload": payload, "csrf_bd_gem_nk": csrf_token or ""},
        quote_via=urllib.parse.quote,
    ).encode("utf-8")


def search_request_url():
    """GeM listing search is a POST to this URL; the keyword is never a path segment."""
    return GEM_ALL_BIDS_DATA


class KeywordSearchTimeout(TimeoutError):
    """Raised when a keyword search exceeds its per-request or wall-clock budget."""


def _save_pdf_body(body, save_path, content_type="", source=""):
    kind = classify_document_body(body, content_type)
    if kind != "pdf":
        logger.warning(
            "Refusing to save %s body as PDF (%s, %d bytes, Content-Type: %s).",
            kind, source or save_path, len(body or b""), content_type or "n/a",
        )
        return False
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "wb") as handle:
        handle.write(body)
    return True


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
                pdf_url = gem_document_url(bid_no)

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
                      referer="https://bidplus.gem.gov.in/all-bids",
                      timeout=DEFAULT_DOWNLOAD_TIMEOUT):
    """
    Fast raw-HTTP PDF download using the harvested browser session cookies
    (BE-27) — ~0.3–1s vs 2–4s for a full Playwright page. Thread-safe.
    Returns True only when the body is a real PDF; callers fall back to
    download_rfp_pdf (browser) on False. HTML interstitials (login, error
    pages, BHEL listing pages) are never written to ``save_path``.
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
        with _urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "") if getattr(resp, "headers", None) else ""
        return _save_pdf_body(body, save_path, content_type, source=pdf_url)
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
        
        # Scenario A: Download event triggered. Playwright will write whatever
        # the server streamed -- including HTML interstitials -- so the file
        # is classified after save and discarded when it is not a PDF.
        if download_container:
            download = download_container[0]
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            download.save_as(save_path)
            try:
                with open(save_path, "rb") as handle:
                    body = handle.read()
            except OSError:
                return False
            suggested_type = ""
            try:
                suggested_type = download.suggested_filename or ""
            except Exception:
                suggested_type = ""
            if classify_document_body(body, suggested_type) == "pdf":
                logger.info(f"Successfully saved PDF via download event: {os.path.basename(save_path)}")
                return True
            logger.warning(
                "Download event for %s was not a PDF; removing %s.",
                pdf_url, os.path.basename(save_path),
            )
            try:
                os.remove(save_path)
            except OSError:
                pass
            return False

        # Scenario B: Loaded inline. Do not trust Content-Type alone -- GeM
        # login/error pages are frequently labelled application/pdf.
        if response and response.status == 200:
            body = response.body()
            content_type = (response.headers or {}).get("content-type", "")
            if _save_pdf_body(body, save_path, content_type, source=pdf_url):
                logger.info(f"Successfully saved PDF via response body: {os.path.basename(save_path)}")
                return True
            logger.warning(
                "Response was not a PDF (Content-Type: %s).",
                content_type or "n/a",
            )
                
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
    
    pdf_url = gem_document_url(b_id)
    
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


def _is_timeout(exc):
    if isinstance(exc, (TimeoutError, socket.timeout, KeywordSearchTimeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        return "timed out" in str(exc).lower()
    return "timed out" in str(exc).lower()


def _post_search_page(keyword, cookie_header, csrf_token, page_num, sort_order,
                      timeout=DEFAULT_SEARCH_TIMEOUT):
    """POST one all-bids-data page. Raises on HTTP/network failure."""
    payload_dict = build_search_payload(keyword, page_num=page_num, sort_order=sort_order)
    data = encode_search_form(payload_dict, csrf_token)
    req = urllib.request.Request(
        search_request_url(),
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://bidplus.gem.gov.in/all-bids",
            "Cookie": cookie_header or "",
        },
        method="POST",
    )
    with _urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_keyword_bids_api(
    keyword,
    cookie_header,
    csrf_token,
    max_pages=2,
    sort_order="Bid-Start-Date-Latest",
    target_count=None,
    min_days_left=None,
    max_days_left=None,
    timeout=DEFAULT_SEARCH_TIMEOUT,
    retries=DEFAULT_SEARCH_RETRIES,
    deadline=DEFAULT_KEYWORD_DEADLINE,
):
    tenders = []
    safety_max_pages = 30 if target_count else max_pages
    matching_target_count = 0
    now = datetime.datetime.now()
    started = time.monotonic()
    query = normalize_search_keyword(keyword)
    if query != (keyword or "").strip():
        logger.info("Keyword '%s' normalised to GeM query '%s'", keyword, query)

    for page_num in range(1, safety_max_pages + 1):
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            logger.error(
                "Keyword '%s' hit the %ss wall-clock deadline on page %d; failing fast.",
                keyword, deadline, page_num,
            )
            break

        page_timeout = min(timeout, max(1, remaining))
        last_error = None
        res_json = None
        attempts = 1 + max(0, int(retries))
        for attempt in range(1, attempts + 1):
            try:
                res_json = _post_search_page(
                    query, cookie_header, csrf_token, page_num, sort_order,
                    timeout=page_timeout,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if _is_timeout(exc) and attempt < attempts:
                    logger.warning(
                        "Keyword '%s' page %d timed out (attempt %d/%d, %ss); retrying.",
                        keyword, page_num, attempt, attempts, page_timeout,
                    )
                    continue
                break

        if last_error is not None:
            if _is_timeout(last_error):
                logger.error(
                    "Keyword '%s' page %d timed out after %s attempt(s) "
                    "(%ss each). Failing fast: %s",
                    keyword, page_num, attempts, page_timeout, last_error,
                )
            else:
                logger.warning(
                    "API request failed for keyword '%s' page %d: %s",
                    keyword, page_num, last_error,
                )
            break

        docs = (
            (res_json or {})
            .get("response", {})
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
    return tenders

"""End-to-end scrape, single-bid and external-ingest pipelines."""

import datetime
import logging_setup
import nlp_classifier
import os
import paths
import random
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

from gemsentry.analysis import analyze_from_card, analyze_rfp_pdf
from gemsentry.config_store import load_keywords, load_scoring_config
from gemsentry.constants import logger
from gemsentry.dateparse import check_date_policy
from gemsentry.defaults import DEFAULT_SCORING_CONFIG
from gemsentry.profile import get_active_workspace, load_company_profile, workspace_paths
from gemsentry.scoring.dates import evaluate_date_window, resolve_min_days_left
from gemsentry.scoring.verdict import apply_verdict, finalize_auto_reject
from gemsentry.sources.attribution import build_host_index, derive_source, normalize_host
from gemsentry.sources.gem.client import (
    DEFAULT_DOWNLOAD_TIMEOUT, download_pdf_http, download_rfp_pdf,
    fetch_keyword_bids_api, parse_cards,
)
from gemsentry.sources.registry import SourceRegistry
from gemsentry.storage import (
    auto_export_summary, build_pdf_index, find_existing_pdf_file,
    load_existing_metadata, save_metadata,
)
from gemsentry.textutils import get_date_folder_name, sanitize_filename, today_iso


def _card_meta(tender):
    return {
        "title": tender.get("title"),
        "department": tender.get("department"),
        "quantity": tender.get("quantity"),
        "keyword": tender.get("keyword"),
        "est_value_inr": tender.get("est_value_inr"),
        "primary_item": tender.get("primary_item"),
        "item_category": tender.get("item_category"),
    }


def _analyze_one(job, scoring_cfg, company_profile):
    """Worker body: analyze a single PDF. Must stay picklable (module level)."""
    _tender, pdf_path, _date_info = job
    return analyze_rfp_pdf(
        pdf_path,
        start_date_str=_tender.get("start_date"),
        end_date_str=_tender.get("end_date"),
        scoring_config=scoring_cfg,
        company_profile=company_profile,
        card_meta=_card_meta(_tender),
    )


def _default_analysis_workers():
    """Leave a core free so the UI thread stays responsive during a scrape."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def analyze_downloaded_pdfs(jobs, scoring_cfg, company_profile, workers=0):
    """Analyze ``[(tender, pdf_path, date_info)]`` and apply each verdict.

    PDF text extraction is pure-Python and GIL-bound, so threads would not
    help; a process pool does. Any pool failure (spawn refused, a worker
    dying) falls back to analyzing the remaining PDFs in-process -- a slow
    scrape is always better than a failed one.
    """
    if not jobs:
        return

    workers = workers or _default_analysis_workers()
    results = {}

    if workers > 1 and len(jobs) > 1:
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_analyze_one, job, scoring_cfg, company_profile): index
                    for index, job in enumerate(jobs)
                }
                for done, future in enumerate(as_completed(futures), 1):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:
                        logger.warning("PDF analysis failed for %s: %s", jobs[index][1], exc)
                        results[index] = None
                    if done % 25 == 0:
                        logger.info("Analyzed %d/%d PDFs...", done, len(jobs))
        except Exception as exc:
            logger.warning("Parallel analysis unavailable (%s); falling back to sequential.", exc)

    for index, job in enumerate(jobs):
        tender, pdf_path, date_info = job
        if index in results:
            analysis = results[index]
        else:
            try:
                analysis = _analyze_one(job, scoring_cfg, company_profile)
            except Exception as exc:
                logger.warning("PDF analysis failed for %s: %s", pdf_path, exc)
                analysis = None

        if analysis:
            if analysis.get("auto_reject") or date_info.get("auto_reject"):
                finalize_auto_reject(analysis, date_info)
            # BE-25: status derives from the fit-gated recommendation;
            # manual pins (status_source == "manual") are preserved.
            apply_verdict(tender, analysis)
        else:
            apply_verdict(tender, analyze_from_card(tender, scoring_cfg, company_profile))

    logger.info("Analyzed %d PDF(s).", len(jobs))


def plan_downloads(tenders, scoring_cfg, company_profile, downloads_dir,
                   pdf_index=None, host_index=None, skip_zero_relevance=True):
    """Split ``tenders`` into what to fetch and what can be analyzed already.

    Returns ``(to_download, to_analyze)``, both lists of
    ``(tender, pdf_path, date_info)``. Tenders that will never get a PDF are
    scored from their listing metadata here and appear in neither list.

    Every skip is a deliberate saving: an expired bid, a bid with no
    business-line match, and -- since the multi-source refactor -- a bid from a
    portal this stage cannot read at all.
    """
    if pdf_index is None:
        pdf_index = build_pdf_index(downloads_dir)
    if host_index is None:
        # Portal attribution for the download gate. Built once: the same host
        # index serves every tender in the plan.
        host_index = build_host_index(SourceRegistry().sources)

    to_analyze = []   # (tender, abs_pdf_path, date_info)
    to_download = []  # (tender, save_path, date_info)
    skipped_date = skipped_fit = 0
    external_portals = Counter()

    for tender in tenders:
        bid_no = tender["bid_no"]

        # 1. Skip already successfully processed tenders
        if tender.get("downloaded") and tender.get("analysis") and tender.get("local_pdf_path"):
            lp = tender["local_pdf_path"]
            lp_abs = lp if os.path.isabs(lp) else os.path.join(paths.ROOT, lp)
            if os.path.exists(lp_abs):
                continue

        sanitized_bid = sanitize_filename(bid_no)
        date_info = evaluate_date_window(
            tender.get("start_date"), tender.get("end_date"), scoring_cfg
        )

        # 2. Reuse a PDF we already have on disk
        existing_rel = pdf_index.get(f"{sanitized_bid}.pdf")
        if existing_rel:
            tender["downloaded"] = True
            tender["local_pdf_path"] = existing_rel
            to_analyze.append(
                (tender, os.path.join(paths.ROOT, existing_rel), date_info)
            )
            continue

        # 3. Portal gate — this stage speaks GeM only. Tenders from the other
        # portals keep their listing-level score and stay visible on the
        # dashboard as card-only; their documents are laid out nothing like a
        # GeM RFP, so fetching them buys no signal, and an unreachable portal
        # would stall the whole run behind its connect timeouts.
        source_id, source_name = derive_source(tender, host_index)
        if source_id != "gem":
            card_analysis = analyze_from_card(tender, scoring_cfg, company_profile)
            card_analysis.setdefault("reasons", []).append(
                f"PDF not fetched: {source_name} documents are outside the "
                f"GeM RFP parser's format; scored from listing metadata only."
            )
            tender["downloaded"] = False
            apply_verdict(tender, card_analysis)
            external_portals[source_name] += 1
            continue

        # 4. Date window — never download what auto-rejects anyway
        if date_info.get("auto_reject"):
            reason = "expired" if date_info.get("is_expired") else "closing too soon"
            # debug: repeats for every stale bid on every run — the plan
            # summary line below reports the aggregate count.
            logger.debug(f"Skipping download for Bid {bid_no}: auto-reject ({reason}).")
            tender["downloaded"] = False
            apply_verdict(
                tender, analyze_from_card(tender, scoring_cfg, company_profile)
            )
            skipped_date += 1
            continue

        # 5. Fit-first policy — zero card relevance (no business-line keyword
        # match, or exclusion veto) means the bid Drops no matter what the PDF
        # says, so skip the download too. The dashboard's "Fetch PDF &
        # Re-analyze" button is the override.
        if skip_zero_relevance:
            card_analysis = analyze_from_card(tender, scoring_cfg, company_profile)
            if not card_analysis.get("business_line"):
                card_analysis.setdefault("reasons", []).append(
                    "Download skipped: no business-line keyword match in "
                    "card title (fit-first download policy)."
                )
                tender["downloaded"] = False
                apply_verdict(tender, card_analysis)
                skipped_fit += 1
                continue

        nlp_res = nlp_classifier.classify_tender(tender)
        tender["domain"] = nlp_res["domain"]
        tender["nlp_category"] = nlp_res["domain_label"]
        domain_folder = nlp_res["domain"]
        target_dir = os.path.join(
            downloads_dir, domain_folder, get_date_folder_name(), sanitized_bid
        )
        to_download.append(
            (tender, os.path.join(target_dir, f"{sanitized_bid}.pdf"), date_info)
        )

    skipped_external = sum(external_portals.values())
    logger.info(
        f"Download plan: {len(to_download)} to fetch, {len(to_analyze)} "
        f"reusable from disk, {skipped_date} skipped (date), "
        f"{skipped_fit} skipped (zero relevance), "
        f"{skipped_external} skipped (non-GeM portal)."
    )
    if skipped_external:
        logger.info(
            "Card-scored only (no PDF parsing for these portals): %s",
            ", ".join(
                f"{name} ({count})" for name, count in external_portals.most_common()
            ),
        )

    return to_download, to_analyze


def scrape(
    selected_keywords=None,
    max_pages=2,
    sort_order="Bid-Start-Date-Latest",
    log_callback=None,
    target_count=None,
    min_days_left=None,
    max_days_left=None,
):
    logging_setup.setup_logging()
    cb_handler = logging_setup.attach_callback(log_callback) if log_callback else None
    logging_setup.start_scrape_session()
    try:
        logger.info("Initializing directories...")
        paths.ensure_dirs()
        # Resolve the active preset's isolated workspace (e.g. 'personel').
        active_profile = load_company_profile()
        scoring_cfg = load_scoring_config()
        min_days_left = resolve_min_days_left(min_days_left, scoring_cfg)
        workspace = get_active_workspace(active_profile)
        tenders_dir, downloads_dir = workspace_paths(workspace)
        os.makedirs(downloads_dir, exist_ok=True)
        if workspace:
            logger.info(f"Active preset workspace: '{workspace}' → {paths.repo_relative(tenders_dir)}")

        # 1. Load dynamic keywords
        if selected_keywords:
            KEYWORDS = selected_keywords
            logger.info(f"Scraping {len(KEYWORDS)} selected keyword(s) for search.")
        else:
            KEYWORDS = load_keywords()

        # 2. Load existing metadata records (scoped to this workspace)
        all_tenders = load_existing_metadata(tenders_dir)
        
        new_tenders_count = 0
        
        with sync_playwright() as p:
            logger.info("Launching browser with stealth settings...")
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
            page.goto("https://bidplus.gem.gov.in/all-bids", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)

            # Harvest cookies & CSRF token for ultra-fast thread-safe urllib worker pool
            cookies = context.cookies()
            cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            csrf_token = next((c['value'] for c in cookies if c['name'] == 'csrf_gem_cookie'), None)
            
            if target_count and (min_days_left is not None or max_days_left is not None):
                logger.info(f"Target Goal Mode Active: Finding at least {target_count} tenders per keyword ending in [{min_days_left} to {max_days_left}] days...")
            else:
                logger.info("Starting high-performance concurrent keyword ingestion...")
            
            def process_keyword(kw):
                tenders = fetch_keyword_bids_api(
                    kw,
                    cookie_header,
                    csrf_token,
                    max_pages=max_pages,
                    sort_order=sort_order,
                    target_count=target_count,
                    min_days_left=min_days_left,
                    max_days_left=max_days_left,
                )
                return kw, tenders

            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_kw = {executor.submit(process_keyword, kw): kw for kw in KEYWORDS}
                for future in as_completed(future_to_kw):
                    kw = future_to_kw[future]
                    try:
                        kw, tenders = future.result()
                        logger.info(f"Keyword '{kw}': Discovered {len(tenders)} tenders")
                        for t in tenders:
                            date_ok, reasons = check_date_policy(t.get("start_date"), t.get("end_date"))
                            if not date_ok:
                                logger.warning(f"  [Date Policy Alert] {t['bid_no']}: {', '.join(reasons)}")

                            if t["bid_no"] not in all_tenders:
                                all_tenders[t["bid_no"]] = t
                                new_tenders_count += 1
                                logger.info(f"  [New Tender Discovered] {t['bid_no']}")
                            else:
                                existing = all_tenders[t["bid_no"]]
                                if kw not in existing["keyword"]:
                                    existing["keyword"] += f", {kw}"
                    except Exception as e:
                        logger.error(f"Error processing keyword '{kw}': {e}")

            if new_tenders_count == 0:
                logger.warning(f"\nFor today ({get_date_folder_name()}), no new tenders could be found.")

            # Download RFP documents
            logger.info(f"\n--- Checking RFP Downloads for {len(all_tenders)} total tenders ---")
            tenders_list = list(all_tenders.values())
            # Reuse the same config/profile snapshot used by discovery so the
            # remaining-time gate and analysis cannot disagree during a run.
            company_profile = load_company_profile()
            
            # --- BE-27 fast pipeline: plan → parallel fetch → analyze ---
            dl_policy = scoring_cfg.get("download_policy") or DEFAULT_SCORING_CONFIG["download_policy"]
            skip_zero_rel = bool(dl_policy.get("skip_zero_relevance_download", True))
            dl_workers = max(1, min(10, int(dl_policy.get("download_workers", 4) or 4)))
            dl_timeout = max(5, min(60, int(
                dl_policy.get("download_timeout") or DEFAULT_DOWNLOAD_TIMEOUT
            )))
            max_fallbacks = max(0, min(500, int(
                dl_policy.get("max_browser_fallbacks",
                              DEFAULT_SCORING_CONFIG["download_policy"]["max_browser_fallbacks"])
                or 0
            )))

            to_download, to_analyze = plan_downloads(
                tenders_list, scoring_cfg, company_profile, downloads_dir,
                skip_zero_relevance=skip_zero_rel,
            )

            # Parallel raw-HTTP downloads with per-request jitter. Only the
            # network fetch runs in workers; tender dicts are mutated on the
            # main thread as results complete.
            failed_http = []
            if to_download:
                def fetch_job(job):
                    job_tender = job[0]
                    time.sleep(random.uniform(0.3, 0.9))
                    return job, download_pdf_http(
                        job_tender.get("pdf_url"), job[1], cookie_header,
                        timeout=dl_timeout,
                    )

                total = len(to_download)
                ok_count = 0
                with ThreadPoolExecutor(max_workers=dl_workers) as pool:
                    futures = [pool.submit(fetch_job, j) for j in to_download]
                    for done, future in enumerate(as_completed(futures), 1):
                        job, ok = future.result()
                        tender, save_path, date_info = job
                        if ok:
                            ok_count += 1
                            tender["downloaded"] = True
                            tender["local_pdf_path"] = paths.repo_relative(save_path)
                            to_analyze.append((tender, save_path, date_info))
                            logger.debug("Downloaded (http): %s", tender["bid_no"])
                        else:
                            failed_http.append(job)
                        # A heartbeat that counts failures too: a stage whose
                        # every request fails used to print nothing at all for
                        # minutes and read as a hang.
                        if done % 10 == 0 or done == total:
                            logger.info(
                                "Downloads: %d/%d (%d ok, %d failed)",
                                done, total, ok_count, len(failed_http),
                            )

            if failed_http:
                # Which host is refusing us is the first thing worth knowing;
                # the per-URL errors stay at debug level in the log file.
                by_host = Counter(
                    normalize_host(job[0].get("pdf_url")) or "?" for job in failed_http
                )
                logger.warning(
                    "HTTP fetch failed for %d document(s): %s",
                    len(failed_http),
                    ", ".join(f"{host} ({count})" for host, count in by_host.most_common()),
                )

            # Browser fallback for HTTP failures (Playwright sync API is
            # single-threaded, so this stays sequential). Capped: at ~15s per
            # attempt an outage on the far end would otherwise stall the run
            # for as long as the failure list is.
            if len(failed_http) > max_fallbacks:
                logger.warning(
                    "%d HTTP fetch failure(s) exceed the browser-fallback cap of %d; "
                    "the remaining %d are card-scored this run.",
                    len(failed_http), max_fallbacks, len(failed_http) - max_fallbacks,
                )
            for index, (tender, save_path, date_info) in enumerate(failed_http):
                if index >= max_fallbacks:
                    tender["downloaded"] = False
                    apply_verdict(
                        tender, analyze_from_card(tender, scoring_cfg, company_profile)
                    )
                    continue
                logger.info(f"HTTP fetch failed; browser fallback for Bid {tender['bid_no']}...")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                if download_rfp_pdf(context, tender.get("pdf_url"), save_path):
                    tender["downloaded"] = True
                    tender["local_pdf_path"] = paths.repo_relative(save_path)
                    to_analyze.append((tender, save_path, date_info))
                else:
                    tender["downloaded"] = False
                    apply_verdict(
                        tender, analyze_from_card(tender, scoring_cfg, company_profile)
                    )
                time.sleep(random.uniform(1.0, 2.0))

            # Analyze every available PDF. Extraction is CPU-bound pure Python,
            # so this fans out over processes; tender dicts are still mutated
            # on the main thread as results arrive.
            analyze_downloaded_pdfs(
                to_analyze, scoring_cfg, company_profile,
                workers=int(dl_policy.get("analysis_workers", 0) or 0),
            )

            browser.close()

        save_metadata(tenders_list, tenders_dir)
        auto_export_summary(tenders_dir, downloads_dir)
        return tenders_list, new_tenders_count
    finally:
        logging_setup.end_scrape_session()
        logging_setup.detach_handler(cb_handler)


def scrape_single_bid(bid_id, log_callback=None):
    logging_setup.setup_logging()
    cb_handler = logging_setup.attach_callback(log_callback) if log_callback else None
    logging_setup.start_scrape_session()
    try:
        logger.info("Initializing directories for manual ID acquisition...")
        paths.ensure_dirs()
        # Route manual acquisitions into the active preset's workspace too.
        active_profile = load_company_profile()
        workspace = get_active_workspace(active_profile)
        tenders_dir, downloads_dir = workspace_paths(workspace)
        os.makedirs(downloads_dir, exist_ok=True)
        if workspace:
            logger.info(f"Active preset workspace: '{workspace}' → {paths.repo_relative(tenders_dir)}")

        bid_id_clean = bid_id.strip()
        logger.info(f"Targeting Bid ID / Number: '{bid_id_clean}'")

        all_tenders = load_existing_metadata(tenders_dir)
        
        target_tender = None
        
        with sync_playwright() as p:
            logger.info("Launching browser with stealth settings...")
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
            
            logger.info("Navigating to base search page...")
            page.goto("https://bidplus.gem.gov.in/all-bids", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            
            # Fill the search input and click search button
            logger.info(f"Typing search query '{bid_id_clean}' in search box...")
            page.fill("#searchBid", bid_id_clean)
            page.wait_for_timeout(500)
            
            logger.info("Clicking search button...")
            page.click("#searchBidRA")
            page.wait_for_timeout(3000) # Wait for AJAX refresh
            
            # Wait for card
            try:
                page.wait_for_selector("div.card, #bidCard", timeout=12000)
                tenders = parse_cards(page.content(), "MANUAL_REQUEST")
                
                # Try to find matching card (partial or exact)
                for t in tenders:
                    if bid_id_clean.lower() in t["bid_no"].lower() or t["bid_no"].lower() in bid_id_clean.lower():
                        target_tender = t
                        break
                
                # Do not default to a random card if no match is found
                pass
                    
            except Exception as e:
                logger.error(f"Failed to find or parse bid cards for ID '{bid_id_clean}': {e}")
                
            if not target_tender:
                logger.warning(f"No tender found on GeM matching ID: '{bid_id_clean}'")
                browser.close()
                return None
                
            bid_no = target_tender["bid_no"]
            pdf_url = target_tender["pdf_url"]
            logger.info(f"Tender found: {bid_no} - {target_tender['title']}")
            
            # Since this is a manual request, we BYPASS the Date Policy Gate check
            logger.info("Manual acquisition request: Bypassing Date Policy Gate check.")
            
            sanitized_bid = sanitize_filename(bid_no)
            nlp_res = nlp_classifier.classify_tender(target_tender)
            target_tender["domain"] = nlp_res["domain"]
            target_tender["nlp_category"] = nlp_res["domain_label"]
            domain_folder = nlp_res["domain"]
            date_folder = get_date_folder_name()
            
            target_dir = os.path.join(downloads_dir, domain_folder, date_folder, sanitized_bid)
            save_path = os.path.join(target_dir, f"{sanitized_bid}.pdf")

            existing_path = find_existing_pdf_file(sanitized_bid, downloads_dir)
            pdf_location = None
            
            if existing_path:
                logger.info(f"RFP PDF already exists in local downloads cache: {existing_path}")
                target_tender["downloaded"] = True
                target_tender["local_pdf_path"] = existing_path
                pdf_location = existing_path
            else:
                os.makedirs(target_dir, exist_ok=True)
                logger.info(f"Downloading RFP PDF from: {pdf_url}...")
                # HTTP-first with harvested cookies; browser fallback (BE-27)
                cookie_header = "; ".join(
                    f"{c['name']}={c['value']}" for c in context.cookies()
                )
                success = download_pdf_http(pdf_url, save_path, cookie_header)
                if not success:
                    success = download_rfp_pdf(context, pdf_url, save_path)
                if success:
                    target_tender["downloaded"] = True
                    target_tender["local_pdf_path"] = paths.repo_relative(save_path)
                    pdf_location = save_path
                else:
                    target_tender["downloaded"] = False
                    logger.error("Download failed for RFP PDF.")
                    
            # Scan and analyze RFP PDF (manual path: still scores date_window from dates)
            scoring_cfg = load_scoring_config()
            company_profile = load_company_profile()
            if target_tender["downloaded"] and pdf_location and os.path.exists(pdf_location):
                logger.info("Scanning and scoring RFP PDF contents...")
                analysis = analyze_rfp_pdf(
                    pdf_location,
                    start_date_str=target_tender.get("start_date"),
                    end_date_str=target_tender.get("end_date"),
                    scoring_config=scoring_cfg,
                    company_profile=company_profile,
                    card_meta=_card_meta(target_tender),
                )
                if analysis:
                    if analysis.get("auto_reject"):
                        finalize_auto_reject(analysis)
                    apply_verdict(target_tender, analysis)
            else:
                apply_verdict(
                    target_tender,
                    analyze_from_card(target_tender, scoring_cfg, company_profile),
                )

            # If the user previously searched for it, update the keyword or preserve it
            if bid_no in all_tenders:
                existing = all_tenders[bid_no]
                kw_list = [k.strip() for k in existing["keyword"].split(",")]
                if "MANUAL_REQUEST" not in kw_list:
                    kw_list.append("MANUAL_REQUEST")
                target_tender["keyword"] = ", ".join(kw_list)
                # Preserve a manually pinned status across re-acquisition
                if existing.get("status_source") == "manual":
                    target_tender["status_source"] = "manual"
                    target_tender["status"] = existing.get("status", target_tender.get("status"))
            else:
                target_tender["keyword"] = "MANUAL_REQUEST"
                
            # Save or update in database
            all_tenders[bid_no] = target_tender
            save_metadata(list(all_tenders.values()), tenders_dir)
            auto_export_summary(tenders_dir, downloads_dir)
            logger.info(f"Successfully processed and updated metadata for Bid: {bid_no}")
            
            browser.close()
            return target_tender

    finally:
        logging_setup.end_scrape_session()
        logging_setup.detach_handler(cb_handler)

def external_tender_to_record(item, now_str=None):
    """Map one adapter-normalized tender onto GeMSentry's metadata schema."""
    now_str = now_str or datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    return {
        "bid_no": item.get("tender_id"),
        "title": item.get("title") or "N/A",
        "quantity": "N/A",
        "department": item.get("buyer_org") or "N/A",
        "start_date": item.get("published_date") or now_str,
        "end_date": item.get("closing_date") or "N/A",
        "est_value_inr": item.get("est_value") or None,
        "pdf_url": item.get("pdf_url") or item.get("url") or "",
        "source_id": item.get("source_id", "external"),
        "source_name": item.get("source_name", "External Portal"),
        "keyword": "multi-source",
        "downloaded": False,
        "local_pdf_path": "",
        "first_seen": today_iso(),
        "status": "Pending Review",
        "status_source": "auto",
        "analysis": None,
    }


def ingest_external_tenders(external_tenders, tenders_dir=None, downloads_dir=None):
    """Score and persist tenders collected from the non-GeM portal adapters.

    Returns the number of newly ingested tenders. Passing ``tenders_dir`` /
    ``downloads_dir`` overrides the active preset workspace.
    """
    if not external_tenders:
        return 0

    company_profile = load_company_profile()
    if tenders_dir is None or downloads_dir is None:
        workspace = get_active_workspace(company_profile)
        tenders_dir, downloads_dir = workspace_paths(workspace)

    # Keyed by bid_no, so the duplicate check is a dict lookup rather than a
    # scan of every record already on disk for every incoming tender.
    records = load_existing_metadata(tenders_dir)
    scoring_cfg = load_scoring_config()

    new_count = 0
    now_str = datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    for item in external_tenders:
        bid_no = item.get("tender_id")
        if not bid_no or bid_no in records:
            continue

        record = external_tender_to_record(item, now_str)
        date_info = evaluate_date_window(
            record.get("start_date"), record.get("end_date"), scoring_cfg
        )
        if date_info.get("auto_reject"):
            logger.debug(
                "Skipping external tender %s: %s",
                bid_no, date_info.get("detail") or "outside actionable date window",
            )
            continue
        analysis = analyze_from_card(record, scoring_cfg, company_profile)
        record["analysis"] = analysis
        record["score"] = analysis.get("score")

        records[bid_no] = record
        new_count += 1

    if new_count:
        save_metadata(list(records.values()), tenders_dir)
        auto_export_summary(tenders_dir, downloads_dir)
        logger.info("Ingested %d new external tender(s) from multi-source portals.", new_count)

    return new_count

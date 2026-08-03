"""
Reconcile downloaded RFP PDFs with workspace metadata — fully offline.

Finds PDFs under a workspace's downloads/ that have no metadata record
(e.g. records lost across resets), rebuilds each record from the PDF itself
(bid number from the filename, dates/buyer from the document header), runs
the full scoring pipeline, and merges into metadata. Existing records are
never overwritten; records missing their PDF link are re-attached.

Usage:
  python tools/reconcile.py                    # main workspace
  python tools/reconcile.py --workspace personel
  python tools/reconcile.py --dry-run          # report only, save nothing
"""
import argparse
import datetime
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypdf import PdfReader  # noqa: E402

import paths  # noqa: E402
import scraper  # noqa: E402

SKIP_DIRS = {"complete_summary", "backups"}
BID_FILE_RE = re.compile(r"^(GEM)_(\d{4})_([A-Z]+)_(\d+)$")


def bid_no_from_filename(stem):
    """GEM_2026_B_7577626 -> GEM/2026/B/7577626 (None if not a bid file)."""
    m = BID_FILE_RE.match(stem)
    if not m:
        return None
    return "/".join(m.groups())


def parse_pdf_header(pdf_path):
    """Extract end/start dates from the GeM bid header (page 1 only — cheap)."""
    try:
        reader = PdfReader(pdf_path)
        text = reader.pages[0].extract_text() or ""
    except Exception:
        return None, None
    clean = re.sub(r"\s+", " ", text)
    end_m = re.search(
        r"Bid End Date/?Time.{0,20}?(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})", clean
    )
    # 'Dated' on the doc is the publish date — closest available start date.
    start_m = re.search(r"Dated.{0,20}?(\d{2}-\d{2}-\d{4})", clean)
    return (start_m.group(1) if start_m else None,
            end_m.group(1) if end_m else None)


def find_orphan_pdfs(downloads_dir, known_bids):
    """Yield (bid_no, keyword, abs_pdf_path) for PDFs missing from metadata."""
    seen = set()
    for root, dirs, files in os.walk(downloads_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            stem = os.path.splitext(fname)[0]
            bid_no = bid_no_from_filename(stem)
            if not bid_no or bid_no in seen:
                continue
            seen.add(bid_no)
            rel = os.path.relpath(root, downloads_dir)
            keyword = rel.split(os.sep)[0] if rel != "." else "reconciled"
            yield bid_no, keyword.replace("_", " "), os.path.join(root, fname)


def build_record(bid_no, keyword, abs_pdf, cfg, profile):
    """Create a full tender record from a PDF alone (offline)."""
    start_date, end_date = parse_pdf_header(abs_pdf)
    tender = {
        "bid_no": bid_no,
        "title": None,
        "quantity": "",
        "department": "",
        "start_date": start_date,
        "end_date": end_date,
        "keyword": keyword,
        "downloaded": True,
        "local_pdf_path": paths.repo_relative(abs_pdf),
        "pdf_url": "",
        "status": "Pending Review",
    }
    analysis = scraper.analyze_rfp_pdf(
        abs_pdf,
        start_date_str=start_date,
        end_date_str=end_date,
        scoring_config=cfg,
        company_profile=profile,
        card_meta={"title": "", "keyword": keyword},
    )
    if analysis is None or analysis.get("analysis_status") != "ok":
        analysis = analysis or scraper.get_failed_analysis("PDF unreadable during reconcile.")
    elif analysis.get("auto_reject"):
        scraper.finalize_auto_reject(analysis)

    # Backfill card fields from parsed signals so the dashboard/Excel read well
    title = analysis.get("primary_item") or analysis.get("item_category")
    tender["title"] = title or f"(reconciled) {bid_no}"
    dept_bits = [b for b in (analysis.get("buyer_dept"), analysis.get("buyer_org")) if b]
    tender["department"] = " | ".join(dept_bits)
    scraper.apply_verdict(tender, analysis)
    return tender


def main():
    parser = argparse.ArgumentParser(description="Reconcile downloaded PDFs into metadata")
    parser.add_argument("--workspace", default="main",
                        help="'main' or a subfolder of tenders/ (default: main)")
    parser.add_argument("--dry-run", action="store_true", help="Report only; save nothing")
    args = parser.parse_args()

    if args.workspace == "main":
        tenders_dir, downloads_dir = paths.TENDERS_DIR, paths.DOWNLOADS_DIR
    else:
        tenders_dir = os.path.join(paths.TENDERS_DIR, args.workspace)
        downloads_dir = os.path.join(tenders_dir, "downloads")
        if not os.path.isdir(downloads_dir):
            sys.exit(f"ERROR: downloads dir not found: {downloads_dir}")

    cfg = scraper.load_scoring_config()
    # Score with the value-band preset that belongs to THIS workspace,
    # not whichever preset happens to be globally active.
    profile = scraper.profile_for_workspace(args.workspace)
    tenders = scraper.load_existing_metadata(tenders_dir)
    print(f"Workspace '{args.workspace}': {len(tenders)} existing records")

    orphans = list(find_orphan_pdfs(downloads_dir, set(tenders)))
    new_orphans = [(b, k, p) for b, k, p in orphans if b not in tenders]
    relink = [(b, k, p) for b, k, p in orphans
              if b in tenders and not tenders[b].get("local_pdf_path")]
    print(f"Found {len(new_orphans)} orphan PDFs to import, {len(relink)} records to re-link")

    if args.dry_run:
        for b, k, p in new_orphans[:20]:
            print(f"  would import {b} ({k})")
        print("(dry run — nothing saved)")
        return

    # Backup metadata before merging
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(tenders_dir, "backups", stamp)
    for name in ("metadata.json", "metadata.csv", "metadata.js"):
        src = os.path.join(tenders_dir, name)
        if os.path.exists(src):
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(backup_dir, name))
    if os.path.isdir(backup_dir):
        print(f"Backup: {paths.repo_relative(backup_dir)}")

    imported = failed = 0
    for i, (bid_no, keyword, abs_pdf) in enumerate(new_orphans, 1):
        try:
            tenders[bid_no] = build_record(bid_no, keyword, abs_pdf, cfg, profile)
            imported += 1
        except Exception as e:
            failed += 1
            print(f"  FAILED {bid_no}: {e}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(new_orphans)}")

    for bid_no, keyword, abs_pdf in relink:
        tenders[bid_no]["downloaded"] = True
        tenders[bid_no]["local_pdf_path"] = paths.repo_relative(abs_pdf)
        scraper.rescore_tender(tenders[bid_no], cfg, profile, reparse=True)

    scraper.save_metadata(list(tenders.values()), tenders_dir)

    counts = {}
    for t in tenders.values():
        counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
    print(f"\nImported {imported} (failed: {failed}), re-linked {len(relink)}")
    print(f"Workspace now has {len(tenders)} tenders. Status: {counts}")


if __name__ == "__main__":
    main()

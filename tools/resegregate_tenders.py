#!/usr/bin/env python
"""
GeMSentry Tender Resegregation Tool (NLP-based).

Re-classifies stored tenders into canonical semantic domain folders using NLP,
moves PDF files to their proper category directories, updates metadata.json,
and cleans up empty legacy search-keyword folders.
"""
import sys
import os
import shutil
import argparse
import json
import logging

# Configure stdout encoding for Windows console unicode support
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import paths
import scraper
import nlp_classifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemsentry.resegregate")


def clean_empty_directories(root_dir: str):
    """Recursively remove empty subdirectories under root_dir."""
    if not os.path.exists(root_dir):
        return
    for current_dir, dirs, files in os.walk(root_dir, topdown=False):
        if current_dir == root_dir:
            continue
        try:
            if not os.listdir(current_dir):
                os.rmdir(current_dir)
                logger.debug(f"Removed empty directory: {current_dir}")
        except Exception as e:
            logger.debug(f"Error removing {current_dir}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Re-segregate stored tenders into NLP category folders.")
    parser.add_argument("--dry-run", action="store_true", help="Preview proposed folder moves without modifying files")
    parser.add_argument("--workspace", type=str, default="main", help="Workspace ('main' or subfolder of tenders/)")
    parser.add_argument("--verbose", action="store_true", help="Print verbose detailed logs per tender")

    args = parser.parse_args()

    tenders_dir, downloads_dir = scraper.workspace_paths("" if args.workspace == "main" else args.workspace)
    metadata_path = os.path.join(tenders_dir, "metadata.json")

    if not os.path.exists(metadata_path):
        logger.error(f"Metadata file not found: {metadata_path}")
        sys.exit(1)

    tenders_dict = scraper.load_existing_metadata(tenders_dir)
    all_tenders = list(tenders_dict.values())
    total_tenders = len(all_tenders)

    logger.info(f"=" * 80)
    logger.info(f"  GeMSentry NLP Tender Resegregation Tool")
    logger.info(f"  Workspace:    '{args.workspace}' -> {tenders_dir}")
    logger.info(f"  Total Tenders: {total_tenders}")
    logger.info(f"  Mode:          {'[DRY RUN - PREVIEW ONLY]' if args.dry_run else '[LIVE MIGRATION]'}")
    logger.info(f"=" * 80)

    domain_counts = {}
    moved_count = 0
    updated_meta_count = 0

    for idx, tender in enumerate(all_tenders, 1):
        bid_no = tender.get("bid_no", f"UNKNOWN_{idx}")
        sanitized_bid = scraper.sanitize_filename(bid_no)
        rel_pdf = tender.get("local_pdf_path", "")
        abs_pdf = os.path.join(paths.ROOT, rel_pdf) if rel_pdf else None

        if abs_pdf and not os.path.exists(abs_pdf):
            # Check if PDF exists in downloads dir under bid_no
            found_path = scraper.find_existing_pdf_file(sanitized_bid, downloads_dir)
            if found_path:
                abs_pdf = os.path.join(paths.ROOT, found_path)
                rel_pdf = found_path

        # Classify tender using NLP engine
        classification = nlp_classifier.classify_tender(tender, pdf_path=abs_pdf if abs_pdf and os.path.exists(abs_pdf) else None)
        domain = classification["domain"]
        domain_label = classification["domain_label"]

        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        tender["domain"] = domain
        tender["nlp_category"] = domain_label

        if not abs_pdf or not os.path.exists(abs_pdf):
            if args.verbose:
                logger.info(f"[{idx}/{total_tenders}] Bid {bid_no} -> Domain: {domain_label} (No local PDF file)")
            continue

        # Extract current date folder or fallback to current date folder name
        pdf_dir = os.path.dirname(abs_pdf)
        dir_parts = pdf_dir.replace("\\", "/").split("/")
        date_folder = dir_parts[-2] if len(dir_parts) >= 2 else scraper.get_date_folder_name()
        if not date_folder or date_folder == downloads_dir.replace("\\", "/").split("/")[-1]:
            date_folder = scraper.get_date_folder_name()

        # Compute target path under canonical domain folder
        target_dir = os.path.join(downloads_dir, domain, date_folder, sanitized_bid)
        target_pdf_path = os.path.join(target_dir, f"{sanitized_bid}.pdf")

        current_dir = os.path.dirname(abs_pdf)
        target_dir_normalized = os.path.abspath(target_dir)
        current_dir_normalized = os.path.abspath(current_dir)

        if current_dir_normalized != target_dir_normalized:
            moved_count += 1
            if args.dry_run:
                if args.verbose or moved_count <= 15:
                    logger.info(f"[MOVE PREVIEW] {bid_no}: {paths.repo_relative(abs_pdf)} -> {paths.repo_relative(target_pdf_path)}")
            else:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    # Move all files in current directory to target directory
                    for fname in os.listdir(current_dir):
                        src_f = os.path.join(current_dir, fname)
                        dst_f = os.path.join(target_dir, fname)
                        if os.path.isfile(src_f):
                            shutil.move(src_f, dst_f)
                    
                    # Remove old empty directory
                    if os.path.exists(current_dir) and not os.listdir(current_dir):
                        os.rmdir(current_dir)

                    new_rel = paths.repo_relative(target_pdf_path)
                    tender["local_pdf_path"] = new_rel
                    updated_meta_count += 1
                    if args.verbose or moved_count <= 10:
                        logger.info(f"[MOVED] {bid_no} -> {new_rel}")
                except Exception as e:
                    logger.error(f"Failed to move PDF for Bid {bid_no}: {e}")
        else:
            tender["local_pdf_path"] = paths.repo_relative(abs_pdf)

    if not args.dry_run:
        # Save updated metadata
        scraper.save_metadata(all_tenders, tenders_dir)
        # Clean up empty legacy folders
        clean_empty_directories(downloads_dir)
        logger.info(f"Updated metadata.json saved with {updated_meta_count} re-routed PDF paths.")

    logger.info(f"\n==================================================================================")
    logger.info(f"  NLP Segregation Summary Report")
    logger.info(f"==================================================================================")
    for dom_key, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True):
        label = nlp_classifier.CANONICAL_DOMAINS.get(dom_key, {}).get("label", dom_key)
        pct = (count / total_tenders) * 100 if total_tenders else 0
        logger.info(f"  📌 {label:<35} : {count:>4} tenders ({pct:.1f}%)")

    logger.info(f"----------------------------------------------------------------------------------")
    logger.info(f"  Total Files Needing Folder Re-allocation: {moved_count}")
    if args.dry_run:
        logger.info(f"  [DRY RUN] Run without --dry-run to apply file moves and update metadata.json.")
    else:
        logger.info(f"  [COMPLETED] Resegregation successfully applied to local storage.")
    logger.info(f"==================================================================================\n")


if __name__ == "__main__":
    main()

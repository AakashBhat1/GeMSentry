"""
Backfill the `first_seen` discovery date on existing tender records.

New tenders get `first_seen` stamped at scrape time. Records created before
that field existed are reconstructed from the scrape logs, which record every
discovery as:

    2026-07-21 16:04:40 [INFO]   [New Tender Discovered] GEM/2026/B/7724730

The earliest such line for a bid is its discovery date. Where a bid predates
the logs, the dated download folder (tenders/downloads/<cat>/<21 jul26>/) is
used as a fallback.

Usage:
  python tools/backfill_first_seen.py --dry-run     # report only
  python tools/backfill_first_seen.py               # active preset workspace
  python tools/backfill_first_seen.py --all         # every workspace
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths  # noqa: E402
import scraper  # noqa: E402

DISCOVERY_RX = re.compile(
    r'^(\d{4}-\d{2}-\d{2})[ T][\d:]+.*?\[New Tender Discovered\]\s*(\S+)',
    re.MULTILINE,
)
# tenders/downloads/<category>/<21 jul26>/<BID>/<BID>.pdf
FOLDER_RX = re.compile(r'[\\/](\d{1,2} [A-Za-z]{3}\d{2})[\\/]')
METADATA_FILES = ("metadata.json", "metadata.csv", "metadata.js")


def discovery_index(log_globs):
    """Map bid_no -> earliest discovery date (YYYY-MM-DD) from scrape logs."""
    seen = {}
    files = []
    for pattern in log_globs:
        files.extend(sorted(glob.glob(pattern)))
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for date_str, bid in DISCOVERY_RX.findall(text):
            bid = bid.strip()
            if bid and (bid not in seen or date_str < seen[bid]):
                seen[bid] = date_str
    return seen


def date_from_folder(local_pdf_path):
    """Fallback: derive the date from a dated download folder ('21 jul26')."""
    if not local_pdf_path:
        return None
    m = FOLDER_RX.search(str(local_pdf_path))
    if not m:
        return None
    try:
        dt = datetime.datetime.strptime(m.group(1).title(), "%d %b%y")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


def resolve_workspaces(args):
    if args.all:
        found = [("main", paths.TENDERS_DIR)]
        for name in sorted(os.listdir(paths.TENDERS_DIR)):
            sub = os.path.join(paths.TENDERS_DIR, name)
            if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "metadata.json")):
                found.append((name, sub))
        return found
    if args.workspace:
        if args.workspace == "main":
            return [("main", paths.TENDERS_DIR)]
        sub = os.path.join(paths.TENDERS_DIR, args.workspace)
        if not os.path.isdir(sub):
            sys.exit(f"ERROR: workspace not found: {sub}")
        return [(args.workspace, sub)]
    return [("active", paths.TENDERS_DIR)]


def backup(tenders_dir):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(tenders_dir, "backups", stamp)
    os.makedirs(dest, exist_ok=True)
    for name in METADATA_FILES:
        src = os.path.join(tenders_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="recompute even where first_seen is already set")
    args = ap.parse_args()

    index = discovery_index([
        os.path.join(paths.LOGS_DIR, "scrapes", "*.log"),
        os.path.join(paths.LOGS_DIR, "gemsentry.log*"),
    ])
    print(f"Discovery index: {len(index)} bids from scrape logs")

    for label, tenders_dir in resolve_workspaces(args):
        meta_path = os.path.join(tenders_dir, "metadata.json")
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as fh:
            data = json.load(fh)
        tenders = data if isinstance(data, list) else data.get("tenders", data)

        stats = collections.Counter()
        for t in tenders:
            if t.get("first_seen") and not args.force:
                stats["already set"] += 1
                continue
            date = index.get(t.get("bid_no"))
            if date:
                stats["from logs"] += 1
            else:
                date = date_from_folder(t.get("local_pdf_path"))
                if date:
                    stats["from folder"] += 1
            if date:
                t["first_seen"] = date
            else:
                stats["unresolved"] += 1

        resolved = sum(1 for t in tenders if t.get("first_seen"))
        print(f"\n=== {label} ({tenders_dir}) ===")
        print(f"  Tenders: {len(tenders)}  |  with first_seen: {resolved} "
              f"({100.0 * resolved / max(1, len(tenders)):.1f}%)")
        for k, v in stats.most_common():
            print(f"    {v:5d}  {k}")
        by_date = collections.Counter(
            t.get("first_seen") or "unknown" for t in tenders)
        for d, n in sorted(by_date.items()):
            print(f"    {n:5d}  {d}")

        if args.dry_run:
            print("  (dry run — nothing saved)")
            continue
        dest = backup(tenders_dir)
        print(f"  Backup: {dest}")
        scraper.save_metadata(tenders, tenders_dir=tenders_dir)
        print("  Saved.")


if __name__ == "__main__":
    main()

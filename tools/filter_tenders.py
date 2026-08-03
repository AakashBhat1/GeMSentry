#!/usr/bin/env python
"""
GeMSentry Tender Deadline Filter Tool
Filter tenders in local storage by remaining days until submission deadline.
"""
import sys
import os
import argparse
import datetime

# Configure stdout encoding for Windows console unicode support
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import scraper
import paths

def main():
    parser = argparse.ArgumentParser(description="Filter tenders by deadline remaining days.")
    parser.add_argument("--min-days", type=int, default=15, help="Minimum remaining days from today (default: 15)")
    parser.add_argument("--max-days", type=int, default=20, help="Maximum remaining days from today (default: 20)")
    parser.add_argument("--keyword", type=str, default=None, help="Optional keyword filter")
    parser.add_argument("--json", action="store_true", help="Output raw JSON array of matching tenders")

    args = parser.parse_args()

    tenders_dict = scraper.load_existing_metadata()
    all_tenders = list(tenders_dict.values())
    now = datetime.datetime.now()

    matching = []
    for t in all_tenders:
        if args.keyword and args.keyword.lower() not in t.get("keyword", "").lower():
            continue

        end_dt = scraper.parse_gem_date(t.get("end_date"))
        if end_dt:
            rem_days = (end_dt - now).days
            if args.min_days <= rem_days <= args.max_days:
                matching.append((t, rem_days))

    if args.json:
        import json
        print(json.dumps([t for t, _ in matching], indent=2))
        return

    print(f"\n==================================================================================")
    print(f"  GeMSentry: Tenders Ending in {args.min_days} to {args.max_days} Days from Today")
    print(f"  Total Found: {len(matching)} out of {len(all_tenders)} stored tenders")
    print(f"==================================================================================\n")

    if not matching:
        print("  No tenders found matching the specified date window.\n")
        return

    for t, rem in sorted(matching, key=lambda x: x[1]):
        bid_no = t.get("bid_no", "N/A")
        title = t.get("title", "N/A")
        dept = t.get("department", "N/A")
        end_date = t.get("end_date", "N/A")
        status = t.get("status", "Pending Review")
        
        print(f"📌 Bid ID:      {bid_no}")
        print(f"   Title:       {title}")
        print(f"   Department:  {dept}")
        print(f"   End Date:    {end_date}  (⏳ {rem} days remaining)")
        print(f"   Status:      {status}")
        print("-" * 80)

if __name__ == "__main__":
    main()

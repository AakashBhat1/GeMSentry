"""Command-line entry point for scraping and deadline filtering.

Usage:  python -m gemsentry.cli --keywords drone "power supply" --pages 3
"""
import argparse
import datetime

from gemsentry.constants import logger
from gemsentry.dateparse import parse_gem_date
from gemsentry.pipeline import scrape
from gemsentry.storage import load_existing_metadata

SORT_CHOICES = [
    "Bid-End-Date-Latest",
    "Bid-End-Date-Oldest",
    "Bid-Start-Date-Latest",
    "Bid-Start-Date-Oldest",
]


def build_parser():
    parser = argparse.ArgumentParser(description="GeM RFP Acquisition CLI Scraper")
    parser.add_argument("--keywords", nargs="+", help="Keywords list to search")
    parser.add_argument("--pages", type=int, default=2, help="Max pages limit per keyword")
    parser.add_argument("--sort", default="Bid-Start-Date-Latest", choices=SORT_CHOICES,
                        help="Sort order option")
    parser.add_argument("--min-days-left", type=int, default=None,
                        help="Filter tenders ending after at least N days from today")
    parser.add_argument("--max-days-left", type=int, default=None,
                        help="Filter tenders ending within at most M days from today")
    parser.add_argument("--target-per-keyword", type=int, default=None,
                        help="Target goal: minimum tenders ending in date window per keyword")
    parser.add_argument("--filter-only", action="store_true",
                        help="Only filter existing database tenders without running a new scrape")
    return parser


def filter_by_deadline(tenders, min_days, max_days):
    """Return [(tender, days_left)] for tenders closing inside the day window."""
    now = datetime.datetime.now()
    out = []
    for t in tenders:
        end_dt = parse_gem_date(t.get("end_date"))
        if not end_dt:
            continue
        days_left = (end_dt - now).days
        if min_days <= days_left <= max_days:
            out.append((t, days_left))
    return out


def report_deadlines(matches, min_days, max_days):
    logger.info("=" * 55)
    logger.info(" Tenders Closing in [%s to %s] Days (%d Found)", min_days, max_days, len(matches))
    logger.info("=" * 55)
    for tender, days_left in matches:
        title = (tender.get("title") or "")[:60]
        logger.info("- [%s] %s...", tender.get("bid_no"), title)
        logger.info("  Department: %s", tender.get("department"))
        logger.info("  End Date:   %s (%s days left)", tender.get("end_date"), days_left)
        logger.info("  Status:     %s", tender.get("status", "Pending Review"))


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.filter_only:
        all_tenders = list(load_existing_metadata().values())
        logger.info("--- Filtering Existing Tenders (%d Total) ---", len(all_tenders))
    else:
        all_tenders, _ = scrape(
            selected_keywords=args.keywords,
            max_pages=args.pages,
            sort_order=args.sort,
            target_count=args.target_per_keyword,
            min_days_left=args.min_days_left,
            max_days_left=args.max_days_left,
        )

    if args.min_days_left is None and args.max_days_left is None:
        return

    min_days = args.min_days_left if args.min_days_left is not None else 0
    max_days = args.max_days_left if args.max_days_left is not None else 9999
    report_deadlines(filter_by_deadline(all_tenders, min_days, max_days), min_days, max_days)


if __name__ == "__main__":
    main()

"""Repair stored bid values from their local RFPs; dry-run unless --apply.

Only records with a differing, explicitly stated PDF value are re-analyzed.
The original complete records are backed up before any database update.
"""

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths
from gemsentry.analysis import rescore_tender
from gemsentry.atomicio import atomic_write_text
from gemsentry.config_store import load_scoring_config
from gemsentry.parsing.signals import parse_estimated_bid_value
from gemsentry.pdf_text import extract_text
from gemsentry.profile import load_company_profile, workspace_paths
from gemsentry.storage import flush_exports, load_existing_metadata, update_record


def corrected_record(tender, cfg, profile):
    """Return a re-analyzed copy only when source evidence corrects a value."""
    pdf = tender.get("local_pdf_path")
    if not pdf:
        return None
    pdf = Path(pdf)
    if not pdf.is_absolute():
        pdf = Path(paths.ROOT) / pdf
    if not pdf.is_file():
        return None
    value = parse_estimated_bid_value(extract_text(str(pdf)))
    if value is None:
        return None
    analysis = tender.get("analysis") or {}
    if analysis.get("est_value_inr") == value and tender.get("est_value_inr") in (None, value):
        return None
    updated = copy.deepcopy(tender)
    updated["est_value_inr"] = value
    rescore_tender(updated, cfg, profile, reparse=True)
    if (updated.get("analysis") or {}).get("analysis_status") != "ok":
        raise ValueError(f"Re-analysis failed for {tender['bid_no']}")
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = workspace_paths()[0]
    cfg, profile = load_scoring_config(), load_company_profile()
    records = load_existing_metadata(directory)
    updates, originals = [], []
    for tender in records.values():
        try:
            updated = corrected_record(tender, cfg, profile)
        except Exception as exc:
            print(f"SKIPPED {tender['bid_no']}: {exc}")
            continue
        if updated is not None:
            originals.append(tender)
            updates.append(updated)
            old = (tender.get("analysis") or {}).get("est_value_inr")
            print(f"{tender['bid_no']}: {old} -> {updated['est_value_inr']}", flush=True)
    print(f"{len(updates)} corrections from {len(records)} records.", flush=True)
    if args.apply and updates:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = Path(directory) / "backups" / f"bid_values_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(backup), json.dumps(originals, indent=2, ensure_ascii=False))
        for updated in updates:
            # Only changed analysis/value fields are patched. User curation
            # and unrelated metadata are not copied back from the scan snapshot.
            update_record(updated["bid_no"], {
                "est_value_inr": updated["est_value_inr"],
                "analysis": updated["analysis"],
                "status": updated["status"],
            }, directory)
        flush_exports()
        print(f"Applied; original records backed up to {backup}")
    elif updates:
        print("Dry run. Use --apply to persist these corrections.")


if __name__ == "__main__":
    main()

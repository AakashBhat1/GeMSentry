"""
Re-score all tenders in one or more workspaces from LOCAL data — no network.

Fast mode (default) re-derives Fit / Risk re-weighting / date window /
recommendation / priority / status from signals already stored in metadata
(~milliseconds per tender). --reparse re-analyzes each local PDF instead
(exact; also picks up parser improvements).

Manual status pins (status_source == "manual") are never overwritten.
A timestamped backup of metadata.{json,csv,js} is written before saving.

Usage:
  python tools/rescore.py                    # active preset's workspace, fast
  python tools/rescore.py --workspace main   # tenders/  (main workspace)
  python tools/rescore.py --workspace personel
  python tools/rescore.py --all              # every workspace found
  python tools/rescore.py --reparse          # full PDF re-parse
  python tools/rescore.py --dry-run          # report changes, save nothing
"""
import argparse
import datetime
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths  # noqa: E402
import scraper  # noqa: E402

METADATA_FILES = ("metadata.json", "metadata.csv", "metadata.js")


def resolve_workspaces(args):
    """Return list of (label, tenders_dir) to process."""
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
    # Default: active preset's workspace
    ws = scraper.get_active_workspace()
    label = ws or "main"
    return [(label, scraper.workspace_paths(ws)[0])]


def backup_metadata(tenders_dir):
    """Copy metadata files into tenders_dir/backups/<timestamp>/ before writing."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(tenders_dir, "backups", stamp)
    copied = False
    for name in METADATA_FILES:
        src = os.path.join(tenders_dir, name)
        if os.path.exists(src):
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(backup_dir, name))
            copied = True
    return backup_dir if copied else None


def rescore_workspace(label, tenders_dir, reparse, dry_run):
    print(f"\n=== Workspace: {label} ({paths.repo_relative(tenders_dir)}) ===")
    if not os.path.exists(os.path.join(tenders_dir, "metadata.csv")):
        print("  No metadata found; skipping.")
        return

    if not dry_run:
        backup = backup_metadata(tenders_dir)
        if backup:
            print(f"  Backup: {paths.repo_relative(backup)}")

    def progress(done, total):
        print(f"  ... {done}/{total}")

    if dry_run:
        cfg = scraper.load_scoring_config()
        profile = scraper.profile_for_workspace(label)
        tenders = scraper.load_existing_metadata(tenders_dir)
        transitions = {}
        for tender in tenders.values():
            old_status = tender.get("status")
            scraper.rescore_tender(tender, cfg, profile, reparse=reparse)
            key = f"{old_status or 'None'} -> {tender.get('status')}"
            transitions[key] = transitions.get(key, 0) + 1
        summary = {"total": len(tenders), "transitions": transitions,
                   "status_counts": {}, "recommendation_counts": {}}
        for t in tenders.values():
            s = t.get("status")
            summary["status_counts"][s] = summary["status_counts"].get(s, 0) + 1
            r = (t.get("analysis") or {}).get("recommendation") or "None"
            summary["recommendation_counts"][r] = summary["recommendation_counts"].get(r, 0) + 1
        print("  (dry run — nothing saved)")
    else:
        summary = scraper.rescore_metadata(
            tenders_dir=tenders_dir, reparse=reparse, progress=progress
        )

    print(f"  Tenders: {summary['total']}")
    print(f"  Status:          {summary['status_counts']}")
    print(f"  Recommendation:  {summary['recommendation_counts']}")
    changed = {k: v for k, v in summary["transitions"].items()
               if k.split(" -> ")[0] != k.split(" -> ")[1]}
    if changed:
        print("  Status changes:")
        for k, v in sorted(changed.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v}")
    else:
        print("  No status changes.")


def main():
    parser = argparse.ArgumentParser(description="Local tender rescore (no network)")
    parser.add_argument("--workspace", help="Workspace name ('main' or subfolder of tenders/)")
    parser.add_argument("--all", action="store_true", help="Rescore every workspace found")
    parser.add_argument("--reparse", action="store_true",
                        help="Fully re-parse local PDFs instead of fast re-derive")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without saving")
    args = parser.parse_args()

    for label, tenders_dir in resolve_workspaces(args):
        rescore_workspace(label, tenders_dir, args.reparse, args.dry_run)


if __name__ == "__main__":
    main()

"""
Export or apply a GeMSentry tuning bundle from the command line — the same
JSON the dashboard's "Tuning JSON" menu downloads and imports.

Usage:
  uv run python tools/tuning.py export current.json     # applied settings -> file
  uv run python tools/tuning.py template example.json   # annotated starter file
  uv run python tools/tuning.py validate my.json        # check only, write nothing
  uv run python tools/tuning.py apply my.json           # validate, then write
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemsentry import tuning  # noqa: E402


def _write(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {path}")


def _read(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("export", "template", "validate", "apply"))
    parser.add_argument("file", help="JSON file to write (export/template) or read (validate/apply)")
    args = parser.parse_args(argv)

    if args.action == "export":
        _write(tuning.export_bundle(), args.file)
        return 0
    if args.action == "template":
        _write(tuning.example_bundle(), args.file)
        return 0

    try:
        bundle = _read(args.file)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read {args.file}: {e}", file=sys.stderr)
        return 1

    if args.action == "validate":
        plan, err = tuning.plan_import(bundle)
        summary = plan["summary"] if plan else None
    else:
        summary, err = tuning.apply_import(bundle)
    if err:
        print(f"Invalid: {err}", file=sys.stderr)
        return 1
    print("Valid (nothing written)." if args.action == "validate" else "Applied.")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

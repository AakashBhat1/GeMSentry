"""One-file tuning bundle: export, validate and apply every tunable setting.

A bundle is a single JSON object that can carry any of three sections:

    scrape_keywords   -> config/keywords.csv          (max 1000 terms)
    company_profile   -> config/company_profile.local.json
    scoring_config    -> config/scoring_config.json

Omitted sections are left untouched. Inside company_profile / scoring_config
every top-level key you include REPLACES the current value of that key; keys
you leave out keep their current value. So {"company_profile": {"buyer_affinity":
{...}}} rewrites only the buyer list, and "business_lines" swaps the full list.

Keys starting with "_" (e.g. "_readme") are notes and are ignored on import.
"""

import copy
import datetime
import os
import tempfile

import paths
from gemsentry import config_store, profile

BUNDLE_FORMAT = 1
MAX_SCRAPE_KEYWORDS = 1000
MAX_KEYWORD_LEN = 120
SECTIONS = ("scrape_keywords", "company_profile", "scoring_config")
# The dashboard renders keywords into HTML; markup characters have no place
# in a GeM search term, so they are refused at the boundary.
_FORBIDDEN_KEYWORD_CHARS = set("<>\"'`\r\n\t")


def _without_notes(section):
    """Drop "_"-prefixed note keys from a section dict."""
    return {k: v for k, v in section.items() if not str(k).startswith("_")}


def read_scrape_keywords():
    """Raw keywords.csv terms in file order (no capacity stripping)."""
    return config_store.read_keyword_lines()


def export_bundle():
    """Current applied settings as one importable bundle."""
    return {
        "gemsentry_tuning": BUNDLE_FORMAT,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "scrape_keywords": read_scrape_keywords(),
        "company_profile": profile.load_company_profile(),
        "scoring_config": config_store.load_scoring_config(),
    }


def normalize_scrape_keywords(values):
    """Return (keywords, error). Trims, collapses spaces, dedupes case-insensitively."""
    if not isinstance(values, list):
        return None, "scrape_keywords must be a list of strings."
    cleaned, seen = [], set()
    for i, value in enumerate(values):
        if not isinstance(value, str):
            return None, f"scrape_keywords[{i}] must be a string."
        term = " ".join(value.split())
        if not term:
            continue
        if len(term) > MAX_KEYWORD_LEN:
            return None, f"scrape_keywords[{i}] is longer than {MAX_KEYWORD_LEN} characters."
        if _FORBIDDEN_KEYWORD_CHARS & set(value):
            return None, f"scrape_keywords[{i}] contains a forbidden character (< > quotes, tabs or newlines)."
        if term.casefold() in seen:
            continue
        seen.add(term.casefold())
        cleaned.append(term)
    if not cleaned:
        return None, "scrape_keywords must contain at least one keyword."
    if len(cleaned) > MAX_SCRAPE_KEYWORDS:
        return None, (f"scrape_keywords has {len(cleaned)} unique keywords; "
                      f"the maximum is {MAX_SCRAPE_KEYWORDS}.")
    return cleaned, None


def _merge_section(current, incoming):
    """Top-level replace: included keys win, omitted keys keep current values."""
    return {**copy.deepcopy(current), **copy.deepcopy(incoming)}


def plan_import(bundle):
    """
    Validate a bundle without writing anything.

    Returns (plan, error). plan maps each present section to the full value
    that would be written, plus a human summary under "summary".
    """
    if not isinstance(bundle, dict):
        return None, "Tuning file must be a JSON object."
    fmt = bundle.get("gemsentry_tuning", BUNDLE_FORMAT)
    if fmt != BUNDLE_FORMAT:
        return None, f"Unsupported gemsentry_tuning format {fmt!r}; expected {BUNDLE_FORMAT}."
    present = [s for s in SECTIONS if bundle.get(s) is not None]
    if not present:
        return None, ("Nothing to apply: include at least one of "
                      "scrape_keywords, company_profile, scoring_config.")

    plan, summary = {}, {}

    if "scrape_keywords" in present:
        keywords, err = normalize_scrape_keywords(bundle["scrape_keywords"])
        if err:
            return None, err
        before = {k.casefold() for k in read_scrape_keywords()}
        after = {k.casefold() for k in keywords}
        plan["scrape_keywords"] = keywords
        summary["scrape_keywords"] = {
            "count": len(keywords),
            "max": MAX_SCRAPE_KEYWORDS,
            "added": len(after - before),
            "removed": len(before - after),
        }

    if "company_profile" in present:
        incoming = bundle["company_profile"]
        if not isinstance(incoming, dict):
            return None, "company_profile must be an object."
        incoming = _without_notes(incoming)
        merged = _merge_section(profile.load_company_profile(), incoming)
        err = profile.validate_company_profile(merged)
        if err:
            return None, f"company_profile: {err}"
        err = _validate_business_line_keywords(merged["business_lines"])
        if err:
            return None, f"company_profile: {err}"
        plan["company_profile"] = merged
        summary["company_profile"] = {
            "replaced_keys": sorted(incoming),
            "business_lines": len(merged["business_lines"]),
            "buyer_affinity": len(merged["buyer_affinity"]),
        }

    if "scoring_config" in present:
        incoming = bundle["scoring_config"]
        if not isinstance(incoming, dict):
            return None, "scoring_config must be an object."
        incoming = _without_notes(incoming)
        merged = _merge_section(config_store.load_scoring_config(), incoming)
        err = config_store.validate_scoring_config(merged)
        if err:
            return None, f"scoring_config: {err}"
        plan["scoring_config"] = merged
        summary["scoring_config"] = {"replaced_keys": sorted(incoming)}

    plan["summary"] = summary
    return plan, None


def _validate_business_line_keywords(lines):
    """Every keyword list on a business line must hold non-empty strings."""
    ids = set()
    for i, line in enumerate(lines):
        line_id = str(line.get("id"))
        if line_id in ids:
            return f"business_lines[{i}].id '{line_id}' is duplicated."
        ids.add(line_id)
        for key in ("keywords", "strong_keywords", "exclude_keywords"):
            for j, kw in enumerate(line.get(key) or []):
                if not isinstance(kw, str) or not kw.strip():
                    return f"business_lines[{i}].{key}[{j}] must be a non-empty string."
    return None


def apply_import(bundle):
    """Validate every section first, then write. Returns (summary, error)."""
    plan, err = plan_import(bundle)
    if err:
        return None, err
    if "scrape_keywords" in plan:
        save_scrape_keywords(plan["scrape_keywords"])
    if "company_profile" in plan:
        profile.save_company_profile(plan["company_profile"])
    if "scoring_config" in plan:
        config_store.save_scoring_config(plan["scoring_config"])
    return plan["summary"], None


def save_scrape_keywords(keywords):
    """Atomically rewrite keywords.csv, one term per line."""
    target = config_store.KEYWORDS_PATH
    paths.ensure_dirs()
    dir_name = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="keywords_", suffix=".csv", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(keywords) + "\n")
        os.replace(tmp_path, target)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def example_bundle():
    """Annotated starter file. Import it as-is only on a fresh install: it
    replaces the business lines, buyer list and scrape keywords."""
    return {
        "_readme": {
            "how_to_use": [
                "1. Download your CURRENT config from the dashboard (Tuning JSON -> Download current) "
                "and edit that, or start from this example.",
                "2. Delete any section you do not want to change. Omitted sections stay as they are.",
                "3. Inside company_profile / scoring_config, each top-level key you include replaces "
                "the current value of that key. Keys you leave out keep their current value.",
                "4. Dashboard -> Tuning JSON -> Import, press Validate, then Apply. Or on a server: "
                "uv run python tools/tuning.py apply <file.json>",
                "5. Changes apply on the next scrape / re-score.",
            ],
            "scrape_keywords": (f"Search terms sent to the portals (keywords.csv). Up to "
                                f"{MAX_SCRAPE_KEYWORDS} unique terms, max {MAX_KEYWORD_LEN} chars each."),
            "business_lines": {
                "id": "Unique short id, e.g. 'drone'.",
                "label": "Name shown on the dashboard.",
                "priority": "Multiplier for this line (1 = normal).",
                "strong_keywords": "A match here is high-confidence relevance.",
                "keywords": "Normal relevance terms (required, non-empty).",
                "exclude_keywords": "A match here marks the tender as NOT this line.",
            },
            "buyer_affinity": "Buyer / organisation name -> score 0.0 to 1.0 (1 = favourite buyer).",
            "scoring_config": ("Weights must be >= 0. status_thresholds: 0 <= reject_max < "
                               "shortlist_min <= 100. fit.fit_min in 0..100."),
            "notes": "Keys starting with '_' are ignored on import, so you can keep notes in the file.",
        },
        "gemsentry_tuning": BUNDLE_FORMAT,
        "scrape_keywords": ["drone", "uav", "power supply", "cctv", "smart meter",
                            "lead acid battery", "dgps survey"],
        "company_profile": {
            "business_lines": [
                {
                    "id": "drone",
                    "label": "Drone / UAV",
                    "priority": 1,
                    "strong_keywords": ["drone", "uav", "unmanned aerial", "quadcopter"],
                    "keywords": ["drone", "drones", "uav", "multirotor", "mapping", "surveillance"],
                    "exclude_keywords": ["manpower", "housekeeping", "security guard"],
                },
                {
                    "id": "power_supply",
                    "label": "Power Supply / Electrical",
                    "priority": 1,
                    "strong_keywords": ["power supply", "rectifier", "battery charger"],
                    "keywords": ["power supply", "psu", "smps", "ups", "voltage regulator"],
                    "exclude_keywords": ["manpower supply", "water supply"],
                },
            ],
            "buyer_affinity": {"INDIAN AIR FORCE": 1.0, "DRDO": 0.65, "NTPC": 0.9},
        },
        "scoring_config": {
            "status_thresholds": {"shortlist_min": 75, "reject_max": 40},
            "fit": {
                "weights": {"relevance": 3, "serviceability": 1, "value_fit": 1,
                            "buyer_affinity": 0, "eligibility_factor": 2},
                "fit_min": 65,
                "unknown_buyer_subscore": 0.4,
                "turnover_gap_subscore": 0.3,
                "weak_relevance_subscore": 0.5,
            },
        },
    }

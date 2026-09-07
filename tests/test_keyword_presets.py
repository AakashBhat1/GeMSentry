"""Every keyword in keywords.csv must be reachable from a quick-preset chip.

The presets live in static/app.js, so the matching is exercised by running that
file's own taxonomy and matcher under node rather than by reimplementing them
here -- a second copy of the matcher would pass while the dashboard failed.
Skipped when node is unavailable; the dashboard's "❓ Uncategorised" chip is the
runtime safety net for exactly that case.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.config_store import load_keywords  # noqa: E402

APP_JS = os.path.join(ROOT, "static", "app.js")
DASHBOARD = os.path.join(ROOT, "dashboard.html")

# Reads app.js, lifts out both category objects and the matcher, and reports
# which presets claim each keyword handed to it on argv.
_PROBE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

function grab(name) {
    const start = src.indexOf(`const ${name} = {`);
    if (start === -1) throw new Error(`${name} not found in app.js`);
    const end = src.indexOf('\n        };', start) + '\n        };'.length;
    let out;
    eval(src.slice(start, end).replace(`const ${name}`, 'out'));
    return out;
}

const matcher = src.slice(
    src.indexOf('function isKwWordChar('),
    src.indexOf('function selectCategoryKeywords('),
);
eval(matcher);

const CATEGORIES = { ...grab('TECHNICAL_CATEGORIES'), ...grab('BUYER_CATEGORIES') };
const keywords = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

const coverage = {};
for (const kw of keywords) {
    const value = kw.toLowerCase().trim();
    coverage[kw] = Object.entries(CATEGORIES)
        .filter(([, terms]) => terms.some(t => termMatchesKeyword(t, value)))
        .map(([name]) => name);
}
process.stdout.write(JSON.stringify({
    categories: Object.keys(CATEGORIES),
    technical: Object.keys(grab('TECHNICAL_CATEGORIES')),
    coverage,
}));
"""


def _run_probe(keywords, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot exercise the dashboard's matcher")

    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    kw_file = tmp_path / "keywords.json"
    kw_file.write_text(json.dumps(keywords), encoding="utf-8")

    result = subprocess.run(
        [node, str(probe), APP_JS, str(kw_file)],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def keywords():
    return load_keywords()


@pytest.fixture
def probe(keywords, tmp_path):
    return _run_probe(keywords, tmp_path)


def test_every_keyword_is_covered_by_a_preset(probe):
    orphans = sorted(kw for kw, cats in probe["coverage"].items() if not cats)
    assert orphans == [], (
        f"{len(orphans)} keyword(s) reachable from no quick preset: "
        f"{', '.join(orphans)}. Add them to a category in "
        f"static/app.js:TECHNICAL_CATEGORIES / BUYER_CATEGORIES."
    )


def test_there_is_a_drones_preset_and_it_finds_the_drone_keywords(probe):
    assert "drones_uav" in probe["categories"]
    for keyword in ("drone", "uav", "Multirotor", "Quadcopter"):
        assert "drones_uav" in probe["coverage"].get(keyword, []), keyword


def test_every_preset_actually_matches_something(probe):
    """A category nobody can reach is dead weight, and usually a typo."""
    used = {cat for cats in probe["coverage"].values() for cat in cats}
    unused = sorted(set(probe["categories"]) - used)
    assert unused == [], f"preset(s) matching no keyword: {', '.join(unused)}"


def test_buyer_presets_stay_out_of_all_technical(probe):
    """"All Technical" means every technology, not every keyword in the file."""
    assert "buyers_agencies" in probe["categories"]
    assert "buyers_agencies" not in probe["technical"]


def _chip_keys():
    html = open(DASHBOARD, encoding="utf-8").read()
    return re.findall(r"selectCategoryKeywords\('([^']+)'\)", html)


def test_every_toolbar_chip_resolves_to_a_real_preset(probe):
    """A chip whose key does not exist silently ticks nothing when clicked."""
    special = {"all_technical", "uncategorised"}
    unknown = [k for k in _chip_keys() if k not in special and k not in probe["categories"]]
    assert unknown == [], f"chip(s) referencing a missing preset: {', '.join(unknown)}"


def test_every_preset_has_a_chip_in_the_toolbar(probe):
    """A preset with no chip is unreachable from the UI."""
    missing = sorted(set(probe["categories"]) - set(_chip_keys()))
    assert missing == [], f"preset(s) with no toolbar chip: {', '.join(missing)}"

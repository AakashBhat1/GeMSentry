"""Tuning bundle: export -> edit -> validate -> apply round-trip and limits."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
import paths  # noqa: E402
from gemsentry import config_store, profile, tuning  # noqa: E402


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """Point every config file the bundle touches at a temp dir."""
    kw = tmp_path / "keywords.csv"
    kw.write_text("keyword\ndrone\n12V 100AH battery\nPower Supply\n", encoding="utf-8")
    example_profile = os.path.join(ROOT, "config", "company_profile.json")
    prof = tmp_path / "company_profile.local.json"
    prof.write_text(open(example_profile, encoding="utf-8").read(), encoding="utf-8")
    scoring = tmp_path / "scoring_config.json"
    scoring.write_text(open(os.path.join(ROOT, "config", "scoring_config.json"),
                            encoding="utf-8").read(), encoding="utf-8")

    missing = str(tmp_path / "absent")
    monkeypatch.setattr(config_store, "KEYWORDS_PATH", str(kw))
    monkeypatch.setattr(config_store, "SCORING_CONFIG_PATH", str(scoring))
    monkeypatch.setattr(profile, "COMPANY_PROFILE_PATH", str(prof))
    for legacy in ("LEGACY_KEYWORDS_PATH", "LEGACY_SCORING_CONFIG_PATH",
                   "COMPANY_PROFILE_EXAMPLE_PATH"):
        monkeypatch.setattr(paths, legacy, missing)
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "load_server_config", lambda: {"auth_token": ""})
    return {"keywords": kw, "profile": prof, "scoring": scoring}


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_keeps_raw_keywords_and_full_sections(cfg_dir):
    bundle = tuning.export_bundle()
    assert bundle["gemsentry_tuning"] == tuning.BUNDLE_FORMAT
    assert bundle["scrape_keywords"] == ["drone", "12V 100AH battery", "Power Supply"]
    assert len(bundle["company_profile"]["business_lines"]) >= 9
    assert "fit" in bundle["scoring_config"]


def test_export_reimports_without_changes(cfg_dir):
    plan, err = tuning.plan_import(tuning.export_bundle())
    assert err is None
    assert plan["summary"]["scrape_keywords"] == {"count": 3, "max": 1000, "added": 0, "removed": 0}


def test_section_import_only_replaces_included_keys(cfg_dir):
    before = _read_json(cfg_dir["profile"])
    summary, err = tuning.apply_import({
        "_readme": "ignored",
        "company_profile": {"_note": "ignored", "buyer_affinity": {"DRDO": 0.7, "NTPC": 0.9}},
    })
    assert err is None
    after = _read_json(cfg_dir["profile"])
    assert after["buyer_affinity"] == {"DRDO": 0.7, "NTPC": 0.9}
    assert after["business_lines"] == before["business_lines"]
    assert "_note" not in after
    assert summary["company_profile"]["replaced_keys"] == ["buyer_affinity"]
    # Untouched sections are not rewritten.
    assert cfg_dir["keywords"].read_text(encoding="utf-8").startswith("keyword\n")


def test_scrape_keywords_are_deduped_and_saved(cfg_dir):
    summary, err = tuning.apply_import({"scrape_keywords": ["  smart   meter ", "Smart Meter", "uav"]})
    assert err is None
    assert cfg_dir["keywords"].read_text(encoding="utf-8") == "smart meter\nuav\n"
    assert summary["scrape_keywords"]["added"] == 2
    assert summary["scrape_keywords"]["removed"] == 3


def test_keyword_limit_is_enforced(cfg_dir):
    ok = [f"term {i}" for i in range(tuning.MAX_SCRAPE_KEYWORDS)]
    assert tuning.plan_import({"scrape_keywords": ok})[1] is None
    _, err = tuning.plan_import({"scrape_keywords": ok + ["one too many"]})
    assert "maximum is 1000" in err


@pytest.mark.parametrize("bundle, fragment", (
    ([], "JSON object"),
    ({}, "Nothing to apply"),
    ({"gemsentry_tuning": 99, "scrape_keywords": ["x"]}, "Unsupported"),
    ({"scrape_keywords": "drone"}, "list of strings"),
    ({"scrape_keywords": ["<script>"]}, "forbidden character"),
    ({"scrape_keywords": ["   "]}, "at least one"),
    ({"company_profile": {"buyer_affinity": {"DRDO": 5}}}, "buyer_affinity.DRDO"),
    ({"company_profile": {"business_lines": []}}, "non-empty list"),
    ({"company_profile": {"business_lines": [
        {"id": "a", "label": "A", "keywords": ["x"]},
        {"id": "a", "label": "B", "keywords": ["y"]}]}}, "duplicated"),
    ({"company_profile": {"business_lines": [
        {"id": "a", "label": "A", "keywords": ["x"], "strong_keywords": [""]}]}}, "non-empty string"),
    ({"scoring_config": {"status_thresholds": {"shortlist_min": 30, "reject_max": 40}}}, "reject_max"),
))
def test_invalid_bundles_are_rejected(cfg_dir, bundle, fragment):
    _, err = tuning.plan_import(bundle)
    assert err and fragment in err


def test_one_bad_section_writes_nothing(cfg_dir):
    before_kw = cfg_dir["keywords"].read_text(encoding="utf-8")
    _, err = tuning.apply_import({
        "scrape_keywords": ["new term"],
        "scoring_config": {"weights": {"emd": -1}},
    })
    assert err
    assert cfg_dir["keywords"].read_text(encoding="utf-8") == before_kw


def test_example_template_is_valid(cfg_dir):
    plan, err = tuning.plan_import(tuning.example_bundle())
    assert err is None
    assert plan["summary"]["company_profile"]["business_lines"] == 2


def test_api_dry_run_then_apply(cfg_dir):
    client = app_module.app.test_client()
    assert client.get("/api/tuning/export").get_json()["scrape_keywords"][0] == "drone"
    assert "_readme" in client.get("/api/tuning/template").get_json()

    body = {"scoring_config": {"fit": {**tuning.export_bundle()["scoring_config"]["fit"], "fit_min": 55}}}
    res = client.post("/api/tuning/import?dry_run=1", json=body)
    assert res.status_code == 200 and res.get_json()["dry_run"] is True
    assert _read_json(cfg_dir["scoring"])["fit"]["fit_min"] == 65

    res = client.post("/api/tuning/import", json=body)
    assert res.status_code == 200
    assert _read_json(cfg_dir["scoring"])["fit"]["fit_min"] == 55

    res = client.post("/api/tuning/import", json={"scrape_keywords": []})
    assert res.status_code == 400

"""Validation tests for the background scrape API payload."""

import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import normalize_scrape_payload  # noqa: E402


def test_scrape_payload_normalizes_and_deduplicates_keywords():
    parsed = normalize_scrape_payload({
        "keywords": [" facial recognition ", "FACIAL RECOGNITION", "rcognition"],
        "max_pages": "3",
    })
    assert parsed["keywords"] == ["facial recognition", "rcognition"]
    assert parsed["max_pages"] == 3
    assert parsed["min_days_left"] == 5.0
    assert parsed["sort_order"] == "Bid-Start-Date-Latest"


@pytest.mark.parametrize("payload", (
    {},
    {"keywords": "facial recognition"},
    {"keywords": [""]},
    {"keywords": ["drone"], "max_pages": 0},
    {"keywords": ["drone"], "sort_order": "not-a-real-sort"},
    {"keywords": ["drone"], "min_days_left": -1},
    {"keywords": ["drone"], "min_days_left": 10, "max_days_left": 5},
))
def test_invalid_scrape_payloads_are_rejected(payload):
    with pytest.raises(ValueError):
        normalize_scrape_payload(payload)


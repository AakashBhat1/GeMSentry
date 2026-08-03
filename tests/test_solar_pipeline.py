"""End-to-end test of the multi-source -> ingest handoff.

Runs offline against a stub adapter and a temporary workspace. Set
GEMSENTRY_LIVE_PORTALS=1 to additionally hit the real portals.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scraper  # noqa: E402
from gemsentry.pipeline import external_tender_to_record  # noqa: E402
from gemsentry.sources.base import BaseAdapter  # noqa: E402
from gemsentry.sources.registry import SourceRegistry  # noqa: E402

REQUIRED_KEYS = ("source_id", "source_name", "tender_id", "title", "buyer_org",
                 "closing_date", "published_date", "url", "scraped_at")


class StubAdapter(BaseAdapter):
    """Returns canned rows so the pipeline can be exercised without network."""

    implemented = True

    def __init__(self, source_config, rows):
        super().__init__(source_config)
        self.rows = rows

    def fetch_active_tenders(self, keywords, max_pages=5):
        keywords_lower = [k.lower() for k in keywords or []]
        return [
            self.normalize_tender(**row) for row in self.rows
            if self.matches_keywords(f"{row['title']} {row['buyer_org']}", keywords_lower)
        ]


def _stub_registry():
    registry = SourceRegistry.__new__(SourceRegistry)
    registry.sources = [
        {"id": "p1", "name": "Portal One", "url": "https://one.example/nicgep/app",
         "engine": "gepnic", "enabled": True},
        {"id": "p2", "name": "Portal Two", "url": "https://two.example/nicgep/app",
         "engine": "gepnic", "enabled": True},
    ]
    registry.adapters = {
        "p1": StubAdapter(registry.sources[0], [
            {"tender_id": "2026_SOLAR_1", "title": "Supply of solar panels",
             "buyer_org": "NTPC", "closing_date": "20-Aug-2026"},
            {"tender_id": "2026_CIVIL_9", "title": "Boundary wall civil works",
             "buyer_org": "MES", "closing_date": "21-Aug-2026"},
        ]),
        # Publishes the same solar tender as p1 -- cross-portal duplicate.
        "p2": StubAdapter(registry.sources[1], [
            {"tender_id": "2026_SOLAR_1", "title": "Supply of solar panels",
             "buyer_org": "NTPC", "closing_date": "20-Aug-2026"},
            {"tender_id": "2026_SOLAR_2", "title": "Solar rooftop installation",
             "buyer_org": "BEL", "closing_date": "25-Aug-2026"},
        ]),
    }
    return registry


class TestMultiSourcePipeline(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp(prefix="gemsentry_test_")
        self.downloads = os.path.join(self.workspace, "downloads")
        os.makedirs(self.downloads, exist_ok=True)
        self.registry = _stub_registry()

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _ingest(self, tenders):
        return scraper.ingest_external_tenders(
            tenders, tenders_dir=self.workspace, downloads_dir=self.downloads
        )

    def test_keyword_filter_applies_across_portals(self):
        tenders = self.registry.fetch_from_all_active(["solar"])
        self.assertEqual({t["tender_id"] for t in tenders}, {"2026_SOLAR_1", "2026_SOLAR_2"})

    def test_same_tender_on_two_portals_is_merged_once(self):
        tenders = self.registry.fetch_from_all_active(["solar"])
        ids = [t["tender_id"] for t in tenders]
        self.assertEqual(len(ids), len(set(ids)), "cross-portal duplicates must collapse")

    def test_adapter_output_matches_the_normalized_schema(self):
        for tender in self.registry.fetch_from_all_active([]):
            for key in REQUIRED_KEYS:
                self.assertIn(key, tender)

    def test_ingest_scores_and_persists_new_tenders(self):
        tenders = self.registry.fetch_from_all_active(["solar"])
        self.assertEqual(self._ingest(tenders), 2)

        stored = scraper.load_existing_metadata(self.workspace)
        self.assertEqual(set(stored), {"2026_SOLAR_1", "2026_SOLAR_2"})
        for record in stored.values():
            self.assertIsNotNone(record["analysis"])
            self.assertEqual(record["keyword"], "multi-source")
            self.assertEqual(record["status_source"], "auto")

    def test_re_ingesting_the_same_batch_adds_nothing(self):
        tenders = self.registry.fetch_from_all_active(["solar"])
        self.assertEqual(self._ingest(tenders), 2)
        self.assertEqual(self._ingest(tenders), 0)
        self.assertEqual(len(scraper.load_existing_metadata(self.workspace)), 2)

    def test_empty_batch_is_a_noop(self):
        self.assertEqual(self._ingest([]), 0)

    def test_rows_without_a_tender_id_are_dropped(self):
        self.assertEqual(self._ingest([{"title": "orphan", "buyer_org": "X"}]), 0)

    def test_record_mapping_fills_schema_defaults(self):
        record = external_tender_to_record({"tender_id": "T1", "source_id": "p1"})
        self.assertEqual(record["bid_no"], "T1")
        self.assertEqual(record["title"], "N/A")
        self.assertEqual(record["department"], "N/A")
        self.assertFalse(record["downloaded"])
        self.assertEqual(record["status"], "Pending Review")


@unittest.skipUnless(os.environ.get("GEMSENTRY_LIVE_PORTALS") == "1",
                     "live portal test; set GEMSENTRY_LIVE_PORTALS=1 to enable")
class TestLivePortals(unittest.TestCase):
    def test_live_multi_source_fetch(self):
        tenders = SourceRegistry().fetch_from_all_active(["solar"])
        for tender in tenders:
            for key in REQUIRED_KEYS:
                self.assertIn(key, tender)


if __name__ == "__main__":
    unittest.main()

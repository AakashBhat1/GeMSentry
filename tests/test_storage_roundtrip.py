"""Metadata persistence must not lose fields across a save/load round-trip."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.storage import load_existing_metadata, save_metadata  # noqa: E402

EXTERNAL_RECORD = {
    "bid_no": "2026_NAVY_781162_1",
    "title": "Supply of Surveillance Drone",
    "quantity": "N/A",
    "department": "Indian Navy",
    "start_date": "01-08-2026 10:00:00",
    "end_date": "20-08-2026 15:00:00",
    "keyword": "multi-source",
    "downloaded": False,
    "local_pdf_path": "",
    "pdf_url": "https://defproc.gov.in/tender/9",
    "first_seen": "2026-08-03",
    "status": "Pending Review",
    "status_source": "auto",
    "source_id": "defproc",
    "source_name": "Defence eProcurement Portal",
    "est_value_inr": 4500000,
    "domain": "drone",
    "nlp_category": "Drone Systems",
    "score": 72,
    "analysis": {"analysis_status": "card_only", "fit_score": 61},
}


class TestMetadataRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gemsentry_store_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_every_field_survives_a_round_trip(self):
        """Portal provenance and derived fields must not be dropped on reload."""
        save_metadata([EXTERNAL_RECORD], self.dir)
        loaded = load_existing_metadata(self.dir)[EXTERNAL_RECORD["bid_no"]]
        for key, value in EXTERNAL_RECORD.items():
            self.assertEqual(loaded.get(key), value, f"field '{key}' was lost")

    def test_source_attribution_specifically_survives(self):
        save_metadata([EXTERNAL_RECORD], self.dir)
        loaded = load_existing_metadata(self.dir)[EXTERNAL_RECORD["bid_no"]]
        self.assertEqual(loaded["source_id"], "defproc")
        self.assertEqual(loaded["source_name"], "Defence eProcurement Portal")

    def test_all_three_export_formats_are_written(self):
        save_metadata([EXTERNAL_RECORD], self.dir)
        for name in ("metadata.json", "metadata.js", "metadata.csv"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, name)), name)

    def test_csv_carries_source_columns(self):
        save_metadata([EXTERNAL_RECORD], self.dir)
        with open(os.path.join(self.dir, "metadata.csv"), encoding="utf-8") as f:
            header = f.readline()
        self.assertIn("Source ID", header)
        self.assertIn("Source Name", header)

    def test_csv_defaults_missing_source_to_gem(self):
        record = {k: v for k, v in EXTERNAL_RECORD.items() if not k.startswith("source_")}
        save_metadata([record], self.dir)
        with open(os.path.join(self.dir, "metadata.csv"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("gem", body)

    def test_missing_workspace_loads_empty(self):
        self.assertEqual(load_existing_metadata(os.path.join(self.dir, "nope")), {})

    def test_corrupt_json_falls_back_to_the_csv(self):
        save_metadata([EXTERNAL_RECORD], self.dir)
        with open(os.path.join(self.dir, "metadata.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        loaded = load_existing_metadata(self.dir)
        self.assertIn(EXTERNAL_RECORD["bid_no"], loaded)
        self.assertEqual(loaded[EXTERNAL_RECORD["bid_no"]]["source_id"], "defproc")

    def test_json_holding_a_non_list_falls_back_to_the_csv(self):
        save_metadata([EXTERNAL_RECORD], self.dir)
        with open(os.path.join(self.dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump({"unexpected": "shape"}, f)
        self.assertIn(EXTERNAL_RECORD["bid_no"], load_existing_metadata(self.dir))

    def test_records_without_a_bid_no_are_skipped(self):
        with open(os.path.join(self.dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump([{"title": "orphan"}, EXTERNAL_RECORD, "not-a-dict"], f)
        self.assertEqual(list(load_existing_metadata(self.dir)), [EXTERNAL_RECORD["bid_no"]])


if __name__ == "__main__":
    unittest.main()

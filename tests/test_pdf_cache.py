"""Tests for the content-hashed PDF text cache and the parallel analysis path."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry import pdf_text  # noqa: E402
from gemsentry.pipeline import _default_analysis_workers, analyze_downloaded_pdfs  # noqa: E402


class CacheTestCase(unittest.TestCase):
    """Redirects the cache at a temp dir so tests never touch the real one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemsentry_cache_")
        self.fixtures = os.path.join(self.tmp, "pdfs")
        os.makedirs(self.fixtures)
        self._real_dir = pdf_text.CACHE_DIR
        pdf_text.CACHE_DIR = os.path.join(self.tmp, "cache")
        self.extractions = []

        self._real_extract = pdf_text.extract_raw_text

        def counting_extract(path, max_pages=pdf_text.MAX_PDF_PAGES):
            self.extractions.append(path)
            return f"raw   text\tfrom\n{os.path.basename(path)}\n"

        pdf_text.extract_raw_text = counting_extract

    def tearDown(self):
        pdf_text.extract_raw_text = self._real_extract
        pdf_text.CACHE_DIR = self._real_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pdf(self, name, content=b"%PDF-1.4 fixture"):
        path = os.path.join(self.fixtures, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path


class TestNormalize(unittest.TestCase):
    def test_collapses_every_whitespace_run_to_one_space(self):
        self.assertEqual(pdf_text.normalize("a  b\tc\n\nd"), "a b c d")

    def test_handles_empty_and_none(self):
        self.assertEqual(pdf_text.normalize(""), "")
        self.assertEqual(pdf_text.normalize(None), "")


class TestExtractTextCaching(CacheTestCase):
    def test_second_call_is_served_from_cache(self):
        pdf = self._pdf("a.pdf")
        first = pdf_text.extract_text(pdf)
        second = pdf_text.extract_text(pdf)
        self.assertEqual(first, second)
        self.assertEqual(len(self.extractions), 1, "second call must not re-extract")

    def test_result_is_normalized(self):
        text = pdf_text.extract_text(self._pdf("a.pdf"))
        self.assertNotIn("\t", text)
        self.assertNotIn("\n", text)
        self.assertIn("raw text from a.pdf", text)

    def test_cache_is_keyed_on_content_not_path(self):
        """The same PDF filed under two names must share one cache entry."""
        same = b"%PDF-1.4 identical"
        pdf_text.extract_text(self._pdf("first.pdf", same))
        pdf_text.extract_text(self._pdf("second.pdf", same))
        self.assertEqual(len(self.extractions), 1)

    def test_changed_content_invalidates_the_entry(self):
        pdf = self._pdf("a.pdf", b"%PDF-1.4 v1")
        pdf_text.extract_text(pdf)
        with open(pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 v2-different")
        pdf_text.extract_text(pdf)
        self.assertEqual(len(self.extractions), 2, "edited bytes must re-extract")

    def test_cache_version_bump_evicts_old_entries(self):
        pdf = self._pdf("a.pdf")
        pdf_text.extract_text(pdf)
        original = pdf_text.CACHE_VERSION
        try:
            pdf_text.CACHE_VERSION = original + 1
            pdf_text.extract_text(pdf)
        finally:
            pdf_text.CACHE_VERSION = original
        self.assertEqual(len(self.extractions), 2)

    def test_page_limit_is_part_of_the_key(self):
        pdf = self._pdf("a.pdf")
        pdf_text.extract_text(pdf, max_pages=4)
        pdf_text.extract_text(pdf, max_pages=12)
        self.assertEqual(len(self.extractions), 2)

    def test_use_cache_false_always_extracts(self):
        pdf = self._pdf("a.pdf")
        pdf_text.extract_text(pdf, use_cache=False)
        pdf_text.extract_text(pdf, use_cache=False)
        self.assertEqual(len(self.extractions), 2)

    def test_unwritable_cache_dir_degrades_to_direct_extraction(self):
        """A broken cache may cost speed, never correctness."""
        pdf = self._pdf("a.pdf")
        pdf_text.CACHE_DIR = os.path.join(pdf, "nested")  # a file, not a directory
        text = pdf_text.extract_text(pdf)
        self.assertIn("raw text from a.pdf", text)

    def test_missing_file_digest_is_none(self):
        self.assertIsNone(pdf_text.file_digest(os.path.join(self.fixtures, "nope.pdf")))

    def test_stats_and_clear(self):
        pdf_text.extract_text(self._pdf("a.pdf", b"one"))
        pdf_text.extract_text(self._pdf("b.pdf", b"two"))
        entries, size = pdf_text.cache_stats()
        self.assertEqual(entries, 2)
        self.assertGreater(size, 0)
        self.assertEqual(pdf_text.clear_cache(), 2)
        self.assertEqual(pdf_text.cache_stats(), (0, 0))

    def test_clear_cache_only_removes_its_own_entries(self):
        """A misconfigured CACHE_DIR must not become a directory wipe."""
        pdf_text.extract_text(self._pdf("a.pdf", b"one"))
        os.makedirs(pdf_text.CACHE_DIR, exist_ok=True)
        bystander = os.path.join(pdf_text.CACHE_DIR, "important.json")
        with open(bystander, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertEqual(pdf_text.clear_cache(), 1)
        self.assertTrue(os.path.exists(bystander))


class TestAnalyzeDownloadedPdfs(unittest.TestCase):
    """The verdict must be applied whether analysis succeeds, fails or is empty."""

    def setUp(self):
        self.cfg = {"weights": {}, "unknown_subscore": 0.5}
        self.profile = {"business_lines": []}

    def test_no_jobs_is_a_noop(self):
        self.assertIsNone(analyze_downloaded_pdfs([], self.cfg, self.profile))

    def test_missing_pdf_falls_back_to_card_scoring(self):
        tender = {"bid_no": "B1", "title": "Drone supply", "status_source": "auto"}
        job = (tender, os.path.join(tempfile.gettempdir(), "does-not-exist.pdf"),
               {"auto_reject": False})
        analyze_downloaded_pdfs([job], None, None, workers=1)
        # analyze_rfp_pdf returns None for a missing path, so the card path runs.
        self.assertIsNotNone(tender.get("analysis"))
        self.assertEqual(tender["analysis"].get("analysis_status"), "card_only")

    def test_default_worker_count_is_sane(self):
        workers = _default_analysis_workers()
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 4)


if __name__ == "__main__":
    unittest.main()

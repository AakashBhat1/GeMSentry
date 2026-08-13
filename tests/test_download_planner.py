"""Tests for the download planner's portal gate.

The GeM RFP parser is the only document reader this pipeline has, so only GeM
tenders may enter the download stage. Everything else is scored from its
listing metadata -- a rule that also keeps one unreachable portal from stalling
a whole run behind its TCP connect timeouts.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.config_store import load_scoring_config  # noqa: E402
from gemsentry.pipeline import plan_downloads  # noqa: E402
from gemsentry.profile import load_company_profile  # noqa: E402
from gemsentry.sources.attribution import build_host_index  # noqa: E402
from gemsentry.sources.gem.client import is_gem_url  # noqa: E402

CFG = load_scoring_config()
PROFILE = load_company_profile()

SOURCES = [
    {"id": "gem", "name": "Government e-Marketplace (GeM)", "url": "https://gem.gov.in/"},
    {"id": "isro", "name": "ISRO e-Procurement Portal", "url": "https://eproc.isro.gov.in/"},
    {"id": "bhel", "name": "Bharat Heavy Electricals Limited (BHEL)",
     "url": "https://tenders.bhel.com/tenders"},
]

# Far enough out that the date window can never auto-reject these fixtures.
START = "12-08-2030 11:00 AM"
END = "30-08-2030 11:00 AM"


def _tender(bid_no, pdf_url, **extra):
    """A card carrying a business line the profile actually matches."""
    record = {
        "bid_no": bid_no,
        "title": "Supply and installation of solar street light with battery",
        "primary_item": "Solar Street Light",
        "item_category": "Solar Street Light",
        "department": "Test Department",
        "quantity": "10",
        "keyword": "solar street light",
        "pdf_url": pdf_url,
        "start_date": START,
        "end_date": END,
        "downloaded": False,
        "local_pdf_path": "",
        "analysis": None,
    }
    record.update(extra)
    return record


class TestPortalGate(unittest.TestCase):
    def setUp(self):
        self.host_index = build_host_index(SOURCES)

    def _plan(self, tenders):
        return plan_downloads(
            tenders, CFG, PROFILE, downloads_dir=os.path.join(ROOT, "downloads"),
            pdf_index={}, host_index=self.host_index,
        )

    def test_gem_tender_is_queued_for_download(self):
        tender = _tender("GEM/2030/B/1234567",
                         "https://bidplus.gem.gov.in/showbidDocument/1234567")
        to_download, to_analyze = self._plan([tender])

        self.assertEqual(len(to_download), 1)
        self.assertEqual(to_download[0][0]["bid_no"], "GEM/2030/B/1234567")
        self.assertEqual(to_analyze, [])

    def test_external_tender_is_never_queued_and_is_card_scored(self):
        tender = _tender("SA203000177501",
                         "https://eproc.isro.gov.in/viewDocumentPT?tenderId=SA203000177501",
                         source_id="isro", source_name="ISRO e-Procurement Portal")
        to_download, to_analyze = self._plan([tender])

        self.assertEqual(to_download, [])
        self.assertEqual(to_analyze, [])
        self.assertFalse(tender["downloaded"])
        self.assertEqual(tender["analysis"]["analysis_status"], "card_only")

    def test_external_skip_states_its_reason(self):
        tender = _tender("BHEL/2030/1",
                         "https://tenders.bhel.com/bid-number-2030-fire-resistant-fluid",
                         source_id="bhel",
                         source_name="Bharat Heavy Electricals Limited (BHEL)")
        self._plan([tender])

        reasons = " ".join(tender["analysis"].get("reasons") or [])
        self.assertIn("PDF not fetched", reasons)
        self.assertIn("Bharat Heavy Electricals", reasons)

    def test_legacy_record_without_source_id_is_still_treated_as_gem(self):
        """Records predating the multi-source refactor carry no source_id."""
        tender = _tender("GEM/2030/B/7654321",
                         "https://bidplus.gem.gov.in/showbidDocument/7654321")
        tender.pop("source_id", None)
        to_download, _ = self._plan([tender])

        self.assertEqual(len(to_download), 1)

    def test_external_pdf_already_on_disk_is_still_analyzed(self):
        """The gate blocks fetching, not reuse of a document we already hold."""
        tender = _tender("SA203000124301",
                         "https://eproc.isro.gov.in/viewDocumentPT?tenderId=SA203000124301",
                         source_id="isro", source_name="ISRO e-Procurement Portal")
        pdf_index = {"SA203000124301.pdf": "downloads/space/SA203000124301.pdf"}
        to_download, to_analyze = plan_downloads(
            [tender], CFG, PROFILE, downloads_dir=os.path.join(ROOT, "downloads"),
            pdf_index=pdf_index, host_index=self.host_index,
        )

        self.assertEqual(to_download, [])
        self.assertEqual(len(to_analyze), 1)

    def test_mixed_batch_splits_by_portal(self):
        gem = _tender("GEM/2030/B/1111111",
                      "https://bidplus.gem.gov.in/showbidDocument/1111111")
        external = _tender("SA203000101401",
                           "https://eproc.isro.gov.in/viewDocumentPT?tenderId=SA203000101401",
                           source_id="isro", source_name="ISRO e-Procurement Portal")
        to_download, _ = self._plan([gem, external])

        queued = [job[0]["bid_no"] for job in to_download]
        self.assertEqual(queued, ["GEM/2030/B/1111111"])


class TestIsGemUrl(unittest.TestCase):
    """The session cookie is only ever attached to a host that passes this."""

    def test_accepts_gem_and_its_subdomains(self):
        self.assertTrue(is_gem_url("https://gem.gov.in/"))
        self.assertTrue(is_gem_url("https://bidplus.gem.gov.in/showbidDocument/1"))

    def test_rejects_other_portals_and_lookalikes(self):
        self.assertFalse(is_gem_url("https://eproc.isro.gov.in/viewDocumentPT?tenderId=1"))
        self.assertFalse(is_gem_url("https://tenders.bhel.com/tenders"))
        self.assertFalse(is_gem_url("https://notgem.gov.in/showbidDocument/1"))
        self.assertFalse(is_gem_url(""))


class TestCookieIsNotLeaked(unittest.TestCase):
    """download_pdf_http must not send the bidplus session to a third party."""

    def setUp(self):
        from gemsentry.sources.gem import client
        self.client = client
        self._real_urlopen = client.urllib.request.urlopen
        self.captured = []

        class _Resp:
            def read(self):
                return b"not a pdf"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, **kwargs):
            self.captured.append(request)
            return _Resp()

        client.urllib.request.urlopen = fake_urlopen

    def tearDown(self):
        self.client.urllib.request.urlopen = self._real_urlopen

    def test_cookie_sent_to_gem(self):
        self.client.download_pdf_http(
            "https://bidplus.gem.gov.in/showbidDocument/1", "unused.pdf", "sid=secret"
        )
        self.assertEqual(self.captured[0].get_header("Cookie"), "sid=secret")

    def test_cookie_withheld_from_other_hosts(self):
        self.client.download_pdf_http(
            "https://eproc.isro.gov.in/viewDocumentPT?tenderId=1", "unused.pdf", "sid=secret"
        )
        self.assertIsNone(self.captured[0].get_header("Cookie"))


if __name__ == "__main__":
    unittest.main()

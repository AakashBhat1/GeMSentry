"""Tests for the multi-source portal registry and the GePNIC adapter."""
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import paths  # noqa: E402
from gemsentry.sources import NATIVE_ENGINES, SourceRegistry  # noqa: E402
from gemsentry.sources.attribution import (  # noqa: E402
    UNKNOWN_SOURCE_ID, UNKNOWN_SOURCE_NAME, annotate_sources, build_host_index, derive_source,
)
from gemsentry.sources.base import UnsupportedAdapter  # noqa: E402
from gemsentry.sources.gepnic import GePNICAdapter  # noqa: E402

LISTING_HTML = """
<table id="table">
  <tr>
    <th>S.No</th><th>e-Published Date</th><th>Bid Submission Closing Date</th>
    <th>Tender Opening Date</th><th>Title and Ref.No./Tender Id</th>
    <th>Organisation Chain</th>
  </tr>
  <tr>
    <td>1</td><td>01-Aug-2026 10:00 AM</td><td>20-Aug-2026 03:00 PM</td>
    <td>21-Aug-2026 03:30 PM</td>
    <td><a href="?page=FrontEndViewTender&amp;id=9">Supply of Surveillance Drone [2026_NAVY_781162_1]</a></td>
    <td>Indian Navy||Material Organisation</td>
  </tr>
  <tr>
    <td>2</td><td>02-Aug-2026 11:00 AM</td><td>22-Aug-2026 03:00 PM</td>
    <td>23-Aug-2026 03:30 PM</td>
    <td><a href="?page=FrontEndViewTender&amp;id=10">Civil works for boundary wall [2026_MES_778293_1]</a></td>
    <td>MES||Chief Engineer</td>
  </tr>
</table>
"""

# Madhya Pradesh (and others) build the header row out of <td>, not <th>,
# and serve an empty listing until the "closing within N days" tab is clicked.
TD_HEADER_HTML = """
<table class="list_table">
  <tr>
    <td>S.No</td><td>e-Published Date</td><td>Bid Submission Closing Date</td>
    <td>Tender Opening Date</td><td>Title and Ref.No./Tender ID</td>
    <td>Organisation Chain</td>
  </tr>
  <tr>
    <td>1</td><td>01-Aug-2026 10:00 AM</td><td>04-Aug-2026 11:00 AM</td>
    <td>05-Aug-2026 11:30 AM</td>
    <td>Vehicle on Rent [2026_SBMG_523857_1]</td>
    <td>SBMG||Bhopal</td>
  </tr>
</table>
"""

TD_HEADER_EMPTY_HTML = """
<table class="list_table">
  <tr>
    <td>S.No</td><td>e-Published Date</td><td>Bid Submission Closing Date</td>
    <td>Tender Opening Date</td><td>Title and Ref.No./Tender ID</td>
    <td>Organisation Chain</td>
  </tr>
  <tr><td>No Tenders found.</td></tr>
</table>
"""

# Same data, different column order and no <th> header row.
POSITIONAL_HTML = """
<table class="list_table">
  <tr>
    <td>1</td><td>01-Aug-2026</td><td>20-Aug-2026</td><td>21-Aug-2026</td>
    <td>Supply of Surveillance Drone [2026_NAVY_781162_1]</td>
    <td>Indian Navy</td>
  </tr>
</table>
"""


def _adapter(url="https://defproc.gov.in/nicgep/app", source_id="defproc"):
    return GePNICAdapter({"id": source_id, "name": "Test Portal", "url": url, "engine": "gepnic"})


class TestSourceRegistry(unittest.TestCase):
    def setUp(self):
        paths.ensure_dirs()
        self.registry = SourceRegistry()

    def test_sources_config_exists(self):
        self.assertTrue(os.path.exists(paths.SOURCES_PATH))

    def test_loads_all_configured_portals(self):
        ids = [s["id"] for s in self.registry.get_all_sources()]
        for expected in ("gem", "defproc", "bel", "cppp_cg", "srijan", "ireps"):
            self.assertIn(expected, ids)

    def test_gepnic_sources_get_a_real_adapter(self):
        self.assertIsInstance(self.registry.adapters["defproc"], GePNICAdapter)
        self.assertEqual(self.registry.adapters["defproc"].engine, "gepnic")

    def test_engines_without_an_adapter_are_not_dispatched(self):
        """Unimplemented engines must not be handed to the thread pool."""
        runnable_ids = {a.source_id for a in self.registry.runnable_adapters()}
        self.assertIn("defproc", runnable_ids)
        # Blocked for reasons recorded in config/sources.json: CAPTCHAs,
        # JS-only portals, or sites that publish no tenders at all.
        for unimplemented in ("srijan", "ireps", "idex", "mii_defence"):
            self.assertNotIn(unimplemented, runnable_ids)
            self.assertIsInstance(self.registry.adapters[unimplemented], UnsupportedAdapter)

    def test_implemented_engines_are_dispatched(self):
        runnable_ids = {a.source_id for a in self.registry.runnable_adapters()}
        for supported in ("defproc", "cppp_cg", "isro", "bhel"):
            self.assertIn(supported, runnable_ids)

    def test_every_blocked_source_explains_why(self):
        """A blocked portal must carry a reason, not look like an unfinished TODO."""
        for source in self.registry.unsupported_sources():
            self.assertTrue(source.get("blocked_reason"), f"{source['id']} has no blocked_reason")

    def test_gem_is_native_and_never_fanned_out(self):
        """GeM has its own pipeline; fanning it out here would be a no-op."""
        self.assertIn("gem", NATIVE_ENGINES)
        self.assertNotIn("gem", {a.source_id for a in self.registry.runnable_adapters()})
        self.assertNotIn("gem", {s["id"] for s in self.registry.unsupported_sources()})

    def test_get_all_sources_annotates_capability(self):
        by_id = {s["id"]: s for s in self.registry.get_all_sources()}
        self.assertTrue(by_id["defproc"]["supported"])
        self.assertFalse(by_id["srijan"]["supported"])
        self.assertTrue(by_id["gem"]["native"])

    def test_toggle_unknown_source_returns_false(self):
        self.assertFalse(self.registry.toggle_source("does-not-exist", False))

    def test_fetch_skips_when_nothing_runnable(self):
        empty = SourceRegistry.__new__(SourceRegistry)
        empty.sources, empty.adapters = [], {}
        self.assertEqual(empty.fetch_from_all_active(["drone"]), [])


class TestGePNICUrlResolution(unittest.TestCase):
    """The Tapestry app path differs per deployment and must not be assumed."""

    def test_honours_configured_app_path(self):
        cases = {
            "https://eprocure.gov.in/eprocure/app": "https://eprocure.gov.in/eprocure/app",
            "https://eprocure.gov.in/epublish/app": "https://eprocure.gov.in/epublish/app",
            "https://etenders.gov.in/eprocure/app": "https://etenders.gov.in/eprocure/app",
            "https://defproc.gov.in/nicgep/app": "https://defproc.gov.in/nicgep/app",
        }
        for configured, expected in cases.items():
            self.assertEqual(_adapter(configured).app_url, expected, configured)

    def test_defaults_when_url_has_no_app_path(self):
        self.assertEqual(
            _adapter("https://eproc.punjab.gov.in/").app_url,
            "https://eproc.punjab.gov.in/nicgep/app",
        )

    def test_date_listing_url_is_built_from_app_url(self):
        adapter = _adapter("https://eprocure.gov.in/eprocure/app")
        self.assertTrue(adapter.date_list_url.startswith("https://eprocure.gov.in/eprocure/app?"))
        self.assertIn("FrontEndListTendersbyDate", adapter.date_list_url)


class TestGePNICParsing(unittest.TestCase):
    def test_parses_rows_using_table_headers(self):
        tenders = _adapter().parse_listing(LISTING_HTML, [])
        self.assertEqual(len(tenders), 2)
        first = tenders[0]
        self.assertEqual(first["tender_id"], "2026_NAVY_781162_1")
        self.assertEqual(first["title"], "Supply of Surveillance Drone")
        self.assertEqual(first["buyer_org"], "Indian Navy||Material Organisation")
        self.assertEqual(first["closing_date"], "20-Aug-2026 03:00 PM")
        self.assertEqual(first["published_date"], "01-Aug-2026 10:00 AM")
        self.assertEqual(first["source_id"], "defproc")

    def test_row_link_resolves_against_the_portal_app_url(self):
        tenders = _adapter("https://eprocure.gov.in/eprocure/app", "cppp").parse_listing(LISTING_HTML, [])
        self.assertTrue(tenders[0]["url"].startswith("https://eprocure.gov.in/eprocure/app?"))

    def test_keyword_filter_matches_title_and_org(self):
        adapter = _adapter()
        self.assertEqual(len(adapter.parse_listing(LISTING_HTML, ["drone"])), 1)
        self.assertEqual(len(adapter.parse_listing(LISTING_HTML, ["navy"])), 1)
        self.assertEqual(len(adapter.parse_listing(LISTING_HTML, ["submarine"])), 0)
        self.assertEqual(len(adapter.parse_listing(LISTING_HTML, [])), 2)

    def test_falls_back_to_positional_columns_without_headers(self):
        tenders = _adapter().parse_listing(POSITIONAL_HTML, ["drone"])
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["tender_id"], "2026_NAVY_781162_1")
        self.assertEqual(tenders[0]["closing_date"], "20-Aug-2026")

    def test_synthetic_ids_are_a_stable_content_digest(self):
        """Must survive a restart: hash() is salted per process, sha1 is not."""
        adapter = _adapter()
        row = "Untagged tender with no id"
        self.assertEqual(adapter._tender_id(row), f"defproc_{hashlib.sha1(row.encode()).hexdigest()[:12]}")
        self.assertNotEqual(adapter._tender_id(row), adapter._tender_id(row + "!"))

    def test_td_built_header_row_is_not_ingested_as_a_tender(self):
        """MP builds its header from <td>; it must not become a fake tender."""
        tenders = _adapter().parse_listing(TD_HEADER_HTML, [])
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["tender_id"], "2026_SBMG_523857_1")
        titles = [t["title"] for t in tenders]
        self.assertNotIn("Title and Ref.No./Tender ID", titles)

    def test_td_built_header_row_still_maps_columns(self):
        tender = _adapter().parse_listing(TD_HEADER_HTML, [])[0]
        self.assertEqual(tender["closing_date"], "04-Aug-2026 11:00 AM")
        self.assertEqual(tender["published_date"], "01-Aug-2026 10:00 AM")
        self.assertEqual(tender["buyer_org"], "SBMG||Bhopal")

    def test_empty_listing_yields_nothing_so_the_browser_fallback_can_run(self):
        """A junk header row here would suppress the escalation to Playwright."""
        self.assertEqual(_adapter().parse_listing(TD_HEADER_EMPTY_HTML, []), [])

    def test_empty_and_malformed_html_is_safe(self):
        adapter = _adapter()
        self.assertEqual(adapter.parse_listing("", []), [])
        self.assertEqual(adapter.parse_listing("<html><body>nope</body></html>", []), [])
        self.assertEqual(adapter.parse_listing("<table><tr><td>a</td></tr></table>", []), [])


SOURCES_FIXTURE = [
    {"id": "gem", "name": "Government e-Marketplace (GeM)", "url": "https://gem.gov.in/"},
    {"id": "defproc", "name": "Defence eProcurement Portal", "url": "https://defproc.gov.in/nicgep/app"},
    {"id": "isro", "name": "ISRO e-Procurement Portal", "url": "https://eproc.isro.gov.in/"},
    {"id": "cppp_cg", "name": "CPPP - CG", "url": "https://eprocure.gov.in/eprocure/app"},
    {"id": "cppp_pub", "name": "CPPP e-Publish", "url": "https://eprocure.gov.in/epublish/app"},
]


class SourceAttributionTest(unittest.TestCase):
    """Legacy records carry no source_id; the portal filter still has to work."""

    def setUp(self):
        self.index = build_host_index(SOURCES_FIXTURE)

    def test_explicit_source_id_is_authoritative(self):
        record = {"source_id": "defproc", "source_name": "Defence eProcurement Portal",
                  "bid_no": "GEM/2026/B/1", "pdf_url": "https://bidplus.gem.gov.in/x"}
        self.assertEqual(derive_source(record, self.index),
                         ("defproc", "Defence eProcurement Portal"))

    def test_subdomain_of_a_configured_host_attributes_to_that_portal(self):
        """GeM serves documents from bidplus.gem.gov.in; config lists gem.gov.in."""
        record = {"bid_no": "GEM/2026/B/7577626",
                  "pdf_url": "https://bidplus.gem.gov.in/showbidDocument/123"}
        self.assertEqual(derive_source(record, self.index)[0], "gem")

    def test_host_wins_over_the_bid_number_prefix(self):
        record = {"bid_no": "GEM/2026/B/9", "pdf_url": "https://eproc.isro.gov.in/tender/9"}
        self.assertEqual(derive_source(record, self.index)[0], "isro")

    def test_gem_bid_prefix_attributes_records_that_have_no_document_url(self):
        record = {"bid_no": "GEM/2026/B/7577626", "pdf_url": ""}
        self.assertEqual(derive_source(record, self.index),
                         ("gem", "Government e-Marketplace (GeM)"))

    def test_unattributable_records_get_their_own_bucket(self):
        record = {"bid_no": "2026_NAVY_781162_1", "pdf_url": "https://unheard-of.example/x"}
        self.assertEqual(derive_source(record, self.index),
                         (UNKNOWN_SOURCE_ID, UNKNOWN_SOURCE_NAME))

    def test_shared_host_resolves_to_the_first_configured_portal(self):
        record = {"bid_no": "X/1", "pdf_url": "https://eprocure.gov.in/epublish/app?id=4"}
        self.assertEqual(derive_source(record, self.index)[0], "cppp_cg")

    def test_annotate_leaves_the_caller_records_untouched(self):
        records = [{"bid_no": "GEM/2026/B/1", "pdf_url": "https://bidplus.gem.gov.in/d/1"}]
        annotated = annotate_sources(records, SOURCES_FIXTURE)
        self.assertEqual(annotated[0]["source_id"], "gem")
        self.assertNotIn("source_id", records[0])

    def test_annotate_covers_every_record(self):
        records = [
            {"bid_no": "GEM/2026/B/1", "pdf_url": "https://bidplus.gem.gov.in/d/1"},
            {"bid_no": "2026_X_1", "pdf_url": "https://defproc.gov.in/nicgep/app?id=1"},
            {"bid_no": "junk", "pdf_url": ""},
        ]
        ids = [r["source_id"] for r in annotate_sources(records, SOURCES_FIXTURE)]
        self.assertEqual(ids, ["gem", "defproc", UNKNOWN_SOURCE_ID])

    def test_malformed_urls_do_not_raise(self):
        for bad in (None, "", "not a url", "http://", "://broken"):
            record = {"bid_no": "junk", "pdf_url": bad}
            self.assertEqual(derive_source(record, self.index)[0], UNKNOWN_SOURCE_ID)


if __name__ == "__main__":
    unittest.main()

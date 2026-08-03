"""Tests for the ISRO and BHEL table adapters.

Fixtures mirror the real markup captured from each portal: ISRO's
``table#tenderListTable`` and BHEL's Drupal ``table.views-table``.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.dateparse import parse_gem_date  # noqa: E402
from gemsentry.sources.bhel import BHELAdapter, parse_description_cell  # noqa: E402
from gemsentry.sources.isro import ISROAdapter  # noqa: E402
from gemsentry.sources.registry import ENGINES  # noqa: E402
from gemsentry.sources.table_adapter import HtmlTableAdapter  # noqa: E402

ISRO_HTML = """
<table id="tenderListTable" class="table table-striped table-hover">
  <thead><tr>
    <th>Tender No</th><th>Centre Name</th><th>Tender Description</th>
    <th>Bid Closing Date (IST)</th><th>Bid Opening Date (IST)</th><th>Actions</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>IS202600054601</td><td>URSC</td>
      <td>Field Resettable Hold-Down Release Actuator</td>
      <td>24-August-2026 14:30</td><td>24-August-2026 15:00</td>
      <td><a href="/viewDocumentPT?tenderId=IS202600054601">Tender Document View</a></td>
    </tr>
    <tr>
      <td>SA202600126101</td><td>SAC</td><td>Space Qualified Resistors</td>
      <td>25-August-2026 13:00</td><td>25-August-2026 14:00</td>
      <td><a href="/viewDocumentPT?tenderId=SA202600126101">Tender Document View</a></td>
    </tr>
    <tr><td>too</td><td>few</td></tr>
  </tbody>
</table>
"""

BHEL_HTML = """
<table class="views-table views-view-table table table-bordered cols-4">
  <thead><tr>
    <th>NIT Number</th><th>Tender Description</th><th>Unit</th><th>Tender Opening Date</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>180563</td>
      <td>Tender NIT Number : NIT_180563 Tender Notification Number : SHAPV00037
          [GEM/2026/B/7865734] Tender Description :
          <a href="https://tenders.bhel.com/supply-robotic-cleaning">Supply of Semi-Automatic Dry
          Robotic Cleaning System</a> Date of Notification : 03-08-2026 07:00:00 PM</td>
      <td>BHEL, SBD</td><td>13-08-2026 07:30:00 PM</td>
    </tr>
    <tr>
      <td>180562</td>
      <td>Tender NIT Number : NIT_180562 Tender Notification Number : Email Enquiry
          Tender Description :
          <a href="/supply-radial-shaft-seal">PROCUREMENT OF RADIAL SHAFT SEAL</a>
          Date of Notification : 03-08-2026 05:29:19 PM</td>
      <td>BHEL, Ranipet</td><td>04-08-2026 09:30:20 AM</td>
    </tr>
  </tbody>
</table>
"""


def isro():
    return ISROAdapter({"id": "isro", "name": "ISRO e-Procurement",
                        "url": "https://eproc.isro.gov.in/", "engine": "isro"})


def bhel(**extra):
    return BHELAdapter({"id": "bhel", "name": "BHEL", "engine": "bhel",
                        "url": "https://tenders.bhel.com/tenders", **extra})


class TestEngineRegistration(unittest.TestCase):
    def test_both_engines_are_registered(self):
        self.assertIs(ENGINES["isro"], ISROAdapter)
        self.assertIs(ENGINES["bhel"], BHELAdapter)

    def test_adapters_report_themselves_implemented(self):
        self.assertTrue(isro().implemented)
        self.assertTrue(bhel().implemented)


class TestISROAdapter(unittest.TestCase):
    def test_parses_every_data_row(self):
        tenders = isro().parse_listing(ISRO_HTML, [])
        self.assertEqual(len(tenders), 2, "the short row must be skipped")

    def test_maps_columns_correctly(self):
        first = isro().parse_listing(ISRO_HTML, [])[0]
        self.assertEqual(first["tender_id"], "IS202600054601")
        self.assertEqual(first["title"], "Field Resettable Hold-Down Release Actuator")
        self.assertEqual(first["buyer_org"], "ISRO - URSC")
        self.assertEqual(first["closing_date"], "24-August-2026 14:30")
        self.assertEqual(first["raw_data"]["bid_opening_date"], "24-August-2026 15:00")
        self.assertEqual(first["source_id"], "isro")

    def test_document_link_is_absolute(self):
        first = isro().parse_listing(ISRO_HTML, [])[0]
        self.assertEqual(
            first["url"],
            "https://eproc.isro.gov.in/viewDocumentPT?tenderId=IS202600054601",
        )

    def test_closing_date_is_parseable_by_the_pipeline(self):
        """An unparseable date would silently disable expiry and urgency scoring."""
        for tender in isro().parse_listing(ISRO_HTML, []):
            self.assertIsNotNone(parse_gem_date(tender["closing_date"]), tender["closing_date"])

    def test_keyword_filter_covers_title_and_centre(self):
        adapter = isro()
        self.assertEqual(len(adapter.parse_listing(ISRO_HTML, ["actuator"])), 1)
        self.assertEqual(len(adapter.parse_listing(ISRO_HTML, ["sac"])), 1)
        self.assertEqual(len(adapter.parse_listing(ISRO_HTML, ["submarine"])), 0)

    def test_listing_url(self):
        self.assertEqual(isro().listing_url(), "https://eproc.isro.gov.in/home.html")


class TestBHELDescriptionCell(unittest.TestCase):
    def test_unpacks_all_four_labelled_fields(self):
        fields = parse_description_cell(
            "Tender NIT Number : NIT_1 Tender Notification Number : ABC [GEM/2026/B/999] "
            "Tender Description : Supply of pumps Date of Notification : 03-08-2026 07:00:00 PM"
        )
        self.assertEqual(fields["tender nit number"], "NIT_1")
        self.assertEqual(fields["tender notification number"], "ABC [GEM/2026/B/999]")
        self.assertEqual(fields["tender description"], "Supply of pumps")
        self.assertEqual(fields["date of notification"], "03-08-2026 07:00:00 PM")

    def test_empty_and_unlabelled_text_is_safe(self):
        self.assertEqual(parse_description_cell(""), {})
        self.assertEqual(parse_description_cell(None), {})
        self.assertEqual(parse_description_cell("no labels at all"), {})


class TestBHELAdapter(unittest.TestCase):
    def test_parses_every_data_row(self):
        self.assertEqual(len(bhel().parse_listing(BHEL_HTML, [])), 2)

    def test_title_comes_from_the_anchor_not_the_packed_cell(self):
        first = bhel().parse_listing(BHEL_HTML, [])[0]
        self.assertEqual(first["title"], "Supply of Semi-Automatic Dry Robotic Cleaning System")
        self.assertNotIn("NIT Number", first["title"])

    def test_maps_dates_and_ids(self):
        first = bhel().parse_listing(BHEL_HTML, [])[0]
        self.assertEqual(first["tender_id"], "BHEL_180563")
        self.assertEqual(first["buyer_org"], "BHEL, SBD")
        self.assertEqual(first["published_date"], "03-08-2026 07:00:00 PM")
        self.assertEqual(first["closing_date"], "13-08-2026 07:30:00 PM")

    def test_extracts_the_gem_cross_reference_when_present(self):
        tenders = bhel().parse_listing(BHEL_HTML, [])
        self.assertEqual(tenders[0]["raw_data"]["gem_reference"], "GEM/2026/B/7865734")
        self.assertEqual(tenders[1]["raw_data"]["gem_reference"], "")

    def test_relative_link_resolves_against_the_listing_url(self):
        second = bhel().parse_listing(BHEL_HTML, [])[1]
        self.assertEqual(second["url"], "https://tenders.bhel.com/supply-radial-shaft-seal")

    def test_dates_are_parseable_by_the_pipeline(self):
        for tender in bhel().parse_listing(BHEL_HTML, []):
            self.assertIsNotNone(parse_gem_date(tender["closing_date"]))
            self.assertIsNotNone(parse_gem_date(tender["published_date"]))

    def test_keyword_filter_matches_clean_title(self):
        adapter = bhel()
        self.assertEqual(len(adapter.parse_listing(BHEL_HTML, ["robotic"])), 1)
        self.assertEqual(len(adapter.parse_listing(BHEL_HTML, ["seal"])), 1)
        self.assertEqual(len(adapter.parse_listing(BHEL_HTML, ["turbine"])), 0)

    def test_tls_verification_is_opt_out_per_source(self):
        self.assertTrue(bhel().verify_tls, "verification must default to on")
        self.assertFalse(bhel(verify_tls=False).verify_tls)


class TestHtmlTableAdapterBase(unittest.TestCase):
    def test_malformed_input_never_raises(self):
        for html in ("", None, "<html>no tables</html>", "<table><tr><td>a</td></tr></table>"):
            self.assertEqual(isro().parse_listing(html, []), [])

    def test_falls_back_to_the_largest_table_when_selector_misses(self):
        class Anon(HtmlTableAdapter):
            table_id = "not-present"

            def row_to_tender(self, cells, row):
                return self.normalize_tender(
                    tender_id=self.cell_text(cells, 0), title=self.cell_text(cells, 1),
                    buyer_org="X",
                )

        html = ("<table><tr><td>ignore</td></tr></table>"
                "<table><tr><td>T1</td><td>alpha</td><td>c</td></tr>"
                "<tr><td>T2</td><td>beta</td><td>c</td></tr></table>")
        tenders = Anon({"id": "a", "url": "https://x.test/"}).parse_listing(html, [])
        self.assertEqual([t["tender_id"] for t in tenders], ["T1", "T2"])

    def test_duplicate_ids_within_one_page_collapse(self):
        html = ISRO_HTML.replace("SA202600126101", "IS202600054601")
        self.assertEqual(len(isro().parse_listing(html, [])), 1)

    def test_a_raising_row_does_not_abort_the_page(self):
        class Fragile(HtmlTableAdapter):
            def row_to_tender(self, cells, row):
                value = self.cell_text(cells, 0)
                if value == "boom":
                    raise ValueError("bad row")
                return self.normalize_tender(tender_id=value, title=value, buyer_org="X")

        html = ("<table><tr><td>ok1</td><td>b</td><td>c</td></tr>"
                "<tr><td>boom</td><td>b</td><td>c</td></tr>"
                "<tr><td>ok2</td><td>b</td><td>c</td></tr></table>")
        tenders = Fragile({"id": "f", "url": "https://x.test/"}).parse_listing(html, [])
        self.assertEqual([t["tender_id"] for t in tenders], ["ok1", "ok2"])


if __name__ == "__main__":
    unittest.main()

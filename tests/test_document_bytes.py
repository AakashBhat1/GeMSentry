"""HTML vs PDF detection for the download/parse path (BHEL interstitials)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.sources.gem import client as gem_client  # noqa: E402
from gemsentry.sources.gem.client import (  # noqa: E402
    classify_document_body,
    download_pdf_http,
    is_pdf_file,
    looks_like_pdf_url,
)

PDF_FIXTURE = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
HTML_LOGIN = (
    b"<!DOCTYPE html>\n<html><head><title>Login</title></head>"
    b"<body>Please sign in to tenders.bhel.com</body></html>"
)
HTML_ERROR = (
    b"<html><head><title>404</title></head>"
    b"<body>Document not found</body></html>"
)
# Interstitial labelled as a PDF because the URL looked like a document.
HTML_WITH_PDF_HEADER = HTML_LOGIN


class _FakeResponse:
    def __init__(self, body, content_type="application/pdf"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestClassifyDocumentBody(unittest.TestCase):
    def test_pdf_magic_bytes(self):
        self.assertEqual(classify_document_body(PDF_FIXTURE, "application/pdf"), "pdf")
        self.assertEqual(classify_document_body(PDF_FIXTURE, "application/octet-stream"), "pdf")

    def test_html_doctype_and_tag(self):
        self.assertEqual(classify_document_body(HTML_LOGIN, "text/html"), "html")
        self.assertEqual(classify_document_body(HTML_ERROR, "text/html; charset=utf-8"), "html")

    def test_html_interstitial_with_pdf_content_type_is_still_html(self):
        self.assertEqual(
            classify_document_body(HTML_WITH_PDF_HEADER, "application/pdf"),
            "html",
        )

    def test_empty_is_unknown(self):
        self.assertEqual(classify_document_body(b"", "application/pdf"), "unknown")


class TestLooksLikePdfUrl(unittest.TestCase):
    def test_gem_document_endpoints(self):
        self.assertTrue(looks_like_pdf_url("https://bidplus.gem.gov.in/showbidDocument/123"))
        self.assertTrue(looks_like_pdf_url(
            "https://bidplus.gem.gov.in/showbidDocument/GEM%2F2026%2FB%2F1"
        ))
        self.assertTrue(looks_like_pdf_url("https://bidplus.gem.gov.in/showradocumentPdf/9"))
        self.assertTrue(looks_like_pdf_url("https://files.example.gov.in/rfp.pdf"))

    def test_bhel_listing_pages_are_not_pdfs(self):
        self.assertFalse(looks_like_pdf_url(
            "https://tenders.bhel.com/supply-robotic-cleaning"
        ))
        self.assertFalse(looks_like_pdf_url("https://tenders.bhel.com/tenders"))
        self.assertFalse(looks_like_pdf_url(""))
        self.assertFalse(looks_like_pdf_url(None))


class TestDownloadPdfHttpFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemsentry_doc_")
        self.save_path = os.path.join(self.tmp, "bid.pdf")
        self._real = gem_client._urlopen

    def tearDown(self):
        gem_client._urlopen = self._real
        try:
            os.remove(self.save_path)
        except OSError:
            pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def _install(self, body, content_type):
        def fake_urlopen(request, **kwargs):
            return _FakeResponse(body, content_type)
        gem_client._urlopen = fake_urlopen

    def test_saves_real_pdf(self):
        self._install(PDF_FIXTURE, "application/pdf")
        self.assertTrue(download_pdf_http("https://bidplus.gem.gov.in/showbidDocument/1",
                                          self.save_path, "sid=1"))
        self.assertTrue(os.path.exists(self.save_path))
        with open(self.save_path, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"%PDF"))
        self.assertTrue(is_pdf_file(self.save_path))

    def test_html_login_page_is_not_written_as_pdf(self):
        self._install(HTML_LOGIN, "text/html")
        self.assertFalse(download_pdf_http(
            "https://tenders.bhel.com/bid-number-2030-fire-resistant-fluid",
            self.save_path, "",
        ))
        self.assertFalse(os.path.exists(self.save_path))

    def test_html_with_pdf_content_type_is_not_written(self):
        self._install(HTML_LOGIN, "application/pdf")
        self.assertFalse(download_pdf_http(
            "https://bidplus.gem.gov.in/showbidDocument/1",
            self.save_path, "sid=1",
        ))
        self.assertFalse(os.path.exists(self.save_path))

    def test_is_pdf_file_rejects_html_saved_with_pdf_extension(self):
        with open(self.save_path, "wb") as handle:
            handle.write(HTML_ERROR)
        self.assertFalse(is_pdf_file(self.save_path))
        with open(self.save_path, "wb") as handle:
            handle.write(PDF_FIXTURE)
        self.assertTrue(is_pdf_file(self.save_path))


class TestDownloadRfpPdfPlaywrightPath(unittest.TestCase):
    """Inline Playwright response must not trust Content-Type over magic bytes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemsentry_pw_")
        self.save_path = os.path.join(self.tmp, "bid.pdf")

    def tearDown(self):
        try:
            os.remove(self.save_path)
        except OSError:
            pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def _context(self, body, content_type, status=200):
        response = Mock()
        response.status = status
        response.body.return_value = body
        response.headers = {"content-type": content_type}
        page = Mock()
        page.goto.return_value = response
        context = Mock()
        context.new_page.return_value = page
        return context, page

    def test_html_inline_response_labelled_pdf_is_rejected(self):
        context, page = self._context(HTML_LOGIN, "application/pdf")
        # page.on("download") should not fire
        page.on = Mock()
        self.assertFalse(gem_client.download_rfp_pdf(
            context, "https://tenders.bhel.com/listing", self.save_path,
        ))
        self.assertFalse(os.path.exists(self.save_path))

    def test_real_pdf_inline_response_is_saved(self):
        context, page = self._context(PDF_FIXTURE, "application/pdf")
        page.on = Mock()
        self.assertTrue(gem_client.download_rfp_pdf(
            context, "https://bidplus.gem.gov.in/showbidDocument/1", self.save_path,
        ))
        self.assertTrue(is_pdf_file(self.save_path))


if __name__ == "__main__":
    unittest.main()

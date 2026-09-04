"""TLS client configuration: mahatenders hostname rewrite and verified CA context."""
import os
import ssl
import sys
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.sources import http as http_mod  # noqa: E402
from gemsentry.sources.gepnic import GePNICAdapter  # noqa: E402
from gemsentry.sources.http import (  # noqa: E402
    APEX_ONLY_TLS_HOSTS,
    build_verified_ssl_context,
    canonicalize_request_url,
    fetch_html,
)


class TestHostnameCanonicalization(unittest.TestCase):
    def test_mahatenders_www_is_rewritten_to_apex(self):
        self.assertIn("mahatenders.gov.in", APEX_ONLY_TLS_HOSTS)
        rewritten = canonicalize_request_url(
            "https://www.mahatenders.gov.in/nicgep/app?page=FrontEndListTendersbyDate"
        )
        parsed = urllib.parse.urlparse(rewritten)
        self.assertEqual(parsed.hostname, "mahatenders.gov.in")
        self.assertEqual(parsed.path, "/nicgep/app")
        self.assertIn("FrontEndListTendersbyDate", parsed.query)

    def test_apex_mahatenders_is_unchanged(self):
        url = "https://mahatenders.gov.in/nicgep/app"
        self.assertEqual(canonicalize_request_url(url), url)

    def test_www_is_kept_when_the_cert_covers_it(self):
        """eprocure.gov.in includes www in its SAN; do not rewrite other portals."""
        url = "https://www.eprocure.gov.in/eprocure/app"
        self.assertEqual(canonicalize_request_url(url), url)

    def test_port_and_path_are_preserved(self):
        rewritten = canonicalize_request_url("https://www.mahatenders.gov.in:443/nicgep/app")
        self.assertEqual(urllib.parse.urlparse(rewritten).netloc, "mahatenders.gov.in:443")


class TestVerifiedSslContext(unittest.TestCase):
    def test_default_context_verifies_certificates(self):
        ctx = build_verified_ssl_context(cafile="")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_unverified_opt_out_is_not_the_default(self):
        self.assertEqual(http_mod._UNVERIFIED_CTX.verify_mode, ssl.CERT_NONE)
        self.assertFalse(http_mod._UNVERIFIED_CTX.check_hostname)
        self.assertNotEqual(http_mod._SSL_CTX.verify_mode, ssl.CERT_NONE)

    def test_supplied_cafile_is_used(self):
        cafile = http_mod._certifi_cafile()
        if not cafile:
            self.skipTest("certifi is not installed")
        ctx = build_verified_ssl_context(cafile=cafile)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


class TestFetchHtmlUsesCanonicalUrlAndTimeout(unittest.TestCase):
    def tearDown(self):
        http_mod._urlopen = urllib.request.urlopen

    def test_www_mahatenders_request_is_issued_against_apex(self):
        captured = []

        class _Resp:
            def read(self):
                return b"<html>ok</html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, **kwargs):
            captured.append((request.full_url, kwargs.get("timeout"), kwargs.get("context")))
            return _Resp()

        http_mod._urlopen = fake_urlopen
        html = fetch_html(
            "https://www.mahatenders.gov.in/nicgep/app",
            timeout=9,
            label="state_mh",
        )
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(len(captured), 1)
        url, timeout, context = captured[0]
        self.assertEqual(urllib.parse.urlparse(url).hostname, "mahatenders.gov.in")
        self.assertEqual(timeout, 9)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_verify_tls_false_uses_unverified_context(self):
        captured = []

        class _Resp:
            def read(self):
                return b"x"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, **kwargs):
            captured.append(kwargs.get("context"))
            return _Resp()

        http_mod._urlopen = fake_urlopen
        fetch_html("https://tenders.bhel.com/tenders", verify_tls=False, label="bhel")
        self.assertIs(captured[0], http_mod._UNVERIFIED_CTX)


class TestGePNICHonoursTlsSettings(unittest.TestCase):
    def test_www_mahatenders_app_url_is_apex(self):
        adapter = GePNICAdapter({
            "id": "state_mh",
            "name": "Maharashtra",
            "engine": "gepnic",
            "url": "https://www.mahatenders.gov.in/nicgep/app",
        })
        self.assertEqual(adapter.app_url, "https://mahatenders.gov.in/nicgep/app")
        self.assertTrue(adapter.date_list_url.startswith("https://mahatenders.gov.in/nicgep/app?"))

    def test_verify_tls_defaults_on_and_is_configurable(self):
        on = GePNICAdapter({"id": "state_mh", "url": "https://mahatenders.gov.in/nicgep/app"})
        off = GePNICAdapter({
            "id": "state_mh",
            "url": "https://mahatenders.gov.in/nicgep/app",
            "verify_tls": False,
        })
        self.assertTrue(on.verify_tls)
        self.assertFalse(off.verify_tls)

    def test_fetch_html_receives_verify_tls(self):
        adapter = GePNICAdapter({
            "id": "state_mh",
            "url": "https://mahatenders.gov.in/nicgep/app",
            "verify_tls": True,
        })
        with patch("gemsentry.sources.gepnic.fetch_html", return_value=None) as mocked:
            with patch.object(adapter, "_browser_fetch", return_value=None):
                adapter.fetch_active_tenders(["meter"])
        mocked.assert_called_once()
        _args, kwargs = mocked.call_args
        self.assertTrue(kwargs.get("verify_tls"))


if __name__ == "__main__":
    unittest.main()

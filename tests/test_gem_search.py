"""Keyword URL/payload construction and search timeout/retry behaviour."""
import io
import json
import os
import sys
import socket
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gemsentry.sources.gem import client as gem_client  # noqa: E402
from gemsentry.sources.gem.client import (  # noqa: E402
    build_search_payload,
    encode_search_form,
    fetch_keyword_bids_api,
    gem_document_url,
    normalize_search_keyword,
    search_request_url,
)

# Exact keywords Aakash reported as HTTP 404s.
REPORTED_404_KEYWORDS = (
    "E-GOVERNANCE",
    "NB-IOT METER",
    "LEAD-ACID BATTERY",
    "FLOODED LEAD ACID BATTERY",
    "NI-CD BATTERY",
)


def _decode_search_bid(request):
    form = urllib.parse.parse_qs(request.data.decode("utf-8"))
    payload = json.loads(form["payload"][0])
    return payload["param"]["searchBid"], payload, form


class _FakeJsonResponse:
    def __init__(self, docs=None):
        self._docs = docs if docs is not None else []

    def read(self):
        return json.dumps({"response": {"response": {"docs": self._docs}}}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestKeywordEncoding(unittest.TestCase):
    def test_hyphens_become_word_separators(self):
        self.assertEqual(normalize_search_keyword("E-GOVERNANCE"), "E GOVERNANCE")
        self.assertEqual(normalize_search_keyword("NB-IOT METER"), "NB IOT METER")
        self.assertEqual(normalize_search_keyword("LEAD-ACID BATTERY"), "LEAD ACID BATTERY")
        self.assertEqual(normalize_search_keyword("NI-CD BATTERY"), "NI CD BATTERY")

    def test_already_spaced_keyword_is_unchanged(self):
        self.assertEqual(
            normalize_search_keyword("FLOODED LEAD ACID BATTERY"),
            "FLOODED LEAD ACID BATTERY",
        )

    def test_whitespace_is_collapsed(self):
        self.assertEqual(normalize_search_keyword("  NB-IOT   METER\t"), "NB IOT METER")

    def test_reported_keywords_never_keep_intra_word_hyphens(self):
        for keyword in REPORTED_404_KEYWORDS:
            encoded = normalize_search_keyword(keyword)
            self.assertNotRegex(
                encoded, r"[A-Za-z0-9]-[A-Za-z0-9]",
                f"{keyword!r} still has a Lucene-NOT hyphen: {encoded!r}",
            )

    def test_search_url_is_a_fixed_endpoint_not_a_keyword_path(self):
        url = search_request_url()
        self.assertEqual(url, "https://bidplus.gem.gov.in/all-bids-data")
        for keyword in REPORTED_404_KEYWORDS:
            self.assertNotIn(keyword, url)
            self.assertNotIn(normalize_search_keyword(keyword).replace(" ", "/"), url)

    def test_form_payload_round_trips_for_reported_keywords(self):
        for keyword in REPORTED_404_KEYWORDS:
            payload = build_search_payload(keyword)
            raw = encode_search_form(payload, csrf_token="tok")
            form = urllib.parse.parse_qs(raw.decode("utf-8"))
            parsed = json.loads(form["payload"][0])
            self.assertEqual(parsed["param"]["searchBid"], normalize_search_keyword(keyword))
            self.assertEqual(parsed["param"]["searchType"], "fullText")
            self.assertEqual(form["csrf_bd_gem_nk"], ["tok"])
            # Compact JSON punctuation (spaces inside the search phrase are %20).
            self.assertNotIn(": ", form["payload"][0])
            self.assertNotIn(", ", form["payload"][0])
            self.assertNotIn("+", raw.decode("utf-8"))

    def test_spaces_are_percent_encoded_not_plus(self):
        raw = encode_search_form(build_search_payload("NB-IOT METER")).decode("utf-8")
        self.assertIn("NB%20IOT%20METER", raw)
        self.assertNotIn("NB+IOT+METER", raw)

    def test_bid_numbers_with_slashes_are_path_encoded(self):
        url = gem_document_url("GEM/2026/B/7553726")
        self.assertEqual(
            url,
            "https://bidplus.gem.gov.in/showbidDocument/GEM%2F2026%2FB%2F7553726",
        )
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.path, "/showbidDocument/GEM%2F2026%2FB%2F7553726")
        self.assertEqual(parsed.path.count("/"), 2, "slashes in the bid no must not add path segments")

    def test_numeric_document_ids_stay_unquoted(self):
        self.assertEqual(
            gem_document_url("9737458"),
            "https://bidplus.gem.gov.in/showbidDocument/9737458",
        )

    def test_doc_to_tender_uses_encoded_document_url(self):
        tender = gem_client.doc_to_tender(
            {"id": "GEM/2026/B/1", "b_bid_number": ["GEM/2026/B/1"]},
            "LEAD-ACID BATTERY",
        )
        self.assertIn("%2F", tender["pdf_url"])
        self.assertNotIn("/GEM/2026/", tender["pdf_url"])


class TestSearchTimeoutAndRetry(unittest.TestCase):
    def tearDown(self):
        gem_client._urlopen = urllib.request.urlopen

    def test_timeout_is_passed_to_urlopen(self):
        captured = []

        def fake_urlopen(request, **kwargs):
            captured.append(kwargs.get("timeout"))
            raise TimeoutError("timed out")

        gem_client._urlopen = fake_urlopen
        tenders = fetch_keyword_bids_api(
            "IOT ENERGY METER", "", "", max_pages=1, timeout=7, retries=0, deadline=30,
        )
        self.assertEqual(tenders, [])
        self.assertEqual(captured, [7])

    def test_timeout_retries_then_fails_fast(self):
        calls = {"n": 0}

        def fake_urlopen(request, **kwargs):
            calls["n"] += 1
            raise TimeoutError("timed out after %ss" % kwargs.get("timeout"))

        gem_client._urlopen = fake_urlopen
        tenders = fetch_keyword_bids_api(
            "IOT ENERGY METER", "", "", max_pages=5, timeout=2, retries=1, deadline=30,
        )
        self.assertEqual(tenders, [])
        self.assertEqual(calls["n"], 2, "one retry then stop; do not keep paginating")

    def test_timeout_then_success_returns_docs(self):
        calls = {"n": 0}

        def fake_urlopen(request, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError(socket.timeout("timed out"))
            search_bid, payload, _form = _decode_search_bid(request)
            self.assertEqual(search_bid, "IOT ENERGY METER")
            self.assertEqual(payload["param"]["searchType"], "fullText")
            return _FakeJsonResponse([{
                "id": "1",
                "b_bid_number": ["GEM/2026/B/1"],
                "bd_category_name": ["IOT energy meter"],
            }])

        gem_client._urlopen = fake_urlopen
        tenders = fetch_keyword_bids_api(
            "IOT ENERGY METER", "", "", max_pages=1, timeout=5, retries=1, deadline=30,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(tenders), 1)
        self.assertEqual(tenders[0]["keyword"], "IOT ENERGY METER")

    def test_wall_clock_deadline_stops_pagination(self):
        calls = {"n": 0}

        def fake_urlopen(request, **kwargs):
            calls["n"] += 1
            raise TimeoutError("timed out")

        gem_client._urlopen = fake_urlopen
        tenders = fetch_keyword_bids_api(
            "IOT ENERGY METER", "", "",
            max_pages=30, timeout=20, retries=3, deadline=0,
        )
        self.assertEqual(tenders, [])
        self.assertEqual(calls["n"], 0, "deadline already expired: do not open a socket")

    def test_http_404_does_not_retry_unbounded(self):
        calls = {"n": 0}

        def fake_urlopen(request, **kwargs):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", hdrs={}, fp=io.BytesIO(),
            )

        gem_client._urlopen = fake_urlopen
        tenders = fetch_keyword_bids_api(
            "E-GOVERNANCE", "", "", max_pages=5, timeout=5, retries=2, deadline=30,
        )
        self.assertEqual(tenders, [])
        self.assertEqual(calls["n"], 1)

    def test_posted_body_uses_normalised_keyword(self):
        captured = []

        def fake_urlopen(request, **kwargs):
            captured.append(request)
            return _FakeJsonResponse([])

        gem_client._urlopen = fake_urlopen
        fetch_keyword_bids_api("LEAD-ACID BATTERY", "cookie", "csrf", max_pages=1)
        self.assertEqual(captured[0].full_url, search_request_url())
        search_bid, _payload, _form = _decode_search_bid(captured[0])
        self.assertEqual(search_bid, "LEAD ACID BATTERY")


if __name__ == "__main__":
    unittest.main()

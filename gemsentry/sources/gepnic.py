"""Adapter for NIC GePNIC e-procurement portals.

GePNIC powers defproc.gov.in, eprocurebel.co.in, eprocure.gov.in, the state
portals and many PSU sites. They all expose the same Tapestry "tenders by
date" listing, which is server-rendered HTML -- so the adapter fetches it over
plain HTTP and only escalates to a browser if that comes back empty.
"""

import hashlib
import re
import threading
import urllib.parse
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from gemsentry.constants import logger
from gemsentry.sources.base import BaseAdapter
from gemsentry.sources.http import USER_AGENT, fetch_html, canonicalize_request_url

DATE_LIST_QUERY = "page=FrontEndListTendersbyDate&service=page"
DEFAULT_APP_PATH = "/nicgep/app"

# Tender ids look like 2026_NAVY_781162_1 / 2026_MES_778293_1.
TENDER_ID_RX = re.compile(r"\b20\d{2}_[A-Za-z0-9_]+\b")

# Playwright's sync API is not designed for concurrent use, and each call
# launches a full Chromium. Serialise the fallback so a wide fan-out cannot
# spawn one browser per portal at the same time.
_BROWSER_LOCK = threading.Lock()

# Header text fragment -> logical column name.
_HEADER_HINTS = (
    ("publish", "published"),
    ("closing", "closing"),
    ("submission", "closing"),
    ("opening", "opening"),
    ("title", "title"),
    ("tender id", "title"),
    ("organisation", "org"),
    ("organization", "org"),
)

# Layout used when a table has no usable header row.
_POSITIONAL_COLUMNS = {"published": 1, "closing": 2, "opening": 3, "title": 4, "org": 5}


class GePNICAdapter(BaseAdapter):
    """Fetch active tenders from a GePNIC portal's date listing."""

    implemented = True

    def __init__(self, source_config: Dict[str, Any]):
        super().__init__(source_config)
        self.app_url = self._resolve_app_url(self.url)
        self.date_list_url = f"{self.app_url}?{DATE_LIST_QUERY}"

    @staticmethod
    def _resolve_app_url(configured_url: str) -> str:
        """Return the portal's Tapestry ``.../app`` endpoint.

        The app path is *not* the same on every GePNIC deployment -- it is
        ``/nicgep/app`` on defproc, ``/eprocure/app`` on eprocure.gov.in and
        ``/epublish/app`` on the e-publish portal. Honour whatever the source
        config points at instead of assuming one of them.
        """
        parsed = urllib.parse.urlparse(canonicalize_request_url(configured_url))
        base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/app"):
            return f"{base}{path}"
        return f"{base}{DEFAULT_APP_PATH}"

    def fetch_active_tenders(self, keywords: List[str], max_pages: int = 5) -> List[Dict[str, Any]]:
        keywords_lower = [k.lower().strip() for k in (keywords or []) if k and k.strip()]
        logger.info("[%s] fetching %s", self.source_id, self.date_list_url)

        html = fetch_html(self.date_list_url, label=self.source_id, verify_tls=self.verify_tls)
        tenders = self.parse_listing(html, keywords_lower) if html else []

        if not tenders:
            html = self._browser_fetch()
            if html:
                tenders = self.parse_listing(html, keywords_lower)

        logger.info("[%s] %d matching tender(s) for %s", self.source_id, len(tenders), keywords_lower or "*")
        return tenders

    @property
    def verify_tls(self) -> bool:
        """Per-source TLS opt-out; default on. See HtmlTableAdapter.verify_tls."""
        return bool(self.config.get("verify_tls", True))

    def _browser_fetch(self) -> Optional[str]:
        """Render the listing in Chromium; the 'closing soon' tabs need JS."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("[%s] playwright unavailable for browser fallback", self.source_id)
            return None

        with _BROWSER_LOCK:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    try:
                        page = browser.new_page(user_agent=USER_AGENT)
                        page.goto(
                            canonicalize_request_url(self.date_list_url),
                            timeout=30000,
                            wait_until="domcontentloaded",
                        )
                        page.wait_for_timeout(1000)
                        for label in ("Closing within 14 days", "Closing within 7 days", "Closing Today"):
                            if page.query_selector(f"text={label}"):
                                logger.info("[%s] clicking '%s'", self.source_id, label)
                                page.click(f"text={label}")
                                page.wait_for_timeout(2000)
                                break
                        return page.content()
                    finally:
                        browser.close()
            except Exception as exc:
                logger.warning("[%s] browser fallback failed: %s", self.source_id, exc)
                return None

    # -- parsing ---------------------------------------------------------

    def parse_listing(self, html: str, keywords_lower: List[str]) -> List[Dict[str, Any]]:
        """Parse a GePNIC date-listing table into normalized tenders."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        table = self._find_listing_table(soup)
        if table is None:
            logger.debug("[%s] no listing table found in response", self.source_id)
            return []

        columns, header_row = self._column_map(table)
        tenders: List[Dict[str, Any]] = []
        seen: set = set()

        for row in table.find_all("tr"):
            # Some deployments (MP) build the header out of <td>, so it would
            # otherwise sail past the cell-count check and be ingested as a
            # tender titled "Title and Ref.No./Tender ID".
            if row is header_row:
                continue
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            title_cell = self._cell(cells, columns, "title")
            org_cell = self._cell(cells, columns, "org")
            raw_title = title_cell.get_text(separator=" ", strip=True) if title_cell else ""
            if not raw_title:
                continue

            org_chain = org_cell.get_text(separator=" ", strip=True) if org_cell else self.source_name
            if not self.matches_keywords(f"{raw_title} {org_chain}", keywords_lower):
                continue

            tender_id = self._tender_id(raw_title)
            if tender_id in seen:
                continue
            seen.add(tender_id)

            tenders.append(
                self.normalize_tender(
                    tender_id=tender_id,
                    title=self._clean_title(raw_title) or raw_title,
                    buyer_org=org_chain,
                    closing_date=self._text(cells, columns, "closing"),
                    published_date=self._text(cells, columns, "published"),
                    url=self._row_url(title_cell, row),
                )
            )

        return tenders

    def _find_listing_table(self, soup: BeautifulSoup):
        table = soup.find("table", {"id": "table"}) or soup.find("table", {"class": "list_table"})
        if table is not None:
            return table
        for candidate in soup.find_all("table"):
            text = candidate.get_text()
            if "Title" in text and "Date" in text:
                return candidate
        return None

    def _column_map(self, table):
        """Find the header row and map logical column names to indexes.

        Returns ``(column_map, header_row)``; ``header_row`` is ``None`` when
        no header could be identified and fixed positions are used instead.

        Column order differs between GePNIC deployments, and so does the
        markup: most portals use ``<th>`` but some (Madhya Pradesh) build the
        header row out of plain ``<td>``. Both are matched on the label text.
        """
        for row in table.find_all("tr")[:3]:
            cells = row.find_all("th") or row.find_all("td")
            if len(cells) < 4:
                continue
            mapping: Dict[str, int] = {}
            for index, cell in enumerate(cells):
                label = cell.get_text(" ", strip=True).lower()
                for hint, name in _HEADER_HINTS:
                    if hint in label and name not in mapping:
                        mapping[name] = index
            # Require several distinct hits: one stray keyword inside a real
            # tender title must not make a data row look like the header.
            if "title" in mapping and len(mapping) >= 3:
                return mapping, row
        return dict(_POSITIONAL_COLUMNS), None

    @staticmethod
    def _cell(cells, columns: Dict[str, int], name: str):
        index = columns.get(name)
        if index is None or index >= len(cells):
            return None
        return cells[index]

    def _text(self, cells, columns: Dict[str, int], name: str) -> str:
        cell = self._cell(cells, columns, name)
        return cell.get_text(strip=True) if cell else ""

    def _tender_id(self, raw_title: str) -> str:
        match = TENDER_ID_RX.search(raw_title)
        if match:
            return match.group(0)
        # Synthetic id for rows the portal did not tag. Must be a content
        # digest, not hash(): str hashing is salted per process, so hash()
        # would mint a fresh id on every restart and defeat de-duplication.
        digest = hashlib.sha1(raw_title.encode("utf-8")).hexdigest()[:12]
        return f"{self.source_id}_{digest}"

    @staticmethod
    def _clean_title(raw_title: str) -> str:
        return TENDER_ID_RX.sub("", raw_title).strip(" []-|")

    def _row_url(self, title_cell, row) -> str:
        link = (title_cell.find("a") if title_cell else None) or row.find("a")
        href = link.get("href") if link else None
        url = urllib.parse.urljoin(self.app_url, href) if href else self.url
        return canonicalize_request_url(url)

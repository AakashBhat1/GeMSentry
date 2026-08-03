"""Base class for portals that publish tenders as a plain HTML table.

Several portals (ISRO, BHEL, ...) render their listing server-side as one
table with stable headers. Those adapters differ only in *where* the table is
and *which column means what*, so the fetch/find/iterate/normalize loop lives
here and subclasses supply a small amount of declarative configuration.
"""

from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from gemsentry.constants import logger
from gemsentry.sources.base import BaseAdapter
from gemsentry.sources.http import fetch_html


class HtmlTableAdapter(BaseAdapter):
    """Fetch one listing page and turn each table row into a tender."""

    implemented = True

    #: Path appended to the portal root, or an absolute URL. Subclass sets one.
    listing_path: str = ""
    #: ``id`` of the listing table, when the portal gives it one.
    table_id: Optional[str] = None
    #: CSS class on the listing table, used when ``table_id`` is absent.
    table_class: Optional[str] = None
    #: Minimum ``<td>`` count for a row to be considered data rather than chrome.
    min_cells: int = 3

    def listing_url(self) -> str:
        if self.listing_path.startswith("http"):
            return self.listing_path
        return f"{self.url.rstrip('/')}/{self.listing_path.lstrip('/')}" if self.listing_path else self.url

    def fetch_active_tenders(self, keywords: List[str], max_pages: int = 5) -> List[Dict[str, Any]]:
        keywords_lower = [k.lower().strip() for k in (keywords or []) if k and k.strip()]
        url = self.listing_url()
        logger.info("[%s] fetching %s", self.source_id, url)

        html = fetch_html(url, label=self.source_id, verify_tls=self.verify_tls)
        tenders = self.parse_listing(html, keywords_lower)
        logger.info("[%s] %d matching tender(s) for %s",
                    self.source_id, len(tenders), keywords_lower or "*")
        return tenders

    @property
    def verify_tls(self) -> bool:
        """Per-source TLS opt-out for portals with a broken certificate chain."""
        return bool(self.config.get("verify_tls", True))

    # -- parsing ---------------------------------------------------------

    def find_table(self, soup: BeautifulSoup):
        if self.table_id:
            table = soup.find("table", {"id": self.table_id})
            if table is not None:
                return table
        if self.table_class:
            table = soup.find("table", class_=self.table_class)
            if table is not None:
                return table
        # Last resort: whichever table has the most rows.
        tables = [t for t in soup.find_all("table") if len(t.find_all("tr")) > 1]
        return max(tables, key=lambda t: len(t.find_all("tr")), default=None)

    def parse_listing(self, html: Optional[str], keywords_lower: List[str]) -> List[Dict[str, Any]]:
        if not html:
            return []

        table = self.find_table(BeautifulSoup(html, "html.parser"))
        if table is None:
            logger.debug("[%s] no listing table in response", self.source_id)
            return []

        tenders: List[Dict[str, Any]] = []
        seen = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < self.min_cells:
                continue
            try:
                tender = self.row_to_tender(cells, row)
            except Exception as exc:
                logger.debug("[%s] skipping unparseable row: %s", self.source_id, exc)
                continue
            if not tender or not tender.get("tender_id"):
                continue
            if not self.matches_keywords(self.row_haystack(tender), keywords_lower):
                continue
            if tender["tender_id"] in seen:
                continue
            seen.add(tender["tender_id"])
            tenders.append(tender)
        return tenders

    def row_haystack(self, tender: Dict[str, Any]) -> str:
        return f"{tender.get('title', '')} {tender.get('buyer_org', '')}"

    def row_to_tender(self, cells, row) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def cell_text(cells, index: int) -> str:
        if index >= len(cells):
            return ""
        return cells[index].get_text(" ", strip=True)

    def cell_link(self, cells, index: int) -> str:
        if index >= len(cells):
            return ""
        link = cells[index].find("a")
        href = link.get("href") if link else None
        if not href:
            return ""
        from urllib.parse import urljoin
        return urljoin(self.listing_url(), href)

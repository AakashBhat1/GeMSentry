"""Adapter contract shared by every non-GeM procurement portal."""

import datetime
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from gemsentry.constants import logger


def squash(value: Optional[str]) -> str:
    """Trim and collapse every internal whitespace run to a single space."""
    return " ".join((value or "").split())


class BaseAdapter(ABC):
    """One adapter instance per configured portal source.

    Subclasses implement :meth:`fetch_active_tenders` and return records built
    by :meth:`normalize_tender` so the ingest path sees one uniform schema
    regardless of which portal a tender came from.
    """

    def __init__(self, source_config: Dict[str, Any]):
        self.source_id: str = source_config.get("id", "unknown")
        self.source_name: str = source_config.get("name", "Unknown Source")
        self.url: str = source_config.get("url", "")
        self.category: str = source_config.get("category", "general")
        self.engine: str = source_config.get("engine", "generic")
        self.enabled: bool = source_config.get("enabled", True)
        self.config: Dict[str, Any] = source_config

    @abstractmethod
    def fetch_active_tenders(self, keywords: List[str], max_pages: int = 5) -> List[Dict[str, Any]]:
        """Return normalized tender dicts matching any of ``keywords``."""

    def normalize_tender(
        self,
        tender_id: str,
        title: str,
        buyer_org: str,
        closing_date: str = "",
        est_value: float = 0.0,
        emd_amount: float = 0.0,
        url: str = "",
        pdf_url: str = "",
        published_date: str = "",
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format one portal row into GeMSentry's standard external schema."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "tender_id": squash(tender_id),
            # Portal markup wraps freely, so collapse internal whitespace too --
            # a stray newline would otherwise end up in the stored title and in
            # every keyword match run against it.
            "title": squash(title),
            "buyer_org": squash(buyer_org),
            "closing_date": squash(closing_date),
            "published_date": squash(published_date) or now_str,
            "est_value": est_value,
            "emd_amount": emd_amount,
            "url": url or self.url,
            "pdf_url": pdf_url,
            "scraped_at": now_str,
            "raw_data": raw_data or {},
        }

    @staticmethod
    def matches_keywords(text: str, keywords_lower: List[str]) -> bool:
        """Empty keyword list means 'accept everything'."""
        if not keywords_lower:
            return True
        haystack = (text or "").lower()
        return any(kw in haystack for kw in keywords_lower)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.source_id} enabled={self.enabled}>"


class UnsupportedAdapter(BaseAdapter):
    """Placeholder for a configured portal whose engine has no implementation.

    It never runs -- the registry filters these out before dispatching work --
    but it keeps the source visible in the dashboard's source list.
    """

    implemented = False

    def fetch_active_tenders(self, keywords: List[str], max_pages: int = 5) -> List[Dict[str, Any]]:
        logger.debug("[%s] engine '%s' has no adapter yet; skipping.", self.source_id, self.engine)
        return []

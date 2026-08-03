"""Adapter for the ISRO e-procurement portal (eproc.isro.gov.in).

The landing page server-renders every live tender into ``table#tenderListTable``
with a stable six-column layout, so no browser or session is needed:

    Tender No | Centre Name | Tender Description
              | Bid Closing Date (IST) | Bid Opening Date (IST) | Actions
"""

from typing import Any, Dict, List, Optional

from gemsentry.sources.table_adapter import HtmlTableAdapter

TENDER_NO, CENTRE, DESCRIPTION, CLOSING, OPENING, ACTIONS = range(6)


class ISROAdapter(HtmlTableAdapter):
    """Live tenders across all ISRO centres (URSC, SAC, VSSC, LPSC, ...)."""

    listing_path = "home.html"
    table_id = "tenderListTable"
    min_cells = 5

    def row_to_tender(self, cells, row) -> Optional[Dict[str, Any]]:
        tender_no = self.cell_text(cells, TENDER_NO)
        description = self.cell_text(cells, DESCRIPTION)
        if not tender_no or not description:
            return None

        centre = self.cell_text(cells, CENTRE)
        return self.normalize_tender(
            tender_id=tender_no,
            title=description,
            # Centre names are acronyms (URSC, SAC); qualify them so buyer
            # matching and the dashboard both stay readable.
            buyer_org=f"ISRO - {centre}" if centre else "ISRO",
            closing_date=self.cell_text(cells, CLOSING),
            url=self.cell_link(cells, ACTIONS) or self.listing_url(),
            raw_data={
                "centre": centre,
                "bid_opening_date": self.cell_text(cells, OPENING),
            },
        )

    def row_haystack(self, tender: Dict[str, Any]) -> str:
        centre = (tender.get("raw_data") or {}).get("centre", "")
        return f"{tender.get('title', '')} {tender.get('buyer_org', '')} {centre}"


__all__: List[str] = ["ISROAdapter"]

"""Adapter for the BHEL tender portal (tenders.bhel.com).

``bhel.com/tenders`` only shows a three-row teaser; the real listing lives on
``tenders.bhel.com/tenders`` as a Drupal view table:

    NIT Number | Tender Description | Unit | Tender Opening Date

The description cell is not plain text -- it packs four labelled fields into
one string, with the clean subject line repeated as the anchor text::

    Tender NIT Number : NIT_180563
    Tender Notification Number : SHAPV00037 [GEM/2026/B/7865734]
    Tender Description : Supply of Semi-Automatic Dry Robotic Cleaning System...
    Date of Notification : 03-08-2026 07:00:00 PM

So the title comes from the anchor and the rest is unpacked into ``raw_data``.
The bracketed GeM reference, when present, means the same tender is also on
GeM -- kept for future cross-portal de-duplication.

One portal quirk: the host serves a self-signed certificate chain, so the
source config opts this one portal out of TLS verification.
"""

import re
from typing import Any, Dict, List, Optional

from gemsentry.sources.table_adapter import HtmlTableAdapter

NIT_NUMBER, DESCRIPTION, UNIT, OPENING_DATE = range(4)

# "Label : value" pairs inside the description cell, non-greedy up to the next
# known label so a value containing a colon does not truncate the match.
_FIELD_RX = re.compile(
    r"(Tender NIT Number|Tender Notification Number|Tender Description|Date of Notification)"
    r"\s*:\s*(.*?)(?=\s*(?:Tender NIT Number|Tender Notification Number|"
    r"Tender Description|Date of Notification)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)

_GEM_REF_RX = re.compile(r"\[?(GEM/\d{4}/[A-Z]/\d+)\]?", re.IGNORECASE)


def parse_description_cell(text: str) -> Dict[str, str]:
    """Unpack the packed 'Label : value' description cell into a dict."""
    fields = {}
    for label, value in _FIELD_RX.findall(text or ""):
        fields[label.strip().lower()] = " ".join(value.split())
    return fields


class BHELAdapter(HtmlTableAdapter):
    """Live tenders across BHEL units (Trichy, Ranipet, SBD, ISG Bangalore...)."""

    listing_path = "https://tenders.bhel.com/tenders"
    table_class = "views-table"
    min_cells = 4

    def row_to_tender(self, cells, row) -> Optional[Dict[str, Any]]:
        nit_number = self.cell_text(cells, NIT_NUMBER)
        raw_description = self.cell_text(cells, DESCRIPTION)
        if not nit_number or not raw_description:
            return None

        fields = parse_description_cell(raw_description)

        # The anchor text is the clean subject; the packed cell is the fallback.
        link = cells[DESCRIPTION].find("a") if DESCRIPTION < len(cells) else None
        title = (link.get_text(" ", strip=True) if link else "") \
            or fields.get("tender description") \
            or raw_description
        if not title:
            return None

        notification_number = fields.get("tender notification number", "")
        gem_ref = _GEM_REF_RX.search(notification_number)
        unit = self.cell_text(cells, UNIT)

        return self.normalize_tender(
            tender_id=f"BHEL_{nit_number}",
            title=title,
            buyer_org=unit or "BHEL",
            # The listing's "Tender Opening Date" runs ~10 days after the
            # notification date, i.e. it is when bids are opened rather than
            # when the tender was published -- so it is the operative deadline.
            closing_date=self.cell_text(cells, OPENING_DATE),
            published_date=fields.get("date of notification", ""),
            url=self.cell_link(cells, DESCRIPTION) or self.listing_url(),
            raw_data={
                "nit_number": nit_number,
                "unit": unit,
                "notification_number": notification_number,
                "gem_reference": gem_ref.group(1) if gem_ref else "",
            },
        )

    def row_haystack(self, tender: Dict[str, Any]) -> str:
        raw = tender.get("raw_data") or {}
        return " ".join([
            tender.get("title", ""),
            tender.get("buyer_org", ""),
            raw.get("notification_number", ""),
        ])


__all__: List[str] = ["BHELAdapter", "parse_description_cell"]

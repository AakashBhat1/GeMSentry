"""Whole-document bid signal extraction."""

import re

from gemsentry.parsing.fields import parse_yes_no_field
from gemsentry.parsing.text import _first_ascii_phrase, _first_number, _window_after
from gemsentry.textutils import _match_indian_state, _parse_inr_amount


def extract_bid_signals(text_clean, card_meta=None):
    """
    Extract Phase-2 bid signals from full PDF text (+ optional card meta).
    Returns (signals_dict, signal_parsed_flags).
    BE-16: prefer card_meta est_value_inr; do not scrape disclaimer prose.
    """
    card_meta = card_meta or {}
    flags = {
        "est_value_inr": False,
        "primary_item": False,
        "item_category": False,
        "buyer_org": False,
        "buyer_dept": False,
        "consignee_state": False,
        "mii_required": False,
        "mse_pref": False,
    }
    signals = {
        "est_value_inr": None,
        "est_value_estimated": False,
        "est_value_source": None,
        "primary_item": None,
        "item_category": None,
        "buyer_org": None,
        "buyer_dept": None,
        "consignee_state": None,
        "mii_required": "unknown",
        "mse_pref": "unknown",
        "rfp_min_turnover_inr": None,
        "rfp_min_experience_years": None,
        "total_quantity": None,
    }

    # --- BE-16: estimated value from card first ---
    card_val = card_meta.get("est_value_inr")
    if card_val is not None:
        amount = _parse_inr_amount(card_val)
        if amount is not None and amount > 0:
            signals["est_value_inr"] = amount
            flags["est_value_inr"] = True
    if signals["est_value_inr"] is None:
        # Fallback: only accept a number that sits RIGHT AFTER the label
        # (not the disclaimer "Estimated Bid Value indicated above…")
        snip, m = _window_after(
            text_clean,
            r'Estimated\s+Bid\s+Value(?:\s+in\s+INR(?:\s*\([^)]*\))?)?',
            window=30
        )
        if m is not None and snip:
            # Reject disclaimer continuation words
            if not re.match(r'\s*(indicated|is\s+being|declared|solely|above)', snip, re.IGNORECASE):
                num = _first_number(snip)
                if num:
                    amount = _parse_inr_amount(num)
                    # Sanity: GeM bid values are typically >= 1000 INR
                    if amount is not None and amount >= 1000:
                        signals["est_value_inr"] = amount
                        flags["est_value_inr"] = True

    # Item category / primary item (BE-15). The current GeM listing JSON
    # exposes these fields directly, so preserve that structured evidence
    # before falling back to PDF label parsing.
    card_category = str(card_meta.get("item_category") or "").strip()
    card_primary = str(card_meta.get("primary_item") or "").strip()
    if card_category and card_category.upper() not in {"N/A", "NA", "NIL"}:
        signals["item_category"] = card_category
        flags["item_category"] = True
    if card_primary and card_primary.upper() not in {"N/A", "NA", "NIL"}:
        signals["primary_item"] = card_primary
        flags["primary_item"] = True

    stop_item = (
        "GeMARPTS", "MSE Exemption", "Startup Exemption", "Minimum Average",
        "Bidder", "Total Quantity", "Bid Number", "EMD Detail", "ePBG Detail",
        "EMD Amount", "Bid End", "Ministry/State"
    )
    snip, _ = _window_after(text_clean, r'Item\s+Category', window=200)
    if not signals["item_category"] and snip:
        cat = _first_ascii_phrase(snip, max_len=120, stop_words=stop_item)
        # Reject bare numbers / single-char noise (e.g. "1 , 2 , 3" category lists)
        if cat and len(cat) >= 3 and not re.match(r'^[\d\s,]+$', cat):
            signals["item_category"] = cat
            flags["item_category"] = True
            if not signals["primary_item"]:
                signals["primary_item"] = cat.split(",")[0].strip()[:100]
                flags["primary_item"] = True
    if not signals["primary_item"] and card_meta.get("title"):
        signals["primary_item"] = str(card_meta["title"])[:120]
        flags["primary_item"] = True

    # Total quantity
    snip, _ = _window_after(text_clean, r'Total\s+Quantity', window=40)
    if snip:
        num = _first_number(snip)
        if num:
            try:
                signals["total_quantity"] = int(float(num.replace(",", "")))
            except ValueError:
                pass

    # Ministry / Department / Organisation (BE-15)
    stop_min = ("Department", "Organisation", "Organization", "Office")
    snip, _ = _window_after(text_clean, r'Ministry/State\s+Name', window=120)
    ministry = _first_ascii_phrase(snip, max_len=90, stop_words=stop_min) if snip else None

    snip, _ = _window_after(text_clean, r'Department\s+Name', window=120)
    dept = _first_ascii_phrase(snip, max_len=90, stop_words=("Organisation", "Organization", "Office")) if snip else None

    snip, _ = _window_after(text_clean, r'Organisation\s+Name', window=120)
    org = _first_ascii_phrase(snip, max_len=90, stop_words=("Office", "Total Quantity", "Item Category")) if snip else None

    if org and org.upper() not in ("N/A", "NA", "NIL", "***", "****"):
        signals["buyer_org"] = org.upper()
        flags["buyer_org"] = True
    elif card_meta.get("department") and str(card_meta["department"]).strip() not in ("", "N/A"):
        # Card department often is "Ministry | Department | Org" — take last segment
        raw = str(card_meta["department"]).strip()
        parts = [p.strip() for p in re.split(r'\s*\|\s*', raw) if p.strip()]
        signals["buyer_org"] = (parts[-1] if parts else raw).upper()
        flags["buyer_org"] = True
        if len(parts) >= 2:
            signals["buyer_dept"] = parts[1].upper() if len(parts) > 1 else parts[0].upper()
            flags["buyer_dept"] = True

    if dept:
        signals["buyer_dept"] = dept.upper()
        flags["buyer_dept"] = True
    if not signals["buyer_dept"] and ministry:
        signals["buyer_dept"] = ministry.upper()
        flags["buyer_dept"] = True
        if not signals["buyer_org"]:
            signals["buyer_org"] = ministry.upper()
            flags["buyer_org"] = True

    # Consignee state
    cons_match = re.search(r'Consignee.{0,400}', text_clean, re.IGNORECASE)
    state = None
    if cons_match:
        state = _match_indian_state(cons_match.group(0))
    if not state and ministry:
        state = _match_indian_state(ministry)
    if not state:
        state = _match_indian_state(text_clean[:3000])
    if state:
        signals["consignee_state"] = state
        flags["consignee_state"] = True

    # MII / MSE purchase preference
    mii = parse_yes_no_field(text_clean, [r'MII\s+Purchase\s+Preference'], window=80)
    if mii:
        signals["mii_required"] = mii
        flags["mii_required"] = True
    mse_pref = parse_yes_no_field(text_clean, [r'MSE\s+Purchase\s+Preference'], window=80)
    if mse_pref:
        signals["mse_pref"] = mse_pref
        flags["mse_pref"] = True

    # Min turnover / experience (eligibility inputs only)
    # Prefer explicit "Minimum Average Annual Turnover of the bidder (For 3 Years)  X"
    snip, m = _window_after(
        text_clean,
        r'Minimum\s+Average\s+Annual\s+Turnover(?:\s+of\s+the\s+bidder)?',
        window=80
    )
    if not m:
        snip, m = _window_after(
            text_clean,
            r'Average\s+Annual\s+Turnover(?:\s+of\s+the\s+bidder)?',
            window=80
        )
    if snip:
        num = _first_number(snip)
        if num:
            amount = _parse_inr_amount(num)
            # Turnover criteria on GeM are usually >= 10,000 INR (ignore year counts)
            if amount is not None and amount >= 10000:
                signals["rfp_min_turnover_inr"] = amount

    snip, m = _window_after(
        text_clean,
        r'Minimum\s+(?:Years?\s+of\s+)?(?:Past\s+)?Experience|Years\s+of\s+Past\s+Experience',
        window=40
    )
    if snip:
        num = _first_number(snip)
        if num:
            try:
                yrs = int(float(num.replace(",", "")))
                if 1 <= yrs <= 50:
                    signals["rfp_min_experience_years"] = yrs
            except ValueError:
                pass

    return signals, flags

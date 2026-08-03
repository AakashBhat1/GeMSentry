"""Business-line fit scoring and omnibus dilution."""

import re

from gemsentry.textmatch import keyword_hit
from gemsentry.defaults import DEFAULT_SCORING_CONFIG
from gemsentry.scoring.dates import _linear_ramp
from gemsentry.scoring.verdict import build_score_breakdown


def split_bid_items(title, primary_item, item_category):
    """
    Split an omnibus GeM bid into its individual line items.

    Buyers routinely bundle 20-30 unrelated items into one bid and GeM renders
    them comma-separated in both the title and item_category. Whichever source
    yields more items wins, since the title is often truncated.

    Returns [] when the bid is not a multi-item list (nothing to dilute).
    """
    best = []
    for source in (item_category, title):
        text = str(source or "").strip()
        if not text:
            continue
        parts = [p.strip(" \t,-") for p in text.split(",")]
        parts = [p for p in parts if p]
        if len(parts) > len(best):
            best = parts
    return best if len(best) > 1 else []


def _apply_omnibus_dilution(best_score, best_line, best_matched, items, fit_cfg,
                            kw_hit):
    """
    Guard against incidental matches inside bundled multi-item bids (BE-30).

    A 30-item bid whose *subject* is unrelated to us does not become relevant
    because one buried line item happens to contain a keyword — that is how a
    bagpipe bid ("Bagpipe Plastic Drone"), a library book list ("Drone
    Engineering") and a gym-equipment bid all scored as strong matches.

    The signal that separates them from genuine bundles is the **primary item**:
    a real drone bid leads with "UAV FRAME", a bagpipe bid leads with "Bagpipe
    Chanter Reed". So we only dilute when the lead item matches nothing *and*
    the matching items are a small minority.

    Returns (score, detail_suffix).
    """
    if not items or best_line is None or best_score <= 0 or not best_matched:
        return best_score, ""

    min_items = int(fit_cfg.get("omnibus_min_items", 6))
    min_ratio = float(fit_cfg.get("omnibus_min_match_ratio", 0.34))
    if len(items) < min_items:
        return best_score, ""

    matched_terms = [m.lower() for m in best_matched]
    primary_matches = any(kw_hit(term, items[0].lower()) for term in matched_terms)
    if primary_matches:
        return best_score, ""

    hit_items = sum(
        1 for it in items
        if any(kw_hit(term, it.lower()) for term in matched_terms)
    )
    ratio = hit_items / float(len(items))
    if ratio >= min_ratio:
        return best_score, ""

    # Graded by how thin the evidence is. A 46-item gym bid with one incidental
    # hit is not "weakly relevant", it is irrelevant — demoting such a bid only
    # to weak still cleared the fit gate. Bids near the ratio keep a weak score
    # so a genuine bundle whose subject sits mid-list still surfaces for review.
    weak_rel = float(fit_cfg.get("weak_relevance_subscore", 0.5))
    if ratio < min_ratio / 2.0:
        diluted = 0.0
    else:
        diluted = 0.0 if best_score <= weak_rel else weak_rel
    return diluted, (
        f" Diluted: bundled bid of {len(items)} items — lead item "
        f"'{items[0][:40]}' matches nothing and only {hit_items} item(s) "
        f"({ratio:.0%}) match."
    )


def compute_fit_score(analysis, signals, eligibility, profile, cfg, card_meta=None):
    """
    Company-aware Fit score 0-100 (BE-10).
    Returns (fit_score, fit_breakdown, business_line_dict|None).
    """
    card_meta = card_meta or {}
    fit_cfg = cfg.get("fit") or DEFAULT_SCORING_CONFIG.get("fit", {})
    fit_weights = fit_cfg.get("weights") or DEFAULT_SCORING_CONFIG["fit"]["weights"]
    unknown_sub = float(cfg.get("unknown_subscore", 0.5))
    weak_rel = float(fit_cfg.get("weak_relevance_subscore", 0.5))
    unknown_buyer = float(fit_cfg.get("unknown_buyer_subscore", 0.4))
    gap_sub = float(fit_cfg.get("turnover_gap_subscore", 0.3))

    # --- relevance ---
    # BE-20: match against bid CONTENT only (title + primary_item + item_category).
    # Do NOT include card_meta["keyword"] — that is the fuzzy GeM SEARCH TERM and
    # causes circular false matches (e.g. Manpower bid found under "POWER SUPPLY").
    title = str(card_meta.get("title") or "")
    haystack = " ".join([
        title,
        str(signals.get("primary_item") or ""),
        str(signals.get("item_category") or ""),
    ]).lower()

    # The bid's lead item is its actual subject; used to break ties between
    # business lines that score equally (a CCTV bid listing a UPS and a power
    # supply must file under AI/IT, not Power Supply — list order is not a
    # ranking).
    _lead_items = split_bid_items(
        title, signals.get("primary_item"), signals.get("item_category")
    )
    lead_text = (_lead_items[0] if _lead_items
                 else str(signals.get("primary_item") or title)).lower()

    acronym_len = int(fit_cfg.get("lone_acronym_max_len", 3))
    candidates = []  # (score, lead_match, hits, line, matched)
    suppressed = None  # (label, [exclusion terms]) when a keyword match was vetoed
    cross_line_hits = set()  # distinct keywords matched across all non-vetoed lines
    for line in profile.get("business_lines") or []:
        kws = line.get("keywords") or []
        matched = [kw for kw in kws if keyword_hit(kw, haystack)]
        hits = len(matched)
        # Negative/exclusion keywords: presence signals a non-fit context
        # (e.g. "manpower supply for power plant" hitting a product line).
        excludes = line.get("exclude_keywords") or []
        excluded = [kw for kw in excludes if keyword_hit(kw, haystack)]
        if excluded:
            if hits and suppressed is None:
                suppressed = (line.get("label"), excluded)
            continue  # vetoed: this line cannot be the relevance match
        cross_line_hits.update(kw.lower() for kw in matched)
        # Strong keywords: unambiguous product terms ("drone", "cctv") count as
        # a full match even alone — a title saying "drone" IS a drone bid.
        strong_kws = {k.lower() for k in (line.get("strong_keywords") or [])}
        strong_hit = any(kw.lower() in strong_kws for kw in matched)
        # A lone short acronym is not evidence: "CRM" is Certified Reference
        # Material on a chemicals bid, "PCB" is polychlorinated biphenyl, "UPS"
        # is a courier. Unambiguous acronyms are declared strong_keywords
        # ("uav"), so those are exempt.
        lone_acronym = (
            hits == 1 and not strong_hit
            and len(matched[0].strip()) <= acronym_len
        )
        if hits >= 2 or strong_hit:
            s = 1.0
        elif hits == 1 and not lone_acronym:
            s = weak_rel
        else:
            s = 0.0
        priority = float(line.get("priority", 1.0) or 1.0)
        s = min(1.0, s * priority)
        if s > 0:
            lead_match = any(keyword_hit(kw, lead_text) for kw in matched)
            candidates.append((s, lead_match, hits, line, matched))

    # Rank: score, then whether the line explains the bid's lead item, then
    # depth of evidence. Ties previously fell to profile order.
    best_score, best_line, best_matched = 0.0, None, []
    if candidates:
        s, _lead, _hits, line, matched = max(
            candidates, key=lambda c: (c[0], c[1], c[2])
        )
        best_score, best_line, best_matched = s, line, matched

    # Cross-line corroboration: two distinct product keywords are strong
    # evidence even when they sit in different business lines ("camera drone"
    # hits Drone and AI/IT once each — the same strength as two hits in one
    # line). Upgrade the weak single-line match.
    if best_line is not None and 0 < best_score < 1.0 and len(cross_line_hits) >= 2:
        priority = float(best_line.get("priority", 1.0) or 1.0)
        best_score = min(1.0, 1.0 * priority)
        best_matched = sorted(cross_line_hits)

    # Omnibus dilution (BE-30): applied after corroboration so a bundled bid
    # cannot be rescued by two incidental matches in different business lines.
    bid_items = split_bid_items(
        title, signals.get("primary_item"), signals.get("item_category")
    )
    best_score, dilution_note = _apply_omnibus_dilution(
        best_score, best_line, best_matched, bid_items, fit_cfg, keyword_hit
    )
    if best_score <= 0:
        best_line = None

    matched_note = f" [matched: {', '.join(best_matched)}]" if best_matched else ""
    suppressed_note = ""
    if best_line is None and suppressed is not None:
        suppressed_note = (
            f" Match for {suppressed[0]} vetoed by exclusion terms: "
            f"{', '.join(suppressed[1])}."
        )

    # Q2 avoid soft penalty on relevance
    avoid = profile.get("avoid_rules") or {}
    if avoid.get("gem_q2_category") and re.search(r'\(Q2\)', haystack, re.IGNORECASE):
        best_score *= 0.7
        if best_score > 0:
            rel_detail = f"Matched business line with Q2 soft penalty; subscore={best_score:.3f}.{matched_note}"
        else:
            rel_detail = f"No business-line match; Q2 category present.{suppressed_note}"
    else:
        if best_line and best_score >= 1.0:
            rel_detail = f"Strong match: {best_line.get('label')}.{matched_note}"
        elif best_line and best_score > 0:
            rel_detail = f"Weak match: {best_line.get('label')}.{matched_note}{dilution_note}"
        else:
            rel_detail = (f"No business-line keyword match."
                          f"{matched_note}{dilution_note}{suppressed_note}")

    # --- serviceability ---
    soft_states = [s.lower() for s in (profile.get("serviceability") or {}).get("soft_avoid_states") or []]
    soft_pen = float((profile.get("serviceability") or {}).get("soft_avoid_penalty", 0.5))
    state = signals.get("consignee_state")
    if not state:
        svc_sub = unknown_sub
        svc_detail = f"Consignee state unknown; subscore={unknown_sub}."
    elif state.lower() in soft_states:
        svc_sub = soft_pen
        svc_detail = f"Soft-avoid state {state}; penalty subscore={soft_pen}."
    else:
        svc_sub = 1.0
        svc_detail = f"Serviceable state {state}."

    # --- value fit ---
    vp = profile.get("value_preference") or {}
    sweet_min = float(vp.get("sweet_min_inr", 500000))
    sweet_max = float(vp.get("sweet_max_inr", 30000000))
    val = signals.get("est_value_inr")
    if val is None:
        val_sub = unknown_sub
        val_detail = f"Est. value unknown; subscore={unknown_sub}."
    elif sweet_min <= val <= sweet_max:
        val_sub = 1.0
        val_detail = f"Value ₹{val:,} inside sweet band [{int(sweet_min):,}, {int(sweet_max):,}]."
    elif val < sweet_min:
        val_sub = max(0.0, min(1.0, val / sweet_min if sweet_min > 0 else 1.0))
        val_detail = f"Value ₹{val:,} below sweet min ₹{int(sweet_min):,}."
    else:
        upper = sweet_max * 3.0 if sweet_max > 0 else val
        val_sub = _linear_ramp(val, sweet_max, upper)
        val_detail = f"Value ₹{val:,} above sweet max ₹{int(sweet_max):,}."

    # --- buyer affinity ---
    buyer = (signals.get("buyer_org") or signals.get("buyer_dept") or "")
    buyer_u = buyer.upper()
    affinity_map = profile.get("buyer_affinity") or {}
    buy_sub = None
    matched_buyer = None
    for key, score in affinity_map.items():
        if buyer_u and (key.upper() in buyer_u or buyer_u in key.upper()):
            if buy_sub is None or float(score) > buy_sub:
                buy_sub = float(score)
                matched_buyer = key
    if buy_sub is None:
        if not buyer_u:
            buy_sub = unknown_buyer
            buy_detail = f"Buyer unknown; default subscore={unknown_buyer}."
        else:
            buy_sub = unknown_buyer
            buy_detail = f"Buyer '{buyer_u}' not in affinity map; default={unknown_buyer}."
    else:
        buy_detail = f"Buyer affinity for {matched_buyer}={buy_sub}."

    # --- eligibility factor ---
    verdict = (eligibility or {}).get("verdict", "unknown")
    if verdict == "eligible":
        elig_sub = 1.0
        elig_detail = "Eligibility: eligible."
    elif verdict == "turnover_gap":
        elig_sub = gap_sub
        elig_detail = f"Eligibility: turnover_gap; subscore={gap_sub}."
    else:
        elig_sub = unknown_sub
        elig_detail = f"Eligibility: unknown; subscore={unknown_sub}."

    criteria = [
        ("relevance", best_score, rel_detail),
        ("serviceability", svc_sub, svc_detail),
        ("value_fit", val_sub, val_detail),
        ("buyer_affinity", buy_sub, buy_detail),
        ("eligibility_factor", elig_sub, elig_detail),
    ]
    fit_score, fit_breakdown = build_score_breakdown(criteria, fit_weights)

    business_line = None
    if best_line and best_score > 0:
        # Report only the keywords this line actually owns. Cross-line
        # corroboration pools hits from every line to decide the score, so the
        # raw list could credit Drone / UAV with "software" -- a keyword that
        # line does not have. That is a misleading audit trail in the UI and in
        # metadata. Scoring above is deliberately left on the pooled set.
        own_kws = {k.lower() for k in (best_line.get("keywords") or [])}
        owned = [kw for kw in best_matched if kw.lower() in own_kws]
        business_line = {
            "id": best_line.get("id"),
            "label": best_line.get("label"),
            "matched_keywords": owned or best_matched,
        }

    return fit_score, fit_breakdown, business_line

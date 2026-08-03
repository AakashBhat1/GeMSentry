"""RFP analysis orchestration (PDF, card, re-derive, rescore)."""

import datetime
import os
import paths
import re

from gemsentry.config_store import load_scoring_config
from gemsentry.constants import MAX_PDF_PAGES, TENDERS_DIR, TOTAL_ANALYSIS_FIELDS, TOTAL_SIGNAL_FIELDS, logger
from gemsentry.defaults import DEFAULT_SCORING_CONFIG
from gemsentry.pdf_text import extract_text
from gemsentry.parsing.fields import parse_emd_amount, parse_emd_required, parse_epbg_percentage, parse_epbg_required, parse_prebid_required
from gemsentry.parsing.relaxation import RELAX_STATE_RANK, detect_doc_has_exemption_table, parse_relaxation_block, relaxation_granted
from gemsentry.parsing.signals import extract_bid_signals
from gemsentry.profile import load_company_profile, profile_for_workspace, workspace_paths
from gemsentry.scoring.dates import _linear_ramp, evaluate_date_window
from gemsentry.scoring.eligibility import compute_eligibility
from gemsentry.scoring.exemptions import _best_relaxed_bar, _describe_relaxation, _exemption_pair_subscore, get_exemption_label
from gemsentry.scoring.fit import compute_fit_score
from gemsentry.scoring.verdict import apply_verdict, build_score_breakdown, compute_priority_score, compute_recommendation, finalize_auto_reject, get_failed_analysis, scoring_fingerprint
from gemsentry.storage import auto_export_summary, load_existing_metadata, save_metadata


def analyze_rfp_pdf(pdf_path, start_date_str=None, end_date_str=None,
                    scoring_config=None, company_profile=None, card_meta=None):
    """
    Parse RFP PDF with tri-state fields + weighted 0-100 Risk scoring + Fit axis.
    Returns analysis dict, or None if path missing.
    """
    cfg = scoring_config if scoring_config is not None else load_scoring_config()
    profile = company_profile if company_profile is not None else load_company_profile()
    card_meta = card_meta or {}
    unknown_sub = float(cfg.get("unknown_subscore", 0.5))
    weights = cfg.get("weights", DEFAULT_SCORING_CONFIG["weights"])
    emd_cfg = cfg.get("emd", DEFAULT_SCORING_CONFIG["emd"])
    epbg_cfg = cfg.get("epbg", DEFAULT_SCORING_CONFIG["epbg"])

    analysis = {
        "emd_amount": None,
        "emd_status": "Not Required",
        "startup_exemption": "Unknown",
        "mse_exemption": "Unknown",
        "pre_bid_required": "Unknown",
        "pre_bid_date": None,
        "epbg_required": "Unknown",
        "epbg_percentage": None,
        "score": None,
        "score_scale": 100,
        "analysis_status": "ok",
        "parsed_fields": 0,
        "na_fields": 0,
        "total_fields": TOTAL_ANALYSIS_FIELDS,
        "confidence": 0.0,
        "breakdown": [],
        "reasons": [],
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
        "signal_parsed": 0,
        "signal_fields": TOTAL_SIGNAL_FIELDS,
        "eligibility": None,
        "fit_score": None,
        "fit_breakdown": [],
        "business_line": None,
        "recommendation": None,
        "priority_score": None
    }

    if not os.path.exists(pdf_path):
        return None

    try:
        # BE-08: whole PDF up to hard ceiling (keeps Phase-1 fields; more pages
        # for signals). Extraction dominates analysis cost, so it is cached on
        # the file's content hash -- see gemsentry.pdf_text.
        text_clean = extract_text(pdf_path, max_pages=MAX_PDF_PAGES)

        # Track field status: "parsed" | "miss" | "na" (BE-17 not_applicable)
        # 1 emd_required, 2 emd_amount, 3 st_exp, 4 st_turn, 5 mse_exp, 6 mse_turn,
        # 7 prebid_required, 8 epbg_required
        field_status = {
            "emd_required": "miss",
            "emd_amount": "miss",
            "st_exp": "miss",
            "st_turn": "miss",
            "mse_exp": "miss",
            "mse_turn": "miss",
            "prebid_required": "miss",
            "epbg_required": "miss",
        }

        # --- 1. EMD (BE-15 bilingual parsers) ---
        emd_req = parse_emd_required(text_clean) or "unknown"
        if emd_req != "unknown":
            field_status["emd_required"] = "parsed"

        emd_amount = parse_emd_amount(text_clean)
        if emd_amount is not None:
            field_status["emd_amount"] = "parsed"

        # If amount found but required flag unknown, treat as required for scoring
        if emd_req == "unknown" and emd_amount is not None:
            emd_req = "yes"
            field_status["emd_required"] = "parsed"

        # BE-17: amount not applicable when EMD is explicitly not required
        if emd_req == "no" and emd_amount is None:
            field_status["emd_amount"] = "na"

        analysis["emd_amount"] = emd_amount
        free_th = float(emd_cfg.get("free_threshold_inr", 200000))
        max_th = float(emd_cfg.get("max_penalty_threshold_inr", 2000000))

        if emd_req == "no":
            analysis["emd_status"] = "No EMD Required (OK)"
            analysis["reasons"].append("No EMD required.")
            emd_sub = 1.0
            emd_detail = "EMD not required."
        elif emd_req == "unknown":
            if emd_amount is None and "emd" not in text_clean.lower():
                # EMD not mentioned anywhere → treat as not required (common on
                # simpler Bid PDFs); absence is not a risk, so don't penalise.
                analysis["emd_status"] = "No EMD Required (not mentioned)"
                analysis["reasons"].append("No EMD mentioned in document; treated as not required.")
                emd_sub = 1.0
                emd_detail = "EMD not mentioned; treated as not required."
            else:
                # EMD section present but Yes/No could not be parsed → genuine
                # parse gap; stay neutral/unknown.
                analysis["emd_status"] = "Unknown"
                analysis["reasons"].append("EMD required status could not be parsed.")
                emd_sub = unknown_sub
                emd_detail = f"EMD required unknown; subscore={unknown_sub}."
        else:
            # required yes
            if emd_amount is not None:
                analysis["emd_status"] = f"Required ({emd_amount:,} INR)"
                emd_sub = _linear_ramp(emd_amount, free_th, max_th)
                if emd_amount <= free_th:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) within free threshold ({int(free_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ≤ free threshold {int(free_th):,}."
                elif emd_amount >= max_th:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) at/above max penalty threshold ({int(max_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ≥ max penalty {int(max_th):,}."
                else:
                    analysis["reasons"].append(
                        f"EMD amount ({emd_amount:,} INR) on graduated penalty curve "
                        f"({int(free_th):,}–{int(max_th):,} INR)."
                    )
                    emd_detail = f"EMD {emd_amount:,} ramped between {int(free_th):,} and {int(max_th):,}."
            else:
                analysis["emd_status"] = "Required (Amount not parsed)"
                analysis["reasons"].append("EMD required but amount could not be parsed.")
                emd_sub = unknown_sub
                emd_detail = f"EMD required, amount unknown; subscore={unknown_sub}."

        # --- 2–3. Startup / MSE relaxations (BE-15 parsers + BE-17 N/A) ---
        has_exemption_table = detect_doc_has_exemption_table(text_clean)
        st_relax = parse_relaxation_block(text_clean, "startup")
        mse_relax = parse_relaxation_block(text_clean, "mse")
        st_exp, st_turn = st_relax["exp"], st_relax["turn"]
        mse_exp, mse_turn = mse_relax["exp"], mse_relax["turn"]
        st_exp_p, st_turn_p = st_relax["exp_parsed"], st_relax["turn_parsed"]
        mse_exp_p, mse_turn_p = mse_relax["exp_parsed"], mse_relax["turn_parsed"]

        if st_exp_p:
            field_status["st_exp"] = "parsed"
        if st_turn_p:
            field_status["st_turn"] = "parsed"
        if mse_exp_p:
            field_status["mse_exp"] = "parsed"
        if mse_turn_p:
            field_status["mse_turn"] = "parsed"

        exemptions_na = False
        if not has_exemption_table:
            # Absent-by-design on simpler Bid PDFs — not a failed parse
            exemptions_na = True
            for k in ("st_exp", "st_turn", "mse_exp", "mse_turn"):
                if field_status[k] != "parsed":
                    field_status[k] = "na"
            analysis["startup_exemption"] = "Not Applicable"
            analysis["mse_exemption"] = "Not Applicable"
            analysis["reasons"].append(
                "Exemption Check: no exemption table in this doc type (not a denial)."
            )
            # Scoring formulas unchanged: N/A → treat as unknown subscore contribution
            st_exp = st_turn = mse_exp = mse_turn = "unknown"
        else:
            analysis["startup_exemption"] = get_exemption_label(
                st_exp, st_turn, st_relax["exp_years"], st_relax["turnover_inr"])
            analysis["mse_exemption"] = get_exemption_label(
                mse_exp, mse_turn, mse_relax["exp_years"], mse_relax["turnover_inr"])

            for scheme, relax in (("Startup", st_relax), ("MSE", mse_relax)):
                analysis["reasons"].append(
                    _describe_relaxation(scheme, relax)
                )

        # A refused relaxation just means normal terms, which is neutral rather
        # than risky — floor it so relaxations act as a bonus, not their absence
        # as a penalty. Configurable via scoring_config.no_relaxation_floor.
        no_relax_floor = float(cfg.get("no_relaxation_floor", 0.5))
        st_sub = max(_exemption_pair_subscore(st_exp, st_turn, unknown_sub),
                     no_relax_floor)
        mse_sub = max(_exemption_pair_subscore(mse_exp, mse_turn, unknown_sub),
                      no_relax_floor)
        st_detail = f"Startup pair subscore={st_sub:.3f} (exp={st_exp}, turn={st_turn})."
        mse_detail = f"MSE pair subscore={mse_sub:.3f} (exp={mse_exp}, turn={mse_turn})."

        # Neutralize: a missing exemption table is absent-by-design on simpler Bid
        # PDFs, not a denial. As a DPIIT startup + Udyam MSE, don't penalise it.
        # Boost: a tender that explicitly relaxes Startup/MSE experience or turnover
        # is easier for us to win — flag it so priority ranking can reward it.
        exemptions_favorable = False
        if exemptions_na:
            st_sub = 1.0
            mse_sub = 1.0
            st_detail = "No exemption table (absent-by-design); neutral full credit."
            mse_detail = "No exemption table (absent-by-design); neutral full credit."
        else:
            exemptions_favorable = any(
                relaxation_granted(v) for v in (st_exp, st_turn, mse_exp, mse_turn)
            )
        analysis["exemptions_favorable"] = exemptions_favorable

        # --- 4. Pre-bid (BE-15 + BE-19: miss ≠ na; only parse success is "parsed") ---
        prebid_req = parse_prebid_required(text_clean) or "unknown"
        if prebid_req != "unknown":
            field_status["prebid_required"] = "parsed"
            analysis["pre_bid_required"] = prebid_req.capitalize()
        else:
            # Unparsed stays miss/unknown — counts against confidence (BE-19)
            analysis["pre_bid_required"] = "Unknown"

        prebid_date_match = re.search(
            r'(?:Pre-Bid\s+Date\s+and\s+Time|Pre-Bid\s+Meeting\s+Date).{0,40}?'
            r'([\d]{1,2}[-/]\d{1,2}[-/]\d{2,4}[^\d]{0,20}[\d:\s]*(?:AM|PM|hrs|GMT)?)',
            text_clean, re.IGNORECASE
        )

        if prebid_req == "yes":
            if prebid_date_match:
                p_date = prebid_date_match.group(1).strip()
                analysis["pre_bid_date"] = p_date
                analysis["reasons"].append(f"Pre-Bid meeting scheduled on: {p_date}.")
                prebid_sub = 0.7
                prebid_detail = "Pre-bid required with parsed date."
            else:
                analysis["reasons"].append(
                    "Pre-bid meeting is required, but date and time are not clearly specified."
                )
                prebid_sub = 0.3
                prebid_detail = "Pre-bid required without parsed date."
        elif prebid_req == "no":
            analysis["reasons"].append("No Pre-bid meeting required.")
            prebid_sub = 1.0
            prebid_detail = "Pre-bid not required."
        else:
            analysis["reasons"].append("Pre-bid required status could not be parsed.")
            prebid_sub = unknown_sub
            prebid_detail = f"Pre-bid unknown; subscore={unknown_sub}."

        # --- 5. ePBG (BE-15) ---
        epbg_req = parse_epbg_required(text_clean) or "unknown"
        if epbg_req != "unknown":
            field_status["epbg_required"] = "parsed"
            analysis["epbg_required"] = epbg_req.capitalize()
        else:
            analysis["epbg_required"] = "Unknown"

        epbg_pct_val = parse_epbg_percentage(text_clean)
        if epbg_pct_val is not None:
            analysis["epbg_percentage"] = f"{epbg_pct_val}%"
            if epbg_req == "unknown":
                epbg_req = "yes"
                analysis["epbg_required"] = "Yes"
                field_status["epbg_required"] = "parsed"

        free_pct = float(epbg_cfg.get("free_threshold_pct", 3.0))
        max_pct = float(epbg_cfg.get("max_penalty_pct", 10.0))

        if epbg_req == "no":
            analysis["reasons"].append("No ePBG required.")
            epbg_sub = 1.0
            epbg_detail = "ePBG not required."
        elif epbg_req == "unknown":
            analysis["reasons"].append("ePBG required status could not be parsed.")
            epbg_sub = unknown_sub
            epbg_detail = f"ePBG unknown; subscore={unknown_sub}."
        else:
            if epbg_pct_val is not None:
                analysis["reasons"].append(
                    f"ePBG / Performance Guarantee required: {epbg_pct_val}%."
                )
                epbg_sub = _linear_ramp(epbg_pct_val, free_pct, max_pct)
                epbg_detail = f"ePBG {epbg_pct_val}% (free≤{free_pct}%, max pen≥{max_pct}%)."
            else:
                analysis["reasons"].append("ePBG required (Percentage details not parsed).")
                epbg_sub = unknown_sub
                epbg_detail = f"ePBG required, pct unknown; subscore={unknown_sub}."

        # --- 6. Date window (BE-03) — formulas unchanged ---
        date_info = evaluate_date_window(start_date_str, end_date_str, cfg)
        date_sub = date_info["subscore"]
        date_detail = date_info["detail"]
        for r in date_info["reasons"]:
            analysis["reasons"].append(r)

        # --- Confidence (BE-19: meaningful signal)
        # total_fields stays fixed at 8; na only for evidence-based absences;
        # confidence = parsed / (total − na). Unparsed misses count against conf.
        total_fields = TOTAL_ANALYSIS_FIELDS
        na_count = sum(1 for v in field_status.values() if v == "na")
        parsed_count = sum(1 for v in field_status.values() if v == "parsed")
        denom = max(1, total_fields - na_count)
        analysis["parsed_fields"] = parsed_count
        analysis["na_fields"] = na_count
        analysis["total_fields"] = total_fields
        analysis["confidence"] = round(parsed_count / denom, 4)
        analysis["field_status"] = field_status

        # --- Weighted final Risk score + breakdown (Phase-1, unchanged 6 criteria) ---
        criteria = [
            ("emd", emd_sub, emd_detail),
            ("startup_exemption", st_sub, st_detail),
            ("mse_exemption", mse_sub, mse_detail),
            ("prebid", prebid_sub, prebid_detail),
            ("date_window", date_sub, date_detail),
            ("epbg", epbg_sub, epbg_detail),
        ]
        final_score, breakdown = build_score_breakdown(criteria, weights)
        analysis["score"] = final_score
        analysis["breakdown"] = breakdown
        analysis["analysis_status"] = "ok"
        analysis["is_expired"] = bool(date_info["is_expired"])
        analysis["auto_reject"] = bool(date_info.get("auto_reject"))

        # Hard-reject expired bids AND bids closing sooner than the minimum days
        # needed to prepare (min_days_to_bid): force score 0 (BE-03).
        if analysis["auto_reject"]:
            analysis["score"] = 0
            reject_msg = ("Auto-Rejected: bid expired" if date_info["is_expired"]
                          else "Auto-Rejected: closing too soon to bid")
            analysis["reasons"].insert(0, reject_msg)

        # --- BE-08: new bid signals (separate confidence tally) ---
        signals, signal_flags = extract_bid_signals(text_clean, card_meta=card_meta)
        for k in (
            "est_value_inr", "primary_item", "item_category",
            "buyer_org", "buyer_dept", "consignee_state",
            "mii_required", "mse_pref"
        ):
            analysis[k] = signals.get(k)
        analysis["signal_parsed"] = sum(1 for v in signal_flags.values() if v)
        analysis["signal_fields"] = TOTAL_SIGNAL_FIELDS

        # --- Derive bid value from EMD when value is missing ---
        # On GeM the EMD is typically ~5% of the estimated bid value, so
        # value ≈ EMD × 20 (config emd.value_multiplier). Used for value_fit
        # and band filtering, but flagged so the estimate is never mistaken
        # for a parsed figure.
        analysis["est_value_estimated"] = False
        analysis["est_value_source"] = None
        if analysis.get("est_value_inr") is None and emd_amount is not None:
            mult = float(emd_cfg.get("value_multiplier", 20))
            est = int(round(emd_amount * mult))
            signals["est_value_inr"] = est
            analysis["est_value_inr"] = est
            analysis["est_value_estimated"] = True
            analysis["est_value_source"] = "emd_x{:g}".format(mult)
            analysis["reasons"].append(
                f"Bid value not stated; estimated ₹{est:,} from EMD ₹{emd_amount:,} (×{mult:g})."
            )

        # --- BE-09: soft eligibility gate ---
        # We qualify as both DPIIT Startup and Udyam MSE, so take whichever
        # scheme grants the more favourable relaxation on each dimension.
        best_exp_state = max(
            (st_exp, mse_exp), key=lambda s: RELAX_STATE_RANK.get(s, 0)
        )
        relax_exp_years = _best_relaxed_bar(
            st_relax["exp_years"] if st_exp == "partial" else None,
            mse_relax["exp_years"] if mse_exp == "partial" else None,
        )
        relax_turn_inr = _best_relaxed_bar(
            st_relax["turnover_inr"] if st_turn == "partial" else None,
            mse_relax["turnover_inr"] if mse_turn == "partial" else None,
        )
        if not exemptions_na:
            signals["relax_experience_state"] = best_exp_state
            signals["relax_experience_years"] = relax_exp_years

        eligibility = compute_eligibility(
            signals, st_turn, mse_turn, profile, exemptions_na=exemptions_na,
            relax_turnover_inr=None if exemptions_na else relax_turn_inr
        )
        analysis["eligibility"] = eligibility
        if eligibility.get("verdict") == "turnover_gap":
            analysis["reasons"].append(f"Eligibility: {eligibility.get('detail')}")

        # --- BE-10: Fit score ---
        fit_score, fit_breakdown, business_line = compute_fit_score(
            analysis, signals, eligibility, profile, cfg, card_meta=card_meta
        )
        analysis["fit_score"] = fit_score
        analysis["fit_breakdown"] = fit_breakdown
        analysis["business_line"] = business_line

        # --- BE-11: two-axis recommendation ---
        analysis["recommendation"] = compute_recommendation(
            fit_score,
            analysis.get("score"),
            eligibility,
            bool(analysis.get("auto_reject")),
            cfg,
            relevance_matched=business_line is not None
        )

        # --- Feature B: blended Priority score for best-first ranking ---
        analysis["priority_score"] = compute_priority_score(
            fit_score,
            analysis.get("score"),
            eligibility,
            bool(analysis.get("auto_reject")),
            cfg,
            exemptions_favorable=bool(analysis.get("exemptions_favorable"))
        )

        analysis["config_fingerprint"] = scoring_fingerprint(cfg, profile)
        analysis["scored_at"] = datetime.datetime.now().isoformat(timespec="seconds")

    except Exception as e:
        logger.error(f"Error parsing PDF metadata: {e}")
        failed = get_failed_analysis(f"PDF parsing error: {e}")
        return failed

    return analysis


def analyze_from_card(tender, scoring_config=None, company_profile=None):
    """
    Fallback scoring from card metadata when no RFP PDF is available (BE-26).
    Scores Fit from title/department/value so the tender still ranks instead of
    sitting at priority null. Risk stays None (tender terms unknown), which caps
    the recommendation at Review and makes priority Fit-only.
    """
    cfg = scoring_config if scoring_config is not None else load_scoring_config()
    profile = company_profile if company_profile is not None else load_company_profile()

    analysis = get_failed_analysis("RFP PDF document is not available for analysis.")
    analysis["analysis_status"] = "card_only"
    analysis["reasons"] = ["Scored from card metadata only (RFP PDF not available)."]

    date_info = evaluate_date_window(
        tender.get("start_date"), tender.get("end_date"), cfg
    )
    analysis["is_expired"] = bool(date_info.get("is_expired"))
    analysis["auto_reject"] = bool(date_info.get("auto_reject"))
    for r in date_info.get("reasons", []):
        analysis["reasons"].append(r)

    signals = {
        "est_value_inr": tender.get("est_value_inr"),
        "primary_item": None,
        "item_category": None,
        "buyer_org": tender.get("department"),
        "buyer_dept": None,
        "consignee_state": None,
    }
    analysis["est_value_inr"] = signals["est_value_inr"]
    analysis["buyer_org"] = signals["buyer_org"]

    eligibility = {
        "verdict": "unknown",
        "flags": ["card_only"],
        "detail": "No RFP PDF; eligibility unknown (scored from card metadata).",
    }
    analysis["eligibility"] = eligibility

    card_meta = {
        "title": tender.get("title"),
        "department": tender.get("department"),
        "quantity": tender.get("quantity"),
        "keyword": tender.get("keyword"),
    }
    fit_score, fit_breakdown, business_line = compute_fit_score(
        analysis, signals, eligibility, profile, cfg, card_meta=card_meta
    )
    analysis["fit_score"] = fit_score
    analysis["fit_breakdown"] = fit_breakdown
    analysis["business_line"] = business_line

    analysis["recommendation"] = compute_recommendation(
        fit_score, None, eligibility, analysis["auto_reject"], cfg,
        relevance_matched=business_line is not None
    )
    analysis["priority_score"] = compute_priority_score(
        fit_score, None, eligibility, analysis["auto_reject"], cfg
    )

    if analysis["auto_reject"]:
        finalize_auto_reject(analysis, date_info)

    analysis["config_fingerprint"] = scoring_fingerprint(cfg, profile)
    analysis["scored_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return analysis


def rederive_analysis(tender, analysis, cfg, profile):
    """
    Recompute the derived scoring layer from STORED signals — no PDF I/O.
    Refreshes: date window (dates drift daily), risk re-weighting from the
    stored per-criterion subscores, Fit, recommendation, priority. Parsed
    fields (EMD, exemptions, signals) are kept as-is. Mutates analysis.
    """
    date_info = evaluate_date_window(
        tender.get("start_date"), tender.get("end_date"), cfg
    )
    analysis["is_expired"] = bool(date_info.get("is_expired"))
    analysis["auto_reject"] = bool(date_info.get("auto_reject"))

    breakdown = analysis.get("breakdown") or []
    if breakdown:
        weights = cfg.get("weights", DEFAULT_SCORING_CONFIG["weights"])
        criteria = []
        for b in breakdown:
            name = b.get("criterion")
            sub = b.get("subscore", 0)
            detail = b.get("detail", "")
            if name == "date_window":
                sub, detail = date_info["subscore"], date_info["detail"]
            criteria.append((name, sub, detail))
        risk, new_breakdown = build_score_breakdown(criteria, weights)
        analysis["score"] = risk
        analysis["breakdown"] = new_breakdown

    signals = {k: analysis.get(k) for k in (
        "est_value_inr", "primary_item", "item_category",
        "buyer_org", "buyer_dept", "consignee_state",
        "mii_required", "mse_pref",
    )}
    eligibility = analysis.get("eligibility") or {"verdict": "unknown"}
    card_meta = {
        "title": tender.get("title"),
        "department": tender.get("department"),
        "quantity": tender.get("quantity"),
        "keyword": tender.get("keyword"),
    }
    fit_score, fit_breakdown, business_line = compute_fit_score(
        analysis, signals, eligibility, profile, cfg, card_meta=card_meta
    )
    analysis["fit_score"] = fit_score
    analysis["fit_breakdown"] = fit_breakdown
    analysis["business_line"] = business_line

    analysis["recommendation"] = compute_recommendation(
        fit_score, analysis.get("score"), eligibility,
        analysis["auto_reject"], cfg,
        relevance_matched=business_line is not None
    )
    analysis["priority_score"] = compute_priority_score(
        fit_score, analysis.get("score"), eligibility,
        analysis["auto_reject"], cfg,
        exemptions_favorable=bool(analysis.get("exemptions_favorable")),
    )
    if analysis["auto_reject"]:
        finalize_auto_reject(analysis, date_info)

    analysis["config_fingerprint"] = scoring_fingerprint(cfg, profile)
    analysis["scored_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return analysis


def rescore_tender(tender, scoring_cfg, profile, reparse=False):
    """
    Recompute all verdicts for one tender with zero network I/O.
    Default (fast): re-derive from stored signals in-place (~ms/tender).
    reparse=True: fully re-analyze the local PDF (exact; picks up parser fixes).
    Tenders without a usable analysis fall back to card-metadata scoring.
    Mutates and returns the tender.
    """
    pdf_rel = tender.get("local_pdf_path")
    abs_pdf = None
    if pdf_rel:
        abs_pdf = pdf_rel if os.path.isabs(pdf_rel) else os.path.join(paths.ROOT, pdf_rel)

    analysis = None
    if reparse and abs_pdf and os.path.exists(abs_pdf):
        analysis = analyze_rfp_pdf(
            abs_pdf,
            start_date_str=tender.get("start_date"),
            end_date_str=tender.get("end_date"),
            scoring_config=scoring_cfg,
            company_profile=profile,
            card_meta={
                "title": tender.get("title"),
                "department": tender.get("department"),
                "quantity": tender.get("quantity"),
                "keyword": tender.get("keyword"),
                "est_value_inr": tender.get("est_value_inr"),
            },
        )
        if analysis is not None and analysis.get("analysis_status") == "ok":
            if analysis.get("auto_reject"):
                finalize_auto_reject(analysis)
        else:
            analysis = None  # corrupt/unreadable PDF → fall through

    if analysis is None:
        existing = tender.get("analysis")
        if existing and existing.get("analysis_status") in ("ok", "card_only"):
            if existing.get("analysis_status") == "ok":
                analysis = rederive_analysis(tender, existing, scoring_cfg, profile)
            else:
                analysis = analyze_from_card(tender, scoring_cfg, profile)
        else:
            analysis = analyze_from_card(tender, scoring_cfg, profile)

    return apply_verdict(tender, analysis)


def rescore_metadata(tenders_dir=None, reparse=False, progress=None, profile=None):
    """
    Re-run scoring for every tender in a workspace's metadata (no scraping).
    Saves metadata and returns a summary dict with status transition counts.
    """
    tenders_dir = tenders_dir if tenders_dir is not None else workspace_paths()[0]
    cfg = load_scoring_config()
    if profile is None:
        workspace = os.path.relpath(tenders_dir, TENDERS_DIR)
        profile = profile_for_workspace("" if workspace == "." else workspace)
    tenders = load_existing_metadata(tenders_dir)

    transitions = {}
    total = len(tenders)
    for i, tender in enumerate(tenders.values()):
        old_status = tender.get("status")
        rescore_tender(tender, cfg, profile, reparse=reparse)
        new_status = tender.get("status")
        key = f"{old_status or 'None'} -> {new_status}"
        transitions[key] = transitions.get(key, 0) + 1
        if progress and (i + 1) % 50 == 0:
            progress(i + 1, total)

    save_metadata(list(tenders.values()), tenders_dir)
    auto_export_summary(tenders_dir, os.path.join(tenders_dir, "downloads"))
    counts = {}
    recs = {}
    for t in tenders.values():
        counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
        rec = (t.get("analysis") or {}).get("recommendation")
        recs[rec or "None"] = recs.get(rec or "None", 0) + 1
    return {
        "total": total,
        "transitions": transitions,
        "status_counts": counts,
        "recommendation_counts": recs,
        "fingerprint": scoring_fingerprint(cfg, profile),
    }

"""
Backward-compatible facade over the ``gemsentry`` package.

The scraper used to be one 4k-line module. It now lives in focused modules
under ``gemsentry/`` (see ``gemsentry/__init__.py``). This file re-exports the
public surface so existing callers -- ``app.py``, ``tools/*``, ``tests/*`` --
keep working with ``import scraper``.

New code should import from the specific module instead, e.g.
``from gemsentry.scoring.fit import compute_fit_score``.
"""

from gemsentry.constants import (
    TENDERS_DIR, DOWNLOADS_DIR, SCORING_CONFIG_PATH, COMPANY_PROFILE_PATH, KEYWORDS_PATH,
    logger, TOTAL_ANALYSIS_FIELDS, TOTAL_SIGNAL_FIELDS, MAX_PDF_PAGES, _INDIAN_STATES,
    CRORE_INR, LAKH_INR
)
from gemsentry.defaults import DEFAULT_SCORING_CONFIG, DEFAULT_COMPANY_PROFILE
from gemsentry.textutils import (
    sanitize_filename, sanitize_folder_name, today_iso, get_date_folder_name,
    _parse_inr_amount, _clean_english_phrase, _match_indian_state
)
from gemsentry.dateparse import parse_gem_date, parse_iso_date_to_gem, check_date_policy
from gemsentry.config_store import (
    _resolve_config_path, load_keywords, load_scoring_config, validate_scoring_config,
    save_scoring_config
)
from gemsentry.profile import (
    load_company_profile, _apply_active_preset, workspace_label, profile_for_workspace,
    get_active_workspace, workspace_paths, validate_company_profile, save_company_profile
)
from gemsentry.storage import (
    build_pdf_index, find_existing_pdf_file, load_existing_metadata, save_metadata,
    auto_export_summary, clear_workspace
)
from gemsentry.parsing.text import (
    _window_after, _first_yes_no, _first_ascii_phrase, _first_number
)
from gemsentry.parsing.relaxation import (
    RELAX_WORD, RELAX_SCOPES, RELAX_LABEL_RX, RELAX_GRADE_RX, RELAX_EXP_AMOUNT_RX,
    RELAX_TURN_AMOUNT_RX, RELAX_VALUE_WINDOW, RELAX_STATE_RANK, relaxation_granted,
    detect_doc_has_exemption_table, _empty_relaxation, parse_relaxation_block,
    _scope_dimensions, _parse_relaxation_amounts, parse_exemption_pair
)
from gemsentry.parsing.fields import (
    parse_yes_no_field, parse_emd_required, parse_epbg_required, parse_emd_amount,
    parse_epbg_percentage, parse_prebid_required
)
from gemsentry.parsing.signals import extract_bid_signals
from gemsentry.scoring.dates import _linear_ramp, evaluate_date_window
from gemsentry.scoring.exemptions import (
    _format_lakhs, get_exemption_label, _best_relaxed_bar, _describe_relaxation,
    _PARTIAL_RELAX_CREDIT, _exemption_pair_subscore
)
from gemsentry.scoring.eligibility import compute_eligibility
from gemsentry.scoring.fit import split_bid_items, _apply_omnibus_dilution, compute_fit_score
from gemsentry.scoring.verdict import (
    get_failed_analysis, build_score_breakdown, status_from_score,
    RECOMMENDATION_TO_STATUS, status_from_recommendation, apply_verdict,
    finalize_auto_reject, scoring_fingerprint, compute_recommendation,
    compute_priority_score
)
from gemsentry.analysis import (
    analyze_rfp_pdf, analyze_from_card, rederive_analysis, rescore_tender,
    rescore_metadata
)
from gemsentry.sources.gem.client import (
    parse_cards, select_sort_order, download_pdf_http, download_rfp_pdf, doc_to_tender,
    is_gem_url, _SSL_CTX, fetch_keyword_bids_api
)
from gemsentry.pipeline import (
    scrape, scrape_single_bid, ingest_external_tenders, plan_downloads
)

__all__ = [
    "COMPANY_PROFILE_PATH", "CRORE_INR", "DEFAULT_COMPANY_PROFILE",
    "DEFAULT_SCORING_CONFIG", "DOWNLOADS_DIR", "KEYWORDS_PATH", "LAKH_INR",
    "MAX_PDF_PAGES", "RECOMMENDATION_TO_STATUS", "RELAX_EXP_AMOUNT_RX", "RELAX_GRADE_RX",
    "RELAX_LABEL_RX", "RELAX_SCOPES", "RELAX_STATE_RANK", "RELAX_TURN_AMOUNT_RX",
    "RELAX_VALUE_WINDOW", "RELAX_WORD", "SCORING_CONFIG_PATH", "TENDERS_DIR",
    "TOTAL_ANALYSIS_FIELDS", "TOTAL_SIGNAL_FIELDS", "_INDIAN_STATES",
    "_PARTIAL_RELAX_CREDIT", "_SSL_CTX", "_apply_active_preset", "_apply_omnibus_dilution",
    "_best_relaxed_bar", "_clean_english_phrase", "_describe_relaxation",
    "_empty_relaxation", "_exemption_pair_subscore", "_first_ascii_phrase",
    "_first_number", "_first_yes_no", "_format_lakhs", "_linear_ramp",
    "_match_indian_state", "_parse_inr_amount", "_parse_relaxation_amounts",
    "_resolve_config_path", "_scope_dimensions", "_window_after", "analyze_from_card",
    "analyze_rfp_pdf", "apply_verdict", "auto_export_summary", "build_pdf_index",
    "build_score_breakdown", "check_date_policy", "clear_workspace", "compute_eligibility",
    "compute_fit_score", "compute_priority_score", "compute_recommendation",
    "detect_doc_has_exemption_table", "doc_to_tender", "download_pdf_http",
    "download_rfp_pdf", "evaluate_date_window", "extract_bid_signals",
    "fetch_keyword_bids_api", "finalize_auto_reject", "find_existing_pdf_file",
    "get_active_workspace", "get_date_folder_name", "get_exemption_label",
    "get_failed_analysis", "ingest_external_tenders", "is_gem_url", "load_company_profile",
    "load_existing_metadata", "load_keywords", "load_scoring_config", "logger",
    "parse_cards", "parse_emd_amount", "parse_emd_required", "parse_epbg_percentage",
    "parse_epbg_required", "parse_exemption_pair", "parse_gem_date",
    "parse_iso_date_to_gem", "parse_prebid_required", "parse_relaxation_block",
    "parse_yes_no_field", "plan_downloads", "profile_for_workspace", "rederive_analysis",
    "relaxation_granted", "rescore_metadata", "rescore_tender", "sanitize_filename",
    "sanitize_folder_name", "save_company_profile", "save_metadata", "save_scoring_config",
    "scoring_fingerprint", "scrape", "scrape_single_bid", "select_sort_order",
    "split_bid_items", "status_from_recommendation", "status_from_score", "today_iso",
    "validate_company_profile", "validate_scoring_config", "workspace_label",
    "workspace_paths"
]

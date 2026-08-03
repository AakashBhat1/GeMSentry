"""Shared constants and the package logger."""

import logging
import paths


# Path constants — single source of truth is paths.py (re-exported for callers)
TENDERS_DIR = paths.TENDERS_DIR


DOWNLOADS_DIR = paths.DOWNLOADS_DIR


SCORING_CONFIG_PATH = paths.SCORING_CONFIG_PATH


COMPANY_PROFILE_PATH = paths.COMPANY_PROFILE_PATH


KEYWORDS_PATH = paths.KEYWORDS_PATH


logger = logging.getLogger("gemsentry")


TOTAL_ANALYSIS_FIELDS = 8


TOTAL_SIGNAL_FIELDS = 8  # est_value, primary_item, item_category, buyer_org, buyer_dept, consignee_state, mii_required, mse_pref


MAX_PDF_PAGES = 12


_INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Puducherry", "Jammu and Kashmir", "Ladakh", "Chandigarh"
]


CRORE_INR = 10000000


LAKH_INR = 100000

# Per-dimension states, ordered weakest → strongest for merge resolution.

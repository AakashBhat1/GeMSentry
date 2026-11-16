"""GeMSentry Multi-Company CRM, Annexures, Folder Migration, and RBAC Service Layer."""

from gemsentry.crm.annexures import (
    classify_local_filename,
    download_gem_annexures,
    extract_pdf_annexure_links,
    is_safe_gem_url,
)
from gemsentry.crm.migration import organize_and_import_legacy_folders
from gemsentry.crm.repository import CRMRepository
from gemsentry.crm.service import CRMService

crm_service = CRMService()

__all__ = [
    "CRMRepository",
    "CRMService",
    "classify_local_filename",
    "crm_service",
    "download_gem_annexures",
    "extract_pdf_annexure_links",
    "is_safe_gem_url",
    "organize_and_import_legacy_folders",
]

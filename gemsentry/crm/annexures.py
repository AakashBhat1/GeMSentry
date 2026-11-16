"""GeM PDF embedded annexure extraction, filename classification, and SSRF-safe downloader."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import certifi
import requests
from pypdf import PdfReader

logger = logging.getLogger("gemsentry.crm.annexures")

DEFAULT_MAX_ANNEXURE_BYTES = 25 * 1024 * 1024  # 25 MiB
MAX_REDIRECT_HOPS = 5
STREAM_CHUNK_SIZE = 64 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BOILERPLATE_SUBSTRINGS = (
    "gtc/pdfbydate",
    "/apis/v1/gtc",
    "/terms-and-conditions",
    "/privacy-policy",
    "/user-manual",
    "/contact-us",
)

_SAFE_FILENAME_RX = re.compile(r"[^A-Za-z0-9._-]+")
_GEM_PRIMARY_BID_RX = re.compile(
    r"^(?:(?:first|second|third|fourth|fifth)[-_])?"
    r"(?:gem[-_]bidding[-_]\d+|gem[-_]\d{4}[-_][a-z]+[-_]\d+|tender[-_]document[-_][a-z0-9_-]+)"
    r"(?:\s*\(\d+\))?\.pdf$",
    re.IGNORECASE,
)


def is_safe_gem_url(url: str) -> bool:
    """Allow strictly HTTPS URLs hosted on gem.gov.in or *.gem.gov.in on standard port 443."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme.lower() != "https":
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    return host == "gem.gov.in" or host.endswith(".gem.gov.in")


def is_boilerplate_gem_url(url: str) -> bool:
    """Return True for generic GeM GTC / policy boilerplate links rather than bid annexures."""
    low = (url or "").strip().lower()
    if not low:
        return True
    for token in BOILERPLATE_SUBSTRINGS:
        if token in low:
            return True

    parsed = urlparse(low)
    clean_path = parsed.path.rstrip("/")
    if not clean_path and not parsed.query:
        return True
    if clean_path.endswith("/downloadomppdfile"):
        return True
    if clean_path in ("/terms", "/privacy", "/help", "/contact", "/training"):
        return True
    return False


def sanitize_filename(name: str, fallback: str = "annexure.pdf") -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    # Normalize browser copy suffix like "file (1).pdf" -> "file_1.pdf" or keep clean stem
    base = re.sub(r"\s+\((\d+)\)(?=\.[A-Za-z0-9]{1,8}$)", r"_\1", base)
    cleaned = _SAFE_FILENAME_RX.sub("_", base)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    if not cleaned:
        return fallback
    if "." not in cleaned:
        ext = os.path.splitext(fallback)[1] or ".pdf"
        cleaned = f"{cleaned}{ext}"
    return cleaned[:180]


def _filename_from_gem_url(url: str, index: int = 1) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("fileDownloadPath", "file", "filename", "path", "doc"):
        vals = qs.get(key)
        if vals and vals[0]:
            candidate = os.path.basename(unquote(vals[0]).replace("\\", "/"))
            if candidate and "." in candidate:
                return sanitize_filename(candidate, fallback=f"annexure_{index}.pdf")

    path_base = os.path.basename(unquote(parsed.path).rstrip("/"))
    if path_base and "." in path_base:
        return sanitize_filename(path_base, fallback=f"annexure_{index}.pdf")
    if path_base and path_base.isdigit():
        return f"gem_doc_{path_base}.pdf"
    return f"gem_annexure_{index}.pdf"


def classify_gem_url(url: str, filename: str = "") -> str:
    """Classify a GeM embedded link into boq | atc | tech_spec | annexure."""
    u_low = (url or "").lower()
    f_low = (filename or os.path.basename(urlparse(u_low).path)).lower()
    ext = os.path.splitext(f_low)[1]

    if (
        ext in (".csv", ".xls", ".xlsx")
        or "boqlineitemsdocument" in u_low
        or re.search(r"(?:^|[_\-\s/])(?:boq|price[_-]?bid|line[_-]?items)", f_low)
    ):
        return "boq"

    if (
        "/slafds" in u_low
        or "buyeratc" in f_low
        or "buyeratc" in u_low
        or re.search(r"(?:^|[_\-\s/])(?:atc\d*|t_and_c|corrigendum)", f_low)
    ):
        return "atc"

    if (
        "specificationdocument" in u_low
        or re.search(
            r"(?:spec|gsqr|qrs|tech[_-]?qr|datasheet|datasht|catalogue|scope|sow\b|technical)",
            f_low,
        )
    ):
        return "tech_spec"

    if "boqdocument" in u_low or "/boq/" in u_low:
        return "boq"

    return "annexure"


def classify_local_filename(filename: str, primary_bid_filename: str | None = None) -> str:
    """Classify a local dossier filename into rfp | tech_spec | boq | atc | annexure | other."""
    base = os.path.basename((filename or "").replace("\\", "/")).strip()
    if not base:
        return "other"

    low = base.lower()
    stem, ext = os.path.splitext(low)

    if primary_bid_filename:
        prim_base = os.path.basename(primary_bid_filename.replace("\\", "/")).strip().lower()
        if prim_base and low == prim_base:
            return "rfp"

    # Check explicit BOQ / ATC / Spec markers BEFORE generic GeM name substrings
    # (e.g. "Technical_Specification_GEM2026B7733568.docx" is tech_spec, not rfp).
    if ext in (".csv", ".xls", ".xlsx") or re.search(
        r"(?:^|[_\-\s])(?:boq|price[_-]?bid|bill[_-]?of[_-]?quant)", stem
    ):
        return "boq"

    if re.search(r"(?:^|[_\-\s])(?:atc[a-z0-9]*|buyeratc|t_and_c|corrigendum)", stem):
        return "atc"

    if re.search(
        r"(?:spec|gsqr|qrs|tech[_-]?qr|datasheet|datasht|catalogue|scope|sow\b|tech[_-]?pack|tech[_-]?pages)",
        stem,
    ):
        return "tech_spec"

    if _GEM_PRIMARY_BID_RX.match(base):
        return "rfp"

    if primary_bid_filename is None and ext == ".pdf" and re.search(
        r"(?:^|[_\-\s])(?:rfp[a-z0-9]*|tender\d*|tenderdocsall|bidding|eoi|final_tender_doc|draft_tender|solar_final_rfp)",
        stem,
    ):
        return "rfp"

    if ext in (".pdf", ".doc", ".docx", ".zip") and (
        re.search(r"(?:annex|attach|performa|qa_doc|^cs_|^rfp)", stem)
        or re.match(r"^\d{7,14}(?:[_\s()-]+\d+)?$", stem)
        or re.match(r"^work_\d+", stem)
    ):
        return "annexure"

    return "other"


def _deref(obj: Any) -> Any:
    if hasattr(obj, "get_object"):
        try:
            return obj.get_object()
        except Exception:
            return obj
    return obj


def extract_pdf_annexure_links(pdf_path: str) -> list[dict[str, Any]]:
    """Extract unique embedded https://*.gem.gov.in/... annexure links from PDF annotations."""
    if not pdf_path or not os.path.isfile(pdf_path):
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    try:
        reader = PdfReader(pdf_path)
        for page_idx, page in enumerate(reader.pages):
            annots = page.annotations or []
            for raw_annot in annots:
                annot = _deref(raw_annot)
                if not isinstance(annot, dict):
                    continue
                action = _deref(annot.get("/A"))
                if not isinstance(action, dict):
                    continue
                uri_obj = _deref(action.get("/URI"))
                if not uri_obj:
                    continue
                uri = str(uri_obj).strip()
                if not uri or uri in seen_urls:
                    continue
                if not is_safe_gem_url(uri):
                    continue
                if is_boilerplate_gem_url(uri):
                    continue

                seen_urls.add(uri)
                fname = _filename_from_gem_url(uri, index=len(results) + 1)
                kind = classify_gem_url(uri, fname)
                results.append(
                    {
                        "url": uri,
                        "filename": fname,
                        "kind": kind,
                        "file_kind": kind,
                        "page": page_idx + 1,
                    }
                )
    except Exception as exc:
        logger.warning("Could not extract PDF annexure links from %s: %s", pdf_path, exc)

    return results


def _extract_cd_filename(content_disposition: str) -> str | None:
    if not content_disposition:
        return None
    # RFC 5987 filename*=UTF-8''...
    m_star = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;\r\n]+)", content_disposition, re.IGNORECASE)
    if m_star:
        return unquote(m_star.group(1).strip().strip("\"'"))
    m_plain = re.search(r'filename\s*=\s*"([^"]+)"', content_disposition, re.IGNORECASE)
    if m_plain:
        return m_plain.group(1).strip()
    m_unquoted = re.search(r"filename\s*=\s*([^;\r\n]+)", content_disposition, re.IGNORECASE)
    if m_unquoted:
        return m_unquoted.group(1).strip().strip("\"'")
    return None


def _create_verified_session() -> requests.Session:
    session = requests.Session()
    session.verify = certifi.where()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        }
    )
    return session


def _unique_dest_path(dest_dir_abs: str, candidate_name: str, sha_prefix: str = "") -> tuple[str, str]:
    clean = sanitize_filename(candidate_name)
    stem, ext = os.path.splitext(clean)
    target_name = clean
    target_abs = os.path.abspath(os.path.join(dest_dir_abs, target_name))
    if os.path.commonpath([dest_dir_abs, target_abs]) != dest_dir_abs:
        raise ValueError("Resolved annexure destination escapes dossier directory.")

    if not os.path.exists(target_abs):
        return target_name, target_abs

    suffix = f"_{sha_prefix[:8]}" if sha_prefix else "_2"
    counter = 2
    while os.path.exists(target_abs):
        tag = suffix if counter == 2 and sha_prefix else f"_{counter}"
        target_name = f"{stem[:140]}{tag}{ext}"
        target_abs = os.path.abspath(os.path.join(dest_dir_abs, target_name))
        if os.path.commonpath([dest_dir_abs, target_abs]) != dest_dir_abs:
            raise ValueError("Resolved annexure destination escapes dossier directory.")
        counter += 1
    return target_name, target_abs


def download_gem_annexures(
    links: list[dict[str, Any] | str],
    dest_dir: str,
    timeout: int = 12,
    max_bytes: int = DEFAULT_MAX_ANNEXURE_BYTES,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Download GeM annexures with strict host/TLS/redirect/size checks.

    Returns ``(saved_files, warnings)``. Never raises on network or validation errors.
    """
    saved_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not links:
        return saved_files, warnings

    dest_dir_abs = os.path.abspath(dest_dir)
    os.makedirs(dest_dir_abs, exist_ok=True)

    # Index existing SHA-256 digests in dest_dir to avoid re-saving identical bytes
    existing_digests: set[str] = set()
    try:
        for existing_name in os.listdir(dest_dir_abs):
            full = os.path.join(dest_dir_abs, existing_name)
            if os.path.isfile(full):
                h = hashlib.sha256()
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(STREAM_CHUNK_SIZE), b""):
                        h.update(chunk)
                existing_digests.add(h.hexdigest())
    except OSError:
        pass

    session = _create_verified_session()
    try:
        for idx, item in enumerate(links, start=1):
            if isinstance(item, str):
                orig_url = item.strip()
                hint_name = ""
                hint_kind = ""
            elif isinstance(item, dict):
                orig_url = str(item.get("url") or "").strip()
                hint_name = str(item.get("filename") or "").strip()
                hint_kind = str(item.get("file_kind") or item.get("kind") or "").strip()
            else:
                continue

            if not orig_url:
                continue

            if not is_safe_gem_url(orig_url):
                warnings.append(f"Blocked non-GeM or insecure annexure URL: {orig_url}")
                continue

            current_url = orig_url
            resp: requests.Response | None = None
            try:
                for _hop in range(MAX_REDIRECT_HOPS + 1):
                    resp = session.get(
                        current_url,
                        timeout=timeout,
                        stream=True,
                        allow_redirects=False,
                        verify=certifi.where(),
                    )
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("Location") or ""
                        resp.close()
                        resp = None
                        if not loc:
                            warnings.append(f"Redirect missing Location header for {orig_url}")
                            break
                        next_url = urljoin(current_url, loc)
                        if not is_safe_gem_url(next_url):
                            warnings.append(
                                f"Blocked untrusted redirect from {orig_url} to {next_url}"
                            )
                            break
                        current_url = next_url
                        continue
                    break
                else:
                    warnings.append(f"Too many redirects fetching {orig_url}")
                    continue

                if resp is None:
                    continue

                try:
                    if resp.status_code >= 400:
                        warnings.append(f"HTTP {resp.status_code} fetching {orig_url}")
                        continue

                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if ctype == "text/html":
                        warnings.append(f"Skipped HTML portal error page for {orig_url}")
                        continue

                    clen = resp.headers.get("Content-Length")
                    if clen:
                        try:
                            if int(clen) > max_bytes:
                                warnings.append(
                                    f"Skipped {orig_url}: Content-Length {clen} exceeds {max_bytes} byte cap."
                                )
                                continue
                        except ValueError:
                            pass

                    cd_name = _extract_cd_filename(resp.headers.get("Content-Disposition") or "")
                    candidate_name = (
                        sanitize_filename(cd_name)
                        if cd_name
                        else sanitize_filename(hint_name or _filename_from_gem_url(current_url, idx))
                    )

                    tmp_path = os.path.join(dest_dir_abs, f".dl_{os.getpid()}_{idx}.tmp")
                    digest = hashlib.sha256()
                    total_bytes = 0
                    too_large = False

                    try:
                        with open(tmp_path, "wb") as out_fh:
                            for chunk in resp.iter_content(chunk_size=STREAM_CHUNK_SIZE):
                                if not chunk:
                                    continue
                                total_bytes += len(chunk)
                                if total_bytes > max_bytes:
                                    too_large = True
                                    break
                                digest.update(chunk)
                                out_fh.write(chunk)
                    except Exception:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                        raise

                    if too_large:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                        warnings.append(
                            f"Aborted {orig_url}: download exceeded {max_bytes} byte cap."
                        )
                        continue

                    if total_bytes == 0:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                        warnings.append(f"Skipped empty response from {orig_url}")
                        continue

                    sha_hex = digest.hexdigest()
                    if sha_hex in existing_digests:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        continue

                    final_name, final_abs = _unique_dest_path(dest_dir_abs, candidate_name, sha_hex)
                    os.replace(tmp_path, final_abs)
                    existing_digests.add(sha_hex)

                    kind = hint_kind or classify_gem_url(orig_url, final_name)
                    guessed_mime = (
                        ctype
                        if ctype and ctype != "application/octet-stream"
                        else (mimetypes.guess_type(final_name)[0] or "application/octet-stream")
                    )
                    saved_files.append(
                        {
                            "filename": final_name,
                            "original_filename": cd_name or hint_name or final_name,
                            "abs_path": final_abs,
                            "size_bytes": total_bytes,
                            "sha256": sha_hex,
                            "file_kind": kind,
                            "mime_type": guessed_mime,
                            "source_url": orig_url,
                        }
                    )
                finally:
                    resp.close()
            except Exception as exc:
                warnings.append(f"Failed downloading {orig_url}: {type(exc).__name__}: {exc}")
    finally:
        session.close()

    return saved_files, warnings

"""Legacy tender folder backup, deduplication, New-folder unbundling, and CRM DB import."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader

import paths
from gemsentry import storage
from gemsentry.crm.annexures import (
    classify_local_filename,
    extract_pdf_annexure_links,
    sanitize_filename,
)
from gemsentry.crm.repository import CRMRepository, safe_bid_slug, slugify
from gemsentry.master_sheet import master_sheet_manager

logger = logging.getLogger("gemsentry.crm.migration")

_HASH_CHUNK = 64 * 1024

IGNORED_FILENAMES = frozenset({
    "agents.md",
    "thumbs.db",
    ".ds_store",
    "desktop.ini",
})

_COPY_SUFFIX_RX = re.compile(r"(?:\s+-\s+copy(?:\s*\(\d+\))?|\s*\(\d+\))$", re.IGNORECASE)
_NEW_FOLDER_RX = re.compile(r"^new\s+folder\b", re.IGNORECASE)
_DATE_WRAPPER_RX = re.compile(
    r"^(?:"
    r"\d{1,2}[-_\s][a-z0-9]{2,6}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\d*(?:\(\d+\))?|"
    r"\d{1,2}[a-z]{3,9}\d{2,4}|"
    r"\d{1,2}"
    r")$",
    re.IGNORECASE,
)
_STRUCTURAL_WRAPPERS = frozenset({
    "tenders",
    "rfps",
    "gem",
    "dpp",
    "idex",
    "old",
    "small",
})

_GEM_BID_RX = re.compile(r"GEM[-_/](\d{4})[-_/](\w+)[-_/](\d+)", re.IGNORECASE)
_GEM_BIDDING_ID_RX = re.compile(
    r"(?:^(?:first|second|third|fourth|fifth)[-_])?gem[-_]bidding[-_](\d+)",
    re.IGNORECASE,
)
_WORK_ID_RX = re.compile(r"\bwork[_-](\d+)\b", re.IGNORECASE)
_BOQ_ID_RX = re.compile(r"\bboq[_-](\d+)\b", re.IGNORECASE)
_TENDER_DOC_RX = re.compile(r"^tender[_-]document[_-]([a-z0-9_-]+)", re.IGNORECASE)
_TS_STAMP_RX = re.compile(r"(20\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})")


@dataclass
class SourceFileEntry:
    abs_path: str
    filename: str
    clean_name: str
    sha256: str
    size_bytes: int
    is_copy_named: bool


@dataclass
class DiscoveredDossier:
    company_id: str
    disk_folder: str
    subfolder_slug: str
    stage: str
    specificity: int
    bid_no: str
    bid_slug: str
    title_hint: str = ""
    primary_filename: str = ""
    files: list[SourceFileEntry] = field(default_factory=list)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_copy_suffix(filename: str) -> tuple[str, bool]:
    stem, ext = os.path.splitext(filename)
    stripped = _COPY_SUFFIX_RX.sub("", stem).strip()
    if stripped and stripped != stem:
        return f"{stripped}{ext}", True
    return filename, False


def _is_ignored_file(filename: str) -> bool:
    low = filename.lower()
    if low in IGNORED_FILENAMES or low.startswith("."):
        return True
    if low.endswith(".tmp"):
        return True
    if (low.startswith("crm_tracker_") or low.startswith("vendor_requirements_")) and low.endswith(".xlsx"):
        return True
    return False


def _is_legacy_wrapper_segment(seg: str) -> bool:
    s = seg.strip()
    low = s.lower()
    if not s:
        return True
    if _NEW_FOLDER_RX.match(s):
        return True
    if low in _STRUCTURAL_WRAPPERS:
        return True
    if _DATE_WRAPPER_RX.match(s):
        return True
    return False


def _canonical_gem_bid(year: str, series: str, num: str) -> tuple[str, str]:
    bid_no = f"GEM/{year}/{series.upper()}/{num}"
    bid_slug = f"GEM_{year}_{series.upper()}_{num}"
    return bid_no, bid_slug


def _extract_pdf_header_meta(pdf_path: str) -> dict[str, str]:
    """Fast 1-page header sniff for PDFs absent from metadata.db."""
    out: dict[str, str] = {}
    if not pdf_path or not pdf_path.lower().endswith(".pdf") or not os.path.isfile(pdf_path):
        return out
    try:
        reader = PdfReader(pdf_path)
        if not reader.pages:
            return out
        raw = reader.pages[0].extract_text() or ""
        text = re.sub(r"\s+", " ", raw)

        m_bid = re.search(r"GEM/\d{4}/[A-Z]+/\d+", text, re.IGNORECASE)
        if m_bid:
            parts = m_bid.group(0).split("/")
            out["bid_no"], out["bid_slug"] = _canonical_gem_bid(parts[1], parts[2], parts[3])

        m_end = re.search(
            r"Bid\s+End\s+Date/Time.*?(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})",
            text,
            re.IGNORECASE,
        )
        if m_end:
            out["end_date"] = m_end.group(1)

        for label, field_key in (
            (r"Organisation\s+Name", "organisation"),
            (r"Department\s+Name", "department"),
            (r"Item\s+Category", "title"),
        ):
            m_lbl = re.search(rf"{label}\s*[:\-]?\s*([A-Za-z0-9 ,.&()/_\-]{{3,120}})", text, re.IGNORECASE)
            if m_lbl:
                val = m_lbl.group(1).strip(" :-")
                for stop in ("Ministry", "Department", "Organisation", "Office", "Total Quantity", "Item Category", "MSE", "Startup"):
                    idx = val.find(stop)
                    if idx > 2:
                        val = val[:idx].strip()
                if len(val) >= 3:
                    out[field_key] = val[:140]
    except Exception as exc:
        logger.debug("Header sniff skipped for %s: %s", pdf_path, exc)
    return out


class _MetadataLookup:
    """Indexes scraped metadata across main and personel workspaces for fast enrichment."""

    def __init__(self, tenders_root: str) -> None:
        self.by_bid: dict[str, dict[str, Any]] = {}
        self.by_slug: dict[str, dict[str, Any]] = {}
        self.by_pdf_name: dict[str, dict[str, Any]] = {}
        self.by_bidding_num: dict[str, dict[str, Any]] = {}

        dirs_to_check = [tenders_root]
        personel_dir = os.path.join(tenders_root, "personel")
        if os.path.isdir(personel_dir):
            dirs_to_check.append(personel_dir)

        for tdir in dirs_to_check:
            if not os.path.isdir(tdir):
                continue
            # Avoid creating empty metadata.db on clean tmp_path if no store exists
            has_store = any(
                os.path.exists(os.path.join(tdir, fname))
                for fname in ("metadata.db", "metadata.json", "metadata.csv")
            )
            if not has_store:
                continue
            try:
                records = storage.load_existing_metadata(tdir)
            except Exception:
                records = {}
            for bid_no, rec in (records or {}).items():
                if not isinstance(rec, dict):
                    continue
                self._index_record(bid_no, rec)

    def _index_record(self, bid_no: str, rec: dict[str, Any]) -> None:
        norm_bid = bid_no.strip().lower()
        self.by_bid.setdefault(norm_bid, rec)
        self.by_slug.setdefault(safe_bid_slug(bid_no).lower(), rec)

        local_pdf = str(rec.get("local_pdf_path") or "")
        if local_pdf:
            base = os.path.basename(local_pdf.replace("\\", "/")).lower()
            if base:
                self.by_pdf_name.setdefault(base, rec)
            m_num = _GEM_BIDDING_ID_RX.search(os.path.splitext(base)[0])
            if m_num:
                self.by_bidding_num.setdefault(m_num.group(1), rec)

        pdf_url = str(rec.get("pdf_url") or "")
        m_url_num = re.search(r"/(?:showbidDocument|bidding|bid)/(\d{5,12})", pdf_url)
        if m_url_num:
            self.by_bidding_num.setdefault(m_url_num.group(1), rec)

    def find(self, bid_no: str = "", bid_slug: str = "", filename: str = "") -> dict[str, Any] | None:
        if bid_no and bid_no.lower() in self.by_bid:
            return self.by_bid[bid_no.lower()]
        if bid_slug and bid_slug.lower() in self.by_slug:
            return self.by_slug[bid_slug.lower()]
        if filename:
            clean_f, _ = _strip_copy_suffix(filename)
            for cand in (filename.lower(), clean_f.lower()):
                if cand in self.by_pdf_name:
                    return self.by_pdf_name[cand]
            m_num = _GEM_BIDDING_ID_RX.search(os.path.splitext(clean_f)[0])
            if m_num and m_num.group(1) in self.by_bidding_num:
                return self.by_bidding_num[m_num.group(1)]
        return None


def _resolve_subfolder_and_stage(
    company_id: str, rel_parts: tuple[str, ...], known_subfolders: set[str]
) -> tuple[str, str, int]:
    low_parts = [p.strip().lower() for p in rel_parts if p.strip()]
    penalty = -2 if (low_parts and low_parts[0] == "tenders") else 0

    if company_id == "etspl":
        if "shortlisted" in low_parts:
            return "shortlisted", "shortlisted", 30 + penalty
        if "rejected" in low_parts:
            return "rejected", "rejected", 30 + penalty
        if any("crime_analytics" in p for p in low_parts):
            return "crime_analytics", "review", 25 + penalty
        if "defproc" in low_parts or "brd" in low_parts:
            return "defproc", "review", 25 + penalty
        if "gis" in low_parts:
            return "gis", "review", 25 + penalty
        if "ioe" in low_parts:
            return "ioe", "review", 25 + penalty
        if "solar" in low_parts:
            return "solar", "review", 25 + penalty
        if "tech" in low_parts:
            return "tech", "review", 25 + penalty
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders and sp != "general":
                return sp, "review", 20 + penalty
        return "general", "review", 5 + penalty

    if company_id == "hanmars":
        if "sandeep" in low_parts:
            return "sandeep", "review", 25
        if "biometrics" in low_parts:
            return "biometrics", "review", 20
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders:
                return sp, "review", 20
        return "biometrics", "review", 10

    if company_id == "rajiv_mital":
        if any(p in ("ai interceptor", "ai_interceptor") for p in low_parts):
            return "ai_interceptor", "review", 25
        if any(p in ("enterprisegradedrone", "enterprise_drone") for p in low_parts):
            return "enterprise_drone", "review", 25
        if any(p in ("lidar drones", "lidar_drones") for p in low_parts):
            return "lidar_drones", "review", 25
        if "drone" in low_parts:
            return "drone", "review", 15
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders:
                return sp, "review", 20
        return "drone", "review", 10

    if company_id == "rajiv_tyagi":
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders:
                return sp, "review", 20
        return "power_supply", "review", 15

    if company_id == "small":
        if "mangesh" in low_parts:
            return "mangesh", "review", 25
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders and sp != "small":
                return sp, "review", 20
        return "general", "review", 10

    if company_id == "watchdogs":
        for p in low_parts:
            sp = slugify(p)
            if sp in known_subfolders:
                return sp, "review", 20
        return "general", "review", 15

    for p in low_parts:
        sp = slugify(p)
        if sp in known_subfolders:
            return sp, "review", 20
    return "general", "review", 10


def _identify_primary_anchor(
    entry: SourceFileEntry, meta_lookup: _MetadataLookup
) -> tuple[str, str, str] | None:
    """If ``entry`` is a primary bid anchor, return ``(bid_no, bid_slug, title_hint)``."""
    clean = entry.clean_name
    stem, ext = os.path.splitext(clean)
    ext_low = ext.lower()

    # Do not treat spec/boq/atc files whose names happen to include a GeM ID as primary RFP anchors
    kind = classify_local_filename(clean)
    if kind in ("boq", "atc", "tech_spec"):
        return None

    m_gem = _GEM_BID_RX.search(stem)
    if m_gem and ext_low == ".pdf":
        bid_no, bid_slug = _canonical_gem_bid(m_gem.group(1), m_gem.group(2), m_gem.group(3))
        return bid_no, bid_slug, ""

    m_bidding = _GEM_BIDDING_ID_RX.match(stem)
    if m_bidding and ext_low == ".pdf":
        num = m_bidding.group(1)
        rec = meta_lookup.find(filename=clean)
        if rec and rec.get("bid_no"):
            b = str(rec["bid_no"])
            return b, safe_bid_slug(b), str(rec.get("title") or "")
        hdr = _extract_pdf_header_meta(entry.abs_path)
        if hdr.get("bid_no"):
            return hdr["bid_no"], hdr["bid_slug"], hdr.get("title", "")
        return f"GEM-BIDDING-{num}", f"GeM-Bidding-{num}", ""

    m_tdoc = _TENDER_DOC_RX.match(stem)
    if m_tdoc and ext_low == ".pdf":
        code = m_tdoc.group(1)
        return code, safe_bid_slug(code), ""

    m_work = _WORK_ID_RX.match(stem)
    if m_work and ext_low == ".zip":
        wnum = m_work.group(1)
        return f"DEFPROC/WORK/{wnum}", f"work_{wnum}", ""

    return None


def _directory_needs_migration(company_root: str, known_subfolders: set[str]) -> bool:
    """Check whether a company directory contains any non-canonical files/folders."""
    if not os.path.isdir(company_root):
        return False
    for cur_root, dirs, files in os.walk(company_root):
        rel = os.path.relpath(cur_root, company_root)
        parts = () if rel in (".", "") else tuple(rel.split(os.sep))
        valid_files = [f for f in files if not _is_ignored_file(f)]

        for d in dirs:
            if _NEW_FOLDER_RX.match(d) or d.lower() in _STRUCTURAL_WRAPPERS or _DATE_WRAPPER_RX.match(d):
                return True

        if len(parts) == 0 and valid_files:
            return True
        if len(parts) == 1:
            if slugify(parts[0]) not in known_subfolders:
                return True
            if valid_files:
                return True
        if len(parts) > 2:
            return True
        for f in valid_files:
            _, is_copy = _strip_copy_suffix(f)
            if is_copy:
                return True
    return False


def _discover_company_dossiers(
    company: dict[str, Any],
    company_root: str,
    known_subfolders: set[str],
    meta_lookup: _MetadataLookup,
) -> list[DiscoveredDossier]:
    discovered: list[DiscoveredDossier] = []
    cid = company["id"]
    disk_folder = company["disk_folder"]

    for cur_root, _dirs, files in os.walk(company_root):
        valid_names = sorted(f for f in files if not _is_ignored_file(f))
        if not valid_names:
            continue

        rel = os.path.relpath(cur_root, company_root)
        parts = () if rel in (".", "") else tuple(rel.split(os.sep))
        sf_slug, stage, specificity = _resolve_subfolder_and_stage(cid, parts, known_subfolders)

        # Already in <subfolder>/<dossier> clean path -> slight specificity boost so in-place wins ties
        is_clean_slot = (
            len(parts) == 2
            and parts[0] == sf_slug
            and not _is_legacy_wrapper_segment(parts[1])
        )
        if is_clean_slot:
            specificity += 50

        entries: list[SourceFileEntry] = []
        for fname in valid_names:
            full = os.path.join(cur_root, fname)
            try:
                st = os.stat(full)
                if st.st_size == 0:
                    continue
                sha = _sha256_file(full)
            except OSError:
                continue
            clean_name, is_copy = _strip_copy_suffix(fname)
            entries.append(
                SourceFileEntry(
                    abs_path=full,
                    filename=fname,
                    clean_name=clean_name,
                    sha256=sha,
                    size_bytes=st.st_size,
                    is_copy_named=is_copy,
                )
            )

        if not entries:
            continue

        # Check if parent folder name carries explicit GeM bid + title
        leaf_dir = parts[-1] if parts else ""
        leaf_clean, _ = _strip_copy_suffix(leaf_dir)
        folder_gem_match = _GEM_BID_RX.search(leaf_clean)
        folder_work_match = _WORK_ID_RX.search(leaf_clean)

        anchors: dict[str, DiscoveredDossier] = {}
        non_anchors: list[SourceFileEntry] = []

        for ent in entries:
            anc = _identify_primary_anchor(ent, meta_lookup)
            if anc is not None:
                b_no, b_slug, t_hint = anc
                key = b_slug.lower()
                if key not in anchors:
                    anchors[key] = DiscoveredDossier(
                        company_id=cid,
                        disk_folder=disk_folder,
                        subfolder_slug=sf_slug,
                        stage=stage,
                        specificity=specificity,
                        bid_no=b_no,
                        bid_slug=b_slug,
                        title_hint=t_hint,
                        primary_filename=ent.clean_name,
                        files=[ent],
                    )
                else:
                    anchors[key].files.append(ent)
            else:
                non_anchors.append(ent)

        if len(anchors) == 1:
            sole = next(iter(anchors.values()))
            if folder_gem_match:
                f_bid, f_slug = _canonical_gem_bid(
                    folder_gem_match.group(1), folder_gem_match.group(2), folder_gem_match.group(3)
                )
                sole.bid_no = f_bid
                sole.bid_slug = f_slug
                after = leaf_clean[folder_gem_match.end():].strip(" -_:")
                if after and not sole.title_hint:
                    sole.title_hint = after
            elif parts and not _is_legacy_wrapper_segment(leaf_clean) and not sole.title_hint:
                if leaf_clean.lower() != sole.bid_slug.lower() and leaf_clean.lower() != sf_slug.lower():
                    sole.title_hint = leaf_clean
            sole.files.extend(non_anchors)
            discovered.append(sole)

        elif len(anchors) >= 2:
            # Multi-bid directory: match supporting files to their owning bid via
            # 1) explicit bid/work ID in filename, 2) PDF embedded link filenames, 3) shared timestamp.
            anchor_list = list(anchors.values())
            annot_map: dict[str, set[str]] = {}
            stamp_owner: dict[str, DiscoveredDossier] = {}

            for dos in anchor_list:
                tokens: set[str] = {dos.bid_slug.lower(), dos.bid_no.lower().replace("/", "")}
                m_w = _WORK_ID_RX.search(dos.bid_slug)
                if m_w:
                    tokens.add(m_w.group(1))
                prim_entry = dos.files[0]
                if prim_entry.abs_path.lower().endswith(".pdf"):
                    for lnk in extract_pdf_annexure_links(prim_entry.abs_path):
                        tokens.add(str(lnk.get("filename") or "").lower())
                        url_tail = os.path.basename(str(lnk.get("url") or "").lower())
                        if url_tail:
                            tokens.add(url_tail)
                        m_ts = _TS_STAMP_RX.search(str(lnk.get("url") or ""))
                        if m_ts:
                            stamp_owner[m_ts.group(1)] = dos
                annot_map[dos.bid_slug] = tokens

            unmatched: list[SourceFileEntry] = []
            for nent in non_anchors:
                cname_low = sanitize_filename(nent.clean_name).lower()
                raw_low = nent.clean_name.lower()
                compact_low = re.sub(r"[^a-z0-9]", "", raw_low)
                matched_dos: DiscoveredDossier | None = None

                for dos in anchor_list:
                    toks = annot_map[dos.bid_slug]
                    if cname_low in toks or raw_low in toks:
                        matched_dos = dos
                        break
                    for tok in toks:
                        if len(tok) >= 6 and tok in compact_low:
                            matched_dos = dos
                            break
                    if matched_dos:
                        break

                if matched_dos is None:
                    m_ts = _TS_STAMP_RX.search(raw_low)
                    if m_ts and m_ts.group(1) in stamp_owner:
                        matched_dos = stamp_owner[m_ts.group(1)]

                if matched_dos is not None:
                    matched_dos.files.append(nent)
                    m_ts = _TS_STAMP_RX.search(raw_low)
                    if m_ts:
                        stamp_owner.setdefault(m_ts.group(1), matched_dos)
                else:
                    unmatched.append(nent)

            # Standalone RFP/EOI PDFs in a work-zip folder become their own dossiers;
            # remaining unmatched annexures attach to the first PDF anchor.
            pdf_anchors = [d for d in anchor_list if d.primary_filename.lower().endswith(".pdf")]
            for uent in unmatched:
                if pdf_anchors:
                    pdf_anchors[0].files.append(uent)
                else:
                    stem = os.path.splitext(uent.clean_name)[0]
                    b_slug = safe_bid_slug(stem)
                    discovered.append(
                        DiscoveredDossier(
                            company_id=cid,
                            disk_folder=disk_folder,
                            subfolder_slug=sf_slug,
                            stage=stage,
                            specificity=specificity,
                            bid_no=b_slug,
                            bid_slug=b_slug,
                            title_hint=stem.replace("_", " "),
                            primary_filename=uent.clean_name,
                            files=[uent],
                        )
                    )

            discovered.extend(anchor_list)

        else:
            # Zero GeM/work-zip primary anchors in cur_root
            boq_num = None
            for ent in entries:
                m_boq = _BOQ_ID_RX.search(ent.clean_name)
                if m_boq:
                    boq_num = m_boq.group(1)
                    break

            if folder_work_match:
                wnum = folder_work_match.group(1)
                b_no, b_slug = f"DEFPROC/WORK/{wnum}", f"work_{wnum}"
                prim = next((e.clean_name for e in entries if e.clean_name.lower().endswith(".pdf")), entries[0].clean_name)
                discovered.append(
                    DiscoveredDossier(
                        company_id=cid,
                        disk_folder=disk_folder,
                        subfolder_slug=sf_slug,
                        stage=stage,
                        specificity=specificity,
                        bid_no=b_no,
                        bid_slug=b_slug,
                        primary_filename=prim,
                        files=entries,
                    )
                )
            elif boq_num and len(entries) > 1:
                b_no, b_slug = f"DPP/BOQ/{boq_num}", f"BOQ_{boq_num}"
                prim = next((e.clean_name for e in entries if e.clean_name.lower().endswith(".pdf")), entries[0].clean_name)
                discovered.append(
                    DiscoveredDossier(
                        company_id=cid,
                        disk_folder=disk_folder,
                        subfolder_slug=sf_slug,
                        stage=stage,
                        specificity=specificity,
                        bid_no=b_no,
                        bid_slug=b_slug,
                        primary_filename=prim,
                        files=entries,
                    )
                )
            elif parts and not _is_legacy_wrapper_segment(leaf_clean) and leaf_clean.lower() != sf_slug.lower():
                # Named project folder (or already-clean non-GeM dossier)
                hdr_bid = None
                for ent in entries:
                    if ent.clean_name.lower().endswith(".pdf"):
                        hdr = _extract_pdf_header_meta(ent.abs_path)
                        if hdr.get("bid_no"):
                            hdr_bid = (hdr["bid_no"], hdr["bid_slug"], hdr.get("title", ""))
                            break
                if hdr_bid:
                    b_no, b_slug, t_hint = hdr_bid
                else:
                    b_slug = safe_bid_slug(leaf_clean)[:80]
                    b_no = b_slug
                    t_hint = leaf_clean
                prim = next((e.clean_name for e in entries if e.clean_name.lower().endswith(".pdf")), entries[0].clean_name)
                discovered.append(
                    DiscoveredDossier(
                        company_id=cid,
                        disk_folder=disk_folder,
                        subfolder_slug=sf_slug,
                        stage=stage,
                        specificity=specificity,
                        bid_no=b_no,
                        bid_slug=b_slug,
                        title_hint=t_hint,
                        primary_filename=prim,
                        files=entries,
                    )
                )
            else:
                # Loose non-anchor file(s) in root/wrapper folder: check PDF header for GeM bid first
                by_key: dict[str, DiscoveredDossier] = {}
                for ent in entries:
                    hdr = _extract_pdf_header_meta(ent.abs_path) if ent.clean_name.lower().endswith(".pdf") else {}
                    if hdr.get("bid_no"):
                        b_no, b_slug = hdr["bid_no"], hdr["bid_slug"]
                        t_hint = hdr.get("title", "")
                    else:
                        stem = os.path.splitext(ent.clean_name)[0]
                        b_slug = safe_bid_slug(stem)[:80]
                        b_no = b_slug
                        t_hint = stem.replace("_", " ")
                    k = b_slug.lower()
                    if k not in by_key:
                        by_key[k] = DiscoveredDossier(
                            company_id=cid,
                            disk_folder=disk_folder,
                            subfolder_slug=sf_slug,
                            stage=stage,
                            specificity=specificity,
                            bid_no=b_no,
                            bid_slug=b_slug,
                            title_hint=t_hint,
                            primary_filename=ent.clean_name,
                            files=[ent],
                        )
                    else:
                        by_key[k].files.append(ent)
                discovered.extend(by_key.values())

    return discovered


def _prune_empty_legacy_dirs(company_root: str, keep_subfolder_slugs: set[str]) -> int:
    """Remove empty directories bottom-up after verifying files are safely migrated."""
    removed = 0
    if not os.path.isdir(company_root):
        return 0
    for cur_root, _dirs, _files in os.walk(company_root, topdown=False):
        if os.path.abspath(cur_root) == os.path.abspath(company_root):
            continue
        rel = os.path.relpath(cur_root, company_root)
        parts = tuple(rel.split(os.sep))

        # Remove ignored junk files inside otherwise-empty legacy wrapper dirs so empty shells vanish cleanly
        remaining_entries = os.listdir(cur_root)
        non_junk = [x for x in remaining_entries if not _is_ignored_file(x)]
        if not non_junk and any(_is_legacy_wrapper_segment(p) for p in parts):
            for junk in remaining_entries:
                junk_path = os.path.join(cur_root, junk)
                if os.path.isfile(junk_path):
                    try:
                        os.remove(junk_path)
                    except OSError:
                        pass

        # Preserve top-level active CRM subfolder directories even when currently empty
        if len(parts) == 1 and parts[0] in keep_subfolder_slugs and not _NEW_FOLDER_RX.match(parts[0]):
            continue

        try:
            if not os.listdir(cur_root):
                os.rmdir(cur_root)
                removed += 1
        except OSError:
            pass
    return removed


def organize_and_import_legacy_folders(
    repo: CRMRepository,
    tenders_dir: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Back up, deduplicate, unbundle ``New folder*`` & multi-bid folders, and import into ``crm.db``."""
    repo.init_db()
    repo.seed_defaults()

    tenders_root = os.path.abspath(tenders_dir or paths.TENDERS_DIR)
    os.makedirs(tenders_root, exist_ok=True)

    companies = repo.list_companies()
    existing_company_dirs: list[tuple[dict[str, Any], str, set[str]]] = []
    any_dirty = False

    for comp in companies:
        c_root = os.path.abspath(os.path.join(tenders_root, comp["disk_folder"]))
        if os.path.commonpath([tenders_root, c_root]) != tenders_root:
            continue
        if os.path.isdir(c_root):
            sfs = {sf["slug"] for sf in repo.list_subfolders(comp["id"])}
            existing_company_dirs.append((comp, c_root, sfs))
            if _directory_needs_migration(c_root, sfs):
                any_dirty = True

    if not existing_company_dirs:
        return {
            "status": "ok",
            "tenders_dir": tenders_root,
            "backup_dir": None,
            "dossiers_imported": 0,
            "files_imported": 0,
            "duplicates_removed": 0,
            "shells_removed": 0,
        }

    # 1. Pre-migration full backup BEFORE modifying any company folder
    backup_dir: str | None = None
    prev_backup = repo.get_meta("last_migration_backup_dir")
    if any_dirty or force or not prev_backup or not os.path.isdir(prev_backup):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(tenders_root, "backups", f"pre_crm_migration_{stamp}")
        os.makedirs(backup_dir, exist_ok=True)
        for _comp, c_root, _sfs in existing_company_dirs:
            folder_name = os.path.basename(c_root)
            dest_backup = os.path.join(backup_dir, folder_name)
            shutil.copytree(c_root, dest_backup, dirs_exist_ok=True)
        repo.set_meta("last_migration_backup_dir", backup_dir)
    else:
        backup_dir = prev_backup

    meta_lookup = _MetadataLookup(tenders_root)

    dossiers_imported = 0
    files_imported = 0
    duplicates_removed = 0
    shells_removed = 0

    for comp, c_root, known_sfs in existing_company_dirs:
        cid = comp["id"]
        raw_dossiers = _discover_company_dossiers(comp, c_root, known_sfs, meta_lookup)

        # Merge duplicate dossiers for the same (company_id, bid_slug) — e.g. TENDERS/RFPs vs RFPs
        merged_by_bid: dict[str, DiscoveredDossier] = {}
        for dos in raw_dossiers:
            key = dos.bid_slug.lower()
            if key not in merged_by_bid:
                merged_by_bid[key] = dos
            else:
                curr = merged_by_bid[key]
                if dos.specificity > curr.specificity:
                    curr.subfolder_slug = dos.subfolder_slug
                    curr.stage = dos.stage
                    curr.specificity = dos.specificity
                if dos.bid_no.startswith("GEM/") and not curr.bid_no.startswith("GEM/"):
                    curr.bid_no = dos.bid_no
                if dos.title_hint and not curr.title_hint:
                    curr.title_hint = dos.title_hint
                if not curr.primary_filename and dos.primary_filename:
                    curr.primary_filename = dos.primary_filename
                curr.files.extend(dos.files)

        for dos in merged_by_bid.values():
            sf = repo.ensure_subfolder(cid, dos.subfolder_slug.replace("_", " ").title(), slug=dos.subfolder_slug)
            known_sfs.add(sf["slug"])

            dest_dossier_abs = os.path.abspath(os.path.join(c_root, sf["slug"], dos.bid_slug))
            if os.path.commonpath([c_root, dest_dossier_abs]) != c_root:
                raise ValueError(f"Unsafe dossier path resolved: {dest_dossier_abs}")
            os.makedirs(dest_dossier_abs, exist_ok=True)

            # Group files by SHA-256 so exact copies (including "(1)" and TENDERS/RFPs duplicates) collapse cleanly
            by_sha: dict[str, list[SourceFileEntry]] = {}
            for fent in dos.files:
                by_sha.setdefault(fent.sha256, []).append(fent)

            saved_entries: list[tuple[str, SourceFileEntry, str]] = []
            used_names: set[str] = set()

            for sha, group in by_sha.items():
                # Prefer already-in-dest entry, else non-copy-named entry, else shortest clean name
                group_sorted = sorted(
                    group,
                    key=lambda e: (
                        0 if os.path.abspath(os.path.dirname(e.abs_path)) == dest_dossier_abs else 1,
                        1 if e.is_copy_named else 0,
                        len(e.clean_name),
                        e.filename.lower(),
                    ),
                )
                winner = group_sorted[0]
                target_name = sanitize_filename(winner.clean_name)
                if target_name.lower() in used_names:
                    stem, ext = os.path.splitext(target_name)
                    target_name = f"{stem[:140]}_{sha[:8]}{ext}"
                used_names.add(target_name.lower())

                dest_file_abs = os.path.abspath(os.path.join(dest_dossier_abs, target_name))
                if os.path.commonpath([dest_dossier_abs, dest_file_abs]) != dest_dossier_abs:
                    continue

                if os.path.abspath(winner.abs_path) != dest_file_abs:
                    if os.path.exists(dest_file_abs):
                        existing_sha = _sha256_file(dest_file_abs)
                        if existing_sha != sha:
                            stem, ext = os.path.splitext(target_name)
                            target_name = f"{stem[:140]}_{sha[:8]}{ext}"
                            dest_file_abs = os.path.abspath(os.path.join(dest_dossier_abs, target_name))
                            shutil.copy2(winner.abs_path, dest_file_abs)
                    else:
                        shutil.copy2(winner.abs_path, dest_file_abs)

                # Verify destination integrity BEFORE removing migrated / duplicate source files
                if os.path.isfile(dest_file_abs) and os.path.getsize(dest_file_abs) == winner.size_bytes:
                    dest_sha = _sha256_file(dest_file_abs)
                    if dest_sha == sha:
                        for idx_src, src_ent in enumerate(group_sorted):
                            if os.path.abspath(src_ent.abs_path) != dest_file_abs and os.path.exists(src_ent.abs_path):
                                try:
                                    os.remove(src_ent.abs_path)
                                    if idx_src > 0 or src_ent.is_copy_named:
                                        duplicates_removed += 1
                                except OSError as exc:
                                    logger.debug("Could not remove migrated source %s: %s", src_ent.abs_path, exc)

                saved_entries.append((target_name, winner, dest_file_abs))

            if not saved_entries:
                continue

            # Enrich metadata from metadata.db / personel / finalized store / 1-page PDF header
            scraped = meta_lookup.find(
                bid_no=dos.bid_no,
                bid_slug=dos.bid_slug,
                filename=dos.primary_filename,
            )
            rfp_tuple = next(
                (
                    t for t in saved_entries
                    if classify_local_filename(t[0], dos.primary_filename) == "rfp"
                ),
                saved_entries[0],
            )
            hdr_meta = (
                _extract_pdf_header_meta(rfp_tuple[2])
                if not scraped and rfp_tuple[0].lower().endswith(".pdf")
                else {}
            )

            canonical_bid_no = (
                str(scraped["bid_no"])
                if scraped and scraped.get("bid_no")
                else (hdr_meta.get("bid_no") or dos.bid_no)
            )
            analysis = (scraped.get("analysis") if isinstance(scraped, dict) else None) or {}
            fin_rec = master_sheet_manager.get_record(canonical_bid_no) or {}

            title = (
                (scraped.get("title") if scraped else None)
                or fin_rec.get("title")
                or dos.title_hint
                or analysis.get("primary_item")
                or hdr_meta.get("title")
                or canonical_bid_no
            )
            organisation = (
                analysis.get("buyer_org")
                or (scraped.get("department") if scraped else None)
                or fin_rec.get("organisation")
                or hdr_meta.get("organisation")
                or hdr_meta.get("department")
                or ""
            )
            department = (
                (scraped.get("department") if scraped else None)
                or hdr_meta.get("department")
                or organisation
            )
            location = analysis.get("consignee_state") or fin_rec.get("location") or ""
            work_category = (
                fin_rec.get("work_category")
                or (analysis.get("business_line") or {}).get("label")
                or comp.get("category")
                or ""
            )
            end_date = (
                (scraped.get("end_date") if scraped else None)
                or fin_rec.get("end_date")
                or hdr_meta.get("end_date")
                or ""
            )
            emd_amount = analysis.get("emd_amount")
            est_value = analysis.get("est_value_inr") or (scraped.get("est_value_inr") if scraped else None)

            dossier_rel = os.path.relpath(dest_dossier_abs, tenders_root).replace("\\", "/")
            rfp_rel = os.path.relpath(rfp_tuple[2], tenders_root).replace("\\", "/")

            tender_row = repo.upsert_tender(
                {
                    "bid_no": canonical_bid_no,
                    "bid_slug": dos.bid_slug,
                    "company_id": cid,
                    "subfolder_id": sf["id"],
                    "sl_no": fin_rec.get("sl_no"),
                    "title": str(title),
                    "organisation": str(organisation),
                    "department": str(department),
                    "location": str(location),
                    "work_category": str(work_category),
                    "source_id": str((scraped or {}).get("source_id") or "gem"),
                    "source_name": str((scraped or {}).get("source_name") or "GEM"),
                    "start_date": str((scraped or {}).get("start_date") or ""),
                    "end_date": str(end_date),
                    "emd_amount": emd_amount,
                    "est_value_inr": est_value,
                    "dossier_rel_path": dossier_rel,
                    "rfp_pdf_rel_path": rfp_rel if rfp_rel.lower().endswith(".pdf") else "",
                    "pdf_url": str((scraped or {}).get("pdf_url") or ""),
                    "drive_link": str(fin_rec.get("rfp_link") or ""),
                    "tech_spec_url": str(fin_rec.get("tech_spec_url") or ""),
                    "stage": dos.stage,
                    "master_synced": 1 if fin_rec else 0,
                    "metadata_json": json.dumps(analysis, ensure_ascii=False) if analysis else "{}",
                }
            )
            dossiers_imported += 1

            prim_clean = sanitize_filename(dos.primary_filename) if dos.primary_filename else rfp_tuple[0]
            for target_name, winner_ent, dest_file_abs in saved_entries:
                f_rel = os.path.relpath(dest_file_abs, tenders_root).replace("\\", "/")
                f_kind = classify_local_filename(target_name, primary_bid_filename=prim_clean)
                mime = mimetypes.guess_type(target_name)[0] or "application/octet-stream"
                repo.upsert_file(
                    {
                        "crm_tender_id": tender_row["id"],
                        "filename": target_name,
                        "original_filename": winner_ent.clean_name,
                        "rel_path": f_rel,
                        "file_kind": f_kind,
                        "mime_type": mime,
                        "size_bytes": winner_ent.size_bytes,
                        "sha256": winner_ent.sha256,
                    }
                )
                files_imported += 1

        shells_removed += _prune_empty_legacy_dirs(c_root, known_sfs)

    return {
        "status": "ok",
        "tenders_dir": tenders_root,
        "backup_dir": backup_dir,
        "dossiers_imported": dossiers_imported,
        "files_imported": files_imported,
        "duplicates_removed": duplicates_removed,
        "shells_removed": shells_removed,
    }

"""Concept-aware query expansion and result verification for tender portals."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import re
from typing import Dict, Iterable, Optional, Tuple

import paths
from gemsentry.constants import logger
from gemsentry.textmatch import keyword_hit


_FUZZY_ALIAS_THRESHOLD = 0.88


@dataclass(frozen=True)
class SearchPlan:
    """A canonical user intent and the portal queries used to retrieve it."""

    canonical_keyword: str
    queries: Tuple[str, ...]
    concept_id: Optional[str] = None
    positive_terms: Tuple[str, ...] = ()
    exclude_terms: Tuple[str, ...] = ()


def _normalize(value) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _dedupe(values: Iterable[str]) -> Tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        result.append(clean)
        seen.add(key)
    return tuple(result)


def load_search_concepts(path: Optional[str] = None) -> Dict[str, dict]:
    """Load configurable aliases; malformed config degrades to exact search."""
    config_path = path or paths.SEARCH_CONCEPTS_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("top-level value must be an object")
        return payload
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Search concept config unavailable (%s): %s", config_path, exc)
        return {}


def _concept_aliases(concept: dict) -> Tuple[str, ...]:
    return _dedupe([
        concept.get("canonical_keyword", ""),
        *(concept.get("aliases") or []),
    ])


def _alias_similarity(query: str, alias: str) -> float:
    """Compare a query with an alias and each distinctive alias token."""
    if len(query) < 6:
        return 0.0
    candidates = [alias, *alias.split()]
    return max(
        (SequenceMatcher(None, query, candidate).ratio()
         for candidate in candidates if len(candidate) >= 6),
        default=0.0,
    )


def build_search_plan(keyword: str, concepts: Optional[Dict[str, dict]] = None) -> SearchPlan:
    """Resolve a keyword to a configurable concept and its query variants."""
    clean_keyword = re.sub(r"\s+", " ", str(keyword or "")).strip()
    if not clean_keyword:
        raise ValueError("keyword must not be empty")

    normalized = _normalize(clean_keyword)
    catalog = concepts if concepts is not None else load_search_concepts()
    selected = None
    selected_id = None
    selected_score = 0.0

    for concept_id, concept in catalog.items():
        if not isinstance(concept, dict):
            continue
        aliases = [_normalize(alias) for alias in _concept_aliases(concept)]
        if normalized in aliases:
            selected_id, selected = concept_id, concept
            break
        score = max((_alias_similarity(normalized, alias) for alias in aliases), default=0.0)
        if score >= _FUZZY_ALIAS_THRESHOLD and score > selected_score:
            selected_id, selected, selected_score = concept_id, concept, score

    if selected is None:
        return SearchPlan(canonical_keyword=clean_keyword, queries=(clean_keyword,))

    canonical = str(selected.get("canonical_keyword") or clean_keyword).strip()
    queries = _dedupe([canonical, *(selected.get("queries") or []), clean_keyword])
    return SearchPlan(
        canonical_keyword=canonical,
        queries=queries,
        concept_id=selected_id,
        positive_terms=_dedupe(selected.get("positive_terms") or []),
        exclude_terms=_dedupe(selected.get("exclude_terms") or []),
    )


def expand_keywords(keywords: Iterable[str]) -> Tuple[str, ...]:
    """Flatten concept query plans for adapters that filter listings locally."""
    expanded = []
    for keyword in keywords or ():
        expanded.extend(build_search_plan(keyword).queries)
    return _dedupe(expanded)


def matches_search_result(tender: dict, plan: SearchPlan) -> bool:
    """Require actual card evidence for expanded concepts.

    GeM full-text search is broad enough that ``facial recognition`` returns
    ``Facial Tissue Papers``. Known concepts therefore apply positive and
    negative evidence to the card itself. Unknown queries retain portal
    behavior so this layer never narrows an unconfigured user search.
    """
    if plan.concept_id is None:
        return True

    haystack = " ".join([
        str(tender.get("title") or ""),
        str(tender.get("item_category") or ""),
        str(tender.get("primary_item") or ""),
    ]).casefold()
    if any(keyword_hit(term, haystack) for term in plan.exclude_terms):
        return False
    return any(keyword_hit(term, haystack) for term in plan.positive_terms)

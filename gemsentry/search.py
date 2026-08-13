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
_RELATED_TERM_THRESHOLD = 0.62
_MAX_PROFILE_QUERIES = 8
_MAX_PROFILE_POSITIVE_TERMS = 16
_ANCHOR_STOPWORDS = {
    "based", "custom", "goods", "service", "services", "supply", "system", "systems",
}


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


def load_company_profile(path: Optional[str] = None) -> dict:
    """Load business-line vocabulary used for profile-wide search planning."""
    profile_path = path or paths.COMPANY_PROFILE_PATH
    try:
        with open(profile_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("top-level value must be an object")
        return payload
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Company profile unavailable for search planning (%s): %s", profile_path, exc)
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


def _profile_candidates(profile: dict):
    """Yield every configured business term with its owning line and strength."""
    for line in profile.get("business_lines") or ():
        if not isinstance(line, dict):
            continue
        strong = {_normalize(term) for term in line.get("strong_keywords") or ()}
        for term in _dedupe([
            *(line.get("strong_keywords") or []),
            *(line.get("keywords") or []),
        ]):
            normalized = _normalize(term)
            if normalized:
                yield line, term, normalized in strong


def _stem_token(token: str) -> str:
    """Small deterministic stemmer for product phrases (meter/metering, cable/cables)."""
    if len(token) > 6 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _related_profile_terms(canonical: str, line: dict) -> Tuple[str, ...]:
    """Find close aliases within one business line without expanding to the whole line."""
    base = _normalize(canonical)
    base_tokens = {_stem_token(token) for token in base.split() if len(token) > 2}
    candidates = []
    strong = {_normalize(term) for term in line.get("strong_keywords") or ()}
    for term in _dedupe([
        *(line.get("strong_keywords") or []),
        *(line.get("keywords") or []),
    ]):
        normalized = _normalize(term)
        if not normalized or normalized == base:
            continue
        tokens = {_stem_token(token) for token in normalized.split() if len(token) > 2}
        shared = base_tokens & tokens
        similarity = SequenceMatcher(None, base, normalized).ratio()
        # Single-token searches only gain spelling/inflection variants. This
        # prevents a query such as "software" from exploding into every IT term.
        if len(base_tokens) <= 1:
            related = bool(shared) and similarity >= 0.72
        else:
            related = bool(shared) and similarity >= _RELATED_TERM_THRESHOLD
        if related:
            candidates.append((normalized in strong, similarity, len(normalized), term))
    candidates.sort(reverse=True)
    return _dedupe(item[3] for item in candidates)


def _query_anchors(phrase: str) -> Tuple[str, ...]:
    """Return up to two distinctive words for portals that mishandle phrases."""
    tokens = {
        token for token in _normalize(phrase).split()
        if len(token) >= 5 and token not in _ANCHOR_STOPWORDS
    }
    return tuple(sorted(tokens, key=lambda token: (-len(token), token))[:2])


def _profile_search_plan(clean_keyword: str, profile: dict) -> Optional[SearchPlan]:
    normalized = _normalize(clean_keyword)
    selected = None
    best_rank = None
    for line, term, is_strong in _profile_candidates(profile):
        alias = _normalize(term)
        exact = normalized == alias
        similarity = 1.0 if exact else _alias_similarity(normalized, alias)
        if not exact and similarity < _FUZZY_ALIAS_THRESHOLD:
            continue
        rank = (exact, similarity, is_strong, len(alias))
        if best_rank is None or rank > best_rank:
            selected = line, term
            best_rank = rank

    if selected is None:
        return None

    line, canonical = selected
    related = _related_profile_terms(canonical, line)
    positive_terms = _dedupe([canonical, *related])[:_MAX_PROFILE_POSITIVE_TERMS]
    queries = _dedupe([
        canonical,
        clean_keyword,
        *_query_anchors(canonical),
        *related,
    ])[:_MAX_PROFILE_QUERIES]
    concept_slug = re.sub(r"[^a-z0-9]+", "_", _normalize(canonical)).strip("_")
    return SearchPlan(
        canonical_keyword=str(canonical),
        queries=queries,
        concept_id=f"profile:{line.get('id') or 'business_line'}:{concept_slug}",
        positive_terms=positive_terms,
        exclude_terms=_dedupe(line.get("exclude_keywords") or []),
    )


def build_search_plan(
    keyword: str,
    concepts: Optional[Dict[str, dict]] = None,
    profile: Optional[dict] = None,
) -> SearchPlan:
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
        profile_plan = _profile_search_plan(
            clean_keyword,
            profile if profile is not None else load_company_profile(),
        )
        if profile_plan is not None:
            return profile_plan
        # Even an unknown phrase must be verified against the returned card.
        # GeM full-text occasionally returns entirely unrelated listings.
        return SearchPlan(
            canonical_keyword=clean_keyword,
            queries=_dedupe([clean_keyword, *_query_anchors(clean_keyword)]),
            positive_terms=(clean_keyword,),
        )

    canonical = str(selected.get("canonical_keyword") or clean_keyword).strip()
    queries = _dedupe([
        canonical,
        *(selected.get("queries") or []),
        clean_keyword,
        *_query_anchors(canonical),
    ])
    return SearchPlan(
        canonical_keyword=canonical,
        queries=queries,
        concept_id=selected_id,
        positive_terms=_dedupe(selected.get("positive_terms") or []),
        exclude_terms=_dedupe(selected.get("exclude_terms") or []),
    )


def expand_keywords(keywords: Iterable[str]) -> Tuple[str, ...]:
    """Expand safe positive phrases for adapters that filter listings locally.

    GeM alone receives broad single-word anchors because its results pass back
    through ``matches_search_result``. Other adapters use these values as the
    final local OR-filter, so giving them anchors would create false positives.
    """
    concepts = load_search_concepts()
    profile = load_company_profile()
    expanded = []
    for keyword in keywords or ():
        plan = build_search_plan(keyword, concepts=concepts, profile=profile)
        expanded.extend(plan.positive_terms or plan.queries)
    return _dedupe(expanded)


def matches_search_result(tender: dict, plan: SearchPlan) -> bool:
    """Require actual card evidence for expanded concepts.

    GeM full-text search is broad enough that ``facial recognition`` returns
    ``Facial Tissue Papers`` and ``smart meter`` can return an ANPR bundle.
    Every result therefore needs card evidence; known/profile concepts add
    aliases and business-line exclusions, while unknown phrases verify the
    literal user intent.
    """
    haystack = " ".join([
        str(tender.get("title") or ""),
        str(tender.get("item_category") or ""),
        str(tender.get("primary_item") or ""),
    ]).casefold()
    if any(keyword_hit(term, haystack) for term in plan.exclude_terms):
        return False
    if any(keyword_hit(term, haystack) for term in plan.positive_terms):
        return True

    # Phrase order and inserted qualifiers vary on tender cards ("smart
    # prepaid energy meter"). Accept a multi-word canonical intent when every
    # meaningful token is present as a whole word; exclusions still win above.
    tokens = [token for token in _normalize(plan.canonical_keyword).split() if len(token) > 2]
    if len(tokens) > 1:
        return all(keyword_hit(token, haystack) for token in tokens)
    return False

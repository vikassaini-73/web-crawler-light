"""
New York DOS Registry Resolver

Searches the NY Active Corporations dataset, scores candidates using
multiple signals, and enriches the best match with filings/stock/entity.

Scoring (0–100 pts):
  name similarity       40 pts  (fuzzy after normalization)
  city match            20 pts
  ZIP / postal match    20 pts
  county match          10 pts
  entity_type presence  10 pts  (tie-break; any entity type = active)

Resolution rules (mirrors the UK resolver):
  RESOLVED   top_score >= 65  AND  gap >= 15
  AMBIGUOUS  top_score >= 50  AND  gap <  15
  UNRESOLVED top_score <  50

The system prefers NO MATCH over a WRONG MATCH.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fuzzy matching — thefuzz (pure python, no C dependency required)
try:
    from thefuzz import fuzz
    _HAS_FUZZ = True
except ImportError:
    _HAS_FUZZ = False
    logger.warning(
        "[NYResolver] thefuzz not installed — falling back to basic name matching. "
        "Install with: pip install thefuzz"
    )

# ── Legal suffix normalization ────────────────────────────────────────────────

_SUFFIX_REPLACEMENTS = [
    (r"\bINCORPORATED\b", "INC"),
    (r"\bCORPORATION\b",  "CORP"),
    (r"\bCOMPANY\b",      "CO"),
    (r"\bLIMITED\b",      "LTD"),
    (r"\bLIABILITY\b",    ""),
    (r"\bPARTNERSHIP\b",  "PRTNR"),
]

_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")

SCORE_RESOLVED_MIN  = 65
SCORE_AMBIGUOUS_MIN = 50
SCORE_GAP_MIN       = 15


def _normalize_name(name: str) -> str:
    """
    Normalize a company name for fuzzy comparison:
      1. Uppercase
      2. Strip punctuation
      3. Normalize legal suffixes
      4. Collapse whitespace
    """
    n = name.upper().strip()
    n = _PUNCT_RE.sub(" ", n)
    for pattern, replacement in _SUFFIX_REPLACEMENTS:
        n = re.sub(pattern, replacement, n)
    n = _SPACE_RE.sub(" ", n).strip()
    return n


def _name_score(website_name: str, candidate_name: str) -> float:
    """Compute name similarity 0–40 pts."""
    a = _normalize_name(website_name)
    b = _normalize_name(candidate_name)

    if not a or not b:
        return 0.0

    if _HAS_FUZZ:
        # token_set_ratio handles word reordering and extra words well
        ratio = fuzz.token_set_ratio(a, b) / 100.0
    else:
        # Fallback: character-level overlap
        import difflib
        ratio = difflib.SequenceMatcher(None, a, b).ratio()

    pts = ratio * 40.0

    # Exact match bonus after normalization
    if a == b:
        pts = 40.0
    elif a in b or b in a:
        pts = max(pts, 35.0)

    return round(pts, 2)


def _city_score(website_city: Optional[str], candidate_city: Optional[str]) -> float:
    """City match 0–20 pts."""
    if not website_city or not candidate_city:
        return 0.0
    if website_city.upper().strip() == candidate_city.upper().strip():
        return 20.0
    # Partial city match (e.g. "New York" in "New York City")
    if (website_city.upper() in candidate_city.upper() or
            candidate_city.upper() in website_city.upper()):
        return 10.0
    return 0.0


def _zip_score(website_zip: Optional[str], candidate_zip: Optional[str]) -> float:
    """ZIP code match 0–20 pts."""
    if not website_zip or not candidate_zip:
        return 0.0
    # Normalize: strip spaces, take first 5 digits
    a = re.sub(r"\D", "", website_zip)[:5]
    b = re.sub(r"\D", "", candidate_zip)[:5]
    if not a or not b:
        return 0.0
    if a == b:
        return 20.0
    # First 3 digits = same postal zone
    if len(a) >= 3 and a[:3] == b[:3]:
        return 8.0
    return 0.0


def _county_score(website_city: Optional[str], candidate_county: Optional[str]) -> float:
    """County match 0–10 pts (supporting signal)."""
    if not website_city or not candidate_county:
        return 0.0
    # NYC boroughs → county mapping
    borough_county = {
        "new york city": "new york", "nyc": "new york",
        "manhattan": "new york",
        "brooklyn": "kings",
        "queens": "queens",
        "bronx": "bronx",
        "staten island": "richmond",
    }
    city_lower = website_city.lower()
    county_lower = candidate_county.lower()

    mapped = borough_county.get(city_lower)
    if mapped and mapped in county_lower:
        return 10.0
    if city_lower in county_lower or county_lower in city_lower:
        return 5.0
    return 0.0


def _entity_type_score(entity_type: Optional[str]) -> float:
    """Tie-breaker: any entity_type present = 10 pts."""
    return 10.0 if entity_type else 0.0


def _score_candidate(
    candidate: Dict[str, Any],
    search_name: str,
    city: Optional[str],
    postal_code: Optional[str],
) -> Dict[str, Any]:
    """Score a single NY DOS candidate record against the website data."""
    cand_name   = candidate.get("current_entity_name", "")
    cand_city   = candidate.get("dos_process_city", "")
    cand_zip    = candidate.get("dos_process_zip", "")
    cand_county = candidate.get("county", "")
    cand_type   = candidate.get("entity_type", "")

    s_name   = _name_score(search_name, cand_name)
    s_city   = _city_score(city, cand_city)
    s_zip    = _zip_score(postal_code, cand_zip)
    s_county = _county_score(city, cand_county)
    s_type   = _entity_type_score(cand_type)

    # Bonus: exact ZIP match is a very strong signal for tiebreaking
    zip_exact_bonus = 0.0
    if postal_code and cand_zip:
        a = re.sub(r"\D", "", postal_code)[:5]
        b = re.sub(r"\D", "", cand_zip)[:5]
        if a and b and a == b:
            zip_exact_bonus = 5.0  # enough to separate tied candidates

    total = s_name + s_city + s_zip + s_county + s_type + zip_exact_bonus

    out = dict(candidate)
    out["_score_total"] = round(total, 2)
    out["_score_detail"] = {
        "name":            round(s_name, 2),
        "city":            round(s_city, 2),
        "zip":             round(s_zip, 2),
        "county":          round(s_county, 2),
        "entity_type":     round(s_type, 2),
        "zip_exact_bonus": round(zip_exact_bonus, 2),
    }
    return out


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class NYResolutionResult:
    """
    Full record of a New York DOS resolution attempt.

    status:
      "resolved"   — confident unique match
      "ambiguous"  — multiple near-equal candidates, refused to guess
      "unresolved" — no candidate scored high enough
      "not_found"  — search returned no results
      "unavailable"— API error / timeout
      "skipped"    — no name available to search with
    """
    status: str
    matched: bool = False
    dos_id: Optional[str] = None
    matched_name: Optional[str] = None
    match_method: Optional[str] = None
    confidence: float = 0.0
    search_name: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    # Enrichment — populated after resolution
    filings: List[Dict[str, Any]] = field(default_factory=list)
    stock_info: List[Dict[str, Any]] = field(default_factory=list)
    entity_info: List[Dict[str, Any]] = field(default_factory=list)
    address_info: List[Dict[str, Any]] = field(default_factory=list)
    raw_profile: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":       self.status,
            "matched":      self.matched,
            "dos_id":       self.dos_id,
            "matched_name": self.matched_name,
            "match_method": self.match_method,
            "confidence":   round(self.confidence, 4),
            "search_name":  self.search_name,
            "candidates":   self.candidates,
            "signals":      self.signals,
        }


# ── Resolver ──────────────────────────────────────────────────────────────────

class NewYorkCompanyResolver:
    """
    Resolves company identity against the NY DOS Active Corporations dataset.

    Usage:
        client = NewYorkCompanyClient()
        resolver = NewYorkCompanyResolver(client)
        result = await resolver.resolve(company_data)
    """

    def __init__(self, client: Any):
        self.client = client

    async def resolve(self, website_data: Any) -> NYResolutionResult:
        """Main resolution entry point. Never raises."""
        try:
            return await self._resolve_inner(website_data)
        except Exception as e:
            logger.error(f"[NYResolver] Unexpected error: {e}", exc_info=True)
            return NYResolutionResult(
                status="unavailable",
                signals={"error": str(e)},
            )

    async def _resolve_inner(self, website_data: Any) -> NYResolutionResult:
        search_name = (
            getattr(website_data, "legal_name", None)
            or getattr(website_data, "company_name", None)
            or ""
        ).strip()

        if not search_name:
            logger.info("[NYResolver] No company name available — skipping.")
            return NYResolutionResult(status="skipped")

        city        = getattr(website_data, "city", None) or ""
        postal_code = getattr(website_data, "postal_code", None) or ""

        logger.info(f"[NYResolver] Searching NY DOS for: {search_name!r}")

        # ── Search active corporations ────────────────────────────────────────
        raw_candidates = await self.client.search_by_name(search_name)

        if not raw_candidates:
            logger.info(f"[NYResolver] No results for: {search_name!r}")
            return NYResolutionResult(
                status="not_found",
                search_name=search_name,
            )

        # ── Score all candidates ──────────────────────────────────────────────
        scored = [
            _score_candidate(c, search_name, city, postal_code)
            for c in raw_candidates
        ]
        scored.sort(key=lambda c: c["_score_total"], reverse=True)

        # Clean candidate list for output (top 10, no internal score keys)
        clean_candidates = [
            {
                "dos_id":       c.get("dos_id"),
                "entity_name":  c.get("current_entity_name"),
                "entity_type":  c.get("entity_type"),
                "county":       c.get("county"),
                "jurisdiction": c.get("jurisdiction"),
                "filed_date":   c.get("initial_dos_filing_date"),
                "address":      c.get("dos_process_address_1"),
                "city":         c.get("dos_process_city"),
                "state":        c.get("dos_process_state"),
                "zip":          c.get("dos_process_zip"),
            }
            for c in scored[:10]
        ]

        best   = scored[0]
        second = scored[1] if len(scored) > 1 else None

        top_score = best["_score_total"]
        gap = top_score - (second["_score_total"] if second else 0.0)

        logger.info(
            f"[NYResolver] Best candidate: {best.get('current_entity_name')!r} "
            f"score={top_score:.1f} gap={gap:.1f}"
        )

        # ── Tiebreak: if ambiguous but one candidate has exact ZIP match, resolve it ──
        # This handles cases like "BARNES & NOBLE BOOKSELLERS, INC." vs
        # "BARNES & NOBLE INC." where ZIP uniquely identifies the entity.
        if top_score >= SCORE_AMBIGUOUS_MIN and gap < SCORE_GAP_MIN and postal_code:
            zip_clean = re.sub(r"\D", "", postal_code)[:5]
            zip_matches = [
                c for c in scored
                if re.sub(r"\D", "", c.get("dos_process_zip") or "")[:5] == zip_clean
                and c["_score_total"] >= SCORE_AMBIGUOUS_MIN
            ]
            if len(zip_matches) == 1:
                # Only one candidate has the exact ZIP — safe to resolve
                best = zip_matches[0]
                top_score = best["_score_total"]
                gap = SCORE_GAP_MIN  # override gap to force resolution
                logger.info(
                    f"[NYResolver] Tiebreak via ZIP {zip_clean} → "
                    f"'{best.get('current_entity_name')}'"
                )

        # ── Apply resolution rules ────────────────────────────────────────────
        if top_score >= SCORE_RESOLVED_MIN and gap >= SCORE_GAP_MIN:
            dos_id = best.get("dos_id")
            confidence = round(min(top_score / 100.0 + 0.10, 1.0), 4)

            logger.info(
                f"[NYResolver] RESOLVED → DOS ID {dos_id} "
                f"'{best.get('current_entity_name')}' "
                f"(score={top_score:.1f}, gap={gap:.1f})"
            )

            result = NYResolutionResult(
                status="resolved",
                matched=True,
                dos_id=dos_id,
                matched_name=best.get("current_entity_name"),
                match_method="name_scored",
                confidence=confidence,
                search_name=search_name,
                candidates=clean_candidates,
                signals={
                    "top_score": top_score,
                    "gap": gap,
                    "score_detail": best["_score_detail"],
                    "threshold": {
                        "resolved_min": SCORE_RESOLVED_MIN,
                        "gap_min": SCORE_GAP_MIN,
                    },
                },
                raw_profile=best,
            )

            # ── Enrich the resolved match ─────────────────────────────────────
            if dos_id:
                await self._enrich(result, dos_id)

            return result

        elif top_score >= SCORE_AMBIGUOUS_MIN:
            logger.warning(
                f"[NYResolver] AMBIGUOUS — top={top_score:.1f}, gap={gap:.1f}. "
                "Multiple candidates too close — refusing to guess."
            )

            # Even in ambiguous case, enrich the best candidate so the UI
            # can show real registry data rather than an empty panel.
            # matched=False means we are NOT claiming a verified identity match.
            best_dos_id = best.get("dos_id")
            ambiguous_result = NYResolutionResult(
                status="ambiguous",
                matched=False,
                dos_id=best_dos_id,
                matched_name=best.get("current_entity_name"),
                match_method=None,
                search_name=search_name,
                confidence=round(top_score / 100.0, 4),
                candidates=clean_candidates,
                signals={
                    "top_score": top_score,
                    "gap": gap,
                    "reason": f"Gap {gap:.1f} < {SCORE_GAP_MIN} — not auto-selected",
                },
                raw_profile=best,
            )

            # Enrich best candidate — gives real filing/address/entity data
            if best_dos_id:
                await self._enrich(ambiguous_result, best_dos_id)

            return ambiguous_result

        else:
            logger.info(
                f"[NYResolver] UNRESOLVED — top score {top_score:.1f} "
                f"< threshold {SCORE_RESOLVED_MIN}"
            )
            return NYResolutionResult(
                status="unresolved",
                matched=False,
                search_name=search_name,
                confidence=round(top_score / 100.0, 4),
                candidates=clean_candidates,
                signals={
                    "top_score": top_score,
                    "reason": f"Best score {top_score:.1f} < minimum {SCORE_RESOLVED_MIN}",
                },
            )

    async def _enrich(self, result: NYResolutionResult, dos_id: str):
        """
        Parallel enrichment: filings, stock, entity, addresses.
        All use corpid_num=dos_id (verified correct join key).
        All are best-effort — failures are logged and ignored.
        """
        import asyncio

        async def safe(coro):
            try:
                return await coro
            except Exception as e:
                logger.debug(f"[NYResolver] Enrichment error: {e}")
                return []

        # All four enrichment calls use dos_id directly.
        # get_addresses() was previously called with film_num — now fixed to dos_id.
        # get_entity_info() was previously called with film_num — now fixed to dos_id.
        filings, stock, entity, addresses = await asyncio.gather(
            safe(self.client.get_filings(dos_id)),
            safe(self.client.get_stock_info(dos_id)),
            safe(self.client.get_entity_info(dos_id)),
            safe(self.client.get_addresses(dos_id)),
        )

        result.filings      = filings   or []
        result.stock_info   = stock     or []
        result.entity_info  = entity    or []
        result.address_info = addresses or []

        logger.info(
            f"[NYResolver] Enriched DOS {dos_id}: "
            f"filings={len(result.filings)} "
            f"stock={len(result.stock_info)} "
            f"entity={len(result.entity_info)} "
            f"addresses={len(result.address_info)}"
        )

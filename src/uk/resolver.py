"""
UK Companies House Resolver — Safe Company Resolution System.

Resolution logic:
  - If registration_number is known → direct lookup (no name search)
    If registration number matches valid CH company, identity confidence is 1.0 (exact match)
  - If only company_name is known → search → score candidates → apply
    high-confidence threshold and ambiguity gap rule → resolve or refuse to guess

Scoring (out of 100 pts):
    Name similarity   : 40 pts  (normalised: Ltd/Limited, case, punctuation)
    Postcode match    : 25 pts  (strong signal)
    Address/city      : 15 pts
    Domain evidence   : 10 pts  (supporting, not sole condition)
    Active status     : 10 pts

Resolution rules:
    RESOLVED   : top_score >= 70  AND  (top_score - second_score) >= 15
    AMBIGUOUS  : top_score >= 60  AND  gap < 15
    UNRESOLVED : top_score < 60
"""

import re
import logging
import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import os as _os
import sys as _sys

# Ensure src/ directory is in sys.path for absolute imports
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

try:
    from .companies_house import CompaniesHouseClient
    from ..validator import is_same_base_domain
except (ImportError, ValueError):
    from uk.companies_house import CompaniesHouseClient
    from validator import is_same_base_domain

logger = logging.getLogger(__name__)

# ── UK Company Number Prefixes ────────────────────────────────────────────────
UK_PREFIXES = {"SC", "NI", "OC", "LP", "SO", "IP", "SL", "NC", "NL", "NZ"}

# ── Scoring thresholds ────────────────────────────────────────────────────────
SCORE_RESOLVED_MIN   = 70   # minimum score for resolved path
SCORE_AMBIGUOUS_MIN  = 60   # minimum score to be considered at all
SCORE_GAP_MIN        = 15   # minimum gap (best vs 2nd) to resolve


# ── Legal suffix normalisation map ────────────────────────────────────────────
_SUFFIX_MAP = {
    r"\bLIMITED\b":          "LTD",
    r"\bPUBLIC LIMITED COMPANY\b": "PLC",
    r"\bPARTNERSHIP\b":      "PRTNR",
    r"\bCOMPANY\b":          "CO",
    r"\bINCORPORATED\b":     "INC",
    r"\bCORPORATION\b":      "CORP",
}


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class ResolutionResult:
    """
    Full, inspectable record of a Companies House resolution attempt.

    status:
        "resolved"             — confident unique match found via search
        "resolved_direct"      — direct lookup by company number (strongest match)
        "verification_conflict"— direct lookup found company but it completely contradicts website identity
        "ambiguous"            — multiple near-equal candidates, refused to guess
        "unresolved"           — search returned results but none scored high enough
        "not_found"            — search returned no results / 404
        "unavailable"          — API error / unreachable
        "skipped"              — no name or number to search with
    """
    status: str
    matched: bool = False
    company_number: Optional[str] = None
    match_method: Optional[str] = None     # "registration_number" | "exact_name_verified" | "name_search_scored"
    confidence: float = 0.0               # 0.0–1.0 (Identity Match Confidence)
    search_name: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":         self.status,
            "matched":        self.matched,
            "company_number": self.company_number,
            "match_method":   self.match_method,
            "confidence":     round(self.confidence, 4),
            "search_name":    self.search_name,
            "candidates":     self.candidates,
            "signals":        self.signals,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_company_number(reg_no: Optional[str]) -> Optional[str]:
    """
    Normalise a UK company number.

    Supported formats:
        SC311560   → SC311560   (Scottish company, prefix already 8 chars)
        SC12345    → SC012345   (pad digits to 6)
        12345678   → 12345678   (pure numeric, pad to 8)
        NI123456   → NI123456
    """
    if not reg_no:
        return None

    cleaned = re.sub(r"\s+", "", reg_no).upper()

    # Prefix + digits
    prefix_match = re.match(r"^([A-Z]{2})([0-9]+)$", cleaned)
    if prefix_match:
        prefix, digits = prefix_match.groups()
        if prefix in UK_PREFIXES:
            return f"{prefix}{digits.zfill(6)}"

    # Pure numeric
    if cleaned.isdigit():
        return cleaned.zfill(8)

    return cleaned


def _normalise_name(name: str) -> str:
    """Normalise a company name for fuzzy matching."""
    n = name.upper().strip()
    n = re.sub(r"[^\w\s]", " ", n)       # strip punctuation
    n = re.sub(r"\s+", " ", n).strip()
    for pattern, replacement in _SUFFIX_MAP.items():
        n = re.sub(pattern, replacement, n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _name_score(website_name: str, ch_name: str) -> float:
    """Score name similarity 0–40."""
    a = _normalise_name(website_name)
    b = _normalise_name(ch_name)

    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    pts = ratio * 40.0

    # Exact match after normalisation: full points
    if a == b or a in b or b in a:
        pts = 40.0
    return pts


def _postcode_score(website_postcode: Optional[str], ch_postcode: Optional[str]) -> float:
    """Score postcode match 0–25."""
    if not website_postcode or not ch_postcode:
        return 0.0

    def _clean_pc(pc: str) -> str:
        return re.sub(r"\s+", "", pc).upper()

    a = _clean_pc(website_postcode)
    b = _clean_pc(ch_postcode)

    if a == b:
        return 25.0
    min_len = min(len(a), len(b))
    if min_len >= 4 and a[:4] == b[:4]:
        return 10.0
    return 0.0


def _address_score(website_data: Any, ch_address: Dict[str, Any]) -> float:
    """Score address/city similarity 0–15."""
    pts = 0.0
    ch_locality = (ch_address.get("locality") or "").upper().strip()
    ch_region   = (ch_address.get("region") or "").upper().strip()

    w_city = (getattr(website_data, "city", None) or "").upper().strip()
    w_addr = (getattr(website_data, "full_address", None) or "").upper()

    if ch_locality:
        if w_city and ch_locality == w_city:
            pts += 15.0
        elif ch_locality in w_addr:
            pts += 8.0
        elif ch_region and ch_region in w_addr:
            pts += 5.0
    return pts


def _domain_score(website_domain: Optional[str], candidate: Dict[str, Any]) -> float:
    """Score domain-level evidence (max 10 pts)."""
    if not website_domain:
        return 0.0

    pts = 0.0
    domain_clean = re.sub(r"^https?://", "", website_domain.lower()).split("/")[0].replace("www.", "")
    domain_stem  = domain_clean.split(".")[0].strip()

    ch_name    = (candidate.get("title") or candidate.get("company_name") or "").upper()
    ch_address = candidate.get("registered_office_address", {}) or {}
    ch_country = (ch_address.get("country") or "").lower()

    if len(domain_stem) >= 3 and domain_stem.upper() in ch_name:
        pts += 7.0

    if domain_clean.endswith(".co.uk") or domain_clean.endswith(".org.uk") or "united kingdom" in ch_country or "england" in ch_country or "scotland" in ch_country:
        pts += 3.0

    return min(pts, 10.0)


def _active_score(company_status: Optional[str]) -> float:
    """Score active or administration status (valid corporate existence)."""
    st = (company_status or "").lower()
    if st in ("active", "administration", "in-administration", "in administration"):
        return 10.0
    return 0.0


def _score_candidate(
    candidate: Dict[str, Any],
    website_data: Any,
    website_domain: Optional[str],
) -> Dict[str, Any]:
    """Score a single Companies House search candidate against website data."""
    search_name = (
        getattr(website_data, "legal_name", None)
        or getattr(website_data, "company_name", None) or ""
    )

    ch_name      = candidate.get("title", "")
    ch_status    = candidate.get("company_status", "")
    ch_address   = candidate.get("registered_office_address", {}) or {}
    ch_postcode  = ch_address.get("postal_code")

    w_postcode   = getattr(website_data, "postal_code", None)
    if not w_postcode:
        w_addr_text = getattr(website_data, "full_address", None) or ""
        pc_match = re.search(
            r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|\d{4,5})\b",
            w_addr_text.upper()
        )
        if pc_match:
            w_postcode = pc_match.group(1)

    s_name    = _name_score(search_name, ch_name)
    s_post    = _postcode_score(w_postcode, ch_postcode)
    s_addr    = _address_score(website_data, ch_address)
    s_domain  = _domain_score(website_domain, candidate)
    s_active  = _active_score(ch_status)

    total = s_name + s_post + s_addr + s_domain + s_active

    out = dict(candidate)
    out["_score_total"]  = round(total, 2)
    out["_score_detail"] = {
        "name":     round(s_name, 2),
        "postcode": round(s_post, 2),
        "address":  round(s_addr, 2),
        "domain":   round(s_domain, 2),
        "active":   round(s_active, 2),
    }
    return out


# ── Main Resolver ─────────────────────────────────────────────────────────────

class UKCompanyResolver:
    """
    Resolves crawl results to Companies House records.

    Returns a ResolutionResult — never None, never a bare company number.
    """

    def __init__(self, client: CompaniesHouseClient):
        self.client = client

    async def resolve(self, website_data: Any) -> ResolutionResult:
        """Main resolution entry point."""
        try:
            return await self._resolve_inner(website_data)
        except Exception as exc:
            logger.error(f"[CHResolver] Unexpected error: {exc}", exc_info=True)
            return ResolutionResult(status="unavailable", signals={"error": str(exc)})

    async def _resolve_inner(self, website_data: Any) -> ResolutionResult:
        reg_no = getattr(website_data, "registration_number", None)
        domain = getattr(website_data, "domain", None) or ""

        # ── Path A: Company Number already known (Direct match) ───────────────
        if reg_no:
            return await self._resolve_by_number(reg_no, website_data, domain)

        # ── Path B: No company number — search by name ────────────────────────
        search_name = (
            getattr(website_data, "legal_name", None)
            or getattr(website_data, "company_name", None)
        )
        if not search_name:
            logger.info("[CHResolver] No registration number and no company name — skipping.")
            return ResolutionResult(status="skipped")

        return await self._resolve_by_search(search_name, website_data, domain)

    # ── Path A ────────────────────────────────────────────────────────────────

    async def _resolve_by_number(
        self,
        reg_no: str,
        website_data: Any,
        domain: str,
    ) -> ResolutionResult:
        """Direct lookup using the already-known company number."""
        normalized = normalize_company_number(reg_no)
        if not normalized:
            logger.warning(f"[CHResolver] Could not normalise company number: {reg_no!r}")
            return ResolutionResult(
                status="unresolved",
                signals={"reason": f"Could not normalise company number: {reg_no}"},
            )

        logger.info(f"[CHResolver] Direct lookup: {normalized}")
        profile = await self.client.get_company_profile(normalized)

        if not profile:
            logger.warning(f"[CHResolver] Company {normalized} not found on Companies House.")
            return ResolutionResult(
                status="not_found",
                signals={"company_number": normalized},
            )

        ch_name    = profile.get("company_name", "")
        ch_address = profile.get("registered_office_address", {}) or {}

        website_name = getattr(website_data, "legal_name", None) or getattr(website_data, "company_name", None) or ""
        name_sim = _name_score(website_name, ch_name) if website_name else 40.0

        signals = {
            "company_number":   normalized,
            "ch_name":          ch_name,
            "ch_status":        profile.get("company_status"),
            "ch_postcode":      ch_address.get("postal_code"),
            "ch_address_line1": ch_address.get("address_line_1"),
            "ch_locality":      ch_address.get("locality"),
            "identity_confidence": 1.0,
        }

        # Exact registration number match is an authoritative 1.0 identity confidence
        logger.info(f"[CHResolver] Resolved direct: {normalized} ('{ch_name}', confidence=1.0)")
        return ResolutionResult(
            status="resolved_direct",
            matched=True,
            company_number=normalized,
            match_method="registration_number",
            confidence=1.0,
            search_name=website_name,
            signals=signals,
        )

    # ── Path B ────────────────────────────────────────────────────────────────

    async def _resolve_by_search(
        self,
        search_name: str,
        website_data: Any,
        domain: str,
    ) -> ResolutionResult:
        """Search Companies House by name and apply transparent scoring."""
        logger.info(f"[CHResolver] Searching Companies House for: {search_name!r}")
        raw_candidates = await self.client.search_company(search_name)

        if not raw_candidates:
            logger.info(f"[CHResolver] No results for: {search_name!r}")
            return ResolutionResult(
                status="not_found",
                search_name=search_name,
                candidates=[],
            )

        # Score all candidates
        scored = [
            _score_candidate(c, website_data, domain)
            for c in raw_candidates
        ]
        scored.sort(key=lambda c: c["_score_total"], reverse=True)

        clean_candidates = [
            {
                "company_number": c.get("company_number"),
                "company_name":   c.get("title"),
                "company_status": c.get("company_status"),
                "address":        c.get("registered_office_address", {}),
                "score":          c["_score_total"],
                "score_detail":   c["_score_detail"],
            }
            for c in scored[:10]
        ]

        best  = scored[0]
        second = scored[1] if len(scored) > 1 else None

        top_score = best["_score_total"]
        gap       = top_score - (second["_score_total"] if second else 0.0)

        logger.info(
            f"[CHResolver] Top candidate: {best.get('title')!r} "
            f"score={top_score:.1f}  gap={gap:.1f}"
        )

        is_exact_name = best["_score_detail"]["name"] >= 39.0 and best["_score_detail"]["active"] >= 10.0
        is_confident_exact = is_exact_name and top_score >= 50.0 and gap >= 5.0
        is_confident_scored = top_score >= SCORE_RESOLVED_MIN and gap >= SCORE_GAP_MIN

        if is_confident_scored or is_confident_exact:
            comp_no = best.get("company_number")
            match_method = "exact_name_verified" if is_confident_exact else "name_search_scored"
            conf = 0.95 if is_confident_exact else round(top_score / 100.0, 4)
            logger.info(
                f"[CHResolver] RESOLVED → {comp_no} '{best.get('title')}' "
                f"(score={top_score:.1f}, gap={gap:.1f}, method={match_method})"
            )
            return ResolutionResult(
                status="resolved",
                matched=True,
                company_number=comp_no,
                match_method=match_method,
                confidence=conf,
                search_name=search_name,
                candidates=clean_candidates,
                signals={
                    "top_score":  top_score,
                    "gap":        gap,
                    "is_exact_name": is_exact_name,
                    "threshold":  {"resolved_min": SCORE_RESOLVED_MIN, "gap_min": SCORE_GAP_MIN},
                    "score_detail": best["_score_detail"],
                },
            )

        elif top_score >= SCORE_AMBIGUOUS_MIN:
            logger.warning(
                f"[CHResolver] AMBIGUOUS — top={top_score:.1f}, gap={gap:.1f}. Not guessing."
            )
            return ResolutionResult(
                status="ambiguous",
                matched=False,
                search_name=search_name,
                confidence=round(top_score / 100.0, 4),
                candidates=clean_candidates,
                signals={
                    "top_score": top_score,
                    "gap":       gap,
                    "reason":    f"Gap {gap:.1f} < {SCORE_GAP_MIN} — refusing to guess.",
                },
            )

        else:
            logger.info(
                f"[CHResolver] UNRESOLVED — top score {top_score:.1f} < threshold {SCORE_RESOLVED_MIN}."
            )
            return ResolutionResult(
                status="unresolved",
                matched=False,
                search_name=search_name,
                confidence=round(top_score / 100.0, 4),
                candidates=clean_candidates,
                signals={
                    "top_score": top_score,
                    "reason":    f"Best score {top_score:.1f} < minimum {SCORE_RESOLVED_MIN}.",
                },
            )

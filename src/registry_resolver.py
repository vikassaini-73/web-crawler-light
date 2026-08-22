"""
Generic Registry Resolver

Routes a detected jurisdiction to the correct registry client and returns
a normalized RegistryResolutionResult.

Routing table:
  United Kingdom          → Companies House (existing UK integration)
  United States + NY      → New York DOS Open Data
  United States + unknown → registry_status = "state_required"
  United States + other   → registry_status = "unsupported"
  Other country           → registry_status = "unsupported"
  No country detected     → registry_status = "no_jurisdiction"

NEVER pretends a lookup happened when it did not.
NEVER routes a non-NY US company to the NY registry.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class RegistryResolutionResult:
    """
    Unified registry result regardless of which registry was used.

    registry_type:
      "companies_house"   — UK CH lookup (existing)
      "ny_dos"            — New York DOS lookup
      "unsupported"       — country/state has no registry client yet
      "state_required"    — US company but state unknown
      "no_jurisdiction"   — no country detected at all
      "error"             — unexpected failure

    registry_status mirrors the underlying resolver status:
      "resolved", "resolved_direct", "ambiguous",
      "unresolved", "not_found", "unavailable", "skipped",
      plus the routing statuses above.
    """
    registry_type: str
    registry_status: str
    matched: bool = False
    confidence: float = 0.0
    company_number: Optional[str] = None   # CH: company_number / NY: dos_id
    matched_name: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    official_profile: Optional[Dict[str, Any]] = None
    resolution_detail: Optional[Dict[str, Any]] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registry_type":    self.registry_type,
            "registry_status":  self.registry_status,
            "matched":          self.matched,
            "confidence":       round(self.confidence, 4),
            "company_number":   self.company_number,
            "matched_name":     self.matched_name,
            "country":          self.country,
            "state":            self.state,
            "message":          self.message,
        }


# ── Helper: build CH result from existing UKCompanyResolver ──────────────────

async def _resolve_uk(company_data: Any) -> RegistryResolutionResult:
    """Run the existing UK Companies House resolver and adapt to RegistryResolutionResult."""
    from uk import CompaniesHouseClient, UKCompanyResolver, CompaniesHouseMapper

    ch_api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
    if not ch_api_key:
        return RegistryResolutionResult(
            registry_type="companies_house",
            registry_status="unavailable",
            country="United Kingdom",
            message="COMPANIES_HOUSE_API_KEY not configured",
        )

    try:
        ch_client = CompaniesHouseClient(ch_api_key)
        resolver  = UKCompanyResolver(ch_client)
        resolution = await resolver.resolve(company_data)

        result = RegistryResolutionResult(
            registry_type="companies_house",
            registry_status=resolution.status,
            matched=resolution.matched,
            confidence=resolution.confidence,
            company_number=resolution.company_number,
            country="United Kingdom",
            resolution_detail=resolution.to_dict(),
        )

        if resolution.matched and resolution.company_number:
            import asyncio
            comp_no = resolution.company_number
            profile, officers, psc, history, charges, insolvency = await asyncio.gather(
                ch_client.get_company_profile(comp_no),
                ch_client.get_officers(comp_no),
                ch_client.get_psc(comp_no),
                ch_client.get_filing_history(comp_no),
                ch_client.get_charges(comp_no),
                ch_client.get_insolvency(comp_no),
            )
            if profile:
                mapper = CompaniesHouseMapper()
                result.official_profile = mapper.build_normalized_profile(
                    profile=profile,
                    officers=officers,
                    psc=psc,
                    filing_history=history,
                    charges=charges,
                    insolvency=insolvency,
                )
                result.matched_name = profile.get("company_name")

        return result

    except Exception as e:
        logger.error(f"[RegistryResolver] UK CH error: {e}", exc_info=True)
        return RegistryResolutionResult(
            registry_type="companies_house",
            registry_status="error",
            country="United Kingdom",
            message=str(e),
        )


# ── Helper: build NY result ───────────────────────────────────────────────────

async def _resolve_ny(company_data: Any) -> RegistryResolutionResult:
    """Run the New York DOS resolver and adapt to RegistryResolutionResult."""
    from ny import NewYorkCompanyClient, NewYorkCompanyResolver, NewYorkMapper

    try:
        client   = NewYorkCompanyClient()
        resolver = NewYorkCompanyResolver(client)
        ny_result = await resolver.resolve(company_data)

        result = RegistryResolutionResult(
            registry_type="ny_dos",
            registry_status=ny_result.status,
            matched=ny_result.matched,
            confidence=ny_result.confidence,
            company_number=ny_result.dos_id,
            matched_name=ny_result.matched_name,
            country="United States",
            state="New York",
            resolution_detail=ny_result.to_dict(),
        )

        if ny_result.matched or (ny_result.raw_profile and ny_result.dos_id):
            # Build normalized profile for resolved AND ambiguous cases
            # so the UI always shows real registry data, not scores/signals
            mapper = NewYorkMapper()
            result.official_profile = mapper.build_normalized_profile(ny_result)

        return result

    except Exception as e:
        logger.error(f"[RegistryResolver] NY DOS error: {e}", exc_info=True)
        return RegistryResolutionResult(
            registry_type="ny_dos",
            registry_status="error",
            country="United States",
            state="New York",
            message=str(e),
        )


# ── Main RegistryResolver ─────────────────────────────────────────────────────

class RegistryResolver:
    """
    Routes a JurisdictionResult + CompanyData to the correct registry client.

    Usage:
        resolver = RegistryResolver()
        result = await resolver.resolve(jurisdiction_result, company_data)
    """

    async def resolve(
        self,
        jurisdiction: Any,          # JurisdictionResult
        company_data: Any,          # CompanyData
    ) -> RegistryResolutionResult:
        """Main entry point. Never raises."""
        try:
            return await self._route(jurisdiction, company_data)
        except Exception as e:
            logger.error(f"[RegistryResolver] Unexpected error: {e}", exc_info=True)
            return RegistryResolutionResult(
                registry_type="error",
                registry_status="error",
                message=str(e),
            )

    async def _route(
        self,
        jurisdiction: Any,
        company_data: Any,
    ) -> RegistryResolutionResult:

        country     = getattr(jurisdiction, "country", None)
        state_abbr  = getattr(jurisdiction, "state_abbr", None)
        state_name  = getattr(jurisdiction, "state", None)
        j_conf      = getattr(jurisdiction, "confidence", 0.0)

        # ── No jurisdiction detected ──────────────────────────────────────────
        if not country:
            logger.info("[RegistryResolver] No country detected — skipping registry.")
            return RegistryResolutionResult(
                registry_type="no_jurisdiction",
                registry_status="no_jurisdiction",
                message="Country could not be determined from website content.",
            )

        logger.info(
            f"[RegistryResolver] Routing: country={country!r} "
            f"state={state_name!r} ({state_abbr}) "
            f"jurisdiction_confidence={j_conf:.2f}"
        )

        # ── United Kingdom → Companies House ─────────────────────────────────
        if country == "United Kingdom":
            logger.info("[RegistryResolver] → Companies House (UK)")
            return await _resolve_uk(company_data)

        # ── United States ─────────────────────────────────────────────────────
        if country == "United States":

            if not state_abbr:
                logger.info(
                    "[RegistryResolver] USA detected but state unknown — "
                    "cannot select registry."
                )
                return RegistryResolutionResult(
                    registry_type="unsupported",
                    registry_status="state_required",
                    country="United States",
                    message=(
                        "US company detected but state/jurisdiction could not be "
                        "determined. Add more address/location signals to the website "
                        "for state-level registry routing."
                    ),
                )

            if state_abbr == "NY":
                logger.info("[RegistryResolver] → New York DOS Open Data")
                return await _resolve_ny(company_data)

            # US state detected but no client implemented yet
            logger.info(
                f"[RegistryResolver] US state {state_name} ({state_abbr}) "
                "has no registry client — unsupported."
            )
            return RegistryResolutionResult(
                registry_type="unsupported",
                registry_status="unsupported",
                country="United States",
                state=state_name,
                message=(
                    f"Registry lookup for {state_name} is not yet implemented. "
                    "Only New York (NY) and United Kingdom are currently supported."
                ),
            )

        # ── Other country — no client ─────────────────────────────────────────
        logger.info(
            f"[RegistryResolver] No registry client for country={country!r}"
        )
        return RegistryResolutionResult(
            registry_type="unsupported",
            registry_status="unsupported",
            country=country,
            message=(
                f"Registry lookup for {country} is not yet implemented. "
                "Currently supported: United Kingdom, United States (New York)."
            ),
        )

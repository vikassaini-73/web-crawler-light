import os
import json
import logging
import asyncio
import re
from typing import Any, Dict, List, Optional, AsyncGenerator

# Load .env from any parent directory (works regardless of CWD or how this module is imported)
def _load_env():
    try:
        from dotenv import load_dotenv
        search = os.path.dirname(os.path.abspath(__file__))
        for _ in range(7):
            candidate = os.path.join(search, '.env')
            if os.path.isfile(candidate):
                load_dotenv(candidate, override=False)
                return
            search = os.path.dirname(search)
    except Exception:
        pass

_load_env()

try:
    from .lightpanda_reader import LightpandaReader
    from .extractor import CompanyDataExtractor
    from .page_selector import PageSelector
    from .url_discovery import URLDiscovery, normalize_domain_url
    from .wikipedia_fallback import get_wikipedia_company_data
    from .models import CompanyData, FieldEvidence
    from .jurisdiction_detector import detect_jurisdiction
    from .registry_resolver import RegistryResolver
    from .uk import CompaniesHouseClient, UKCompanyResolver, CompaniesHouseMapper
    from .telemetry import TelemetryLogger
except ImportError:
    from lightpanda_reader import LightpandaReader
    from extractor import CompanyDataExtractor
    from page_selector import PageSelector
    from url_discovery import URLDiscovery, normalize_domain_url
    from wikipedia_fallback import get_wikipedia_company_data
    from models import CompanyData, FieldEvidence
    from jurisdiction_detector import detect_jurisdiction
    from registry_resolver import RegistryResolver
    from uk import CompaniesHouseClient, UKCompanyResolver, CompaniesHouseMapper
    from telemetry import TelemetryLogger

logger = logging.getLogger(__name__)



def is_uk_company(data: CompanyData, start_url: str) -> bool:
    """Legacy helper — kept for backward compatibility with /enrich endpoint."""
    domain_lower = (data.domain or start_url or "").lower()
    if domain_lower.endswith(".uk") or ".co.uk" in domain_lower or ".org.uk" in domain_lower:
        return True
    country_lower = (data.country or "").lower()
    if country_lower in ("united kingdom", "uk", "great britain", "gb", "scotland", "england", "wales", "northern ireland"):
        return True
    reg = (data.registration_number or "").upper().strip()
    if reg:
        if reg.startswith(("SC", "NI", "OC", "LP", "SO", "IP", "SL", "NC", "NL", "NZ")):
            return True
        if reg.isdigit() and len(reg) in (7, 8):
            return True
    if data.postal_code:
        try:
            from validator import validate_uk_postcode
        except ImportError:
            from .validator import validate_uk_postcode
        if validate_uk_postcode(data.postal_code):
            return True
    return False


class CompanyIntelligencePipeline:
    """Centralized pipeline for company research, extraction, UK registry resolution and enrichment."""

    def __init__(self, max_pages: int = 10, enable_ch: bool = True):
        self.max_pages = max_pages
        self.enable_ch = enable_ch
        self.telemetry = TelemetryLogger()

    @property
    def ch_api_key(self) -> Optional[str]:
        """Always read from env at call time so _load_env() has time to run first."""
        return os.getenv("COMPANIES_HOUSE_API_KEY")

    async def run_generator(self, domain_or_url: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Run the full research pipeline as an async generator.
        Yields structured status logs, intermediate states, and final normalized entity profile.

        Flow:
            URL Discovery → Page Selection → Lightpanda (Local Headless) → Extraction
            → Companies House (UK) → Wikipedia → Final JSON
        """
        start_url = normalize_domain_url(domain_or_url)
        yield {"type": "log", "level": "PHASE", "msg": f"🚀 Starting entity intelligence research for: {start_url}"}

        # 1. URL Discovery
        yield {"type": "log", "level": "INFO", "msg": "🔍 Phase 1: Discovering URLs (robots.txt, sitemaps, BFS internal links)..."}
        discovery = URLDiscovery(start_url)
        all_urls = await discovery.discover_all()
        if not all_urls:
            all_urls = [start_url]
        yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Discovered {len(all_urls)} unique URLs."}
        self.telemetry.log_discovery(
            domain=start_url,
            sitemaps_found=1 if discovery.sitemap_urls_count > 0 else 0,
            sitemap_urls=discovery.sitemap_urls_count,
            bfs_urls=discovery.bfs_discovered_count,
            total_unique=len(all_urls)
        )

        # 2. Page Selection & Scoring
        yield {"type": "log", "level": "INFO", "msg": "🎯 Phase 2: Scoring and selecting high-priority pages..."}
        selector = PageSelector(start_url, max_pages=self.max_pages)
        selected_items = selector.select_relevant_pages(all_urls)
        target_urls = [item[0] for item in selected_items]
        yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Selected {len(target_urls)} pages for extraction."}
        self.telemetry.log_selection(selected_items)

        # 3. Lightpanda Reader — read each selected page (High-performance headless)
        yield {"type": "log", "level": "INFO", "msg": f"📖 Phase 3: Reading {len(target_urls)} pages via Lightpanda..."}
        for idx, url in enumerate(target_urls, 1):
            yield {"type": "log", "level": "INFO", "msg": f"   Reading page {idx}/{len(target_urls)}: {url}"}
        reader = LightpandaReader()
        crawled_pages = await reader.read_pages(target_urls)
        yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Successfully read {len(crawled_pages)}/{len(target_urls)} pages via Lightpanda."}
        self.telemetry.log_crawling(crawled_pages)

        # 4. Core Data Extraction
        yield {"type": "log", "level": "PHASE", "msg": "🏗️ Phase 4: Core Company Identity Extraction & Validation..."}
        extractor = CompanyDataExtractor(start_url)
        company_data = extractor.extract_all(crawled_pages)

        # Initialize normalized company (website data) and sources
        company_data.company = {
            "name": company_data.company_name,
            "legal_name": company_data.legal_name,
            "website": company_data.website or company_data.domain,
            "country": company_data.country,
            "state": company_data.state_province,
            "city": company_data.city,
            "address": company_data.full_address,
            "postal_code": company_data.postal_code,
            "phone": company_data.phone,
            "email": company_data.email
        }
        company_data.sources["website"] = start_url

        # Initialize default verification state
        company_data.verification = {
            "status": "unverified",
            "matched": False,
            "confidence": 0.0,
            "match_method": None,
            "search_name": None
        }

        yield {"type": "json", "content": company_data.model_dump()}
        yield {"type": "log", "level": "SUCCESS", "msg": "✅ Core extraction complete."}
        self.telemetry.log_extraction(company_data)

        # 5. Jurisdiction Detection
        yield {"type": "log", "level": "PHASE", "msg": "🌍 Phase 5: Detecting company jurisdiction..."}
        jurisdiction = detect_jurisdiction(company_data)
        company_data.jurisdiction_detection = jurisdiction.to_dict()

        if jurisdiction.country:
            loc_parts = [jurisdiction.country]
            if jurisdiction.state:
                loc_parts.insert(0, jurisdiction.state)
            loc_str = " → ".join(reversed(loc_parts))
            yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Jurisdiction detected: {loc_str} (confidence: {jurisdiction.confidence:.0%})"}
        else:
            yield {"type": "log", "level": "INFO", "msg": "ℹ️ Could not determine jurisdiction from website content."}

        # 6. Registry Resolution
        yield {"type": "log", "level": "PHASE", "msg": "🏛️ Phase 6: Selecting and querying official registry..."}
        reg_resolver = RegistryResolver()
        reg_result = await reg_resolver.resolve(jurisdiction, company_data)

        company_data.registry_data = reg_result.to_dict()

        if reg_result.registry_status in ("unsupported", "state_required", "no_jurisdiction"):
            yield {"type": "log", "level": "INFO", "msg": f"ℹ️ Registry: {reg_result.message or reg_result.registry_status}"}

        elif reg_result.registry_type == "companies_house":
            # Merge CH data into CompanyData using existing logic
            company_data.companies_house_status = reg_result.registry_status
            company_data.identity_match = reg_result.matched
            company_data.identity_confidence = reg_result.confidence
            company_data.companies_house_resolution = reg_result.resolution_detail

            if reg_result.matched and reg_result.official_profile:
                company_data.official_registry_profile = reg_result.official_profile
                company_data.identity_match_method = "companies_house"

                # ── NEW: Populate Normalized Final View Fields (Requested Consistency) ────
                ch_profile = reg_result.official_profile
                company_data.registry = {
                    "source":          "UK Companies House",
                    "registry_type":   "companies_house",
                    "registry_name":   "Companies House",
                    "dos_id":          ch_profile.get("registration_number"),
                    "company_name":    ch_profile.get("legal_name"),
                    "entity_type":     ch_profile.get("company_type"),
                    "jurisdiction":    ch_profile.get("jurisdiction") or "United Kingdom",
                    "company_status":  ch_profile.get("company_status"),
                }
                company_data.registered_address = ch_profile.get("registered_address") or {}
                company_data.full_registered_address = ch_profile.get("full_registered_address")
                company_data.filing_history  = ch_profile.get("filing_history", [])

                res = reg_result.resolution_detail or {}
                company_data.verification = {
                    "status":       reg_result.registry_status,
                    "matched":      reg_result.matched,
                    "confidence":   reg_result.confidence,
                    "match_method": "companies_house",
                    "search_name":  res.get("search_name"),
                }

                company_data.sources.update({
                    "companies_house": f"https://find-and-update.company-information.service.gov.uk/company/{reg_result.company_number}"
                })
                # ──────────────────────────────────────────────────────────────

                # Merge authoritative CH fields directly onto CompanyData
                source_url = (
                    f"https://find-and-update.company-information.service.gov.uk"
                    f"/company/{reg_result.company_number}"
                )
                if ch_profile.get("legal_name"):
                    company_data.legal_name = ch_profile["legal_name"]
                    company_data.field_evidence["legal_name"] = FieldEvidence(
                        value=company_data.legal_name,
                        source_url=source_url,
                        category="legal",
                        method="companies_house_registry",
                        confidence=1.0,
                    )
                if ch_profile.get("registration_number"):
                    company_data.registration_number = ch_profile["registration_number"]
                    company_data.field_evidence["registration_number"] = FieldEvidence(
                        value=company_data.registration_number,
                        source_url=source_url,
                        category="legal",
                        method="companies_house_registry",
                        confidence=1.0,
                    )

                reg_addr = ch_profile.get("registered_address", {})
                if reg_addr:
                    company_data.official_registered_address = reg_addr

                company_data.company_status  = ch_profile.get("company_status")
                company_data.company_type    = ch_profile.get("company_type")
                company_data.jurisdiction    = ch_profile.get("jurisdiction") or "United Kingdom"
                if ch_profile.get("sic_codes"):
                    company_data.sic_codes = ch_profile["sic_codes"]
                if ch_profile.get("officers", {}).get("directors"):
                    company_data.directors = ch_profile["officers"]["directors"]
                if ch_profile.get("officers", {}).get("management"):
                    company_data.management = ch_profile["officers"]["management"]
                company_data.persons_with_significant_control = ch_profile.get("psc", [])
                company_data.filing_history  = ch_profile.get("filing_history", [])
                company_data.charges         = ch_profile.get("charges", [])
                company_data.insolvency      = ch_profile.get("insolvency")

                yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Companies House verified: {reg_result.company_number} — {reg_result.matched_name} (confidence: {reg_result.confidence:.0%})"}
            else:
                yield {"type": "log", "level": "INFO", "msg": f"ℹ️ Companies House: {reg_result.registry_status}"}

        elif reg_result.registry_type == "ny_dos":
            company_data.identity_match = reg_result.matched
            company_data.identity_confidence = reg_result.confidence
            company_data.identity_match_method = "ny_dos"

            if reg_result.official_profile:
                # official_profile is built by NewYorkMapper for BOTH resolved
                # AND ambiguous cases — always shows real registry data.
                company_data.official_registry_profile = reg_result.official_profile
                ny_profile = reg_result.official_profile

                # Populate NY-specific fields on CompanyData
                company_data.ny_dos_id        = ny_profile.get("dos_id")
                company_data.ny_entity_type   = ny_profile.get("entity_type")
                company_data.ny_county        = ny_profile.get("county")
                company_data.ny_jurisdiction  = ny_profile.get("jurisdiction")
                company_data.ny_filing_date   = ny_profile.get("filing_date")
                company_data.ny_filing_history = ny_profile.get("filing_history", [])
                company_data.ny_stock_info    = ny_profile.get("stock_info", [])
                company_data.ny_entity_info   = ny_profile.get("entity_info", [])

                # ── NEW: Populate Normalized Final View Fields (Requested) ────
                company_data.registry = {
                    "source":          ny_profile.get("source"),
                    "registry_type":   ny_profile.get("registry_type"),
                    "registry_name":   "New York DOS",
                    "dos_id":          ny_profile.get("dos_id"),
                    "company_name":    ny_profile.get("company_name"),
                    "entity_type":     ny_profile.get("entity_type"),
                    "jurisdiction":    ny_profile.get("jurisdiction"),
                    "county":          ny_profile.get("county"),
                    "filing_date":     ny_profile.get("filing_date"),
                    "company_status":  ny_profile.get("company_status"),
                    "registered_in_new_york": True,
                }
                company_data.registered_address = ny_profile.get("registered_address") or {}
                company_data.full_registered_address = ny_profile.get("full_registered_address")
                company_data.filing_history  = ny_profile.get("filing_history", [])
                company_data.address_records = ny_profile.get("address_records", [])
                company_data.stock_info     = ny_profile.get("stock_info", [])
                company_data.entity_info    = ny_profile.get("entity_info", [])

                res = ny_profile.get("resolution", {})
                company_data.verification = {
                    "status":       res.get("status"),
                    "matched":      res.get("matched"),
                    "confidence":   res.get("confidence"),
                    "match_method": res.get("match_method"),
                    "search_name":  res.get("search_name"),
                }

                company_data.sources.update({
                    "ny_active_corporations": "https://data.ny.gov/resource/n9v6-gdp6.json",
                    "ny_filings":             "https://data.ny.gov/resource/63wc-4exh.json",
                    "ny_addresses":           "https://data.ny.gov/resource/2tms-hftb.json",
                    "ny_entity":              "https://data.ny.gov/resource/3gg2-jgnp.json",
                    "ny_stock":               "https://data.ny.gov/resource/kiwr-v7e8.json",
                })
                # ──────────────────────────────────────────────────────────────

                # Set official registered address from NY record
                ny_addr = ny_profile.get("registered_address") or {}
                if ny_addr:
                    company_data.official_registered_address = ny_addr

                # Set company status from entity enrichment
                if ny_profile.get("company_status"):
                    company_data.company_status = ny_profile["company_status"]

                if reg_result.matched:
                    # Authoritative legal name only when fully resolved
                    if ny_profile.get("company_name") and not company_data.legal_name:
                        company_data.legal_name = ny_profile["company_name"]
                        # Sync to normalized company object
                        company_data.company["legal_name"] = company_data.legal_name

                    # Preserve actual jurisdiction from registry (esp. for Foreign entities)
                    reg_jurisdiction = ny_profile.get("jurisdiction")
                    if reg_jurisdiction and reg_jurisdiction.upper() != "NEW YORK":
                        company_data.jurisdiction = reg_jurisdiction
                    else:
                        company_data.jurisdiction = "New York"

                    yield {"type": "log", "level": "SUCCESS", "msg": f"✅ NY DOS verified: DOS ID {ny_profile.get('dos_id')} — {reg_result.matched_name} (confidence: {reg_result.confidence:.0%})"}
                else:
                    status = reg_result.registry_status
                    dos_id_shown = ny_profile.get("dos_id") or "N/A"
                    name_shown = ny_profile.get("company_name") or reg_result.matched_name or "?"
                    msg_map = {
                        "ambiguous":  f"⚠️ NY DOS: Best candidate DOS ID {dos_id_shown} — {name_shown} (not auto-confirmed — review Official Registry tab)",
                        "unresolved": f"ℹ️ NY DOS: No confident match found (score too low)",
                        "not_found":  f"ℹ️ NY DOS: No records found for search term",
                    }
                    yield {"type": "log", "level": "WARN" if status == "ambiguous" else "INFO",
                           "msg": msg_map.get(status, f"ℹ️ NY DOS: {status}")}
            else:
                yield {"type": "log", "level": "INFO", "msg": f"ℹ️ NY DOS: {reg_result.registry_status}"}

        yield {"type": "json", "content": company_data.model_dump()}

        # 7. Wikipedia Enrichment
        yield {"type": "log", "level": "INFO", "msg": "📚 Phase 7: Supplementary Wikipedia Enrichment..."}
        brand = (
            company_data.legal_name
            or company_data.brand_name
            or company_data.company_name
            or start_url
        )
        wiki_data = await get_wikipedia_company_data(brand, start_url)
        if wiki_data:
            yield {"type": "wiki_json", "content": wiki_data}
            if wiki_data.get("_domain_match"):
                yield {"type": "log", "level": "SUCCESS", "msg": "✅ Wikipedia supplementary data verified."}
                self._merge_wiki_data(company_data, wiki_data)
                yield {"type": "json", "content": company_data.model_dump()}
            else:
                yield {"type": "log", "level": "INFO", "msg": "ℹ️ Wikipedia page found (unverified domain — supplementary context only)."}
        else:
            yield {"type": "log", "level": "INFO", "msg": "ℹ️ No Wikipedia article found for supplementary context."}

        # Final completeness score
        important_fields = ["company_name", "legal_name", "registration_number",
                            "vat_tax_number", "full_address", "postal_code", "email", "phone"]
        filled = sum(1 for f in important_fields if getattr(company_data, f, None))
        company_data.data_completeness = round(filled / len(important_fields), 2)

        yield {"type": "log", "level": "SUCCESS", "msg": "✨ Pipeline Execution Complete!"}
        yield {"type": "done", "content": company_data.model_dump()}

    def _merge_wiki_data(self, data: CompanyData, wiki: Dict[str, Any]):
        """Merge supplementary non-authoritative Wikipedia fields."""
        for field in ["industry", "business_description", "parent_company"]:
            val = wiki.get(field)
            if val and not getattr(data, field, None):
                setattr(data, field, val)
                data.field_evidence[field] = FieldEvidence(
                    value=val,
                    source_url=wiki.get("_source", "Wikipedia"),
                    category="enrichment",
                    method="wikipedia_infobox",
                    confidence=0.80
                )

        if wiki.get("subsidiaries") and not data.subsidiaries:
            data.subsidiaries = wiki["subsidiaries"]

import os
import json
import logging
import asyncio
import re
from typing import Any, Dict, List, Optional, AsyncGenerator

# Load .env
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
    from .page_selector import PageSelector, score_url
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
    from page_selector import PageSelector, score_url
    from url_discovery import URLDiscovery, normalize_domain_url
    from wikipedia_fallback import get_wikipedia_company_data
    from models import CompanyData, FieldEvidence
    from jurisdiction_detector import detect_jurisdiction
    from registry_resolver import RegistryResolver
    from uk import CompaniesHouseClient, UKCompanyResolver, CompaniesHouseMapper
    from telemetry import TelemetryLogger

logger = logging.getLogger(__name__)

class CompanyIntelligencePipeline:
    """Adaptive pipeline that finds all URLs first, then scrapes priority pages iteratively."""

    def __init__(self, max_pages: int = 10, enable_ch: bool = True):
        self.max_pages = max_pages
        self.enable_ch = enable_ch
        self.telemetry = TelemetryLogger()

    def is_data_sufficient(self, data: CompanyData) -> bool:
        """Checks if we have enough identifying information to stop crawling."""
        # Need at least a name and one more key identifier
        has_name = bool(data.legal_name or (data.company_name and data.company_name != data.domain))
        has_contact = bool(data.email or data.phone)
        has_legal = bool(data.registration_number or data.vat_tax_number or data.full_address)
        
        # High confidence if we have name + (legal identifier or contact)
        return has_name and (has_legal or has_contact)

    async def run_generator(self, domain_or_url: str) -> AsyncGenerator[Dict[str, Any], None]:
        start_url = normalize_domain_url(domain_or_url)
        yield {"type": "log", "level": "PHASE", "msg": f"🚀 Starting Deep Research for: {start_url}"}

        # 1. COMPLETE URL Discovery (No small limits)
        yield {"type": "log", "level": "INFO", "msg": "🔍 Phase 1: Discovering ALL URLs from the website (Sitemaps, BFS Crawl)..."}
        discovery = URLDiscovery(start_url)
        # discovery.discover_all() now has higher internal limits (5000 sitemaps, 2000 BFS)
        all_urls = await discovery.discover_all()
        if not all_urls:
            all_urls = [start_url]
        
        yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Discovery Complete. Found {len(all_urls)} unique URLs on the site."}

        # 2. Score and Prioritize all discovered URLs
        scored_urls = []
        for u in all_urls:
            score, cat = score_url(u, start_url)
            scored_urls.append((u, score, cat))
        
        # Sort by relevance (Legal/About/Contact first)
        scored_urls.sort(key=lambda x: x[1], reverse=True)

        # 3. Iterative Adaptive Scraping
        reader = LightpandaReader()
        extractor = CompanyDataExtractor(start_url)
        
        all_crawled_pages = []
        processed_urls = []
        company_data = None
        
        # We process in batches of 'max_pages' (default 10)
        batch_size = self.max_pages
        max_total_scrape = 100 # Maximum safety limit for scraping
        
        yield {"type": "log", "level": "PHASE", "msg": "🏗️ Phase 2: Adaptive Scraping (Prioritizing high-value pages)..."}

        while len(processed_urls) < len(scored_urls) and len(processed_urls) < max_total_scrape:
            # Get next batch of URLs that haven't been processed
            remaining = [x[0] for x in scored_urls if x[0] not in processed_urls]
            if not remaining:
                break
            
            current_batch = remaining[:batch_size]
            yield {"type": "log", "level": "INFO", "msg": f"   Scraping next {len(current_batch)} priority pages (Total processed: {len(processed_urls)})..."}
            
            new_pages = await reader.read_pages(current_batch)
            all_crawled_pages.extend(new_pages)
            processed_urls.extend(current_batch)

            # Perform extraction on what we have so far
            company_data = extractor.extract_all(all_crawled_pages)
            
            # Check if we have enough identifying info to stop early
            if self.is_data_sufficient(company_data):
                yield {"type": "log", "level": "SUCCESS", "msg": "✅ Found sufficient company details (Name + Contact/Legal). Stopping scrape."}
                break
            else:
                if len(processed_urls) < max_total_scrape and len(processed_urls) < len(scored_urls):
                    yield {"type": "log", "level": "WARN", "msg": "   Details still incomplete. Fetching more pages..."}

        # Final extraction if not already done
        if not company_data:
            company_data = extractor.extract_all(all_crawled_pages)

        # 4. Normalized Data View
        company_data.company = {
            "name": company_data.company_name,
            "legal_name": company_data.legal_name,
            "website": company_data.website or start_url,
            "country": company_data.country,
            "address": company_data.full_address,
            "email": company_data.email,
            "phone": company_data.phone
        }
        
        yield {"type": "json", "content": company_data.model_dump()}

        # 5. Jurisdiction Detection
        yield {"type": "log", "level": "PHASE", "msg": "🌍 Phase 3: Detecting jurisdiction..."}
        jurisdiction = detect_jurisdiction(company_data)
        company_data.jurisdiction_detection = jurisdiction.to_dict()

        # 6. Registry Resolution (UK/NY etc.)
        yield {"type": "log", "level": "PHASE", "msg": "🏛️ Phase 4: Querying official registries..."}
        reg_resolver = RegistryResolver()
        reg_result = await reg_resolver.resolve(jurisdiction, company_data)
        company_data.registry_data = reg_result.to_dict()

        if reg_result.matched and reg_result.official_profile:
             company_data.legal_name = reg_result.official_profile.get("legal_name") or company_data.legal_name
             yield {"type": "log", "level": "SUCCESS", "msg": f"✅ Registry Verified: {reg_result.matched_name}"}

        # 7. Wikipedia Enrichment
        brand = company_data.legal_name or company_data.company_name or start_url
        wiki_data = await get_wikipedia_company_data(brand, start_url)
        if wiki_data and wiki_data.get("_domain_match"):
             yield {"type": "log", "level": "SUCCESS", "msg": "✅ Wikipedia enrichment added."}

        yield {"type": "log", "level": "SUCCESS", "msg": "✨ Adaptive Pipeline Complete!"}
        yield {"type": "done", "content": company_data.model_dump()}

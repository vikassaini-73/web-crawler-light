import os
import logging
import base64
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"

class CompaniesHouseClient:
    """Low-level Companies House API Client."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("COMPANIES_HOUSE_API_KEY")
        if not self.api_key:
            logger.warning("Companies House API key not found in environment.")
        
        # Companies House uses Basic Auth with the API key as the username and no password.
        auth_string = f"{self.api_key}:"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded_auth}",
            "User-Agent": "CompanyDomainCrawler/1.0 (company identity research)"
        }

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Perform an authorized request to Companies House API."""
        if not self.api_key:
            return None

        url = f"{COMPANIES_HOUSE_BASE_URL}{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=self.headers) as client:
                resp = await client.get(url, params=params)
                
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    logger.warning("Companies House API Rate Limit Exceeded.")
                    return None
                if resp.status_code == 401:
                    logger.error("Companies House API Unauthorized. Check API key.")
                    return None
                
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Companies House API Error ({url}): {e}")
            return None

    async def search_company(self, query: str) -> List[Dict[str, Any]]:
        """Search for a company by name."""
        data = await self._request("/search/companies", params={"q": query})
        return data.get("items", []) if data else []

    async def get_company_profile(self, company_number: str) -> Optional[Dict[str, Any]]:
        """Fetch basic company profile."""
        return await self._request(f"/company/{company_number}")

    async def get_officers(self, company_number: str) -> List[Dict[str, Any]]:
        """Fetch company officers."""
        data = await self._request(f"/company/{company_number}/officers")
        return data.get("items", []) if data else []

    async def get_psc(self, company_number: str) -> List[Dict[str, Any]]:
        """Fetch Persons with Significant Control (PSC)."""
        data = await self._request(f"/company/{company_number}/persons-with-significant-control")
        return data.get("items", []) if data else []

    async def get_filing_history(self, company_number: str) -> List[Dict[str, Any]]:
        """Fetch company filing history."""
        data = await self._request(f"/company/{company_number}/filing-history")
        return data.get("items", []) if data else []

    async def get_charges(self, company_number: str) -> List[Dict[str, Any]]:
        """Fetch company charges."""
        data = await self._request(f"/company/{company_number}/charges")
        return data.get("items", []) if data else []

    async def get_insolvency(self, company_number: str) -> Optional[Dict[str, Any]]:
        """Fetch insolvency information."""
        return await self._request(f"/company/{company_number}/insolvency")

    async def get_registers(self, company_number: str) -> Optional[Dict[str, Any]]:
        """Fetch information about the company's registers."""
        return await self._request(f"/company/{company_number}/registers")

    async def get_exemptions(self, company_number: str) -> Optional[Dict[str, Any]]:
        """Fetch information about PSC exemptions."""
        return await self._request(f"/company/{company_number}/exemptions")

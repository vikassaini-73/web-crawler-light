"""
New York Department of State — Open Data API Client

VERIFIED LIVE API SCHEMAS (confirmed 2026-08-21):

n9v6-gdp6 — Active Corporations (PRIMARY SEARCH):
  dos_id, current_entity_name, initial_dos_filing_date,
  county, jurisdiction, entity_type, dos_process_name,
  dos_process_address_1, dos_process_city, dos_process_state, dos_process_zip

63wc-4exh — All Filings (FILING HISTORY):
  corpid_num, film_num, date_filed, approved_date, eff_date,
  dura_date, dis_eff_date, mod_certcode, entitytype, documenttype,
  law, nfp_type, corp_name, cnty_prin_ofc, juris, amd_corp_name_flag
  JOIN KEY: corpid_num = dos_id  ← VERIFIED

2tms-hftb — Addresses (ADDRESS ENRICHMENT):
  corpid_num, film_num, date_filed, addr_type,
  name, addr1, city, state, zip5, country
  JOIN KEY: corpid_num = dos_id  ← VERIFIED

kiwr-v7e8 — Stock (STOCK INFO):
  corpid_num, film_num, date_filed, stock_num_shrs, stock_type, stock_val_shr
  JOIN KEY: corpid_num = dos_id  ← VERIFIED

3gg2-jgnp — Entity (ENTITY INFO):
  corpid_num, film_num, date_filed, mod_cert_code, status
  JOIN KEY: corpid_num = dos_id  ← VERIFIED

CRITICAL: All enrichment datasets link to the primary via corpid_num = dos_id.
The old code used film_num LIKE '{dos_id}%' for filings — this was WRONG.
The correct query is corpid_num='{dos_id}' across all enrichment datasets.

Environment variables:
  NY_SOCRATA_APP_TOKEN   optional Socrata app token (higher rate limits)
  NY_API_TIMEOUT         optional timeout in seconds (default: 20)
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Dataset endpoints ─────────────────────────────────────────────────────────

_BASE = "https://data.ny.gov/resource"

DATASET_ACTIVE_CORPS = f"{_BASE}/n9v6-gdp6.json"   # primary search
DATASET_ALL_FILINGS  = f"{_BASE}/63wc-4exh.json"   # filing history
DATASET_ADDRESSES    = f"{_BASE}/2tms-hftb.json"   # address enrichment
DATASET_STOCK        = f"{_BASE}/kiwr-v7e8.json"   # stock info
DATASET_ENTITY       = f"{_BASE}/3gg2-jgnp.json"   # entity info

DEFAULT_TIMEOUT = 20.0
DEFAULT_SEARCH_LIMIT = 25


def _get_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "CompanyDomainCrawler/2.0 (company identity research)",
    }
    token = os.getenv("NY_SOCRATA_APP_TOKEN", "").strip()
    if token:
        headers["X-App-Token"] = token
    return headers


def _get_timeout() -> float:
    try:
        return float(os.getenv("NY_API_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except (ValueError, TypeError):
        return DEFAULT_TIMEOUT


class NewYorkCompanyClient:
    """
    Async HTTP client for the New York DOS Open Data API.
    All methods return parsed JSON (list of dicts) or empty list on error.
    Never raises to callers.
    """

    # ── Primary: Active Corporations ─────────────────────────────────────────

    async def search_by_name(
        self,
        name: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> List[Dict[str, Any]]:
        """
        Search Active Corporations by entity name (case-insensitive contains).
        Returns up to `limit` records ordered by name.
        """
        if not name or not name.strip():
            return []

        safe = name.strip().replace("'", "''")
        params = {
            "$where": f"upper(current_entity_name) LIKE upper('%{safe}%')",
            "$limit": str(limit),
            "$order": "current_entity_name ASC",
            "$select": (
                "dos_id,current_entity_name,initial_dos_filing_date,"
                "county,jurisdiction,entity_type,"
                "dos_process_name,dos_process_address_1,dos_process_address_2,"
                "dos_process_city,dos_process_state,dos_process_zip,"
                "chairman_name,chairman_address_1,chairman_city,chairman_state,chairman_zip,"
                "registered_agent_name,registered_agent_state"
            ),
        }
        return await self._get(DATASET_ACTIVE_CORPS, params)

    async def get_by_dos_id(self, dos_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single Active Corporation record by exact DOS ID with full fields."""
        if not dos_id:
            return None
        params = {
            "$where": f"dos_id='{dos_id}'",
            "$limit": "1",
            "$select": (
                "dos_id,current_entity_name,initial_dos_filing_date,"
                "county,jurisdiction,entity_type,"
                "dos_process_name,dos_process_address_1,dos_process_address_2,"
                "dos_process_city,dos_process_state,dos_process_zip,"
                "chairman_name,chairman_address_1,chairman_city,chairman_state,chairman_zip,"
                "registered_agent_name,registered_agent_state"
            ),
        }
        results = await self._get(DATASET_ACTIVE_CORPS, params)
        return results[0] if results else None

    # ── Enrichment: All Filings ───────────────────────────────────────────────

    async def get_filings(
        self,
        dos_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Get filing history for a DOS ID.

        VERIFIED: 63wc-4exh contains corpid_num column.
        corpid_num = dos_id (same value, direct join).
        Previous code used film_num LIKE '{dos_id}%' — this was WRONG.
        """
        if not dos_id:
            return []
        safe = dos_id.strip().replace("'", "''")
        params = {
            "$where": f"corpid_num='{safe}'",
            "$limit": str(limit),
            "$order": "date_filed DESC",
            "$select": (
                "corpid_num,film_num,date_filed,approved_date,eff_date,"
                "dura_date,mod_certcode,entitytype,documenttype,"
                "law,corp_name,cnty_prin_ofc,juris"
            ),
        }
        return await self._get(DATASET_ALL_FILINGS, params)

    # ── Enrichment: Addresses ─────────────────────────────────────────────────

    async def get_addresses(self, dos_id: str) -> List[Dict[str, Any]]:
        """
        Get registered address records for a DOS ID.

        VERIFIED: 2tms-hftb contains corpid_num column AND full address fields:
          name, addr1, city, state, zip5, country, addr_type
        Previous code accepted film_num — now correctly uses dos_id via corpid_num.
        """
        if not dos_id:
            return []
        safe = dos_id.strip().replace("'", "''")
        params = {
            "$where": f"corpid_num='{safe}'",
            "$limit": "10",
            "$order": "date_filed DESC",
            "$select": (
                "corpid_num,film_num,date_filed,addr_type,"
                "name,addr1,city,state,zip5,country"
            ),
        }
        return await self._get(DATASET_ADDRESSES, params)

    # ── Enrichment: Stock ─────────────────────────────────────────────────────

    async def get_stock_info(self, dos_id: str) -> List[Dict[str, Any]]:
        """
        Get stock information for a DOS ID.
        VERIFIED: kiwr-v7e8 corpid_num = dos_id.
        """
        if not dos_id:
            return []
        safe = dos_id.strip().replace("'", "''")
        params = {
            "$where": f"corpid_num='{safe}'",
            "$limit": "10",
            "$order": "date_filed DESC",
            "$select": "corpid_num,film_num,date_filed,stock_num_shrs,stock_type,stock_val_shr",
        }
        return await self._get(DATASET_STOCK, params)

    # ── Enrichment: Entity ────────────────────────────────────────────────────

    async def get_entity_info(self, dos_id: str) -> List[Dict[str, Any]]:
        """
        Get entity records for a DOS ID.
        VERIFIED: 3gg2-jgnp contains corpid_num AND status fields.
        Previous code accepted film_num — now correctly uses dos_id via corpid_num.
        """
        if not dos_id:
            return []
        safe = dos_id.strip().replace("'", "''")
        params = {
            "$where": f"corpid_num='{safe}'",
            "$limit": "10",
            "$order": "date_filed DESC",
            "$select": "corpid_num,film_num,date_filed,mod_cert_code,status",
        }
        return await self._get(DATASET_ENTITY, params)

    # ── Internal HTTP helper ──────────────────────────────────────────────────

    async def _get(
        self,
        url: str,
        params: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """GET a Socrata endpoint. Returns list or empty list on any failure."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_get_timeout()),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params, headers=_get_headers())

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    logger.warning(f"[NYClient] Unexpected response shape from {url}: {type(data)}")
                    return []

                elif resp.status_code == 429:
                    logger.warning(
                        f"[NYClient] Rate limited by {url}. "
                        "Add NY_SOCRATA_APP_TOKEN env var for higher limits."
                    )
                    return []

                elif resp.status_code == 404:
                    logger.debug(f"[NYClient] 404 from {url}")
                    return []

                else:
                    logger.warning(f"[NYClient] HTTP {resp.status_code} from {url} params={params}")
                    return []

        except httpx.TimeoutException:
            logger.warning(f"[NYClient] Timeout fetching {url}")
            return []
        except Exception as e:
            logger.error(f"[NYClient] Error fetching {url}: {type(e).__name__}: {e}")
            return []

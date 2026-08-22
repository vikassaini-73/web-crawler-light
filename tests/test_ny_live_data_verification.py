"""
NY DOS Live Data Verification — DOS ID 3322706 (Barnes & Noble Booksellers, Inc.)

These tests verify that the NY client and mapper correctly handle the EXACT
data returned by the live NY DOS API for DOS ID 3322706.

All expected values are taken directly from live API responses verified
on 2026-08-21. Tests use mocked HTTP to avoid real API calls in CI.

LIVE API FINDINGS:
==================

n9v6-gdp6 (Active Corps):
  dos_id:               "3322706"
  current_entity_name:  "BARNES & NOBLE BOOKSELLERS, INC."
  entity_type:          "FOREIGN BUSINESS CORPORATION"
  jurisdiction:         "Delaware"     ← incorporated in Delaware
  county:               "New York"     ← operates in NY county
  initial_dos_filing:   "2006-02-17"
  dos_process_name:     "CAPITOL SERVICES, INC."   ← registered agent
  dos_process_address:  "1218 CENTRAL AVE, STE 100, ALBANY, NY 12205"  ← agent address
  chairman_name:        "JAMES DAUNT"               ← CEO
  chairman_address:     "33 E 17TH STREET / 122 5TH AVE, NEW YORK, NY 10011/10003"

63wc-4exh (Filings) — 15 records:
  Most recent: BIENNIAL STATEMENT 2026-02-02
  Also: CERTIFICATE OF CHANGE 2024-09-05
  Also: CERTIFICATE OF MERGER 2007-02-02 (merger with B&N.com LLC)
  First filing: APPLICATION OF AUTHORITY 2006-02-17

2tms-hftb (Addresses):
  addr_type="1": CAPITOL SERVICES, INC. — 1218 CENTRAL AVE, ALBANY, NY 12205 (agent)
  addr_type="2": Registered Agent Revoked (empty address)
  addr_type="3": JAMES DAUNT — 33 E 17TH STREET, NEW YORK, NY 10003 (HQ)  ← BEST
  addr_type="3": JAMES DAUNT — 122 5TH AVE, NEW YORK, NY 10011 (alternate)

kiwr-v7e8 (Stock):
  [] — empty (foreign corporation, no stock filed in NY)

3gg2-jgnp (Entity):
  status: "Active" for all recent records
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── Verified live data fixtures ───────────────────────────────────────────────

LIVE_ACTIVE_CORPS_3322706 = {
    "dos_id": "3322706",
    "current_entity_name": "BARNES & NOBLE BOOKSELLERS, INC.",
    "initial_dos_filing_date": "2006-02-17T00:00:00.000",
    "county": "New York",
    "jurisdiction": "Delaware",
    "entity_type": "FOREIGN BUSINESS CORPORATION",
    "dos_process_name": "CAPITOL SERVICES, INC.",
    "dos_process_address_1": "1218 CENTRAL AVE",
    "dos_process_address_2": "STE 100",
    "dos_process_city": "ALBANY",
    "dos_process_state": "NY",
    "dos_process_zip": "12205",
    "chairman_name": "JAMES DAUNT",
    "chairman_address_1": "JAMES DAUNT",
    "chairman_city": "NEW YORK",
    "chairman_state": "NY",
    "chairman_zip": "10011",
    "registered_agent_name": "Registered Agent Revoked",
    "registered_agent_state": "NEW YORK",
}

LIVE_FILINGS_3322706 = [
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "approved_date": "2026-02-02T00:00:00.000",
        "eff_date": "2026-02-02T00:00:00.000",
        "dura_date": "PERPETUAL", "for_inc_date": "10/27/1998",
        "mod_certcode": "32FB A", "entitytype": "FOREIGN BUSINESS CORPORATION",
        "documenttype": "BIENNIAL STATEMENT", "law": "1304 BCL",
        "nfp_type": "NODISPLAY", "corp_name": "BARNES & NOBLE BOOKSELLERS, INC.",
        "cnty_prin_ofc": "New York", "juris": "DE",
    },
    {
        "corpid_num": "3322706", "film_num": "060217000994",
        "date_filed": "2006-02-17T00:00:00.000",
        "approved_date": "2006-02-17T00:00:00.000",
        "eff_date": "2006-02-17T00:00:00.000",
        "dura_date": "PERPETUAL",
        "mod_certcode": "01FB A", "entitytype": "FOREIGN BUSINESS CORPORATION",
        "documenttype": "APPLICATION OF AUTHORITY", "law": "1304 BCL",
        "nfp_type": "NODISPLAY", "corp_name": "BARNES & NOBLE BOOKSELLERS, INC.",
        "cnty_prin_ofc": "New York", "juris": "DE",
    },
]

LIVE_ADDRESSES_3322706 = [
    # addr_type=1 = registered agent (Albany — NOT the HQ)
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "addr_type": "1", "name": "CAPITOL SERVICES, INC.",
        "addr1": "1218 CENTRAL AVE", "addr2": "STE 100",
        "city": "ALBANY", "state": "NY", "zip5": "12205", "country": "USA",
    },
    # addr_type=2 = revoked agent (no address)
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "addr_type": "2", "name": "Registered Agent Revoked",
    },
    # addr_type=3 = principal office / chairman = ACTUAL HQ
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "addr_type": "3", "name": "JAMES DAUNT",
        "addr1": "33 E 17TH STREET",
        "city": "NEW YORK", "state": "NY", "zip5": "10003", "country": "USA",
    },
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "addr_type": "3", "name": "JAMES DAUNT",
        "addr1": "122 5TH AVE",
        "city": "NEW YORK", "state": "NY", "zip5": "10011", "country": "USA",
    },
]

LIVE_ENTITY_3322706 = [
    {
        "corpid_num": "3322706", "film_num": "260202006097",
        "date_filed": "2026-02-02T00:00:00.000",
        "mod_cert_code": "32FB", "status": "Active",
    },
    {
        "corpid_num": "3322706", "film_num": "240906000763",
        "date_filed": "2024-09-06T00:00:00.000",
        "mod_cert_code": "27FB", "status": "Active",
    },
]

LIVE_STOCK_3322706 = []  # Foreign corps don't file stock info in NY


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestNYMapperWithLiveData(unittest.TestCase):
    """Verify mapper produces correct output using live API data for DOS 3322706."""

    def test_map_profile_chairman_extracted(self):
        from ny.mapper import NewYorkMapper
        profile = NewYorkMapper.map_profile(LIVE_ACTIVE_CORPS_3322706)

        self.assertEqual(profile["dos_id"], "3322706")
        self.assertEqual(profile["company_name"], "BARNES & NOBLE BOOKSELLERS, INC.")
        self.assertEqual(profile["entity_type"], "FOREIGN BUSINESS CORPORATION")
        self.assertEqual(profile["jurisdiction"], "Delaware")
        self.assertEqual(profile["county"], "New York")

        # Chairman info must be extracted
        self.assertIsNotNone(profile["chairman"])
        self.assertEqual(profile["chairman"]["name"], "JAMES DAUNT")
        self.assertEqual(profile["chairman"]["city"], "NEW YORK")

        # Registered agent info
        self.assertEqual(profile["dos_process_name"], "CAPITOL SERVICES, INC.")
        self.assertEqual(profile["registered_agent_name"], "Registered Agent Revoked")

    def test_best_address_prefers_type3_principal_office(self):
        """
        addr_type=3 (principal office / chairman address) = 33 E 17TH ST, NYC
        MUST be preferred over addr_type=1 (agent = Albany).
        This was WRONG in the previous version which preferred type=1.
        """
        from ny.mapper import NewYorkMapper
        result = NewYorkMapper.best_registered_address(LIVE_ADDRESSES_3322706)

        self.assertIsNotNone(result)
        # Must be the principal office address, NOT the Albany agent address
        self.assertEqual(result["address_line_1"], "33 E 17TH STREET")
        self.assertEqual(result["city"], "NEW YORK")
        self.assertEqual(result["zip"], "10003")
        self.assertEqual(result["addr_type"], "3")
        self.assertEqual(result["addr_type_label"], "Principal Office")
        self.assertNotEqual(result["city"], "ALBANY")  # Must NOT be Albany

    def test_best_address_skips_revoked_type2(self):
        """addr_type=2 (revoked agent with no address) must be skipped."""
        from ny.mapper import NewYorkMapper
        # Only provide type=2 (no address) and type=1 (agent)
        records = [
            {"addr_type": "2", "name": "Revoked"},  # no addr1/city
            {"addr_type": "1", "name": "AGENT", "addr1": "123 MAIN", "city": "ALBANY",
             "state": "NY", "zip5": "12200", "country": "USA", "date_filed": "2020-01-01"},
        ]
        result = NewYorkMapper.best_registered_address(records)
        self.assertEqual(result["city"], "ALBANY")  # falls to type=1 since type=2 has no address

    def test_map_filings_latest_first(self):
        from ny.mapper import NewYorkMapper
        result = NewYorkMapper.map_filings(LIVE_FILINGS_3322706)

        self.assertEqual(len(result), 2)
        # First should be most recent (BIENNIAL STATEMENT 2026)
        self.assertEqual(result[0]["document_type"], "BIENNIAL STATEMENT")
        self.assertEqual(result[0]["corp_name"], "BARNES & NOBLE BOOKSELLERS, INC.")
        self.assertEqual(result[0]["law"], "1304 BCL")
        self.assertEqual(result[0]["jurisdiction"], "DE")
        # Last should be original APPLICATION OF AUTHORITY 2006
        self.assertEqual(result[1]["document_type"], "APPLICATION OF AUTHORITY")

    def test_map_entity_status_active(self):
        from ny.mapper import NewYorkMapper
        result = NewYorkMapper.map_entity(LIVE_ENTITY_3322706)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["status"], "Active")
        self.assertEqual(result[0]["corpid_num"], "3322706")

    def test_map_stock_empty_for_foreign_corp(self):
        from ny.mapper import NewYorkMapper
        result = NewYorkMapper.map_stock(LIVE_STOCK_3322706)
        self.assertEqual(result, [])

    def test_map_addresses_all_types(self):
        from ny.mapper import NewYorkMapper
        result = NewYorkMapper.map_addresses(LIVE_ADDRESSES_3322706)

        self.assertEqual(len(result), 4)

        # Find each type
        type1 = next((r for r in result if r["addr_type"] == "1"), None)
        type2 = next((r for r in result if r["addr_type"] == "2"), None)
        type3_list = [r for r in result if r["addr_type"] == "3"]

        self.assertIsNotNone(type1)
        self.assertEqual(type1["city"], "ALBANY")
        self.assertEqual(type1["addr_type_label"], "Registered Agent")

        self.assertIsNotNone(type2)
        self.assertIsNone(type2.get("addr1"))  # revoked — no address

        self.assertEqual(len(type3_list), 2)  # two principal office addresses
        cities = {r["city"] for r in type3_list}
        self.assertIn("NEW YORK", cities)

    def test_build_normalized_profile_complete(self):
        """Full profile build with all live data produces correct final output."""
        from ny.mapper import NewYorkMapper
        from ny.new_york_resolver import NYResolutionResult

        result = NYResolutionResult(
            status="ambiguous",
            matched=False,
            dos_id="3322706",
            matched_name="BARNES & NOBLE BOOKSELLERS, INC.",
            match_method=None,
            confidence=0.55,
            search_name="Barnes & Noble Booksellers, Inc",
            raw_profile=LIVE_ACTIVE_CORPS_3322706,
            filings=LIVE_FILINGS_3322706,
            stock_info=LIVE_STOCK_3322706,
            entity_info=LIVE_ENTITY_3322706,
            address_info=LIVE_ADDRESSES_3322706,
        )

        profile = NewYorkMapper.build_normalized_profile(result)

        # Core fields
        self.assertEqual(profile["dos_id"], "3322706")
        self.assertEqual(profile["company_name"], "BARNES & NOBLE BOOKSELLERS, INC.")
        self.assertEqual(profile["entity_type"], "FOREIGN BUSINESS CORPORATION")
        self.assertEqual(profile["jurisdiction"], "Delaware")
        self.assertEqual(profile["company_status"], "Active")

        # Address — must be principal office (33 E 17TH ST) not agent (Albany)
        addr = profile["registered_address"]
        self.assertEqual(addr["city"], "NEW YORK")
        self.assertEqual(addr["zip"], "10003")
        self.assertEqual(addr["addr_type_label"], "Principal Office")

        # Filing history
        self.assertEqual(len(profile["filing_history"]), 2)
        self.assertEqual(profile["filing_history"][0]["document_type"], "BIENNIAL STATEMENT")

        # Stock — empty for foreign corp
        self.assertEqual(profile["stock_info"], [])

        # Entity info with status
        self.assertEqual(profile["entity_info"][0]["status"], "Active")

        # Chairman info
        self.assertEqual(profile["chairman"]["name"], "JAMES DAUNT")

        # Registered agent
        self.assertEqual(profile["registered_agent"], "CAPITOL SERVICES, INC.")
        self.assertEqual(profile["registered_agent_status"], "Registered Agent Revoked")

        # Address records — all 4
        self.assertEqual(len(profile["address_records"]), 4)

        # Resolution metadata (status/match info only — no scores)
        self.assertEqual(profile["resolution"]["status"], "ambiguous")
        self.assertFalse(profile["resolution"]["matched"])
        self.assertEqual(profile["resolution"]["search_name"], "Barnes & Noble Booksellers, Inc")
        # Scores must NOT be in the profile
        self.assertNotIn("score", profile)
        self.assertNotIn("candidates", profile)
        self.assertNotIn("signals", profile)
        self.assertNotIn("top_score", profile)


class TestNYClientQueryForDos3322706(unittest.IsolatedAsyncioTestCase):
    """Verify client builds correct queries for DOS 3322706."""

    def _mock_client(self, return_data):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = return_data

        p = patch("httpx.AsyncClient")
        mock_cls = p.start()
        self.addCleanup(p.stop)
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=False)
        mock_inst.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_inst
        return mock_inst

    async def test_get_filings_query_uses_corpid_num(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._mock_client(LIVE_FILINGS_3322706)

        results = await client.get_filings("3322706")

        params = mock.get.call_args[1]["params"]
        self.assertEqual(params["$where"], "corpid_num='3322706'")
        self.assertIn("date_filed DESC", params["$order"])
        self.assertEqual(len(results), 2)

    async def test_get_addresses_query_uses_corpid_num(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._mock_client(LIVE_ADDRESSES_3322706)

        results = await client.get_addresses("3322706")

        params = mock.get.call_args[1]["params"]
        self.assertEqual(params["$where"], "corpid_num='3322706'")
        self.assertIn("addr1", params["$select"])
        self.assertIn("zip5", params["$select"])
        self.assertEqual(len(results), 4)

    async def test_get_entity_query_uses_corpid_num(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._mock_client(LIVE_ENTITY_3322706)

        results = await client.get_entity_info("3322706")

        params = mock.get.call_args[1]["params"]
        self.assertEqual(params["$where"], "corpid_num='3322706'")
        self.assertIn("status", params["$select"])
        self.assertEqual(results[0]["status"], "Active")

    async def test_get_stock_returns_empty_for_foreign_corp(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        self._mock_client(LIVE_STOCK_3322706)  # empty list

        results = await client.get_stock_info("3322706")
        self.assertEqual(results, [])

    async def test_search_by_name_includes_chairman_in_select(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._mock_client([LIVE_ACTIVE_CORPS_3322706])

        await client.search_by_name("Barnes Noble")

        params = mock.get.call_args[1]["params"]
        self.assertIn("chairman_name", params["$select"])
        self.assertIn("chairman_city", params["$select"])
        self.assertIn("registered_agent_name", params["$select"])


class TestAddressTypePriorityLogic(unittest.TestCase):
    """Unit tests for addr_type priority: 3 > 1 > others."""

    def test_type3_beats_type1(self):
        from ny.mapper import NewYorkMapper
        records = [
            {"addr_type": "1", "name": "AGENT", "addr1": "1218 CENTRAL AVE",
             "city": "ALBANY", "state": "NY", "zip5": "12205", "country": "USA",
             "date_filed": "2026-01-01"},
            {"addr_type": "3", "name": "JAMES DAUNT", "addr1": "33 E 17TH STREET",
             "city": "NEW YORK", "state": "NY", "zip5": "10003", "country": "USA",
             "date_filed": "2026-01-01"},
        ]
        result = NewYorkMapper.best_registered_address(records)
        self.assertEqual(result["city"], "NEW YORK")
        self.assertEqual(result["addr_type"], "3")

    def test_type1_used_when_no_type3(self):
        from ny.mapper import NewYorkMapper
        records = [
            {"addr_type": "1", "name": "AGENT", "addr1": "1218 CENTRAL AVE",
             "city": "ALBANY", "state": "NY", "zip5": "12205", "country": "USA",
             "date_filed": "2026-01-01"},
        ]
        result = NewYorkMapper.best_registered_address(records)
        self.assertEqual(result["city"], "ALBANY")
        self.assertEqual(result["addr_type"], "1")

    def test_fallback_when_all_missing_address(self):
        from ny.mapper import NewYorkMapper
        fallback = {"address_line_1": "FALLBACK ST", "city": "NYC"}
        records = [
            {"addr_type": "2", "name": "Revoked"},  # no addr1
        ]
        result = NewYorkMapper.best_registered_address(records, fallback=fallback)
        self.assertEqual(result["city"], "NYC")

    def test_most_recent_type3_when_multiple(self):
        from ny.mapper import NewYorkMapper
        records = [
            {"addr_type": "3", "name": "CEO", "addr1": "OLD ADDR",
             "city": "NEW YORK", "state": "NY", "zip5": "10001", "country": "USA",
             "date_filed": "2020-01-01"},
            {"addr_type": "3", "name": "CEO", "addr1": "33 E 17TH STREET",
             "city": "NEW YORK", "state": "NY", "zip5": "10003", "country": "USA",
             "date_filed": "2026-02-02"},
        ]
        result = NewYorkMapper.best_registered_address(records)
        # Most recent type=3 should win
        self.assertEqual(result["zip"], "10003")


if __name__ == "__main__":
    unittest.main()

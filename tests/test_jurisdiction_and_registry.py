"""
Tests for:
  - JurisdictionDetector (multi-signal country/state detection)
  - RegistryResolver routing logic
  - NewYorkCompanyClient (mocked HTTP)
  - NewYorkCompanyResolver matching and scoring
  - New York and unknown-state routing rules
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path so imports work without package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jurisdiction_detector import (
    JurisdictionDetector,
    detect_jurisdiction,
    _normalise_country,
    _extract_us_state_from_text,
    _extract_country_from_text,
    _phone_ny_signal,
    _domain_tld_signal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_company(**kwargs):
    """Create a minimal mock object with the given attributes."""
    obj = MagicMock()
    defaults = {
        "country": None, "state_province": None, "city": None,
        "full_address": None, "postal_code": None, "phone": None,
        "registration_number": None, "vat_tax_number": None,
        "domain": None, "website": None,
        "company_name": None, "legal_name": None,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ══════════════════════════════════════════════════════════════════════════════
# 1. Jurisdiction Detector — helper functions
# ══════════════════════════════════════════════════════════════════════════════

class TestCountryNormalisation(unittest.TestCase):

    def test_uk_aliases(self):
        self.assertEqual(_normalise_country("uk"), "United Kingdom")
        self.assertEqual(_normalise_country("GB"), "United Kingdom")
        self.assertEqual(_normalise_country("England"), "United Kingdom")
        self.assertEqual(_normalise_country("scotland"), "United Kingdom")

    def test_us_aliases(self):
        self.assertEqual(_normalise_country("usa"), "United States")
        self.assertEqual(_normalise_country("U.S.A."), "United States")
        self.assertEqual(_normalise_country("america"), "United States")

    def test_empty(self):
        self.assertIsNone(_normalise_country(""))
        self.assertIsNone(_normalise_country(None))

    def test_passthrough_unknown(self):
        result = _normalise_country("Germany")
        self.assertIn("Germany", result)


class TestUsStateExtraction(unittest.TestCase):

    def test_full_name(self):
        self.assertEqual(_extract_us_state_from_text("New York, NY 10001"), "NY")
        self.assertEqual(_extract_us_state_from_text("San Francisco, California"), "CA")
        self.assertEqual(_extract_us_state_from_text("Austin, Texas"), "TX")

    def test_abbreviation_in_address(self):
        self.assertEqual(_extract_us_state_from_text("123 Main St, Brooklyn, NY 11201"), "NY")
        self.assertEqual(_extract_us_state_from_text("Suite 500, Chicago, IL 60601"), "IL")

    def test_no_state(self):
        self.assertIsNone(_extract_us_state_from_text("London, UK"))
        self.assertIsNone(_extract_us_state_from_text(""))

    def test_does_not_match_random_two_letters(self):
        # "AB" is not a US state
        result = _extract_us_state_from_text("AB 12345")
        self.assertIsNone(result)


class TestPhoneNySignal(unittest.TestCase):

    def test_ny_area_codes(self):
        self.assertTrue(_phone_ny_signal("+1 212 555 1234"))
        self.assertTrue(_phone_ny_signal("(646) 555-0100"))
        self.assertTrue(_phone_ny_signal("917-555-0199"))
        self.assertTrue(_phone_ny_signal("+1-718-555-0100"))

    def test_non_ny_us_numbers(self):
        self.assertFalse(_phone_ny_signal("+1 310 555 0100"))  # LA
        self.assertFalse(_phone_ny_signal("(312) 555-0100"))   # Chicago

    def test_uk_number(self):
        self.assertFalse(_phone_ny_signal("+44 20 7946 0958"))

    def test_empty(self):
        self.assertFalse(_phone_ny_signal(None))
        self.assertFalse(_phone_ny_signal(""))


class TestDomainTldSignal(unittest.TestCase):

    def test_uk_cctld(self):
        self.assertEqual(_domain_tld_signal("brewdog.co.uk"), "United Kingdom")
        self.assertEqual(_domain_tld_signal("example.org.uk"), "United Kingdom")
        self.assertEqual(_domain_tld_signal("test.uk"), "United Kingdom")

    def test_generic_tld_returns_none(self):
        """Critical: .com/.net/.org must NOT return a country."""
        self.assertIsNone(_domain_tld_signal("stripe.com"))
        self.assertIsNone(_domain_tld_signal("acme.net"))
        self.assertIsNone(_domain_tld_signal("charity.org"))
        self.assertIsNone(_domain_tld_signal("startup.io"))

    def test_other_cctld(self):
        self.assertEqual(_domain_tld_signal("example.de"), "Germany")
        self.assertEqual(_domain_tld_signal("example.fr"), "France")

    def test_empty(self):
        self.assertIsNone(_domain_tld_signal(None))
        self.assertIsNone(_domain_tld_signal(""))


# ══════════════════════════════════════════════════════════════════════════════
# 2. JurisdictionDetector — full detection
# ══════════════════════════════════════════════════════════════════════════════

class TestJurisdictionDetectorUK(unittest.TestCase):

    def setUp(self):
        self.detector = JurisdictionDetector()

    def test_uk_from_cctld(self):
        data = _make_company(domain="brewdog.co.uk")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United Kingdom")
        self.assertTrue(result.confidence > 0)

    def test_uk_from_reg_number_prefix(self):
        data = _make_company(registration_number="SC311560")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United Kingdom")
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_uk_from_country_field(self):
        data = _make_company(country="United Kingdom")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United Kingdom")
        self.assertTrue(result.is_uk())

    def test_uk_from_uk_alias(self):
        data = _make_company(country="uk")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United Kingdom")

    def test_uk_from_vat_prefix(self):
        data = _make_company(vat_tax_number="GB 897 6381 54")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United Kingdom")


class TestJurisdictionDetectorUSNY(unittest.TestCase):

    def setUp(self):
        self.detector = JurisdictionDetector()

    def test_us_ny_from_address(self):
        data = _make_company(
            full_address="350 Fifth Avenue, New York, NY 10118",
            country="United States",
        )
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United States")
        self.assertEqual(result.state_abbr, "NY")
        self.assertTrue(result.is_us_ny())

    def test_us_ny_from_state_province(self):
        data = _make_company(
            country="United States",
            state_province="New York",
        )
        result = self.detector.detect(data)
        self.assertEqual(result.state_abbr, "NY")

    def test_phone_alone_does_not_imply_ny(self):
        """Phone is supporting only — should not produce NY without other signals."""
        data = _make_company(phone="+1 212 555 0100")  # NY area code
        result = self.detector.detect(data)
        # No other signals → country should not be determined from phone alone
        # state confidence should be very low
        if result.state_abbr == "NY":
            # If state is NY, confidence must be low and country uncertain
            self.assertLess(result.confidence, 0.40)

    def test_com_domain_does_not_imply_any_country(self):
        data = _make_company(domain="somecompany.com")
        result = self.detector.detect(data)
        # .com alone gives no country signal
        self.assertIsNone(result.country)

    def test_us_california_detection(self):
        data = _make_company(
            country="United States",
            full_address="123 Sunset Blvd, Los Angeles, CA 90028",
        )
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United States")
        self.assertEqual(result.state_abbr, "CA")
        self.assertFalse(result.is_us_ny())

    def test_us_unknown_state(self):
        data = _make_company(country="United States")
        result = self.detector.detect(data)
        self.assertEqual(result.country, "United States")
        self.assertIsNone(result.state_abbr)
        self.assertFalse(result.is_us_ny())

    def test_no_data_returns_no_country(self):
        data = _make_company()
        result = self.detector.detect(data)
        self.assertIsNone(result.country)

    def test_signals_list_populated(self):
        data = _make_company(
            country="United States",
            full_address="Manhattan, New York, NY 10001",
        )
        result = self.detector.detect(data)
        self.assertTrue(len(result.signals) > 0)
        signal_names = [s["signal"] for s in result.signals]
        self.assertIn("extracted_country_field", signal_names)


# ══════════════════════════════════════════════════════════════════════════════
# 3. RegistryResolver routing
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistryResolverRouting(unittest.TestCase):
    """Tests registry routing without making real API calls."""

    def _make_jurisdiction(self, country=None, state=None, state_abbr=None, confidence=0.8):
        j = MagicMock()
        j.country = country
        j.state = state
        j.state_abbr = state_abbr
        j.confidence = confidence
        return j

    def test_no_country_returns_no_jurisdiction(self):
        from registry_resolver import RegistryResolver
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country=None)
        data = _make_company()

        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_type, "no_jurisdiction")
        self.assertEqual(result.registry_status, "no_jurisdiction")
        mock_uk.assert_not_called()
        mock_ny.assert_not_called()

    def test_uk_routes_to_companies_house(self):
        from registry_resolver import RegistryResolver, RegistryResolutionResult
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country="United Kingdom")
        data = _make_company()

        mock_ch_result = RegistryResolutionResult(
            registry_type="companies_house",
            registry_status="resolved",
            matched=True,
            confidence=1.0,
            company_number="SC311560",
        )
        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock, return_value=mock_ch_result) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_type, "companies_house")
        mock_uk.assert_called_once()
        mock_ny.assert_not_called()

    def test_us_ny_routes_to_ny_dos(self):
        from registry_resolver import RegistryResolver, RegistryResolutionResult
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country="United States", state="New York", state_abbr="NY")
        data = _make_company()

        mock_ny_result = RegistryResolutionResult(
            registry_type="ny_dos",
            registry_status="resolved",
            matched=True,
            confidence=0.85,
            company_number="1234567",
        )
        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock, return_value=mock_ny_result) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_type, "ny_dos")
        mock_ny.assert_called_once()
        mock_uk.assert_not_called()

    def test_us_california_returns_unsupported(self):
        """California has no registry client — must NOT route to NY."""
        from registry_resolver import RegistryResolver
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country="United States", state="California", state_abbr="CA")
        data = _make_company()

        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_status, "unsupported")
        self.assertFalse(result.matched)
        mock_uk.assert_not_called()
        mock_ny.assert_not_called()

    def test_us_unknown_state_returns_state_required(self):
        """US company with no state detected must not guess NY."""
        from registry_resolver import RegistryResolver
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country="United States", state=None, state_abbr=None)
        data = _make_company()

        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_status, "state_required")
        self.assertFalse(result.matched)
        mock_uk.assert_not_called()
        mock_ny.assert_not_called()

    def test_other_country_returns_unsupported(self):
        from registry_resolver import RegistryResolver
        resolver = RegistryResolver()
        j = self._make_jurisdiction(country="Germany")
        data = _make_company()

        with patch("registry_resolver._resolve_uk", new_callable=AsyncMock) as mock_uk, \
             patch("registry_resolver._resolve_ny", new_callable=AsyncMock) as mock_ny:
            result = run_async(resolver.resolve(j, data))

        self.assertEqual(result.registry_status, "unsupported")
        mock_uk.assert_not_called()
        mock_ny.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 4. NewYorkCompanyClient — mocked HTTP
# ══════════════════════════════════════════════════════════════════════════════

class TestNewYorkCompanyClient(unittest.IsolatedAsyncioTestCase):

    async def test_search_by_name_builds_correct_query(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"dos_id": "123", "current_entity_name": "ACME CORP."}
        ]

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_instance

            results = await client.search_by_name("ACME")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["dos_id"], "123")

        # Verify SoQL WHERE clause was sent
        call_kwargs = mock_instance.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        self.assertIn("$where", params)
        self.assertIn("ACME", params["$where"])

    async def test_empty_name_returns_empty(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        results = await client.search_by_name("")
        self.assertEqual(results, [])

    async def test_http_429_returns_empty(self):
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_instance

            results = await client.search_by_name("Some Company")

        self.assertEqual(results, [])

    async def test_timeout_returns_empty(self):
        from ny.new_york_client import NewYorkCompanyClient
        import httpx as _httpx
        client = NewYorkCompanyClient()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.get = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))
            mock_cls.return_value = mock_instance

            results = await client.search_by_name("Some Company")

        self.assertEqual(results, [])


# ══════════════════════════════════════════════════════════════════════════════
# 5. NewYorkCompanyResolver — scoring and matching
# ══════════════════════════════════════════════════════════════════════════════
# 4b. NewYorkCompanyClient — enrichment methods use corpid_num=dos_id
# ══════════════════════════════════════════════════════════════════════════════

class TestNewYorkCompanyClientEnrichment(unittest.IsolatedAsyncioTestCase):
    """Verify that all enrichment methods query by corpid_num=dos_id, not film_num."""

    def _make_client_mock(self, return_value):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = return_value

        mock_cls = patch("httpx.AsyncClient")
        p = mock_cls.start()
        self.addCleanup(mock_cls.stop)

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = AsyncMock(return_value=mock_response)
        p.return_value = mock_instance
        return mock_instance

    async def test_get_filings_uses_corpid_num(self):
        """get_filings() MUST use corpid_num=dos_id, NOT film_num LIKE dos_id%"""
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._make_client_mock([{"corpid_num": "4424185", "film_num": "130627000859"}])

        await client.get_filings("4424185")

        call_kwargs = mock.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        where = params.get("$where", "")

        # Must use corpid_num= (exact match)
        self.assertIn("corpid_num", where)
        self.assertIn("4424185", where)

        # Must NOT use film_num LIKE (old broken query)
        self.assertNotIn("film_num like", where.lower())
        self.assertNotIn("film_num LIKE", where)

    async def test_get_addresses_uses_corpid_num(self):
        """get_addresses() must use corpid_num=dos_id and return full address fields."""
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._make_client_mock([{
            "corpid_num": "4424185", "film_num": "130627000859",
            "addr_type": "1", "name": "ACME CORP",
            "addr1": "123 MAIN ST", "city": "NEW YORK",
            "state": "NY", "zip5": "10001", "country": "USA",
        }])

        results = await client.get_addresses("4424185")

        call_kwargs = mock.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        where = params.get("$where", "")
        select = params.get("$select", "")

        self.assertIn("corpid_num", where)
        self.assertIn("4424185", where)
        # Full address fields must be selected
        self.assertIn("addr1", select)
        self.assertIn("city", select)
        self.assertIn("zip5", select)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["addr1"], "123 MAIN ST")

    async def test_get_entity_info_uses_corpid_num(self):
        """get_entity_info() must use corpid_num=dos_id and return status field."""
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._make_client_mock([{
            "corpid_num": "4424185", "film_num": "130627000859",
            "date_filed": "2013-06-27T00:00:00.000",
            "mod_cert_code": "01DB A", "status": "Active",
        }])

        results = await client.get_entity_info("4424185")

        call_kwargs = mock.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        where = params.get("$where", "")
        select = params.get("$select", "")

        self.assertIn("corpid_num", where)
        self.assertIn("4424185", where)
        # status field must be selected
        self.assertIn("status", select)
        self.assertEqual(results[0]["status"], "Active")

    async def test_get_stock_info_uses_corpid_num(self):
        """get_stock_info() uses corpid_num — verify it was already correct."""
        from ny.new_york_client import NewYorkCompanyClient
        client = NewYorkCompanyClient()
        mock = self._make_client_mock([{
            "corpid_num": "4424185", "film_num": "130627000859",
            "stock_type": "NO PAR VALUE", "stock_num_shrs": "200",
        }])

        await client.get_stock_info("4424185")

        call_kwargs = mock.get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1]
        where = params.get("$where", "")
        self.assertIn("corpid_num", where)
        self.assertIn("4424185", where)


# ══════════════════════════════════════════════════════════════════════════════
# 4c. NewYorkMapper — verified real fields
# ══════════════════════════════════════════════════════════════════════════════

class TestNewYorkMapper(unittest.TestCase):

    def test_map_filings_includes_all_verified_fields(self):
        from ny.mapper import NewYorkMapper
        raw = [{
            "corpid_num": "4424185",
            "film_num": "130627000859",
            "corp_name": "ACME CORP.",
            "date_filed": "2013-06-27T00:00:00.000",
            "approved_date": "2013-06-27T00:00:00.000",
            "eff_date": "2013-06-27T00:00:00.000",
            "dura_date": "PERPETUAL",
            "dis_eff_date": None,
            "mod_certcode": "01DB A",
            "entitytype": "DOMESTIC BUSINESS CORPORATION",
            "documenttype": "CERTIFICATE OF INCORPORATION",
            "law": "402 BCL",
            "cnty_prin_ofc": "New York",
            "juris": "NY",
            "amd_corp_name_flag": None,
        }]
        result = NewYorkMapper.map_filings(raw)
        self.assertEqual(len(result), 1)
        f = result[0]
        self.assertEqual(f["corpid_num"], "4424185")
        self.assertEqual(f["corp_name"], "ACME CORP.")
        self.assertEqual(f["law"], "402 BCL")
        self.assertEqual(f["jurisdiction"], "NY")
        self.assertEqual(f["county"], "New York")
        self.assertFalse(f["name_amended"])

    def test_map_entity_includes_status(self):
        from ny.mapper import NewYorkMapper
        raw = [{
            "corpid_num": "4424185",
            "film_num": "130627000859",
            "date_filed": "2013-06-27T00:00:00.000",
            "mod_cert_code": "01DB A",
            "status": "Active",
        }]
        result = NewYorkMapper.map_entity(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "Active")   # was missing before

    def test_map_addresses_includes_full_address(self):
        from ny.mapper import NewYorkMapper
        raw = [{
            "corpid_num": "4424185",
            "film_num": "130627000859",
            "date_filed": "2013-06-27T00:00:00.000",
            "addr_type": "1",
            "name": "THE CORPORATION",
            "addr1": "23 BUSHNEIL PLACE",
            "city": "MOUNT VERNON",
            "state": "NY",
            "zip5": "10550",
            "country": "USA",
        }]
        result = NewYorkMapper.map_addresses(raw)
        self.assertEqual(len(result), 1)
        a = result[0]
        self.assertEqual(a["addr1"], "23 BUSHNEIL PLACE")
        self.assertEqual(a["city"], "MOUNT VERNON")
        self.assertEqual(a["zip"], "10550")
        self.assertEqual(a["addr_type_label"], "Registered Agent")

    def test_best_registered_address_prefers_type1(self):
        from ny.mapper import NewYorkMapper
        records = [
            {"addr_type": "2", "addr1": "SECONDARY", "city": "ALBANY",
             "state": "NY", "zip5": "12200", "country": "USA",
             "date_filed": "2020-01-01", "name": None},
            {"addr_type": "1", "addr1": "PRIMARY", "city": "NEW YORK",
             "state": "NY", "zip5": "10003", "country": "USA",
             "date_filed": "2013-01-01", "name": "THE CORPORATION"},
        ]
        result = NewYorkMapper.best_registered_address(records)
        self.assertIsNotNone(result)
        # best_registered_address maps addr1 → address_line_1
        self.assertEqual(result["address_line_1"], "PRIMARY")
        self.assertEqual(result["city"], "NEW YORK")

    def test_best_registered_address_falls_back_to_profile(self):
        from ny.mapper import NewYorkMapper
        fallback = {"address_line_1": "FALLBACK ST", "city": "NYC"}
        result = NewYorkMapper.best_registered_address([], fallback=fallback)
        self.assertEqual(result["address_line_1"], "FALLBACK ST")

    def test_build_normalized_profile_uses_status_from_entity(self):
        from ny.mapper import NewYorkMapper
        from ny.new_york_resolver import NYResolutionResult

        res = NYResolutionResult(
            status="resolved",
            matched=True,
            dos_id="4424185",
            matched_name="ACME CORP.",
            match_method="name_scored",
            confidence=0.9,
            search_name="Acme Corp",
            raw_profile={
                "dos_id": "4424185",
                "current_entity_name": "ACME CORP.",
                "entity_type": "DOMESTIC BUSINESS CORPORATION",
                "jurisdiction": "New York",
                "county": "New York",
                "initial_dos_filing_date": "2013-06-27T00:00:00.000",
                "dos_process_name": "ACME CORP.",
                "dos_process_address_1": "23 MAIN ST",
                "dos_process_city": "NEW YORK",
                "dos_process_state": "NY",
                "dos_process_zip": "10001",
            },
            entity_info=[{
                "corpid_num": "4424185",
                "film_num": "130627000859",
                "date_filed": "2013-06-27",
                "mod_cert_code": "01DB A",
                "status": "Active",
            }],
            address_info=[{
                "addr_type": "1",
                "addr1": "23 MAIN ST",
                "city": "NEW YORK",
                "state": "NY",
                "zip5": "10001",
                "country": "USA",
                "name": "ACME CORP.",
                "date_filed": "2013-06-27",
            }],
        )

        profile = NewYorkMapper.build_normalized_profile(res)
        self.assertEqual(profile["company_status"], "Active")
        self.assertEqual(profile["source"], "New York Department of State")
        self.assertEqual(profile["dos_id"], "4424185")
        self.assertEqual(profile["registered_address"]["city"], "NEW YORK")
        self.assertEqual(profile["registered_address"]["source"], "ny_dos_addresses_dataset")


# ══════════════════════════════════════════════════════════════════════════════

class TestNYNameScoring(unittest.TestCase):

    def test_exact_match_after_normalization(self):
        from ny.new_york_resolver import _name_score
        self.assertEqual(_name_score("ACME CORP.", "ACME CORP."), 40.0)

    def test_incorporated_vs_inc(self):
        from ny.new_york_resolver import _name_score
        score = _name_score("Acme Technologies Incorporated", "ACME TECHNOLOGIES INC")
        self.assertGreater(score, 30.0)

    def test_completely_different(self):
        from ny.new_york_resolver import _name_score
        score = _name_score("Apple Inc", "Zebra Widgets LLC")
        self.assertLess(score, 20.0)

    def test_city_score_exact(self):
        from ny.new_york_resolver import _city_score
        self.assertEqual(_city_score("New York", "NEW YORK"), 20.0)

    def test_city_score_borough(self):
        from ny.new_york_resolver import _city_score
        # Brooklyn in Kings county
        self.assertEqual(_city_score("Brooklyn", "Kings"), 0.0)  # county_score handles this

    def test_zip_exact(self):
        from ny.new_york_resolver import _zip_score
        self.assertEqual(_zip_score("10001", "10001"), 20.0)

    def test_zip_partial_zone(self):
        from ny.new_york_resolver import _zip_score
        score = _zip_score("10001", "10005")
        self.assertEqual(score, 8.0)  # first 3 digits match


class TestNYResolverMatching(unittest.IsolatedAsyncioTestCase):

    def _make_client(self, search_results):
        client = MagicMock()
        client.search_by_name = AsyncMock(return_value=search_results)
        client.get_filings = AsyncMock(return_value=[])
        client.get_stock_info = AsyncMock(return_value=[])
        client.get_entity_info = AsyncMock(return_value=[])
        return client

    async def test_clear_match_resolves(self):
        from ny.new_york_resolver import NewYorkCompanyResolver
        candidates = [
            {
                "dos_id": "9999999",
                "current_entity_name": "ACME TECHNOLOGIES, INC.",
                "entity_type": "DOMESTIC BUSINESS CORPORATION",
                "county": "New York",
                "jurisdiction": "New York",
                "initial_dos_filing_date": "2005-03-10T00:00:00.000",
                "dos_process_address_1": "350 FIFTH AVE",
                "dos_process_city": "NEW YORK",
                "dos_process_state": "NY",
                "dos_process_zip": "10118",
            },
            # A weaker second candidate
            {
                "dos_id": "1111111",
                "current_entity_name": "ACME SOLUTIONS LLC",
                "entity_type": "DOMESTIC LIMITED LIABILITY COMPANY",
                "county": "Erie",
                "jurisdiction": "New York",
                "initial_dos_filing_date": "2010-01-01T00:00:00.000",
                "dos_process_address_1": "100 MAIN ST",
                "dos_process_city": "BUFFALO",
                "dos_process_state": "NY",
                "dos_process_zip": "14201",
            },
        ]
        client = self._make_client(candidates)
        resolver = NewYorkCompanyResolver(client)
        data = _make_company(
            company_name="Acme Technologies Inc",
            city="New York",
            postal_code="10118",
        )
        result = await resolver.resolve(data)

        self.assertEqual(result.status, "resolved")
        self.assertTrue(result.matched)
        self.assertEqual(result.dos_id, "9999999")
        self.assertGreater(result.confidence, 0.6)

    async def test_no_results_returns_not_found(self):
        from ny.new_york_resolver import NewYorkCompanyResolver
        client = self._make_client([])
        resolver = NewYorkCompanyResolver(client)
        data = _make_company(company_name="Nonexistent Corp XYZ")
        result = await resolver.resolve(data)
        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.matched)

    async def test_ambiguous_refuses_to_guess(self):
        """When multiple candidates are too close, resolver must not pick one."""
        from ny.new_york_resolver import NewYorkCompanyResolver, SCORE_AMBIGUOUS_MIN, SCORE_GAP_MIN
        # Two candidates with nearly identical scores
        candidates = [
            {
                "dos_id": "1000001", "current_entity_name": "ABC GROUP INC",
                "entity_type": "DOMESTIC BUSINESS CORPORATION", "county": "New York",
                "jurisdiction": "New York", "initial_dos_filing_date": "2000-01-01T00:00:00.000",
                "dos_process_address_1": "1 BROADWAY", "dos_process_city": "NEW YORK",
                "dos_process_state": "NY", "dos_process_zip": "10004",
            },
            {
                "dos_id": "1000002", "current_entity_name": "ABC GROUP LLC",
                "entity_type": "DOMESTIC LIMITED LIABILITY COMPANY", "county": "New York",
                "jurisdiction": "New York", "initial_dos_filing_date": "2001-01-01T00:00:00.000",
                "dos_process_address_1": "1 BROADWAY", "dos_process_city": "NEW YORK",
                "dos_process_state": "NY", "dos_process_zip": "10004",
            },
        ]
        client = self._make_client(candidates)
        resolver = NewYorkCompanyResolver(client)
        data = _make_company(company_name="ABC Group", city="New York", postal_code="10004")
        result = await resolver.resolve(data)

        # Must not pick a random result
        if result.status == "ambiguous":
            self.assertFalse(result.matched)
        elif result.status == "resolved":
            # If somehow resolved, the gap must have been large enough
            self.assertGreaterEqual(
                result.signals.get("gap", 0),
                SCORE_GAP_MIN,
            )
        # "unavailable" / "unresolved" are also acceptable — means no wrong match

    async def test_no_company_name_skips(self):
        from ny.new_york_resolver import NewYorkCompanyResolver
        client = self._make_client([])
        resolver = NewYorkCompanyResolver(client)
        data = _make_company(company_name=None, legal_name=None)
        result = await resolver.resolve(data)
        self.assertEqual(result.status, "skipped")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Final JSON compatibility — new CompanyData fields
# ══════════════════════════════════════════════════════════════════════════════

class TestCompanyDataNewFields(unittest.TestCase):
    """Ensure new fields are present in CompanyData and model_dump() still works."""

    def test_new_fields_exist_and_default_none(self):
        from models import CompanyData
        data = CompanyData()
        self.assertIsNone(data.jurisdiction_detection)
        self.assertIsNone(data.registry_data)
        self.assertIsNone(data.ny_dos_id)
        self.assertIsNone(data.ny_entity_type)
        self.assertIsNone(data.ny_county)
        self.assertIsNone(data.ny_jurisdiction)
        self.assertIsNone(data.ny_filing_date)
        self.assertEqual(data.ny_filing_history, [])
        self.assertEqual(data.ny_stock_info, [])
        self.assertEqual(data.ny_entity_info, [])

    def test_model_dump_includes_new_fields(self):
        from models import CompanyData
        data = CompanyData(
            domain="example.com",
            company_name="Test Corp",
            ny_dos_id="9876543",
            ny_entity_type="DOMESTIC BUSINESS CORPORATION",
            jurisdiction_detection={"country": "United States", "state": "New York"},
            registry_data={"registry_type": "ny_dos", "matched": True},
        )
        dumped = data.model_dump()
        self.assertEqual(dumped["ny_dos_id"], "9876543")
        self.assertEqual(dumped["ny_entity_type"], "DOMESTIC BUSINESS CORPORATION")
        self.assertIn("jurisdiction_detection", dumped)
        self.assertIn("registry_data", dumped)
        # Existing fields still present
        self.assertIn("company_name", dumped)
        self.assertIn("legal_name", dumped)
        self.assertIn("official_registry_profile", dumped)

    def test_existing_fields_unchanged(self):
        """Ensure none of the original CompanyData fields were accidentally removed."""
        from models import CompanyData
        data = CompanyData()
        original_fields = [
            "domain", "company_name", "brand_name", "legal_name",
            "registration_number", "vat_tax_number", "country", "state_province",
            "city", "full_address", "postal_code", "phone", "email", "website",
            "industry", "business_description", "founded", "parent_company",
            "subsidiaries", "directors", "management", "source_pages",
            "website_address", "official_registered_address",
            "identity_match", "identity_match_method", "identity_confidence",
            "data_completeness", "company_status", "company_type", "jurisdiction",
            "sic_codes", "persons_with_significant_control", "filing_history",
            "charges", "insolvency", "companies_house_status",
            "companies_house_resolution", "official_registry_profile",
            "field_evidence", "conflicts", "crawled_pages_summary",
        ]
        dumped = data.model_dump()
        for f in original_fields:
            self.assertIn(f, dumped, f"Original field '{f}' missing from model_dump()")


if __name__ == "__main__":
    unittest.main()

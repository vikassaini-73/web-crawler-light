"""
Tests for the safe Companies House resolution system.

Tests A–F use unittest.mock — no real API calls are made.

Run:
    python -m pytest tests/test_resolver.py -v
    # or
    python tests/test_resolver.py
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from uk.resolver import (
    UKCompanyResolver,
    ResolutionResult,
    normalize_company_number,
    _normalise_name,
    _name_score,
    _postcode_score,
    SCORE_RESOLVED_MIN,
    SCORE_GAP_MIN,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _website(
    name=None,
    legal_name=None,
    reg_no=None,
    domain="",
    postcode=None,
    city=None,
    address=None,
):
    """Build a minimal website-data object for the resolver."""
    obj = MagicMock()
    obj.company_name        = name
    obj.legal_name          = legal_name
    obj.registration_number = reg_no
    obj.domain              = domain
    obj.postal_code         = postcode
    obj.city                = city
    obj.full_address        = address
    return obj


def _ch_candidate(number, name, status="active", postcode=None, locality=None):
    """Build a mock Companies House search result item."""
    addr = {}
    if postcode:
        addr["postal_code"] = postcode
    if locality:
        addr["locality"] = locality
    return {
        "company_number":           number,
        "title":                    name,
        "company_status":           status,
        "registered_office_address": addr,
    }


def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ── Test cases ────────────────────────────────────────────────────────────────

class TestNormaliseCompanyNumber(unittest.TestCase):
    """Unit tests for the normalisation helper."""

    def test_scottish_prefix(self):
        self.assertEqual(normalize_company_number("SC311560"), "SC311560")

    def test_scottish_prefix_short(self):
        self.assertEqual(normalize_company_number("SC12345"), "SC012345")

    def test_ni_prefix(self):
        self.assertEqual(normalize_company_number("NI123456"), "NI123456")

    def test_pure_numeric(self):
        self.assertEqual(normalize_company_number("12345678"), "12345678")

    def test_pure_numeric_short(self):
        self.assertEqual(normalize_company_number("1234"), "00001234")

    def test_whitespace_stripped(self):
        self.assertEqual(normalize_company_number(" SC 311560 "), "SC311560")

    def test_none_returns_none(self):
        self.assertIsNone(normalize_company_number(None))

    def test_empty_returns_none(self):
        self.assertIsNone(normalize_company_number(""))


class TestNameNormalise(unittest.TestCase):
    """Name-normalisation unit tests."""

    def test_ltd_limited_equal(self):
        a = _normalise_name("ABC Limited")
        b = _normalise_name("ABC Ltd")
        # After normalisation both → "ABC LTD"
        self.assertEqual(a, b)

    def test_case_insensitive(self):
        self.assertEqual(_normalise_name("BREWDOG PLC"), _normalise_name("BrewDog plc"))

    def test_name_score_perfect(self):
        score = _name_score("BrewDog PLC", "BREWDOG PLC")
        self.assertAlmostEqual(score, 40.0, places=0)

    def test_name_score_partial(self):
        score = _name_score("Acme Corp", "Acme Corporation Limited")
        self.assertGreater(score, 15.0)  # partial match
        self.assertLessEqual(score, 40.0)


class TestPostcodeScore(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(_postcode_score("AB41 8BX", "AB41 8BX"), 25.0)

    def test_case_whitespace_insensitive(self):
        self.assertEqual(_postcode_score("ab41 8bx", "AB418BX"), 25.0)

    def test_partial_sector_match(self):
        score = _postcode_score("AB41 8BX", "AB41 9ZZ")
        self.assertEqual(score, 10.0)

    def test_no_match(self):
        self.assertEqual(_postcode_score("SW1A 1AA", "EC1A 1BB"), 0.0)

    def test_missing_website_postcode(self):
        self.assertEqual(_postcode_score(None, "AB41 8BX"), 0.0)


# ── Resolution Tests ──────────────────────────────────────────────────────────

class TestResolverTestA_DirectLookup(unittest.TestCase):
    """
    Test A: Registration number available → direct lookup, NO name search.
    """

    def test_direct_lookup_called_no_search(self):
        """SC311560 present → get_company_profile called, search_company NOT called."""
        client = MagicMock()
        client.get_company_profile = AsyncMock(return_value={
            "company_name":   "BREWDOG PLC",
            "company_number": "SC311560",
            "company_status": "active",
            "registered_office_address": {
                "postal_code":  "AB41 8BX",
                "locality":     "Ellon",
            },
        })
        client.search_company = AsyncMock()  # must NOT be called

        resolver = UKCompanyResolver(client)
        site     = _website(name="BrewDog", reg_no="SC311560", domain="brewdog.com",
                            postcode="AB41 8BX", city="Ellon")

        result = run(resolver.resolve(site))

        # search_company must never be called when reg_no is present
        client.search_company.assert_not_called()
        client.get_company_profile.assert_called_once_with("SC311560")

        self.assertIn(result.status, ("resolved_direct", "resolved"))
        self.assertTrue(result.matched)
        self.assertEqual(result.company_number, "SC311560")
        self.assertIn(result.match_method, ("direct_lookup", "registration_number"))

    def test_direct_lookup_not_found(self):
        """Company number on website but CH returns 404."""
        client = MagicMock()
        client.get_company_profile = AsyncMock(return_value=None)
        client.search_company      = AsyncMock()

        resolver = UKCompanyResolver(client)
        site     = _website(name="Ghost Corp", reg_no="99999999")

        result = run(resolver.resolve(site))

        client.search_company.assert_not_called()
        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.matched)


class TestResolverTestB_ClearMatch(unittest.TestCase):
    """
    Test B: No reg number + one clear candidate → resolved.
    """

    def test_single_strong_candidate_resolved(self):
        client = MagicMock()
        client.get_company_profile = AsyncMock()
        client.search_company = AsyncMock(return_value=[
            _ch_candidate("12345678", "XYZ LIMITED", "active", postcode="SW1A 1AA", locality="London"),
            _ch_candidate("87654321", "XYZZY HOLDINGS", "active", postcode="EC1A 1BB"),
        ])

        resolver = UKCompanyResolver(client)
        site = _website(
            name="XYZ Limited",
            domain="xyz.co.uk",
            postcode="SW1A 1AA",
            city="London",
        )

        result = run(resolver.resolve(site))

        # Must have searched (no reg_no)
        client.search_company.assert_called_once()
        # Strong match: name + postcode + city + active
        self.assertEqual(result.status, "resolved")
        self.assertTrue(result.matched)
        self.assertEqual(result.company_number, "12345678")
        self.assertGreaterEqual(result.confidence, 0.70)

    def test_candidates_preserved_in_result(self):
        client = MagicMock()
        client.search_company = AsyncMock(return_value=[
            _ch_candidate("11111111", "XYZ LIMITED", "active", postcode="SW1A 1AA"),
            _ch_candidate("22222222", "OTHER COMPANY", "active"),
        ])

        resolver = UKCompanyResolver(client)
        site = _website(name="XYZ Limited", postcode="SW1A 1AA")
        result = run(resolver.resolve(site))

        self.assertGreater(len(result.candidates), 0)
        # All candidates must have score field
        for cand in result.candidates:
            self.assertIn("score", cand)


class TestResolverTestC_MultipleCandidates(unittest.TestCase):
    """
    Test C: Multiple candidates → best selected ONLY if clearly ahead.
    """

    def test_best_wins_when_clearly_ahead(self):
        client = MagicMock()
        client.search_company = AsyncMock(return_value=[
            # Winner: name + postcode + city + active = ~90 pts
            _ch_candidate("AAAAAA", "PRECISION WIDGETS LIMITED", "active",
                          postcode="M1 1AA", locality="Manchester"),
            # Runner-up: name only partial, no postcode match
            _ch_candidate("BBBBBB", "PRECISION PLASTICS LTD", "active",
                          postcode="E1 6AN", locality="London"),
            _ch_candidate("CCCCCC", "PRECISION TOOLS PLC", "dissolved",
                          postcode="EX1 1AA"),
        ])

        resolver = UKCompanyResolver(client)
        site = _website(
            name="Precision Widgets Limited",
            postcode="M1 1AA",
            city="Manchester",
        )
        result = run(resolver.resolve(site))

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.company_number, "AAAAAA")


class TestResolverTestD_Ambiguous(unittest.TestCase):
    """
    Test D: Two near-equal candidates → ambiguous, no automatic selection.
    """

    def test_ambiguous_when_gap_is_small(self):
        """
        Two candidates with similar names and no address signals.
        Gap will be < GAP_MIN → must return ambiguous.
        """
        client = MagicMock()
        # Both candidates have similar names, no postcode/city to differentiate
        client.search_company = AsyncMock(return_value=[
            _ch_candidate("AAAA01", "SMITH & JONES LIMITED", "active"),
            _ch_candidate("AAAA02", "SMITH AND JONES LIMITED", "active"),
        ])

        resolver = UKCompanyResolver(client)
        # Website has no postcode or city → only name signal available
        site = _website(name="Smith and Jones Limited")

        result = run(resolver.resolve(site))

        # Must NOT resolve — either ambiguous or unresolved
        self.assertFalse(result.matched)
        self.assertIn(result.status, ("ambiguous", "unresolved"))
        # Candidates must still be preserved
        self.assertGreater(len(result.candidates), 0)


class TestResolverTestE_NoMatch(unittest.TestCase):
    """
    Test E: No Companies House match → unresolved, website data preserved, no crash.
    """

    def test_empty_search_results(self):
        client = MagicMock()
        client.search_company = AsyncMock(return_value=[])

        resolver = UKCompanyResolver(client)
        site = _website(name="Nonexistent Company That Does Not Exist Ltd")

        result = run(resolver.resolve(site))

        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.matched)
        self.assertIsNone(result.company_number)

    def test_low_scoring_results_not_resolved(self):
        client = MagicMock()
        # All candidates have names completely different from search
        client.search_company = AsyncMock(return_value=[
            _ch_candidate("X1", "ALPHA BETA GAMMA PLC", "active"),
            _ch_candidate("X2", "DELTA EPSILON ZETA LTD", "active"),
        ])

        resolver = UKCompanyResolver(client)
        site = _website(name="Something Entirely Different Corp")

        result = run(resolver.resolve(site))

        self.assertFalse(result.matched)
        self.assertIn(result.status, ("unresolved", "ambiguous", "not_found"))


class TestResolverTestF_APIUnavailable(unittest.TestCase):
    """
    Test F: API unavailable / raises exception → status=unavailable, no crash.
    """

    def test_api_raises_exception(self):
        client = MagicMock()
        client.get_company_profile = AsyncMock(side_effect=Exception("Connection refused"))
        client.search_company      = AsyncMock(side_effect=Exception("Connection refused"))

        resolver = UKCompanyResolver(client)
        site = _website(name="Any Company Ltd", reg_no="12345678")

        # Must not raise
        result = run(resolver.resolve(site))

        self.assertEqual(result.status, "unavailable")
        self.assertFalse(result.matched)
        self.assertIn("error", result.signals)

    def test_search_raises_exception(self):
        client = MagicMock()
        client.search_company = AsyncMock(side_effect=RuntimeError("Timeout"))

        resolver = UKCompanyResolver(client)
        site = _website(name="Any Company Ltd")

        result = run(resolver.resolve(site))

        self.assertEqual(result.status, "unavailable")
        self.assertFalse(result.matched)

    def test_skipped_when_no_name_or_number(self):
        client = MagicMock()
        client.search_company = AsyncMock()

        resolver = UKCompanyResolver(client)
        site = _website()   # no name, no reg_no

        result = run(resolver.resolve(site))

        client.search_company.assert_not_called()
        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.matched)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)

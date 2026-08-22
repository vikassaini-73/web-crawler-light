"""
Comprehensive unit tests for extraction, validation, and Companies House identity resolution.
"""

import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from validator import (
    validate_email, validate_phone, validate_registration_number,
    validate_vat_number, validate_uk_postcode, validate_address,
    is_same_base_domain
)
from parser import parse_page_structure, deobfuscate_cf_email
from crawler import PageContent, extract_page_elements
from extractor import CompanyDataExtractor, clean_address_string
from pipeline import is_uk_company, CompanyIntelligencePipeline
from models import CompanyData
from uk.resolver import UKCompanyResolver, ResolutionResult


class TestValidation(unittest.TestCase):
    """Test validator functions for strict rules."""

    def test_reject_placeholder_emails(self):
        self.assertIsNone(validate_email("test@test.com"))
        self.assertIsNone(validate_email("example@example.com"))
        self.assertIsNone(validate_email("name@example.com"))
        self.assertIsNone(validate_email("your@email.com"))
        self.assertIsNone(validate_email("you@example.com"))
        self.assertIsNone(validate_email("john@domain.com"))
        self.assertIsNone(validate_email("someone@example.com"))
        self.assertIsNone(validate_email("admin@domain.com"))

    def test_contextual_example_rejection(self):
        context = "Your Email is invalid, enter a valid email address, for example test@test.com"
        self.assertIsNone(validate_email("test@test.com", context=context))

    def test_accept_valid_company_email(self):
        self.assertEqual(validate_email("People@brewdog.com"), "people@brewdog.com")
        self.assertEqual(validate_email("contact@company.co.uk"), "contact@company.co.uk")
        self.assertEqual(validate_email("support@stripe.com"), "support@stripe.com")

    def test_uk_postcode_validation(self):
        self.assertEqual(validate_uk_postcode("AB41 8BX"), "AB41 8BX")
        self.assertEqual(validate_uk_postcode("ab418bx"), "AB41 8BX")
        self.assertEqual(validate_uk_postcode("G2 1BP"), "G2 1BP")
        self.assertEqual(validate_uk_postcode("SW1A 1AA"), "SW1A 1AA")
        self.assertIsNone(validate_uk_postcode("90210"))  # US Zip, not UK postcode

    def test_vat_number_normalization(self):
        self.assertEqual(validate_vat_number("897 6381 54"), "897 6381 54")
        self.assertEqual(validate_vat_number("GB 897 6381 54"), "GB 897 6381 54")
        self.assertIsNone(validate_vat_number("123"))

    def test_registration_number_validation(self):
        self.assertEqual(validate_registration_number("SC311560"), "SC311560")
        self.assertEqual(validate_registration_number("01234567"), "01234567")

    def test_same_base_domain(self):
        self.assertTrue(is_same_base_domain("brewdog.com", "jobs.brewdog.com"))
        self.assertTrue(is_same_base_domain("https://brewdog.com", "https://pages.brewdog.com/info"))
        self.assertFalse(is_same_base_domain("brewdog.com", "google.com"))


class TestParserAndExtraction(unittest.TestCase):
    """Test Cloudflare deobfuscation and entity extraction."""

    def test_cloudflare_email_deobfuscation(self):
        # Cloudflare encoded hex for 'People@brewdog.com'
        cf_hex = "4313262c332f260321312634272c246d202c2e"
        decoded = deobfuscate_cf_email(cf_hex)
        self.assertEqual(decoded, "People@brewdog.com")

    def test_address_stop_detection(self):
        raw_address = "Address : BrewDog, Balmacassie Industrial Estate, Ellon, Aberdeen, AB41 8BX, Or you can get us on our fancy social media channels:, fb, fb, fb, fb"
        cleaned = clean_address_string(raw_address)
        self.assertNotIn("fb", cleaned)
        self.assertNotIn("fancy social", cleaned)
        self.assertIn("Balmacassie Industrial Estate", cleaned)
        self.assertIn("AB41 8BX", cleaned)

    def test_extractor_brewdog_sample_html(self):
        html = """
        <html>
        <head><title>BrewDog | Company Information</title></head>
        <body>
            <h1>BrewDog Company Information</h1>
            <p>BrewDog PLC.</p>
            <p>Company No: SC311560</p>
            <p>VAT No: 897 6381 54</p>
            <p>Registered Office in Scotland at BrewDog, Balmacassie Commercial Park, Ellon, Aberdeenshire, AB41 8BX</p>
            <p>Email : <a href="/cdn-cgi/l/email-protection#4313262c332f260321312634272c246d202c2e"><span class="__cf_email__" data-cfemail="4313262c332f260321312634272c246d202c2e">[email protected]</span></a></p>
            <p>For example enter test@test.com in the form.</p>
        </body>
        </html>
        """
        page = extract_page_elements("https://brewdog.com/pages/company-information", html)
        extractor = CompanyDataExtractor("https://brewdog.com")
        company_data = extractor.extract_all([page])

        self.assertEqual(company_data.registration_number, "SC311560")
        self.assertEqual(company_data.vat_tax_number, "897 6381 54")
        self.assertIn("BrewDog", company_data.company_name)
        self.assertEqual(company_data.postal_code, "AB41 8BX")
        self.assertEqual(company_data.country, "United Kingdom")
        self.assertEqual(company_data.email, "people@brewdog.com")
        self.assertNotEqual(company_data.email, "test@test.com")


class TestPipelineFlowAndResolver(unittest.TestCase):
    """Test UK country detection, Companies House resolution confidence, and flow."""

    def test_uk_country_detection(self):
        uk_data = CompanyData(domain="brewdog.com", registration_number="SC311560", postal_code="AB41 8BX")
        self.assertTrue(is_uk_company(uk_data, "https://brewdog.com"))

        non_uk_data = CompanyData(domain="stripe.com", country="United States", postal_code="94103")
        self.assertFalse(is_uk_company(non_uk_data, "https://stripe.com"))

    def test_direct_company_number_confidence_100(self):
        """When registration number matches, resolution confidence must be 1.0."""
        client = MagicMock()
        client.get_company_profile = AsyncMock(return_value={
            "company_name": "BREWDOG PLC",
            "company_number": "SC311560",
            "company_status": "administration",
            "type": "plc",
            "registered_office_address": {
                "address_line_1": "Clyde Offices",
                "locality": "Glasgow",
                "postal_code": "G2 1BP"
            }
        })
        client.search_company = AsyncMock()

        resolver = UKCompanyResolver(client)
        # Note: website address is Ellon AB41 8BX while CH registered office is Glasgow G2 1BP
        site = CompanyData(
            domain="brewdog.com",
            company_name="BrewDog",
            registration_number="SC311560",
            postal_code="AB41 8BX",
            city="Ellon"
        )

        import asyncio
        result = asyncio.run(resolver.resolve(site))

        self.assertTrue(result.matched)
        self.assertEqual(result.company_number, "SC311560")
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.match_method, "registration_number")
        client.search_company.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

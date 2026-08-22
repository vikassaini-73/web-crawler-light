import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CompaniesHouseMapper:
    """Maps Companies House API data to normalized internal schema and CompanyData."""

    @staticmethod
    def map_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
        """Map raw company profile data into structured dictionary."""
        mapped = {}

        # Identification
        mapped["legal_name"] = profile.get("company_name")
        mapped["company_name"] = profile.get("company_name")
        mapped["registration_number"] = profile.get("company_number")
        mapped["company_status"] = profile.get("company_status")
        mapped["company_type"] = profile.get("type")
        mapped["jurisdiction"] = profile.get("jurisdiction")

        # Official Registered Office Address
        addr = profile.get("registered_office_address", {})
        parts = [
            addr.get("address_line_1"),
            addr.get("address_line_2"),
            addr.get("locality"),
            addr.get("region"),
            addr.get("postal_code"),
            addr.get("country")
        ]
        mapped["registered_office_address"] = {
            "address_line_1": addr.get("address_line_1"),
            "address_line_2": addr.get("address_line_2"),
            "city": addr.get("locality"),
            "region": addr.get("region"),
            "postal_code": addr.get("postal_code"),
            "country": addr.get("country") or "United Kingdom"
        }
        mapped["full_registered_address"] = ", ".join([p for p in parts if p])
        mapped["registered_city"] = addr.get("locality")
        mapped["registered_postal_code"] = addr.get("postal_code")
        mapped["registered_country"] = addr.get("country") or "United Kingdom"

        # Incorporation Date
        inc_date = profile.get("date_of_creation")
        if inc_date:
            mapped["founded"] = inc_date
            mapped["incorporation_date"] = inc_date

        # SIC Codes (Industry)
        sic_codes = profile.get("sic_codes", [])
        if sic_codes:
            mapped["sic_codes"] = sic_codes
            mapped["industry"] = ", ".join(sic_codes)

        return mapped

    @staticmethod
    def map_officers(officers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Map officers to directors and management."""
        directors = []
        management = []

        for off in officers:
            if off.get("resigned_on"):
                continue

            name = off.get("name")
            role = off.get("officer_role", "").lower()

            if not name:
                continue

            if "director" in role:
                directors.append(name)
            elif "secretary" in role or "manager" in role:
                management.append(name)

        return {
            "directors": directors,
            "management": management
        }

    @staticmethod
    def map_psc(psc_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map Persons with Significant Control."""
        mapped_pscs = []
        for item in psc_items:
            mapped_pscs.append({
                "name": item.get("name"),
                "kind": item.get("kind"),
                "natures_of_control": item.get("natures_of_control", []),
                "nationality": item.get("nationality"),
                "country_of_residence": item.get("country_of_residence")
            })
        return mapped_pscs

    @staticmethod
    def map_filing_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map filing history metadata."""
        return [
            {
                "date": h.get("date"),
                "type": h.get("type"),
                "description": h.get("description"),
                "category": h.get("category")
            }
            for h in history[:10]
        ]

    @classmethod
    def build_normalized_profile(
        cls,
        profile: Dict[str, Any],
        officers: List[Dict[str, Any]],
        psc: List[Dict[str, Any]],
        filing_history: List[Dict[str, Any]],
        charges: List[Dict[str, Any]],
        insolvency: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Produce the standardized normalized Companies House dictionary."""
        p_map = cls.map_profile(profile)
        o_map = cls.map_officers(officers)
        return {
            "company_name": p_map.get("company_name", ""),
            "legal_name": p_map.get("legal_name", ""),
            "registration_number": p_map.get("registration_number", ""),
            "company_status": p_map.get("company_status", ""),
            "company_type": p_map.get("company_type", ""),
            "incorporation_date": p_map.get("incorporation_date", ""),
            "registered_address": p_map.get("registered_office_address", {}),
            "sic_codes": p_map.get("sic_codes", []),
            "officers": o_map,
            "psc": cls.map_psc(psc),
            "filing_history": cls.map_filing_history(filing_history),
            "charges": charges or [],
            "insolvency": insolvency or {}
        }

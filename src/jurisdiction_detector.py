"""
Jurisdiction Detector Module
Multi-signal country and US state detection from extracted CompanyData.

Signal hierarchy (strongest → weakest):
  1. JSON-LD / structured address fields (country, state_province, postal_code)
  2. Full address text — country/state keywords
  3. Registration number format (UK prefixes, etc.)
  4. UK postcode pattern
  5. Domain TLD (.co.uk, .org.uk, .uk)
  6. Phone number area code — SUPPORTING SIGNAL ONLY

Phone number alone is NEVER sufficient to conclude jurisdiction.
.com / .net / .org TLDs do NOT imply any country or state.

Output: JurisdictionResult dataclass with country, state, city,
        confidence (0.0–1.0), and signals list for auditability.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import tldextract

logger = logging.getLogger(__name__)

# ── US state name/abbreviation maps ──────────────────────────────────────────

US_STATE_ABBR: Dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

US_STATE_NAME_TO_ABBR: Dict[str, str] = {v.lower(): k for k, v in US_STATE_ABBR.items()}

# NY area codes — used ONLY as a supporting signal
NY_AREA_CODES = {
    "212", "332", "347", "516", "518", "585", "607", "631",
    "646", "680", "716", "718", "838", "845", "914", "917", "929",
}

# UK TLD patterns
UK_TLDS = {".co.uk", ".org.uk", ".me.uk", ".net.uk", ".ltd.uk", ".plc.uk", ".sch.uk"}

# UK company number prefixes (definitive UK signal)
UK_REG_PREFIXES = {"SC", "NI", "OC", "LP", "SO", "IP", "SL", "NC", "NL", "NZ"}

# Country name normalisation → ISO-style canonical name
COUNTRY_ALIASES: Dict[str, str] = {
    "uk": "United Kingdom", "gb": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "northern ireland": "United Kingdom", "britain": "United Kingdom",
    "usa": "United States", "us": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "america": "United States",
    "united states of america": "United States",
}

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class JurisdictionResult:
    """
    Detected jurisdiction with full signal audit trail.

    country:    canonical country name or None
    state:      US state full name (e.g. "New York") or None
    state_abbr: 2-letter abbreviation (e.g. "NY") or None
    city:       city name from address/content or None
    confidence: 0.0–1.0 aggregate confidence
    signals:    list of dicts describing each contributing signal
    raw_country_text: whatever country text was found before normalisation
    """
    country: Optional[str] = None
    state: Optional[str] = None
    state_abbr: Optional[str] = None
    city: Optional[str] = None
    confidence: float = 0.0
    signals: List[Dict[str, Any]] = field(default_factory=list)
    raw_country_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "country": self.country,
            "state": self.state,
            "state_abbr": self.state_abbr,
            "city": self.city,
            "confidence": round(self.confidence, 3),
            "signals": self.signals,
        }

    def is_uk(self) -> bool:
        return self.country == "United Kingdom"

    def is_us(self) -> bool:
        return self.country == "United States"

    def is_us_ny(self) -> bool:
        return self.is_us() and self.state_abbr == "NY"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_country(raw: str) -> Optional[str]:
    """Return canonical country name or None."""
    if not raw:
        return None
    # Strip trailing punctuation AND internal dots so "U.S.A." → "u.s.a."
    cleaned = raw.strip().lower().rstrip(".,;")
    # Also try without internal dots: "u.s.a." → "usa"
    nodots = cleaned.replace(".", "")
    return (
        COUNTRY_ALIASES.get(cleaned)
        or COUNTRY_ALIASES.get(nodots)
        or (raw.strip().title() if cleaned else None)
    )


def _extract_us_state_from_text(text: str) -> Optional[str]:
    """
    Scan text for US state names or abbreviations.
    Returns 2-letter abbreviation or None.

    Matches:
      - Full state names: "New York", "California"
      - Postal abbreviations in address context: ", NY " / ", NY," / "NY 10001"
    """
    if not text:
        return None

    t_lower = text.lower()

    # 1. Full state name match (longest first to prefer "New Hampshire" over "New")
    sorted_names = sorted(US_STATE_NAME_TO_ABBR.keys(), key=len, reverse=True)
    for name in sorted_names:
        if re.search(r'\b' + re.escape(name) + r'\b', t_lower):
            return US_STATE_NAME_TO_ABBR[name]

    # 2. Abbreviation in address context: ", NY " / ", NY," / "NY 10001" / "(NY)"
    abbr_pattern = re.compile(
        r'(?:,\s*|\(\s*|\b)([A-Z]{2})(?:\s+\d{5}|\s*[,)\s]|$)',
        re.MULTILINE
    )
    for m in abbr_pattern.finditer(text):
        abbr = m.group(1)
        if abbr in US_STATE_ABBR:
            return abbr

    return None


def _extract_country_from_text(text: str) -> Optional[str]:
    """Scan text for explicit country names/aliases."""
    if not text:
        return None
    t_lower = text.lower()

    # Check aliases first (uk/gb/usa etc.)
    for alias, canonical in COUNTRY_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', t_lower):
            return canonical

    # Check "United Kingdom" / "United States" directly
    for name in ("United Kingdom", "United States"):
        if name.lower() in t_lower:
            return name

    return None


def _phone_ny_signal(phone: Optional[str]) -> bool:
    """Return True if phone area code is a known NY area code."""
    if not phone:
        return False
    digits = re.sub(r'\D', '', phone)
    # +1 NXX or just NXX
    if digits.startswith('1') and len(digits) >= 4:
        area = digits[1:4]
    elif len(digits) >= 3:
        area = digits[:3]
    else:
        return False
    return area in NY_AREA_CODES


def _domain_tld_signal(domain: Optional[str]) -> Optional[str]:
    """
    Return country hint from TLD.
    Only returns a country for COUNTRY-CODE TLDs (ccTLDs).
    .com / .net / .org / .io etc. return None.
    """
    if not domain:
        return None

    d = domain.lower().split("/")[0].split("?")[0]

    # UK ccTLDs
    for tld in UK_TLDS:
        if d.endswith(tld):
            return "United Kingdom"
    if d.endswith(".uk"):
        return "United Kingdom"

    # Other common ccTLDs — extend as needed
    ccTLD_map = {
        ".de": "Germany", ".fr": "France", ".it": "Italy",
        ".es": "Spain", ".nl": "Netherlands", ".au": "Australia",
        ".ca": "Canada", ".ie": "Ireland", ".nz": "New Zealand",
        ".in": "India", ".jp": "Japan", ".cn": "China",
    }
    for tld, country in ccTLD_map.items():
        if d.endswith(tld):
            return country

    return None  # .com .net .org .io etc. → no country


# ── Main Detector ─────────────────────────────────────────────────────────────

class JurisdictionDetector:
    """
    Detects country and US state from a CompanyData object using multiple signals.

    Usage:
        detector = JurisdictionDetector()
        result = detector.detect(company_data)
    """

    def detect(self, data: Any) -> JurisdictionResult:
        """
        Run multi-signal detection against a CompanyData instance.
        Returns JurisdictionResult — never raises.
        """
        try:
            return self._detect_inner(data)
        except Exception as e:
            logger.error(f"[JurisdictionDetector] Unexpected error: {e}", exc_info=True)
            return JurisdictionResult(confidence=0.0, signals=[{"error": str(e)}])

    def _detect_inner(self, data: Any) -> JurisdictionResult:
        result = JurisdictionResult()
        signals: List[Dict[str, Any]] = []
        country_votes: Dict[str, float] = {}
        state_votes: Dict[str, float] = {}

        # ── Signal 1: Explicit country field ─────────────────────────────────
        raw_country = getattr(data, "country", None) or ""
        if raw_country:
            canonical = _normalise_country(raw_country)
            if canonical:
                country_votes[canonical] = country_votes.get(canonical, 0) + 0.70
                result.raw_country_text = raw_country
                signals.append({
                    "signal": "extracted_country_field",
                    "value": canonical,
                    "weight": 0.70,
                })

        # ── Signal 2: state_province field ───────────────────────────────────
        raw_state = getattr(data, "state_province", None) or ""
        if raw_state:
            abbr = _extract_us_state_from_text(raw_state)
            if abbr:
                state_votes[abbr] = state_votes.get(abbr, 0) + 0.65
                country_votes["United States"] = country_votes.get("United States", 0) + 0.50
                signals.append({
                    "signal": "extracted_state_province_field",
                    "value": f"{US_STATE_ABBR.get(abbr, abbr)} ({abbr})",
                    "weight": 0.65,
                })

        # ── Signal 3: UK registration number prefix ───────────────────────────
        reg_no = (getattr(data, "registration_number", None) or "").upper().strip()
        if reg_no:
            prefix_match = re.match(r'^([A-Z]{2})', reg_no)
            if prefix_match and prefix_match.group(1) in UK_REG_PREFIXES:
                country_votes["United Kingdom"] = country_votes.get("United Kingdom", 0) + 0.80
                signals.append({
                    "signal": "uk_registration_number_prefix",
                    "value": reg_no,
                    "weight": 0.80,
                })
            elif reg_no.isdigit() and len(reg_no) in (7, 8):
                country_votes["United Kingdom"] = country_votes.get("United Kingdom", 0) + 0.55
                signals.append({
                    "signal": "uk_registration_number_numeric",
                    "value": reg_no,
                    "weight": 0.55,
                })

        # ── Signal 4: UK postcode ─────────────────────────────────────────────
        try:
            from validator import validate_uk_postcode
        except ImportError:
            try:
                from .validator import validate_uk_postcode
            except ImportError:
                validate_uk_postcode = None

        postal = getattr(data, "postal_code", None) or ""
        full_addr = getattr(data, "full_address", None) or ""

        if validate_uk_postcode:
            pc_check = postal or full_addr
            if pc_check and validate_uk_postcode(pc_check):
                country_votes["United Kingdom"] = country_votes.get("United Kingdom", 0) + 0.70
                signals.append({
                    "signal": "uk_postcode",
                    "value": postal or "(in address)",
                    "weight": 0.70,
                })

        # ── Signal 4b: US ZIP code in postal_code field ───────────────────────
        # A 5-digit postal code that is NOT a UK postcode → strong US signal
        if postal and re.match(r'^\d{5}(?:-\d{4})?$', postal.strip()):
            country_votes["United States"] = country_votes.get("United States", 0) + 0.65
            signals.append({
                "signal": "us_zip_code_field",
                "value": postal,
                "weight": 0.65,
            })

        # ── Signal 5: Domain TLD ──────────────────────────────────────────────
        domain = getattr(data, "domain", None) or getattr(data, "website", None) or ""
        tld_country = _domain_tld_signal(domain)
        if tld_country:
            country_votes[tld_country] = country_votes.get(tld_country, 0) + 0.55
            signals.append({
                "signal": "domain_cctld",
                "value": f"{domain} → {tld_country}",
                "weight": 0.55,
            })

        # ── Signal 6: Country/state in full_address + postal_code + city ─────
        # Build a combined address blob from ALL address-related fields
        addr_parts = [
            full_addr,
            getattr(data, "city", None) or "",
            postal,
        ]
        # Also scan website_address dict if present
        website_addr = getattr(data, "website_address", None) or {}
        if isinstance(website_addr, dict):
            addr_parts += [
                website_addr.get("full_address") or "",
                website_addr.get("city") or "",
                website_addr.get("state_province") or "",
                website_addr.get("postal_code") or "",
                website_addr.get("country") or "",
            ]

        combined_addr = " ".join(p for p in addr_parts if p).strip()

        if combined_addr:
            # Country scan
            addr_country = _extract_country_from_text(combined_addr)
            if addr_country:
                country_votes[addr_country] = country_votes.get(addr_country, 0) + 0.55
                signals.append({
                    "signal": "country_in_address",
                    "value": addr_country,
                    "weight": 0.55,
                })

            # State scan — "City, ST ZIP" pattern (highest confidence)
            city_state_zip = re.search(
                r'\b([A-Za-z][A-Za-z\s]{1,25}),\s*([A-Z]{2})\s+(\d{5})\b',
                combined_addr
            )
            if city_state_zip:
                abbr = city_state_zip.group(2)
                if abbr in US_STATE_ABBR:
                    state_votes[abbr] = state_votes.get(abbr, 0) + 0.80
                    country_votes["United States"] = country_votes.get("United States", 0) + 0.65
                    signals.append({
                        "signal": "city_state_zip_pattern",
                        "value": f"{city_state_zip.group(0)} → {US_STATE_ABBR[abbr]} ({abbr})",
                        "weight": 0.80,
                    })
            else:
                # Fallback: any state mention in address
                addr_state = _extract_us_state_from_text(combined_addr)
                if addr_state:
                    state_votes[addr_state] = state_votes.get(addr_state, 0) + 0.55
                    country_votes["United States"] = country_votes.get("United States", 0) + 0.40
                    signals.append({
                        "signal": "state_in_address",
                        "value": f"{US_STATE_ABBR.get(addr_state, addr_state)} ({addr_state})",
                        "weight": 0.55,
                    })

        # ── Signal 7: VAT number country prefix ──────────────────────────────
        vat = (getattr(data, "vat_tax_number", None) or "").upper().strip()
        if vat:
            vat_country_prefixes = {
                "GB": "United Kingdom", "DE": "Germany", "FR": "France",
                "IT": "Italy", "ES": "Spain", "NL": "Netherlands",
                "AU": "Australia", "IE": "Ireland",
            }
            for prefix, country in vat_country_prefixes.items():
                if vat.startswith(prefix):
                    country_votes[country] = country_votes.get(country, 0) + 0.60
                    signals.append({
                        "signal": "vat_number_country_prefix",
                        "value": f"{vat} → {country}",
                        "weight": 0.60,
                    })
                    break

        # ── Signal 8: field_evidence scan ────────────────────────────────────
        # Scan field_evidence dict for any address/country evidence strings
        # that the extractor recorded but may not have surfaced to top-level fields
        field_evidence = getattr(data, "field_evidence", None) or {}
        if isinstance(field_evidence, dict):
            for fname in ("full_address", "city", "country", "postal_code", "state_province"):
                ev = field_evidence.get(fname)
                if ev is None:
                    continue
                # ev is either a FieldEvidence object or a dict
                ev_value = (
                    ev.get("value") if isinstance(ev, dict)
                    else getattr(ev, "value", None)
                ) or ""
                if not ev_value or not isinstance(ev_value, str):
                    continue

                # US City, ST ZIP in evidence value
                csz = re.search(
                    r'\b([A-Za-z][A-Za-z\s]{1,25}),\s*([A-Z]{2})\s+(\d{5})\b',
                    ev_value
                )
                if csz:
                    abbr = csz.group(2)
                    if abbr in US_STATE_ABBR:
                        state_votes[abbr] = state_votes.get(abbr, 0) + 0.70
                        country_votes["United States"] = country_votes.get("United States", 0) + 0.55
                        signals.append({
                            "signal": f"field_evidence_{fname}_city_state_zip",
                            "value": f"{csz.group(0)} → {US_STATE_ABBR[abbr]} ({abbr})",
                            "weight": 0.70,
                        })

                # US ZIP only in evidence
                elif re.match(r'^\d{5}(?:-\d{4})?$', ev_value.strip()):
                    country_votes["United States"] = country_votes.get("United States", 0) + 0.50
                    signals.append({
                        "signal": f"field_evidence_{fname}_zip",
                        "value": ev_value,
                        "weight": 0.50,
                    })

                # Country name in evidence
                ev_country = _extract_country_from_text(ev_value)
                if ev_country:
                    country_votes[ev_country] = country_votes.get(ev_country, 0) + 0.45
                    signals.append({
                        "signal": f"field_evidence_{fname}_country",
                        "value": ev_country,
                        "weight": 0.45,
                    })

                # State in evidence
                ev_state = _extract_us_state_from_text(ev_value)
                if ev_state and not csz:
                    state_votes[ev_state] = state_votes.get(ev_state, 0) + 0.45
                    country_votes["United States"] = country_votes.get("United States", 0) + 0.35
                    signals.append({
                        "signal": f"field_evidence_{fname}_state",
                        "value": f"{US_STATE_ABBR.get(ev_state, ev_state)} ({ev_state})",
                        "weight": 0.45,
                    })

        # ── Signal 9: Phone area code — SUPPORTING ONLY ───────────────────────
        phone = getattr(data, "phone", None) or ""
        if phone and _phone_ny_signal(phone):
            us_score = country_votes.get("United States", 0)
            supporting_weight = 0.15 if us_score > 0 else 0.05
            state_votes["NY"] = state_votes.get("NY", 0) + supporting_weight
            signals.append({
                "signal": "phone_ny_area_code_supporting",
                "value": phone,
                "weight": supporting_weight,
                "note": "Phone is supporting signal only — not definitive proof of NY jurisdiction",
            })

        # ── Aggregate ─────────────────────────────────────────────────────────
        if country_votes:
            best_country = max(country_votes, key=lambda k: country_votes[k])
            best_score = country_votes[best_country]
            total = sum(country_votes.values())
            result.country = best_country
            result.confidence = round(min(best_score / max(total, 1.0) + best_score * 0.3, 1.0), 3)

        if state_votes and result.country == "United States":
            best_state = max(state_votes, key=lambda k: state_votes[k])
            if state_votes[best_state] >= 0.20:
                result.state_abbr = best_state
                result.state = US_STATE_ABBR.get(best_state, best_state)
                result.confidence = round(min(result.confidence + 0.10, 1.0), 3)

        # ── City ──────────────────────────────────────────────────────────────
        result.city = getattr(data, "city", None) or None
        result.signals = signals

        logger.info(
            f"[JurisdictionDetector] country={result.country} state={result.state} "
            f"confidence={result.confidence} signals={len(signals)}"
        )
        return result


# Module-level singleton
_detector = JurisdictionDetector()


def detect_jurisdiction(company_data: Any) -> JurisdictionResult:
    """Convenience function — detect jurisdiction from a CompanyData instance."""
    return _detector.detect(company_data)

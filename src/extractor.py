"""
Extractor Module (Candidate-Based Multi-Source Engine)
Collects, scores, and ranks company identity candidates across JSON-LD, HTML tables,
div key-value grids, meta tags, and structured body text without 'First Value Wins' bias.
Preserves field-level source URLs, evidence snippets, and confidence scores.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

try:
    from .crawler import PageContent
    from .models import CompanyData, ExtractedCandidate, FieldEvidence
    from .validator import (
        validate_email, validate_phone, validate_registration_number,
        validate_vat_number, validate_uk_postcode, validate_address
    )
except ImportError:
    from crawler import PageContent
    from models import CompanyData, ExtractedCandidate, FieldEvidence
    from validator import (
        validate_email, validate_phone, validate_registration_number,
        validate_vat_number, validate_uk_postcode, validate_address
    )

logger = logging.getLogger(__name__)

# spaCy model is loaded lazily (only if it's actually needed as a fallback)
# and cached at module level so repeated pipeline runs don't reload it.
_SPACY_NLP = None
_SPACY_LOAD_FAILED = False

# Generic/tech words spaCy sometimes mislabels as ORG — filtered out of fallback guesses.
_SPACY_NOISE_WORDS = {
    "api", "cli", "html", "sdk", "faq", "url", "http", "https", "pdf",
    "json", "css", "seo", "cta", "ui", "ux", "faqs",
}


def _get_spacy_nlp():
    """Lazily load and cache the spaCy model. Returns None if unavailable."""
    global _SPACY_NLP, _SPACY_LOAD_FAILED
    if _SPACY_NLP is not None:
        return _SPACY_NLP
    if _SPACY_LOAD_FAILED:
        return None
    try:
        import spacy
        _SPACY_NLP = spacy.load("en_core_web_sm")
        logger.info("[spaCy] Fallback NER model loaded.")
    except Exception as e:
        logger.warning(f"[spaCy] Could not load model, fallback disabled: {e}")
        _SPACY_LOAD_FAILED = True
        return None
    return _SPACY_NLP

CORP_SUFFIXES = [
    "Inc.", "Inc", "LLC", "Ltd.", "Ltd", "Limited", "Corp.", "Corp", "Corporation",
    "GmbH", "AG", "S.A.", "S.p.A.", "S.L.", "Pty Ltd", "B.V.", "N.V.", "PLC", "plc", "SE",
    "Co., Ltd.", "Co., Ltd", "S.a.r.l.", "K.K.", "Oy", "AB", "ApS", "Holdings", "Pte. Ltd."
]

ADDRESS_STOP_PHRASES = [
    r"(?i)\brepresented\s+by\b", r"(?i)\bvertreten\s+durch\b", r"(?i)\bmanaging\s+director\b",
    r"(?i)\bgeschäftsführer\b", r"(?i)\bdirectors?\b", r"(?i)\bcompany\s+secretary\b",
    r"(?i)\bcontact\b", r"(?i)\bphone\b", r"(?i)\bemail\b", r"(?i)\btel\b", r"(?i)\bfax\b",
    r"(?i)\bsocial\s+media\b", r"(?i)\bfancy\s+social\b", r"(?i)\bget\s+us\s+on\b",
    r"(?i)\bfollow\s+us\b", r"(?i)\bconnect\s+with\s+us\b", r"(?i)\bopening\s+hours\b",
    r"(?i)\bopening\s+times\b", r"(?i)\bfacebook\b", r"(?i)\btwitter\b", r"(?i)\binstagram\b",
    r"(?i)\blinkedin\b", r"(?i)\byoutube\b", r"(?i)\b\bfb\b", r"(?i)\ball\s+rights\s+reserved\b",
    r"(?i)\bcookie\b", r"(?i)\bprivacy\s+policy\b", r"(?i)\bnewsletter\b", r"(?i)\bcareers\b",
    r"(?i)\bconfirm\s+your\s+age\b", r"(?i)\bsupport\b", r"(?i)\bfaqs\b", r"(?i)\bshop\s+now\b"
]


def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean extra whitespaces, HTML artifacts, and clean punctuation."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip(" ,;\t\r\n")
    return cleaned if cleaned else None


def clean_address_string(raw_addr: str) -> str:
    """Strip repeated tokens, trailing commas, and boundary noise from address."""
    # Split by comma or newline and filter out junk tokens
    tokens = [t.strip(" \t\r\n,;") for t in re.split(r"[,|\n]", raw_addr) if t.strip()]
    cleaned_tokens = []
    seen = set()

    for tok in tokens:
        tok_lower = tok.lower()
        if tok_lower in ["fb", "facebook", "twitter", "instagram", "linkedin", "social media", "fancy social media channels:"]:
            break
        if any(re.search(stop, tok, re.IGNORECASE) for stop in ADDRESS_STOP_PHRASES):
            break
        # Remove consecutive duplicate tokens
        if tok_lower not in seen or len(tok_lower) > 20:
            cleaned_tokens.append(tok)
            seen.add(tok_lower)

    addr = ", ".join(cleaned_tokens)
    # Strip leading label if present
    addr = re.sub(r"(?i)^(address|registered office|head office|location)\s*[:\-]?\s*", "", addr).strip(" ,;")
    return addr


class CompanyDataExtractor:
    """Multi-source candidate collection and evidence ranking engine."""

    def __init__(self, target_url: str):
        self.target_url = target_url
        parsed = urlparse(target_url)
        self.netloc = parsed.netloc.lower()
        self.domain = self.netloc.replace("www.", "")
        self.candidates: List[ExtractedCandidate] = []

    def add_candidate(self, field_name: str, value: Any, source_url: str, category: str, method: str, evidence: str, confidence: float):
        """Add a candidate for ranking."""
        if value is None:
            return
        if isinstance(value, str):
            value = clean_text(value)
            if not value:
                return

        cand = ExtractedCandidate(
            field_name=field_name,
            value=value,
            source_url=source_url,
            category=category,
            method=method,
            evidence=clean_text(evidence[:300]) if evidence else str(value)[:300],
            confidence=round(confidence, 2)
        )
        self.candidates.append(cand)

    def extract_from_json_ld(self, pages: List[PageContent]):
        """Collect candidates from Schema.org JSON-LD structured blocks."""
        for page in pages:
            category = getattr(page.parsed_structure, "category", "general")
            for item in page.json_ld:
                if not isinstance(item, dict):
                    continue

                obj_type = item.get("@type")
                types = [obj_type] if isinstance(obj_type, str) else (obj_type if isinstance(obj_type, list) else [])
                is_org = any(t in ["Organization", "Corporation", "LocalBusiness", "NGO", "Company", "AutomotiveBusiness", "Brewery"] for t in types)

                if is_org:
                    if item.get("name"):
                        self.add_candidate("company_name", item["name"], page.url, category, "json_ld_org_name", f"JSON-LD Organization.name: {item['name']}", 0.95)
                        self.add_candidate("brand_name", item["name"], page.url, category, "json_ld_org_name", f"JSON-LD Organization.name: {item['name']}", 0.90)
                    if item.get("legalName"):
                        self.add_candidate("legal_name", item["legalName"], page.url, category, "json_ld_legal_name", f"JSON-LD Organization.legalName: {item['legalName']}", 0.98)

                    if item.get("vatID"):
                        val = validate_vat_number(item["vatID"])
                        if val:
                            self.add_candidate("vat_tax_number", val, page.url, category, "json_ld_vat", f"JSON-LD vatID: {item['vatID']}", 0.98)
                    if item.get("taxID"):
                        val = validate_vat_number(item["taxID"])
                        if val:
                            self.add_candidate("vat_tax_number", val, page.url, category, "json_ld_tax", f"JSON-LD taxID: {item['taxID']}", 0.98)

                    if item.get("telephone"):
                        val = validate_phone(item["telephone"])
                        if val:
                            self.add_candidate("phone", val, page.url, category, "json_ld_phone", f"JSON-LD telephone: {item['telephone']}", 0.95)
                    if item.get("email"):
                        val = validate_email(item["email"])
                        if val:
                            self.add_candidate("email", val, page.url, category, "json_ld_email", f"JSON-LD email: {item['email']}", 0.95)

                    if item.get("description"):
                        desc = clean_text(item["description"])
                        if desc and len(desc) > 20:
                            self.add_candidate("business_description", desc[:500], page.url, category, "json_ld_desc", f"JSON-LD description: {desc[:100]}", 0.90)

                    addr = item.get("address")
                    if isinstance(addr, dict):
                        if addr.get("addressCountry"):
                            c = addr["addressCountry"]
                            c_name = c if isinstance(c, str) else c.get("name")
                            self.add_candidate("country", c_name, page.url, category, "json_ld_address", f"JSON-LD addressCountry: {c_name}", 0.95)
                        if addr.get("addressLocality"):
                            self.add_candidate("city", addr["addressLocality"], page.url, category, "json_ld_address", f"JSON-LD addressLocality: {addr['addressLocality']}", 0.95)
                        if addr.get("addressRegion"):
                            self.add_candidate("state_province", addr["addressRegion"], page.url, category, "json_ld_address", f"JSON-LD addressRegion: {addr['addressRegion']}", 0.95)
                        if addr.get("postalCode"):
                            pc = validate_uk_postcode(str(addr["postalCode"])) or str(addr["postalCode"])
                            self.add_candidate("postal_code", pc, page.url, category, "json_ld_address", f"JSON-LD postalCode: {addr['postalCode']}", 0.95)

                        street = addr.get("streetAddress")
                        if street:
                            parts = [street, addr.get("addressLocality"), addr.get("addressRegion"), addr.get("postalCode"), addr.get("addressCountry")]
                            full_addr = ", ".join([str(p) for p in parts if p])
                            valid_addr = validate_address(full_addr)
                            if valid_addr:
                                self.add_candidate("full_address", valid_addr, page.url, category, "json_ld_full_address", f"JSON-LD full address: {valid_addr}", 0.98)

                    if item.get("parentOrganization"):
                        p_org = item["parentOrganization"]
                        p_name = p_org.get("name") if isinstance(p_org, dict) else str(p_org)
                        self.add_candidate("parent_company", p_name, page.url, category, "json_ld_parent", f"JSON-LD parentOrganization: {p_name}", 0.95)

                    if item.get("subOrganization"):
                        subs = item["subOrganization"]
                        sub_list = subs if isinstance(subs, list) else [subs]
                        for sub in sub_list:
                            s_name = sub.get("name") if isinstance(sub, dict) else str(sub)
                            if s_name:
                                self.add_candidate("subsidiaries", s_name, page.url, category, "json_ld_subsidiary", f"JSON-LD subOrganization: {s_name}", 0.95)

    def extract_from_html_tables_and_divs(self, pages: List[PageContent]):
        """Collect candidates from HTML <table> elements and key-value divs."""
        kv_field_map = {
            "legal_name": ["legal name", "registered name", "official name", "company name", "firmenname", "raisonsociale"],
            "registration_number": ["company number", "registration number", "registration no", "company no", "handelsregister", "hrb", "hra", "siren", "siret", "kvk", "cvr", "cr no"],
            "vat_tax_number": ["vat number", "vat no", "vat registration", "tax id", "ust-idnr", "tva", "gstin", "ein"],
            "full_address": ["registered office", "registered address", "headquarters", "head office", "address", "firmensitz"],
            "phone": ["telephone", "phone", "tel", "contact number"],
            "email": ["email", "e-mail", "contact email"],
        }

        for page in pages:
            struct = page.parsed_structure
            if not struct:
                continue
            category = "legal" if "legal" in page.url or "imprint" in page.url or "impressum" in page.url or "company-information" in page.url else "general"

            all_kv_dicts = struct.tables_kv + struct.div_kv
            for kv_dict in all_kv_dicts:
                for label, val in kv_dict.items():
                    label_lower = label.lower().strip()
                    val_clean = clean_text(val)
                    if not val_clean:
                        continue

                    for field, aliases in kv_field_map.items():
                        if any(alias in label_lower for alias in aliases):
                            confidence = 0.95 if category == "legal" else 0.85
                            if field == "registration_number":
                                v_reg = validate_registration_number(val_clean)
                                if v_reg:
                                    self.add_candidate(field, v_reg, page.url, category, "html_table_kv", f"Table [{label}]: {val_clean}", confidence)
                            elif field == "vat_tax_number":
                                v_vat = validate_vat_number(val_clean)
                                if v_vat:
                                    self.add_candidate(field, v_vat, page.url, category, "html_table_kv", f"Table [{label}]: {val_clean}", confidence)
                            elif field == "email":
                                v_em = validate_email(val_clean, context=label_lower)
                                if v_em:
                                    self.add_candidate(field, v_em, page.url, category, "html_table_kv", f"Table [{label}]: {val_clean}", confidence)
                            elif field == "phone":
                                v_ph = validate_phone(val_clean)
                                if v_ph:
                                    self.add_candidate(field, v_ph, page.url, category, "html_table_kv", f"Table [{label}]: {val_clean}", confidence)
                            else:
                                self.add_candidate(field, val_clean, page.url, category, "html_table_kv", f"Table [{label}]: {val_clean}", confidence)

    def extract_from_text_and_meta(self, pages: List[PageContent]):
        """Collect candidates using regex on text, footers, meta tags, and headings."""
        reg_patterns = [
            r"(?:Company\s+(?:Registration|Reg)?\s*(?:No\.?|Number|#)|Registration\s+Number|Handelsregister(?:nummer)?|HRB|HRA|SIREN|SIRET|Chamber\s+of\s+Commerce\s+No|CR\s+No|Commercial\s+Register\s+No|Business\s+Registration\s+Number)[:\s]+([A-Z0-9\-\.\/]{4,20})",
            r"(?:Registered\s+in\s+[A-Za-z\s]+(?:under|with)?\s+(?:No\.?|number|#)?)[:\s]*([0-9A-Z\-]{5,15})",
            r"(?:Company\s+No\.?|Company\s+Number)[:\s]*([A-Z]{0,2}[0-9]{6,8})"
        ]

        vat_patterns = [
            r"(?:VAT\s*(?:Reg(?:istration)?\.?\s*)?(?:No\.?|Number|#)|USt-IdNr\.?|TVA|Tax\s+ID|GSTIN|EIN)[:\s]+([A-Z0-9\s\-\.]{6,25})",
            r"\b(GB\s?[0-9]{3}\s?[0-9]{4}\s?[0-9]{2}|GB\s?[0-9]{9}|DE\s?[0-9]{9}|FR\s?[0-9A-Z]{2}\s?[0-9]{9}|NL\s?[0-9]{9}B[0-9]{2}|CHE\-[0-9]{3}\.[0-9]{3}\.[0-9]{3})\b"
        ]

        for page in pages:
            category = "legal" if any(k in page.url.lower() for k in ["legal", "imprint", "impressum", "terms", "company-information"]) else "general"
            text_blocks = [page.text] + page.footer_texts
            combined = "\n".join(text_blocks)

            if page.og_site_name:
                self.add_candidate("company_name", page.og_site_name, page.url, category, "meta_og_site_name", f"og:site_name: {page.og_site_name}", 0.75)
                self.add_candidate("brand_name", page.og_site_name, page.url, category, "meta_og_site_name", f"og:site_name: {page.og_site_name}", 0.75)

            if page.title:
                t_parts = page.title.split("|")[0].split("-")[0].strip()
                t_clean = re.sub(r"(?i)^(welcome\s+to|home\s+page\s+of|official\s+site\s+of|welcome)\s*", "", t_parts).strip()
                if t_clean and len(t_clean) < 60:
                    self.add_candidate("company_name", t_clean, page.url, category, "page_title", f"Title: {page.title}", 0.65)

            if page.meta_description and len(page.meta_description) >= 30:
                self.add_candidate("business_description", page.meta_description, page.url, category, "meta_description", f"meta description: {page.meta_description[:100]}", 0.75)

            # Registration Number
            for pat in reg_patterns:
                m = re.search(pat, combined, re.IGNORECASE)
                if m:
                    val = validate_registration_number(m.group(1))
                    if val:
                        conf = 0.95 if category == "legal" else 0.85
                        self.add_candidate("registration_number", val, page.url, category, "regex_labeled", f"Matched text: {m.group(0)[:150]}", conf)

            # VAT Number
            for pat in vat_patterns:
                m = re.search(pat, combined, re.IGNORECASE)
                if m:
                    val = validate_vat_number(m.group(1))
                    if val:
                        conf = 0.95 if category == "legal" else 0.85
                        self.add_candidate("vat_tax_number", val, page.url, category, "regex_labeled", f"Matched text: {m.group(0)[:150]}", conf)

            # Legal Entity & Corporate Name Patterns
            legal_patterns = [
                # Pattern A: owned and produced by / operated by / administrators of / registered as
                r"(?:owned\s+(?:and\s+produced\s+)?by|operated\s+by|administrators\s+of|registered\s+name\s*[:\-]?|trading\s+as|licensed\s+(?:in\s+the\s+uk\s+)?by)\s+([A-Z0-9][A-Za-z0-9\s,\.\-&]{2,40}?\b(?:" + "|".join([re.escape(s) for s in CORP_SUFFIXES]) + r")\b)",
                # Pattern B: Explicit Company Information heading like 'BrewDog Company Information\nBrewDog PLC.'
                r"(?:Company\s+Information|About\s+Us)\s*\n+([A-Z0-9][A-Za-z0-9\s,\.\-&]{2,40}?\b(?:" + "|".join([re.escape(s) for s in CORP_SUFFIXES]) + r")\b)",
                # Pattern C: Standard Copyright with or without multiple years
                r"(?:©|Copyright|\(c\)|&copy;)\s*(?:Copyright\s*)?(?:[0-9]{4}(?:\s*[-–]\s*[0-9]{4})?)?[,\s;]+(?:all\s+rights\s+reserved[,\s;]+)?(?:by\s+)?([A-Z0-9][A-Za-z0-9\s,\.\-&]{2,40}?\b(?:" + "|".join([re.escape(s) for s in CORP_SUFFIXES]) + r")\b)",
                # Pattern D: Generic corporate suffix pattern in legal texts
                r"\b([A-Z][A-Za-z0-9\s&]{2,35}\b(?:" + "|".join([re.escape(s) for s in CORP_SUFFIXES]) + r")\b)",
            ]

            for pat in legal_patterns:
                for m in re.finditer(pat, combined, re.IGNORECASE if "owned" in pat or "administrators" in pat else 0):
                    c_legal = clean_text(m.group(1))
                    if c_legal and len(c_legal) < 60:
                        conf = 0.95 if category == "legal" else 0.85
                        self.add_candidate("legal_name", c_legal, page.url, category, "legal_text_regex", f"Matched text: {m.group(0)[:150]}", conf)

                        # ── Also scan the rest of the copyright/ownership line for an address ──
                        # e.g. "© 1997-2026 Barnes & Noble Booksellers, Inc. 33 East 17th Street, New York, NY 10003"
                        line_end = combined.find("\n", m.end())
                        line_end = line_end if line_end > 0 else m.end() + 200
                        remainder = combined[m.end(): line_end].strip()
                        if remainder and len(remainder) > 8:
                            self._extract_us_address_signals(remainder, page.url, category)
                            uk_pc2 = validate_uk_postcode(remainder)
                            if uk_pc2:
                                self.add_candidate("postal_code", uk_pc2, page.url, category, "copyright_line_postcode", f"Postcode in copyright line: {uk_pc2}", 0.90)
                                self.add_candidate("country", "United Kingdom", page.url, category, "copyright_line_postcode", "Derived from UK postcode in copyright line", 0.90)
                            if validate_address(remainder):
                                self.add_candidate("full_address", validate_address(remainder), page.url, category, "copyright_line_address", f"Address in copyright line: {remainder[:100]}", 0.85)
                        break

            # 1. First priority: Mailto links from page structure & deobfuscated emails
            if hasattr(page, "parsed_structure") and page.parsed_structure:
                for em in page.parsed_structure.mailto_emails:
                    v_em = validate_email(em)
                    if v_em:
                        self.add_candidate("email", v_em, page.url, category, "mailto_link", f"Mailto/Deobfuscated link: {v_em}", 0.95)

            # 2. Second priority: Explicit labeled Email: in text
            labeled_emails = re.findall(r"(?:Email|E-mail|Contact\s+Email|Mail)[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", combined, re.IGNORECASE)
            for em in labeled_emails:
                v_em = validate_email(em)
                if v_em:
                    self.add_candidate("email", v_em, page.url, category, "labeled_email", f"Labeled email: {v_em}", 0.90)

            # 3. Third priority: Generic regex email search with strict context validation
            emails = re.finditer(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", page.text)
            for m in emails:
                em_str = m.group(0)
                # Check surrounding context (50 chars before and after)
                start_ctx = max(0, m.start() - 60)
                end_ctx = min(len(page.text), m.end() + 60)
                ctx = page.text[start_ctx:end_ctx]
                v_em = validate_email(em_str, context=ctx)
                if v_em:
                    self.add_candidate("email", v_em, page.url, category, "regex_email", f"Email found in text: {v_em}", 0.70)

            # Phones
            phones = re.findall(r"(?:Tel(?:ephone)?|Phone|Call|Contact|Office)[:\s]+(\+?[0-9\s\-\(\)\.]{8,20})", page.text, re.IGNORECASE)
            for ph in phones:
                v_ph = validate_phone(ph)
                if v_ph:
                    self.add_candidate("phone", v_ph, page.url, category, "regex_phone", f"Phone found: {v_ph}", 0.80)

    # ── US address helpers ────────────────────────────────────────────────────

    # US state abbreviation → full name (used by address parser)
    _US_STATE_ABBR: Dict[str, str] = {
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

    # Matches "City, ST 12345" or "City, ST 12345-6789"
    _US_CITY_STATE_ZIP_RE = re.compile(
        r"\b([A-Za-z][A-Za-z\s]{1,25}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b"
    )
    # Bare US ZIP: standalone 5-digit or ZIP+4
    _US_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

    def _extract_us_address_signals(
        self,
        addr: str,
        source_url: str,
        category: str,
    ):
        """
        Scan an address string for US ZIP, state, city, and country signals
        and emit candidates.  Called after every address block is assembled.
        """
        # Try "City, ST ZIP" pattern first (most reliable)
        m = self._US_CITY_STATE_ZIP_RE.search(addr)
        if m:
            city_raw   = m.group(1).strip()
            state_abbr = m.group(2).upper()
            zip_code   = m.group(3)

            if state_abbr in self._US_STATE_ABBR:
                state_name = self._US_STATE_ABBR[state_abbr]
                self.add_candidate("country",        "United States", source_url, category, "us_address_parser", f"US address pattern: {m.group(0)}", 0.90)
                self.add_candidate("state_province",  state_name,     source_url, category, "us_address_parser", f"US state: {state_abbr}", 0.90)
                self.add_candidate("city",            city_raw,       source_url, category, "us_address_parser", f"US city: {city_raw}", 0.88)
                self.add_candidate("postal_code",     zip_code,       source_url, category, "us_address_parser", f"US ZIP: {zip_code}", 0.90)
                return  # full match — no need to fall through

        # Fallback: bare ZIP code in address (weaker signal)
        zip_m = self._US_ZIP_RE.search(addr)
        if zip_m:
            zip_code = zip_m.group(1)
            # Require at least one letter word nearby to reduce false positives
            surrounding = addr[max(0, zip_m.start()-30):zip_m.end()+10]
            if re.search(r"[A-Za-z]{3}", surrounding):
                self.add_candidate("postal_code", zip_code, source_url, category, "us_zip_regex", f"US ZIP in address: {zip_code}", 0.70)
                # Tentative US country signal — low weight, jurisdiction_detector will corroborate
                self.add_candidate("country", "United States", source_url, category, "us_zip_regex", f"5-digit ZIP suggests US: {zip_code}", 0.55)

    def extract_address_with_truncation(self, pages: List[PageContent]):
        """Extract address candidates ensuring clean truncation before boundary markers."""
        label_re = re.compile(
            r"(?i)(?:head\s*office|headquarters|hq|corporate\s+office|registered\s+office\s+(?:in\s+[a-z]+)?\s*at|registered\s+office|"
            r"registered\s+address|principal\s+office|main\s+office|our\s+address|"
            r"office\s+address|company\s+address|visiting\s+address|postal\s+address|"
            r"physical\s+address|mailing\s+address|business\s+address|"
            r"corporate\s+address|address)\s*[:\-]?\s*"
        )

        for page in pages:
            category = "legal" if any(k in page.url for k in ["imprint", "legal", "contact", "company-information", "about", "help"]) else "general"
            text_blobs = [page.text or ""] + list(page.footer_texts or [])

            for blob in text_blobs:
                for m in label_re.finditer(blob):
                    after = blob[m.end(): m.end() + 350]
                    lines = []
                    for raw in after.split("\n"):
                        line = raw.strip(" \t\r|,;")
                        if not line:
                            if lines:
                                break
                            continue

                        stopped = False
                        for stop_pat in ADDRESS_STOP_PHRASES:
                            stop_match = re.search(stop_pat, line)
                            if stop_match:
                                line = line[:stop_match.start()].strip(" \t\r|,;")
                                stopped = True
                                break

                        if line:
                            lines.append(line)

                        if stopped or len(line) > 120 or len(lines) >= 6:
                            break

                    if lines:
                        cand_addr = clean_text(", ".join(lines))
                        cand_addr = clean_address_string(cand_addr)

                        # ── UK postcode ───────────────────────────────────────
                        uk_pc = validate_uk_postcode(cand_addr)
                        if uk_pc:
                            pc_match = re.search(r"\b" + re.escape(uk_pc) + r"\b", cand_addr, re.IGNORECASE)
                            if pc_match:
                                cand_addr = cand_addr[:pc_match.end()].strip(" ,;")
                            self.add_candidate("postal_code", uk_pc, page.url, category, "address_parser", f"Postcode in address: {uk_pc}", 0.95)
                            self.add_candidate("country", "United Kingdom", page.url, category, "address_parser", "Derived from UK Postcode", 0.95)

                        # ── US address signals ────────────────────────────────
                        self._extract_us_address_signals(cand_addr, page.url, category)

                        # ── City fallback (UK-style: Street, City, County, PC) ─
                        addr_parts = [p.strip() for p in cand_addr.split(",") if p.strip()]
                        if len(addr_parts) >= 3 and not self._US_CITY_STATE_ZIP_RE.search(cand_addr):
                            possible_city = addr_parts[-3] if len(addr_parts) >= 4 else addr_parts[-2]
                            possible_city = re.sub(r"[0-9A-Z]{2,4}\s*[0-9A-Z]{3}", "", possible_city).strip()
                            if possible_city and len(possible_city) < 30 and not any(c.isdigit() for c in possible_city):
                                self.add_candidate("city", possible_city, page.url, category, "address_parser", f"City in address: {possible_city}", 0.85)

                        valid_addr = validate_address(cand_addr)
                        if valid_addr:
                            conf = 0.95 if category == "legal" or "contact" in page.url else 0.80
                            self.add_candidate("full_address", valid_addr, page.url, category, "address_block_parser", f"Address block: {valid_addr}", conf)

    def select_best_candidates(self, data: CompanyData):
        """Rank collected candidates per field and populate CompanyData with evidence."""
        field_groups: Dict[str, List[ExtractedCandidate]] = {}
        for cand in self.candidates:
            field_groups.setdefault(cand.field_name, []).append(cand)

        source_urls_set: Set[str] = set()

        for field_name, cand_list in field_groups.items():
            cand_list.sort(key=lambda c: c.confidence, reverse=True)

            if field_name == "subsidiaries":
                sub_set = []
                for c in cand_list:
                    if isinstance(c.value, str) and c.value not in sub_set:
                        sub_set.append(c.value)
                        source_urls_set.add(c.source_url)
                data.subsidiaries = sub_set
                if cand_list:
                    best = cand_list[0]
                    data.field_evidence[field_name] = FieldEvidence(
                        value=sub_set,
                        source_url=best.source_url,
                        category=best.category,
                        method=best.method,
                        evidence=best.evidence,
                        confidence=best.confidence
                    )
            else:
                best = cand_list[0]
                setattr(data, field_name, best.value)
                source_urls_set.add(best.source_url)

                data.field_evidence[field_name] = FieldEvidence(
                    value=best.value,
                    source_url=best.source_url,
                    category=best.category,
                    method=best.method,
                    evidence=best.evidence,
                    confidence=best.confidence
                )

        if not data.company_name:
            if data.legal_name:
                name = data.legal_name
                for s in CORP_SUFFIXES:
                    name = re.sub(r",?\s+" + re.escape(s) + r"$", "", name, flags=re.IGNORECASE).strip()
                data.company_name = name
                data.field_evidence["company_name"] = FieldEvidence(
                    value=name,
                    source_url=data.field_evidence["legal_name"].source_url if "legal_name" in data.field_evidence else self.target_url,
                    category="derived",
                    method="legal_name_suffix_strip",
                    evidence=f"Derived from legal_name: {data.legal_name}",
                    confidence=0.70
                )
            else:
                data.company_name = self.domain
                data.field_evidence["company_name"] = FieldEvidence(
                    value=self.domain,
                    source_url=self.target_url,
                    category="fallback",
                    method="domain_fallback",
                    evidence=f"Domain fallback: {self.domain}",
                    confidence=0.30
                )

        # Build Website Address Structure
        if data.full_address or data.postal_code or data.city or data.country:
            data.website_address = {
                "full_address": data.full_address,
                "city": data.city,
                "state_province": data.state_province,
                "postal_code": data.postal_code,
                "country": data.country
            }

        # Calculate Data Completeness score (0.0 - 1.0)
        important_fields = ["company_name", "legal_name", "registration_number", "vat_tax_number", "full_address", "postal_code", "email", "phone"]
        filled = sum(1 for f in important_fields if getattr(data, f, None))
        data.data_completeness = round(filled / len(important_fields), 2)

        data.source_pages = sorted(list(source_urls_set))

    def extract_from_spacy_fallback(self, pages: List[PageContent]):
        """
        Last-resort candidate source: spaCy NER-based company name guessing.

        Only ever called when JSON-LD, HTML tables/divs, and text/meta
        extraction (all higher-confidence, rule-based methods) found NO
        company_name candidate at all — see extract_all(). Confidence is
        deliberately low (0.30) so a real signal from any other method
        always wins in select_best_candidates().
        """
        nlp = _get_spacy_nlp()
        if nlp is None:
            return

        for page in pages:
            text = getattr(page, "text", None)
            if not text or not text.strip():
                continue

            category = getattr(page.parsed_structure, "category", "general")
            doc = nlp(text[:20000])  # cap length — spaCy slows down on very long text

            orgs = []
            seen = set()
            for ent in doc.ents:
                if ent.label_ != "ORG":
                    continue
                name = ent.text.strip()
                key = name.lower()
                if len(name) <= 2 or key in _SPACY_NOISE_WORDS or key in seen:
                    continue
                seen.add(key)
                orgs.append(name)

            for org in orgs[:3]:  # only the top few guesses per page — keeps noise down
                self.add_candidate(
                    "company_name", org, page.url, category,
                    "spacy_ner_fallback", f"spaCy ORG entity (fallback guess): {org}",
                    confidence=0.30
                )

    def extract_all(self, crawled_pages: List[PageContent]) -> CompanyData:
        """Run multi-source candidate collection, ranking, and validation."""
        print(f"\n[4/5] Extracting company identity candidates from {len(crawled_pages)} pages...")
        data = CompanyData()
        data.domain = self.domain
        data.website = self.target_url

        self.extract_from_json_ld(crawled_pages)
        self.extract_from_html_tables_and_divs(crawled_pages)
        self.extract_from_text_and_meta(crawled_pages)
        self.extract_address_with_truncation(crawled_pages)

        # spaCy fallback: only run if no rule-based method found a company name at all.
        has_company_name = any(c.field_name == "company_name" for c in self.candidates)
        if not has_company_name:
            self.extract_from_spacy_fallback(crawled_pages)

        self.select_best_candidates(data)

        print(f"      Selected best field candidates backed by {len(data.source_pages)} source pages.")
        return data

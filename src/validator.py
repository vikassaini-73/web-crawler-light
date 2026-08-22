"""
Validation Module
Validates emails, phones, registration numbers, VAT/tax numbers, postal codes, and identity matches.
"""

import re
import logging
from typing import Optional
import tldextract

logger = logging.getLogger(__name__)

# Standard UK Postcode regex pattern (handles standard formats e.g. AB41 8BX, SW1A 1AA, G2 1BP, M1 1AA)
UK_POSTCODE_REGEX = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2})\b",
    re.IGNORECASE
)

# Reject obvious placeholder/example emails and test addresses
EXCLUDED_EMAIL_PATTERNS = [
    r"^test@test\.com$",
    r"^example@example\.com$",
    r"^name@example\.com$",
    r"^your@email\.com$",
    r"^you@example\.com$",
    r"^john@domain\.com$",
    r"^email@domain\.com$",
    r"^user@domain\.com$",
    r"^admin@domain\.com$",
    r"^user@host\.com$",
    r"^someone@example\.com$",
    r"^placeholder@",
    r"^no-?email@",
    r"^missing@",
    r"^sample@",
    r"^demo@",
    r"@example\.com$",
    r"@example\.org$",
    r"@example\.net$",
    r"@domain\.com$",
    r"@yourcompany\.com$",
    r"@email\.com$",
    r"@sentry\.io$",
    r"@schema\.org$",
    r"@wixpress\.com$",
    r"@wordpress\.org$",
    r"\.png$", r"\.jpg$", r"\.webp$", r"\.svg$",
    r"^noreply@", r"^no-reply@", r"^donotreply@",
]


def validate_email(email: Optional[str], context: Optional[str] = None) -> Optional[str]:
    """
    Validate email format, reject placeholder/example emails,
    and filter out emails originating from form validation or instructional examples.
    """
    if not email:
        return None
    cleaned = email.strip().lower()
    
    # Must match basic email regex
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", cleaned):
        return None
        
    for pat in EXCLUDED_EMAIL_PATTERNS:
        if re.search(pat, cleaned, re.IGNORECASE):
            return None

    # Contextual validation check: if context explicitly flags it as an example/placeholder
    if context:
        context_lower = context.lower()
        example_cues = [
            "for example", "e.g.", "such as", "sample email", "enter a valid email",
            "invalid email", "placeholder", "your email address", "email@example",
            "name@company.com", "user@company.com"
        ]
        for cue in example_cues:
            if cue in context_lower and cleaned in context_lower:
                return None

    return cleaned


def validate_phone(phone: Optional[str]) -> Optional[str]:
    """Clean and validate phone number format."""
    if not phone:
        return None
    cleaned = re.sub(r"\s+", " ", phone.strip())
    digits = re.sub(r"\D", "", cleaned)
    if not (7 <= len(digits) <= 15):
        return None
    if len(cleaned) < 7:
        return None
    return cleaned


def validate_registration_number(reg_no: Optional[str]) -> Optional[str]:
    """Validate registration / company number format."""
    if not reg_no:
        return None
    cleaned = reg_no.strip(" :.-,#/\t\r\n")
    # Capture leading alphanumeric sequence
    m = re.match(r"^([A-Z]{0,2}[0-9]{5,8}|[A-Z0-9\-]{4,15})", cleaned, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).upper()
    digits = re.sub(r"\D", "", val)
    if not digits:
        return None
    return val


def validate_vat_number(vat_no: Optional[str]) -> Optional[str]:
    """
    Validate VAT / Tax identifier format and normalize (e.g. '897 6381 54' -> '897 6381 54' or 'GB 897 6381 54').
    """
    if not vat_no:
        return None
    cleaned = vat_no.strip(" :.-,#/\t\r\n")
    
    # Match standard VAT format: e.g. GB 897 6381 54 or 897 6381 54 or DE123456789
    m = re.match(r"^([A-Z]{0,2}\s*[0-9\s\.\-]{6,16})", cleaned, re.IGNORECASE)
    if not m:
        return None
        
    raw_val = m.group(1).strip()
    digits = re.sub(r"\D", "", raw_val)
    if len(digits) < 6 or len(digits) > 15:
        return None
    
    normalized = re.sub(r"\s+", " ", raw_val).upper().strip(" ,.-")
    return normalized


def validate_uk_postcode(postcode: Optional[str]) -> Optional[str]:
    """Validate and format a UK postcode (e.g. 'AB41 8BX', 'G2 1BP')."""
    if not postcode:
        return None
    cleaned = re.sub(r"\s+", " ", postcode.strip()).upper()
    match = UK_POSTCODE_REGEX.search(cleaned)
    if match:
        raw_pc = match.group(1).replace(" ", "")
        if len(raw_pc) >= 5:
            formatted = f"{raw_pc[:-3]} {raw_pc[-3:]}"
            return formatted
        return raw_pc
    return None


def validate_address(address: Optional[str]) -> Optional[str]:
    """Validate address cleanliness, length, street markers, and postal code presence."""
    if not address:
        return None
    cleaned = re.sub(r"\s+", " ", address.strip(" ,;-"))
    if len(cleaned) < 10 or len(cleaned) > 300:
        return None

    # Must contain a digit (street number/postcode) OR a street marker word OR a postcode pattern
    has_digit = any(c.isdigit() for c in cleaned)
    has_street_word = bool(re.search(
        r"(?i)\b(street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|way|place|plaza|square|court|ct\.?|house|building|bldg\.?|tower|center|centre|park|estate|industrial\s+estate|commercial\s+park|floor|fl\.?|suite|ste\.?|unit|box|po box|p\.o\. box|str\.?|straße|strasse|rue|via|calle|piazza|platz)\b",
        cleaned
    ))
    has_postcode = bool(re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|\d{5}(?:-\d{4})?|[A-Z]\d[A-Z]\s*\d[A-Z]\d|\d{4,5})\b", cleaned))

    if not (has_digit or has_street_word or has_postcode):
        return None

    if re.search(r"(?i)\b(we are|our mission|all rights reserved|click here|cookies|privacy policy|applicant notice|terms of service)\b", cleaned):
        return None
    return cleaned


def is_same_base_domain(domain_a: str, domain_b: str) -> bool:
    """Check if two domain strings share the same registered base domain (e.g. brewdog.com and jobs.brewdog.com)."""
    if not domain_a or not domain_b:
        return False
        
    try:
        clean_a = re.sub(r"^https?://", "", domain_a.lower()).split("/")[0]
        clean_b = re.sub(r"^https?://", "", domain_b.lower()).split("/")[0]
        ext_a = tldextract.extract(clean_a)
        ext_b = tldextract.extract(clean_b)
        
        base_a = f"{ext_a.domain}.{ext_a.suffix}" if ext_a.domain and ext_a.suffix else clean_a
        base_b = f"{ext_b.domain}.{ext_b.suffix}" if ext_b.domain and ext_b.suffix else clean_b
        return base_a == base_b and base_a != ""
    except Exception:
        return False

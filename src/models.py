"""
Data models for company domain crawler with field-level evidence, candidate scoring,
and separated website vs official registry identity models.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FieldEvidence(BaseModel):
    """Metadata tracking field candidate source, evidence snippet, method and confidence."""

    value: Any = None
    source_url: Optional[str] = None
    category: Optional[str] = None
    method: str = "unknown"
    evidence: Optional[str] = None
    confidence: float = 0.0
    conflict: bool = False


class ExtractedCandidate(BaseModel):
    """Intermediate candidate extracted for a specific field."""

    field_name: str
    value: Any
    source_url: str
    category: str
    method: str
    evidence: str
    confidence: float


class CompanyData(BaseModel):
    """Extracted company identity schema with distinct website vs registry data."""

    domain: Optional[str] = None
    company_name: Optional[str] = None
    brand_name: Optional[str] = None
    legal_name: Optional[str] = None
    registration_number: Optional[str] = None
    vat_tax_number: Optional[str] = None
    country: Optional[str] = None
    state_province: Optional[str] = None
    city: Optional[str] = None
    full_address: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    business_description: Optional[str] = None
    business_activities: Optional[str] = None
    founded: Optional[str] = None
    parent_company: Optional[str] = None
    subsidiaries: List[str] = Field(default_factory=list)
    directors: List[str] = Field(default_factory=list)
    management: List[str] = Field(default_factory=list)
    source_pages: List[str] = Field(default_factory=list)

    # Separated Website Address vs Official Registered Address
    website_address: Dict[str, Any] = Field(default_factory=dict)
    official_registered_address: Dict[str, Any] = Field(default_factory=dict)

    # Distinct Identity Verification & Confidence Model
    identity_match: bool = False
    identity_match_method: Optional[str] = None
    identity_confidence: float = 0.0
    data_completeness: float = 0.0

    # UK Companies House Specific Fields
    company_status: Optional[str] = None
    company_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    sic_codes: List[str] = Field(default_factory=list)
    persons_with_significant_control: List[Dict[str, Any]] = Field(default_factory=list)
    filing_history: List[Dict[str, Any]] = Field(default_factory=list)
    charges: List[Dict[str, Any]] = Field(default_factory=list)
    insolvency: Optional[Dict[str, Any]] = None
    companies_house_status: Optional[str] = None
    companies_house_resolution: Optional[Dict[str, Any]] = None

    # Normalized official company summary
    official_registry_profile: Optional[Dict[str, Any]] = None

    # ── Jurisdiction detection ────────────────────────────────────────────────
    # Populated after Phase 4 (extraction) by JurisdictionDetector.
    # Contains: country, state, state_abbr, city, confidence, signals list.
    jurisdiction_detection: Optional[Dict[str, Any]] = None

    # ── Generic registry result ───────────────────────────────────────────────
    # Populated by RegistryResolver (Phase 5).
    # Contains: registry_type, registry_status, matched, confidence,
    #           company_number (CH number OR NY dos_id), official_profile, etc.
    registry_data: Optional[Dict[str, Any]] = None

    # ── New York DOS specific fields ──────────────────────────────────────────
    # Only populated when registry_type = "ny_dos" and matched = True.
    ny_dos_id: Optional[str] = None
    ny_entity_type: Optional[str] = None
    ny_county: Optional[str] = None
    ny_jurisdiction: Optional[str] = None
    ny_filing_date: Optional[str] = None
    ny_filing_history: List[Dict[str, Any]] = Field(default_factory=list)
    ny_stock_info: List[Dict[str, Any]] = Field(default_factory=list)
    ny_entity_info: List[Dict[str, Any]] = Field(default_factory=list)

    # ── Final Normalized View (Requested) ─────────────────────────────────────
    company: Dict[str, Any] = Field(default_factory=dict)
    registry: Dict[str, Any] = Field(default_factory=dict)
    registered_address: Dict[str, Any] = Field(default_factory=dict)
    full_registered_address: Optional[str] = None
    address_records: List[Dict[str, Any]] = Field(default_factory=list)
    stock_info: List[Dict[str, Any]] = Field(default_factory=list)
    entity_info: List[Dict[str, Any]] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    sources: Dict[str, Any] = Field(default_factory=dict)

    # Detailed field evidence tracking & conflict markers
    field_evidence: Dict[str, FieldEvidence] = Field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    crawled_pages_summary: List[Dict[str, Any]] = Field(default_factory=list)


class DiscoveredURL(BaseModel):
    """Metadata for a discovered URL."""

    url: str
    source: str
    relevance_score: float = 0.0
    category: Optional[str] = None
    depth: int = 0
    anchor_text: Optional[str] = None
    title: Optional[str] = None


class DiscoveryResult(BaseModel):
    """Summary result of URL discovery."""

    domain: str
    total_discovered: int
    sitemap_found: bool
    sitemap_urls_count: int
    homepage_links_count: int
    bfs_discovered_count: int
    urls: List[str] = Field(default_factory=list)

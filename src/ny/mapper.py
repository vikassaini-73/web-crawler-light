"""
New York DOS Registry Mapper

Normalises raw NY DOS API records into the project's existing
registry_data schema (compatible with CompanyData.registry_data).

VERIFIED LIVE API FIELD REFERENCE (confirmed 2026-08-21):

n9v6-gdp6 (Active Corps):
  dos_id, current_entity_name, initial_dos_filing_date,
  county, jurisdiction, entity_type,
  dos_process_name, dos_process_address_1,
  dos_process_city, dos_process_state, dos_process_zip

63wc-4exh (Filings):
  corpid_num, film_num, date_filed, approved_date, eff_date,
  dura_date, dis_eff_date, mod_certcode, entitytype, documenttype,
  law, nfp_type, corp_name, cnty_prin_ofc, juris, amd_corp_name_flag

kiwr-v7e8 (Stock):
  corpid_num, film_num, date_filed,
  stock_num_shrs, stock_type, stock_val_shr

3gg2-jgnp (Entity):
  corpid_num, film_num, date_filed, mod_cert_code, status   ← status was missing

2tms-hftb (Addresses):
  corpid_num, film_num, date_filed, addr_type,
  name, addr1, city, state, zip5, country                   ← full address was missing
"""

from typing import Any, Dict, List, Optional


class NewYorkMapper:
    """Maps NY DOS raw API data to the project's normalized registry_data schema."""

    # ── Profile ───────────────────────────────────────────────────────────────

    @staticmethod
    def map_profile(record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map a single Active Corporations (n9v6-gdp6) record.

        Live API also returns these extra fields not in $select by default:
          chairman_name, chairman_address_1, chairman_city, chairman_state, chairman_zip
          registered_agent_name, registered_agent_state
          dos_process_address_2
        These are now captured when present.
        """
        addr_parts = [
            record.get("dos_process_address_1"),
            record.get("dos_process_address_2"),
            record.get("dos_process_city"),
            record.get("dos_process_state"),
            record.get("dos_process_zip"),
        ]
        full_address = ", ".join(p for p in addr_parts if p)

        # Chairman / principal officer info
        chairman = None
        if record.get("chairman_name"):
            chairman = {
                "name":    record.get("chairman_name"),
                "address": record.get("chairman_address_1"),
                "city":    record.get("chairman_city"),
                "state":   record.get("chairman_state"),
                "zip":     record.get("chairman_zip"),
            }

        return {
            "dos_id":               record.get("dos_id"),
            "company_name":         record.get("current_entity_name"),
            "entity_type":          record.get("entity_type"),
            "jurisdiction":         record.get("jurisdiction"),
            "county":               record.get("county"),
            "filing_date":          record.get("initial_dos_filing_date"),
            "dos_process_name":     record.get("dos_process_name"),
            "registered_agent_name":record.get("registered_agent_name"),
            "registered_agent_state":record.get("registered_agent_state"),
            "chairman":             chairman,
            "registered_address": {
                "address_line_1": record.get("dos_process_address_1"),
                "address_line_2": record.get("dos_process_address_2"),
                "city":           record.get("dos_process_city"),
                "state":          record.get("dos_process_state") or "NY",
                "zip":            record.get("dos_process_zip"),
                "country":        "United States",
            },
            "full_registered_address": full_address,
        }

    # ── Filings ───────────────────────────────────────────────────────────────

    @staticmethod
    def map_filings(filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Map All Filings (63wc-4exh) records.
        Includes all verified fields: corp_name, law, juris, dura_date, etc.
        """
        result = []
        for f in filings[:20]:
            result.append({
                "corpid_num":     f.get("corpid_num"),
                "film_num":       f.get("film_num"),
                "corp_name":      f.get("corp_name"),
                "date_filed":     f.get("date_filed"),
                "approved_date":  f.get("approved_date"),
                "effective_date": f.get("eff_date"),
                "expiry_date":    f.get("dura_date"),
                "dissolved_date": f.get("dis_eff_date"),
                "document_type":  f.get("documenttype"),
                "entity_type":    f.get("entitytype"),
                "cert_code":      f.get("mod_certcode"),
                "law":            f.get("law"),
                "county":         f.get("cnty_prin_ofc"),
                "jurisdiction":   f.get("juris"),
                "name_amended":   f.get("amd_corp_name_flag") == "X",
            })
        return result

    # ── Stock ─────────────────────────────────────────────────────────────────

    @staticmethod
    def map_stock(stock_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map Stock (kiwr-v7e8) records."""
        result = []
        for s in stock_records:
            result.append({
                "corpid_num":      s.get("corpid_num"),
                "film_num":        s.get("film_num"),
                "date_filed":      s.get("date_filed"),
                "stock_type":      s.get("stock_type"),
                "shares":          s.get("stock_num_shrs"),
                "value_per_share": s.get("stock_val_shr"),
            })
        return result

    # ── Entity ────────────────────────────────────────────────────────────────

    @staticmethod
    def map_entity(entity_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Map Entity (3gg2-jgnp) records.
        Now includes verified 'status' field which was missing before.
        """
        result = []
        for e in entity_records:
            result.append({
                "corpid_num": e.get("corpid_num"),
                "film_num":   e.get("film_num"),
                "date_filed": e.get("date_filed"),
                "cert_code":  e.get("mod_cert_code"),
                "status":     e.get("status"),       # e.g. "Active" — was missing
            })
        return result

    # ── Addresses ─────────────────────────────────────────────────────────────

    @staticmethod
    def map_addresses(address_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Map Addresses (2tms-hftb) records.
        Now includes full address fields: name, addr1, city, state, zip5, country.
        Previously only film_num, date_filed, addr_type were mapped.
        """
        result = []
        for a in address_records:
            addr_type = a.get("addr_type", "")
            # addr_type "1" = registered agent address
            result.append({
                "corpid_num": a.get("corpid_num"),
                "film_num":   a.get("film_num"),
                "date_filed": a.get("date_filed"),
                "addr_type":  addr_type,
                "addr_type_label": (
                    "Registered Agent" if addr_type == "1"
                    else f"Type {addr_type}" if addr_type
                    else None
                ),
                "name":    a.get("name"),
                "addr1":   a.get("addr1"),
                "city":    a.get("city"),
                "state":   a.get("state"),
                "zip":     a.get("zip5"),
                "country": a.get("country"),
            })
        return result

    # ── Best registered address from address enrichment ───────────────────────

    @staticmethod
    def best_registered_address(
        address_records: List[Dict[str, Any]],
        fallback: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Pick the most useful address from 2tms-hftb records.

        addr_type meanings (verified from live API):
          "1" = Registered agent / process service address (often a law firm / agent)
          "2" = Former/revoked registered agent
          "3" = Principal office / chairman address = ACTUAL company HQ address

        Priority: addr_type="3" (principal office) → addr_type="1" (agent) → fallback
        We prefer "3" because it represents the company's own address,
        not the registered agent's office address.
        """
        if not address_records:
            return fallback

        # Only consider records that have actual address data
        useful = [
            r for r in address_records
            if r.get("addr1") and r.get("city")
        ]

        if not useful:
            return fallback

        def _priority(r: Dict[str, Any]) -> tuple:
            t = r.get("addr_type", "9")
            # type "3" = principal office (best), "1" = agent, others = last
            type_order = {"3": 0, "1": 1}.get(t, 2)
            # Within same type, most recent date first (string sort DESC = negate lexicographic)
            date_str = r.get("date_filed") or "0000"
            return (type_order, date_str)

        sorted_records = sorted(useful, key=_priority)
        # Within same type_order, we want newest first — sort descending by date
        # Re-sort: primary = type_order ASC, secondary = date DESC
        sorted_records = sorted(
            useful,
            key=lambda r: (
                {"3": 0, "1": 1}.get(r.get("addr_type", "9"), 2),
                # Negate not possible on strings — use a reverse trick:
                # pad and invert: compare as tuple (type, inverted_date)
            )
        )
        # For date descending within same type, do a stable two-pass sort
        sorted_records = sorted(useful, key=lambda r: r.get("date_filed") or "", reverse=True)
        sorted_records = sorted(sorted_records, key=lambda r: {"3": 0, "1": 1}.get(r.get("addr_type", "9"), 2))

        best = sorted_records[0]

        return {
            "address_line_1": best.get("addr1"),
            "address_line_2": best.get("addr2"),
            "city":           best.get("city"),
            "state":          best.get("state"),
            "zip":            best.get("zip5"),
            "country":        best.get("country") or "United States",
            "name":           best.get("name"),
            "addr_type":      best.get("addr_type"),
            "addr_type_label": (
                "Principal Office" if best.get("addr_type") == "3"
                else "Registered Agent" if best.get("addr_type") == "1"
                else f"Type {best.get('addr_type')}"
            ),
            "source": "ny_dos_addresses_dataset",
        }

    # ── Normalised profile ────────────────────────────────────────────────────

    @classmethod
    def build_normalized_profile(
        cls,
        resolution_result: Any,  # NYResolutionResult
    ) -> Dict[str, Any]:
        """
        Build the complete normalized NY registry profile for
        CompanyData.official_registry_profile.

        Uses all verified dataset fields.
        """
        raw      = resolution_result.raw_profile or {}
        profile  = cls.map_profile(raw) if raw else {}
        filings  = cls.map_filings(resolution_result.filings or [])
        stock    = cls.map_stock(resolution_result.stock_info or [])
        entity   = cls.map_entity(resolution_result.entity_info or [])
        addresses = cls.map_addresses(resolution_result.address_info or [])

        # Prefer enriched address over the process-service address on profile
        registered_address = cls.best_registered_address(
            resolution_result.address_info or [],
            fallback=profile.get("registered_address"),
        )

        # Derive company status from entity records if available
        company_status = None
        for e in (resolution_result.entity_info or []):
            if e.get("status"):
                company_status = e["status"]
                break

        return {
            "source":                    "New York Department of State",
            "registry_type":             "NY Open Data",
            "country":                   "United States",
            "state":                     "New York",
            "dos_id":                    resolution_result.dos_id,
            "company_name":              profile.get("company_name"),
            "entity_type":               profile.get("entity_type"),
            "jurisdiction":              profile.get("jurisdiction"),
            "county":                    profile.get("county"),
            "filing_date":               profile.get("filing_date"),
            "company_status":            company_status,
            "registered_agent":          profile.get("dos_process_name"),
            "registered_agent_status":   profile.get("registered_agent_name"),
            "chairman":                  profile.get("chairman"),
            "registered_address":        registered_address or {},
            "full_registered_address":   profile.get("full_registered_address"),
            "filing_history":            filings,
            "stock_info":                stock,
            "entity_info":               entity,
            "address_records":           addresses,
            "resolution": {
                "status":       resolution_result.status,
                "matched":      resolution_result.matched,
                "confidence":   resolution_result.confidence,
                "match_method": resolution_result.match_method,
                "search_name":  resolution_result.search_name,
            },
        }

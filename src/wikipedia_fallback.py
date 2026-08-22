"""
Wikipedia Infobox Fallback Module (Domain Verified & Conflict Aware)
Queries Wikipedia API, verifies target domain match, parses Infobox fields,
and handles conflict markers without silently overwriting verified website data.
"""

import logging
import re
from typing import Any, Dict, Optional
import httpx

try:
    from .validator import is_same_base_domain
except ImportError:
    from validator import is_same_base_domain

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": "CompanyDomainCrawler/1.0 (company identity research; contact: info@example.com)"
}

FIELD_MAP = {
    "name": "legal_name",
    "legal_name": "legal_name",
    "trade_name": "brand_name",
    "type": "company_type",
    "industry": "industry",
    "founded": "founded",
    "hq_location": "full_address",
    "headquarters": "full_address",
    "location": "full_address",
    "parent": "parent_company",
    "subsid": "subsidiaries",
    "subsidiaries": "subsidiaries",
    "homepage": "website",
    "website": "website",
}


async def _search_wikipedia_title(query: str) -> Optional[str]:
    """Find the best-matching Wikipedia article title."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} company",
        "format": "json",
        "srlimit": 3,
    }
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        resp = await client.get(WIKI_API, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None
        for r in results:
            title = r.get("title", "")
            snippet = r.get("snippet", "").lower()
            if "disambiguation" not in snippet:
                return title
        return results[0].get("title")


async def _fetch_infobox_wikitext(title: str) -> Optional[str]:
    """Fetch raw wikitext of lead section."""
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": title,
        "rvprop": "content",
        "rvsection": 0,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        resp = await client.get(WIKI_API, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for _, page in pages.items():
            revisions = page.get("revisions", [])
            if revisions:
                rev = revisions[0]
                if "slots" in rev:
                    return rev["slots"].get("main", {}).get("*")
                return rev.get("*")
    return None


def _extract_website_url(raw_val: str) -> str:
    """Extract clean website URL from wiki templates like {{URL|...}} or raw links."""
    if not raw_val:
        return ""
    # Case 1: {{URL|https://example.com}} or {{URL|1=https://example.com}} or {{URL|example.com}}
    url_match = re.search(r"\{\{[Uu][Rr][Ll]\|(?:1=)?([^|}]+)", raw_val)
    if url_match:
        return url_match.group(1).strip()
    # Case 2: Markdown style [https://example.com example.com]
    link_match = re.search(r"\[(https?://[^\s\]]+)", raw_val)
    if link_match:
        return link_match.group(1).strip()
    # Case 3: Direct http URL in text
    direct_match = re.search(r"https?://[^\s{}|<>\[\]]+", raw_val)
    if direct_match:
        return direct_match.group(0).strip()
    # Case 4: Plain domain
    domain_match = re.search(r"\b([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b", raw_val)
    if domain_match and not raw_val.lower().startswith("{{official"):
        return domain_match.group(1).strip()
    return ""


def _clean_value(raw_val: str) -> str:
    """Clean common wiki markup."""
    val = re.sub(r"<ref[^>]*>.*?</ref>", "", raw_val, flags=re.DOTALL)
    val = re.sub(r"<ref[^>]*/>", "", val)
    val = re.sub(r"\[\[(?:[^\|\]]*\|)?([^\]]+)\]\]", r"\1", val)
    val = re.sub(r"'''?([^']*)'''?", r"\1", val)
    val = re.sub(r"\{\{[^{}]*\}\}", "", val)
    val = re.sub(r"<[^>]+>", "", val)
    val = re.sub(r"\s+", " ", val).strip(" ,;")
    return val


def _parse_infobox(wikitext: str) -> Dict[str, Any]:
    """Parse Infobox template into key-value map."""
    result: Dict[str, Any] = {}
    if not wikitext:
        return result

    match = re.search(r"\{\{\s*Infobox\s+(company|organization)", wikitext, re.IGNORECASE)
    if not match:
        return result

    block = wikitext[match.start():]
    for line in block.split("\n"):
        if "=" not in line:
            continue
        parts = line.split("=", 1)
        raw_key = parts[0].strip("| ").lower()
        raw_val = parts[1].strip()
        if not raw_val:
            continue

        mapped_key = FIELD_MAP.get(raw_key)
        if mapped_key:
            if mapped_key == "website":
                site_url = _extract_website_url(raw_val)
                if site_url:
                    result[mapped_key] = site_url
            elif mapped_key == "subsidiaries":
                val = _clean_value(raw_val)
                if val:
                    items = [x.strip() for x in val.split(",") if x.strip()]
                    result[mapped_key] = items
            else:
                val = _clean_value(raw_val)
                if val:
                    result[mapped_key] = val

    return result


async def _check_page_extlinks_match(title: str, target_domain: str) -> bool:
    """Check if Wikipedia page's external links contain the target domain."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "externallinks",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            resp = await client.get(WIKI_API, params=params)
            if resp.status_code != 200:
                return False
            data = resp.json()
            links = data.get("parse", {}).get("externallinks", [])
            for link in links:
                if is_same_base_domain(target_domain, link):
                    return True
    except Exception as e:
        logger.debug(f"Error checking extlinks for '{title}': {e}")
    return False


async def get_wikipedia_company_data(brand_name: str, domain: str) -> Optional[Dict[str, Any]]:
    """Fetch Wikipedia infobox and perform strict domain verification."""
    try:
        title = await _search_wikipedia_title(brand_name)
        if not title:
            return None

        wikitext = await _fetch_infobox_wikitext(title)
        if not wikitext:
            return None

        data = _parse_infobox(wikitext)
        if not data:
            return None

        wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        data["_source"] = wiki_url
        data["_wikipedia_title"] = title

        # Domain verification:
        # 1. Direct website field from infobox
        site_field = data.get("website") or ""
        domain_match = is_same_base_domain(domain, site_field) if site_field else False

        # 2. If infobox had {{Official URL}} or website was missing, check page external links
        if not domain_match:
            domain_match = await _check_page_extlinks_match(title, domain)
            if domain_match and not site_field:
                data["website"] = domain

        data["_domain_match"] = domain_match

        if not domain_match:
            logger.warning(f"Wikipedia title '{title}' website ('{site_field}') does not match target domain '{domain}'. Flagging unverified.")

        return data

    except Exception as e:
        logger.warning(f"Wikipedia fallback error for '{brand_name}': {e}")
        return None
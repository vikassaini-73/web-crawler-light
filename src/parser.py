"""
Structure-Preserving HTML & JSON-LD Parser Module
Parses HTML while preserving DOM structure, headings, paragraphs, contact blocks,
footers, HTML tables, div-based key-value layouts, Cloudflare-deobfuscated emails, and JSON-LD graphs.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


def deobfuscate_cf_email(cf_hex: str) -> Optional[str]:
    """Decode Cloudflare email obfuscation (XOR cipher)."""
    if not cf_hex or len(cf_hex) < 4:
        return None
    try:
        r = int(cf_hex[:2], 16)
        email = ''.join([chr(int(cf_hex[i:i+2], 16) ^ r) for i in range(2, len(cf_hex), 2)])
        return email.strip()
    except Exception as e:
        logger.debug(f"Failed to deobfuscate CF email '{cf_hex}': {e}")
        return None


class ParsedPageStructure:
    """Structure-preserving representation of a crawled page."""

    def __init__(self, url: str):
        self.url = url
        self.title: Optional[str] = None
        self.meta_description: Optional[str] = None
        self.og_site_name: Optional[str] = None
        self.og_title: Optional[str] = None
        self.canonical_url: Optional[str] = None
        self.headings: List[Tuple[str, str]] = []  # (tag_name, text) e.g. ("h1", "About Us")
        self.paragraphs: List[str] = []
        self.lists: List[List[str]] = []
        self.tables_kv: List[Dict[str, str]] = []  # List of extracted table key-value dicts
        self.div_kv: List[Dict[str, str]] = []     # List of div key-value dicts
        self.contact_blocks: List[str] = []
        self.footer_texts: List[str] = []
        self.json_ld_blocks: List[Dict[str, Any]] = []
        self.mailto_emails: List[str] = []
        self.raw_html: str = ""
        self.clean_text: str = ""


def parse_page_structure(url: str, html: str) -> ParsedPageStructure:
    """Parse raw HTML preserving tables, JSON-LD, headings, key-value structures, and deobfuscating emails."""
    parsed = ParsedPageStructure(url=url)
    parsed.raw_html = html or ""

    if not html or not html.strip():
        return parsed

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 0. Deobfuscate Cloudflare email protection tags into readable text
        for cf_elem in soup.find_all(attrs={"data-cfemail": True}):
            cf_hex = cf_elem["data-cfemail"]
            decoded = deobfuscate_cf_email(cf_hex)
            if decoded:
                cf_elem.string = decoded

        # Also check mailto links
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if href.lower().startswith("mailto:"):
                raw_mail = href[7:].split("?")[0].strip()
                if raw_mail:
                    parsed.mailto_emails.append(raw_mail)
            elif "/cdn-cgi/l/email-protection#" in href:
                cf_hex = href.split("#")[-1].strip()
                decoded = deobfuscate_cf_email(cf_hex)
                if decoded:
                    parsed.mailto_emails.append(decoded)
                    a_tag.string = decoded

        # 1. Title & Meta
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            parsed.title = title_tag.string.strip()

        meta_desc = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "description"})
        if meta_desc and meta_desc.get("content"):
            parsed.meta_description = meta_desc["content"].strip()

        og_site = soup.find("meta", attrs={"property": "og:site_name"})
        if og_site and og_site.get("content"):
            parsed.og_site_name = og_site["content"].strip()

        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            parsed.og_title = og_title["content"].strip()

        canon = soup.find("link", attrs={"rel": lambda x: x and x.lower() == "canonical"})
        if canon and canon.get("href"):
            parsed.canonical_url = canon["href"].strip()

        # 2. JSON-LD parsing (Recursive & Graph-aware)
        _extract_json_ld(soup, parsed)

        # 3. HTML Table Extraction
        _extract_tables(soup, parsed)

        # 4. Div & Definition List Key-Value Extraction
        _extract_div_key_values(soup, parsed)

        # 5. Headings & Structural Paragraphs
        for h_tag in soup.find_all(["h1", "h2", "h3", "h4"]):
            h_text = h_tag.get_text(separator=" ", strip=True)
            if h_text and len(h_text) < 200:
                parsed.headings.append((h_tag.name.lower(), h_text))

        # 6. Contact & Footer Blocks
        for footer in soup.find_all(
            ["footer", "div", "section", "address"],
            class_=lambda c: c and any(w in str(c).lower() for w in ["footer", "legal", "imprint", "copyright", "bottom", "contact", "address", "impressum"]),
        ):
            txt = footer.get_text(separator=" ", strip=True)
            if txt and 15 <= len(txt) <= 3000:
                parsed.footer_texts.append(txt)

        # 7. Clean Body Text
        soup_copy = BeautifulSoup(str(soup), "html.parser")
        for tag in soup_copy(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()

        parsed.clean_text = soup_copy.get_text(separator="\n", strip=True)

        for p in soup_copy.find_all("p"):
            p_text = p.get_text(separator=" ", strip=True)
            if p_text and len(p_text) >= 20:
                parsed.paragraphs.append(p_text)

    except Exception as e:
        logger.error(f"Error parsing HTML structure for {url}: {e}")

    return parsed


def _extract_json_ld(soup: BeautifulSoup, parsed: ParsedPageStructure):
    """Parse all JSON-LD blocks including nested arrays and @graph structures."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string or not script.string.strip():
            continue
        try:
            raw_json = script.string.strip()
            data = json.loads(raw_json)
            _flatten_json_ld_item(data, parsed.json_ld_blocks)
        except Exception:
            pass


def _flatten_json_ld_item(item: Any, target_list: List[Dict[str, Any]]):
    """Recursively unnest JSON-LD objects, lists, and @graph nodes."""
    if isinstance(item, dict):
        if "@graph" in item and isinstance(item["@graph"], list):
            for sub in item["@graph"]:
                _flatten_json_ld_item(sub, target_list)
        else:
            target_list.append(item)
    elif isinstance(item, list):
        for sub in item:
            _flatten_json_ld_item(sub, target_list)


def _extract_tables(soup: BeautifulSoup, parsed: ParsedPageStructure):
    """Extract HTML tables into structured key-value maps."""
    for table in soup.find_all("table"):
        table_kv: Dict[str, str] = {}
        rows = table.find_all("tr")
        for row in rows:
            headers = row.find_all(["th", "td"])
            if len(headers) == 2:
                key = headers[0].get_text(separator=" ", strip=True)
                val = headers[1].get_text(separator=" ", strip=True)
                if key and val and len(key) <= 80 and len(val) <= 500:
                    table_kv[key] = val
            elif len(headers) > 2:
                cells = [h.get_text(separator=" ", strip=True) for h in headers]
                if len(cells) == 2 and cells[0] and cells[1]:
                    table_kv[cells[0]] = cells[1]

        if table_kv:
            parsed.tables_kv.append(table_kv)


def _extract_div_key_values(soup: BeautifulSoup, parsed: ParsedPageStructure):
    """Extract <dl>/<dt>/<dd> and grid/flex key-value pairs."""
    div_kv: Dict[str, str] = {}

    # Definition lists <dl>
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if len(dts) == len(dds):
            for dt, dd in zip(dts, dds):
                k = dt.get_text(separator=" ", strip=True)
                v = dd.get_text(separator=" ", strip=True)
                if k and v and len(k) <= 80 and len(v) <= 500:
                    div_kv[k] = v

    # Common class pairs like (.label, .value), (.field-label, .field-value), (.key, .val)
    for container in soup.find_all(["div", "section", "ul", "ol"]):
        label_elem = container.find(class_=lambda c: c and any(w in str(c).lower() for w in ["label", "key", "term", "title"]))
        value_elem = container.find(class_=lambda c: c and any(w in str(c).lower() for w in ["value", "val", "desc", "data", "content"]))
        if label_elem and value_elem and label_elem != value_elem:
            k = label_elem.get_text(separator=" ", strip=True)
            v = value_elem.get_text(separator=" ", strip=True)
            if k and v and len(k) <= 80 and len(v) <= 500:
                div_kv[k] = v

    if div_kv:
        parsed.div_kv.append(div_kv)

"""
Crawler Module — Page Content Container & HTML Parser Bridge
Provides the PageContent data class and extract_page_elements() helper used by
the Jina Reader adapter (jina_reader.py) and the existing extractor pipeline.

Browser rendering (Crawl4AI / Playwright / Chromium) has been removed.
Page content is now fetched via the Jina AI Reader API (see jina_reader.py).
"""

from typing import Any, Dict, List, Optional

try:
    from .parser import ParsedPageStructure, parse_page_structure
except ImportError:
    from parser import ParsedPageStructure, parse_page_structure


class PageContent:
    """
    Container for a single crawled page.

    Fields are identical to the original class so every downstream consumer
    (extractor.py, telemetry.py, pipeline.py) continues to work unchanged.
    """

    def __init__(self, url: str, status_code: int = 200):
        self.url = url
        self.status_code = status_code
        self.title: Optional[str] = None
        self.meta_description: Optional[str] = None
        self.og_site_name: Optional[str] = None
        self.og_title: Optional[str] = None
        self.html: str = ""
        self.text: str = ""
        self.markdown: str = ""
        self.json_ld: List[Dict[str, Any]] = []
        self.copyright_texts: List[str] = []
        self.footer_texts: List[str] = []
        self.contact_blocks: List[str] = []
        self.parsed_structure: Optional[ParsedPageStructure] = None


def extract_page_elements(url: str, html: str) -> PageContent:
    """
    Parse raw HTML using ParsedPageStructure and map fields to PageContent.

    Called by jina_reader._adapt_jina_response() after the Jina markdown
    response is wrapped in a minimal HTML document.
    """
    page = PageContent(url=url)
    page.html = html or ""

    if not html:
        return page

    structure = parse_page_structure(url, html)
    page.parsed_structure = structure
    page.title = structure.title
    page.meta_description = structure.meta_description
    page.og_site_name = structure.og_site_name
    page.og_title = structure.og_title
    page.text = structure.clean_text
    page.json_ld = structure.json_ld_blocks
    page.footer_texts = structure.footer_texts
    page.contact_blocks = structure.contact_blocks

    return page

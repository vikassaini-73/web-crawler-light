"""
URL Discovery Module (Concurrent Recursive & BFS Enabled with Robust Timeout Policies)
Discovers website URLs via robots.txt, multi-level sitemaps, homepage links,
and multi-depth recursive BFS internal link crawling.
"""

import asyncio
import json
import logging
import os
import re
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs, urlencode
from urllib.robotparser import RobotFileParser
import gzip
import io
import xml.etree.ElementTree as ET

import httpx
import tldextract
from pathlib import Path

try:
    from .validator import is_same_base_domain
except ImportError:
    from validator import is_same_base_domain

logger = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DISCOVERY_TIMEOUT = httpx.Timeout(12.0, connect=8.0)


def log_print(msg: str):
    print(msg, flush=True)


def _resolve_output_path(output_file: str) -> Path:
    """Use Vercel's writable temporary directory for relative output paths."""
    path = Path(output_file)
    if os.getenv("VERCEL") == "1" and not path.is_absolute():
        return Path("/tmp") / path
    return path


IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".exe", ".dmg", ".apk",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flv", ".webm",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".xml", ".rss", ".atom", ".json",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "ref", "source", "mc_eid", "attribute_pa",
    "_hsenc", "_hsmi", "v", "amp"
}


def normalize_domain_url(raw_url: str) -> str:
    """Normalize input URL or domain string to full https URL."""
    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return f"{scheme}://{netloc}{path}"


def get_base_domain(url: str) -> str:
    """Extract registered domain (e.g. brewdog.com from jobs.brewdog.com)."""
    extracted = tldextract.extract(url)
    if not extracted.domain:
        return ""
    return f"{extracted.domain}.{extracted.suffix}".lower()


def is_same_domain(target_url: str, candidate_url: str) -> bool:
    """Check if candidate URL belongs to the same registered base domain."""
    return is_same_base_domain(target_url, candidate_url)


def normalize_url(base_url: str, link: str) -> str | None:
    """Resolve relative link, strip fragments, remove tracking params, and filter media/non-http."""
    if not link:
        return None
    link = link.strip()
    if link.startswith(("mailto:", "tel:", "javascript:", "data:", "#", "whatsapp:")):
        return None

    try:
        resolved = urljoin(base_url, link)
        defragged, _ = urldefrag(resolved)
        parsed = urlparse(defragged)

        if parsed.scheme not in ("http", "https"):
            return None

        path_lower = parsed.path.lower()
        for ext in IGNORED_EXTENSIONS:
            if path_lower.endswith(ext):
                return None

        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        if not path:
            path = "/"

        clean_query = ""
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            filtered_qs = {k: v for k, v in qs.items() if k.lower() not in TRACKING_PARAMS}
            if filtered_qs:
                clean_query = "?" + urlencode(filtered_qs, doseq=True)

        clean_url = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}{clean_query}"
        return clean_url
    except Exception as e:
        logger.debug(f"[normalize_url] Failed on '{link}' with base '{base_url}': {e}")
        return None


class URLDiscovery:
    """Discovers publicly accessible URLs using Robots.txt, Sitemaps & Concurrent BFS crawling."""

    def __init__(self, start_url: str, user_agent: str = DEFAULT_UA):
        self.start_url = normalize_domain_url(start_url)
        parsed = urlparse(self.start_url)
        self.scheme = parsed.scheme
        self.netloc = parsed.netloc
        self.base_domain = get_base_domain(self.start_url)
        self.user_agent = user_agent
        self.discovered_urls: Set[str] = set()
        self.robots_parser: RobotFileParser | None = None
        self.sitemap_urls_count: int = 0
        self.homepage_links_count: int = 0
        self.bfs_discovered_count: int = 0
        self.discovery_errors: List[Dict[str, str]] = []

    async def fetch_robots_txt(self) -> Tuple[RobotFileParser | None, List[str]]:
        """Fetch and parse robots.txt with 10-15s timeout policy."""
        robots_url = f"{self.scheme}://{self.netloc}/robots.txt"
        sitemaps_found: List[str] = []
        rp = RobotFileParser()

        try:
            async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(robots_url, headers={"User-Agent": self.user_agent})
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    for line in resp.text.splitlines():
                        if line.strip().lower().startswith("sitemap:"):
                            parts = line.split(":", 1)
                            if len(parts) == 2:
                                sm = parts[1].strip()
                                if sm:
                                    sitemaps_found.append(sm)
                    logger.info(f"Loaded robots.txt from {robots_url}. Found {len(sitemaps_found)} sitemaps.")
                else:
                    logger.info(f"Robots.txt at {robots_url} returned status {resp.status_code}")
                    rp = None
        except Exception as e:
            err_msg = f"Failed to fetch robots.txt ({robots_url}): {type(e).__name__} - {e}"
            logger.warning(err_msg)
            self.discovery_errors.append({
                "stage": "robots_discovery",
                "url": robots_url,
                "error": type(e).__name__,
                "message": str(e)
            })
            rp = None

        self.robots_parser = rp
        return rp, sitemaps_found

    def is_allowed_by_robots(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt."""
        if not self.robots_parser:
            return True
        try:
            # Check only if on the same host as robots.txt
            parsed = urlparse(url)
            if parsed.netloc.lower() == self.netloc.lower():
                return self.robots_parser.can_fetch(self.user_agent, url)
            return True
        except Exception as e:
            logger.debug(f"Robots check exception on {url}: {e}")
            return True

    async def _parse_single_sitemap(self, client: httpx.AsyncClient, sm_url: str) -> Tuple[List[str], List[str]]:
        """Fetch and parse a single sitemap URL returning (page_urls, child_sitemaps)."""
        urls: List[str] = []
        children: List[str] = []
        try:
            resp = await client.get(sm_url, headers={"User-Agent": self.user_agent})
            if resp.status_code != 200 or not resp.content:
                logger.debug(f"Sitemap {sm_url} returned HTTP {resp.status_code}")
                return urls, children

            content_bytes = resp.content
            if sm_url.endswith(".gz") or resp.headers.get("content-type") == "application/x-gzip":
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(content_bytes)) as gz:
                        content_bytes = gz.read()
                except Exception as gz_err:
                    logger.debug(f"Gzip decompress error for {sm_url}: {gz_err}")

            xml_str = content_bytes.decode("utf-8", errors="ignore")
            if not xml_str.strip():
                return urls, children

            clean_xml = re.sub(r'\sxmlns="[^"]+"', '', xml_str, count=1)
            root = ET.fromstring(clean_xml.encode('utf-8'))

            for loc in root.findall(".//url/loc"):
                if loc.text:
                    norm = normalize_url(self.start_url, loc.text.strip())
                    if norm and is_same_base_domain(self.start_url, norm) and self.is_allowed_by_robots(norm):
                        urls.append(norm)

            for sub_loc in root.findall(".//sitemap/loc"):
                if sub_loc.text:
                    children.append(sub_loc.text.strip())

        except Exception as e:
            err_msg = f"Error parsing sitemap {sm_url}: {type(e).__name__} - {e}"
            logger.debug(err_msg)
            self.discovery_errors.append({
                "stage": "sitemap_parse",
                "url": sm_url,
                "error": type(e).__name__,
                "message": str(e)
            })

        return urls, children

    async def fetch_sitemap_urls_recursive(self, sitemap_list: List[str], max_urls: int = 1000) -> List[str]:
        """Recursively process sitemap index files concurrently with 10-15s timeouts."""
        candidate_sitemaps = list(sitemap_list)
        standard = [
            f"{self.scheme}://{self.netloc}/sitemap.xml",
            f"{self.scheme}://{self.netloc}/sitemap_index.xml",
            f"{self.scheme}://{self.netloc}/sitemap/sitemap.xml",
        ]
        for sm in standard:
            if sm not in candidate_sitemaps:
                candidate_sitemaps.append(sm)

        extracted: Set[str] = set()
        visited_sitemaps: Set[str] = set()
        queue: List[str] = list(candidate_sitemaps)

        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
            while queue and len(extracted) < max_urls and len(visited_sitemaps) < 25:
                batch = [u for u in queue[:5] if u not in visited_sitemaps]
                queue = queue[5:]
                if not batch:
                    break

                for u in batch:
                    visited_sitemaps.add(u)

                tasks = [self._parse_single_sitemap(client, u) for u in batch]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, tuple) and len(res) == 2:
                            page_urls, child_sitemaps = res
                            for pu in page_urls:
                                extracted.add(pu)
                                if len(extracted) >= max_urls:
                                    break
                            for cs in child_sitemaps:
                                if cs not in visited_sitemaps and len(visited_sitemaps) + len(queue) < 30:
                                    queue.append(cs)
                        elif isinstance(res, Exception):
                            logger.debug(f"Sitemap batch task raised: {res}")
                except Exception as e:
                    logger.debug(f"Sitemap gather error: {e}")

        self.sitemap_urls_count = len(extracted)
        logger.info(f"Recursively extracted {len(extracted)} URLs from sitemaps.")
        return list(extracted)

    async def crawl_bfs_internal_links(self, start_urls: List[str], max_depth: int = 2, max_urls: int = 400) -> List[str]:
        """Concurrent multi-depth BFS queue crawl to discover deep internal links across base domain."""
        discovered: Set[str] = set(start_urls)
        queue: List[Tuple[str, int]] = [(u, 0) for u in start_urls[:10]]
        visited: Set[str] = set()

        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
            headers = {"User-Agent": DEFAULT_UA}

            while queue and len(discovered) < max_urls:
                batch_items = []
                while queue and len(batch_items) < 5:
                    u, d = queue.pop(0)
                    if u not in visited and d <= max_depth:
                        visited.add(u)
                        batch_items.append((u, d))

                if not batch_items:
                    break

                async def _fetch_links(target_url: str, depth: int):
                    new_links = []
                    try:
                        resp = await client.get(target_url, headers=headers)
                        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", "").lower():
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(resp.text, "html.parser")
                            for a in soup.find_all("a", href=True):
                                norm = normalize_url(target_url, a["href"])
                                if norm and is_same_base_domain(self.start_url, norm) and self.is_allowed_by_robots(norm):
                                    new_links.append((norm, depth + 1))
                    except Exception as e:
                        logger.debug(f"BFS link fetch error for {target_url}: {e}")
                        self.discovery_errors.append({
                            "stage": "bfs_link_fetch",
                            "url": target_url,
                            "error": type(e).__name__,
                            "message": str(e)
                        })
                    return new_links

                tasks = [_fetch_links(u, d) for u, d in batch_items]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, list):
                            for norm, next_depth in res:
                                if norm not in discovered:
                                    discovered.add(norm)
                                    if next_depth <= max_depth and len(discovered) < max_urls:
                                        queue.append((norm, next_depth))
                except Exception as e:
                    logger.debug(f"BFS gather error: {e}")

        self.bfs_discovered_count = len(discovered) - len(start_urls)
        return list(discovered)

    async def discover_all(self, output_file: str = "output/discovered_urls.json") -> List[str]:
        """Execute full recursive URL discovery pipeline."""
        log_print(f"\n[1/5] Starting Multi-Source URL Discovery for: {self.start_url}")

        self.discovered_urls.add(self.start_url)

        # 1. Robots.txt & Sitemaps
        try:
            _, sitemaps = await self.fetch_robots_txt()
            sitemap_urls = await self.fetch_sitemap_urls_recursive(sitemaps, max_urls=1000)
            for u in sitemap_urls:
                self.discovered_urls.add(u)
        except Exception as e:
            log_print(f"      [!] Sitemap discovery exception: {e}")
            self.discovery_errors.append({
                "stage": "sitemap_discovery_main",
                "url": self.start_url,
                "error": type(e).__name__,
                "message": str(e)
            })
            sitemap_urls = []

        # 2. Multi-depth BFS internal queue discovery
        try:
            seed_urls = [self.start_url] + sitemap_urls[:10]
            bfs_urls = await self.crawl_bfs_internal_links(seed_urls, max_depth=2, max_urls=400)
            for u in bfs_urls:
                self.discovered_urls.add(u)
        except Exception as e:
            log_print(f"      [!] BFS link discovery exception: {e}")
            self.discovery_errors.append({
                "stage": "bfs_discovery_main",
                "url": self.start_url,
                "error": type(e).__name__,
                "message": str(e)
            })

        all_urls = sorted(list(self.discovered_urls))
        log_print(f"      Discovered {len(all_urls)} unique same-domain URLs "
                  f"(Sitemaps: {self.sitemap_urls_count}, BFS Internal: {self.bfs_discovered_count})")

        resolved_output_path = _resolve_output_path(output_file)
        log_print(f"      Resolved output path: {resolved_output_path}")
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_output_path.open("w", encoding="utf-8") as f:
            json.dump({
                "domain": self.start_url,
                "total_discovered": len(all_urls),
                "sitemap_urls_count": self.sitemap_urls_count,
                "bfs_urls_count": self.bfs_discovered_count,
                "urls": all_urls,
                "errors": self.discovery_errors
            }, f, indent=2)
        log_print(f"      Saved discovered URLs to: {resolved_output_path}")

        return all_urls

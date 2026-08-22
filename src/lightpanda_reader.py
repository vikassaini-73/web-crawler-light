"""
Lightpanda Reader Module
Uses the ultra-fast Lightpanda browser engine to fetch and render pages.
Designed for high performance and low memory usage in WSL/Linux.

This module uses the system binary directly to bypass Python 3.14 compatibility issues.
"""

import logging
import os
import asyncio
import shutil
import re
from typing import List, Optional

try:
    from .crawler import PageContent, extract_page_elements
except ImportError:
    from crawler import PageContent, extract_page_elements

logger = logging.getLogger(__name__)

def log_print(msg: str):
    print(msg, flush=True)

class LightpandaReader:
    """
    Fetches rendered HTML using the Lightpanda CLI binary.
    Bypasses Python version compatibility issues by using the system binary.
    """

    def __init__(self, concurrency: int = 5):
        self.concurrency = concurrency
        # Find the binary in system PATH
        self.bin_path = shutil.which("lightpanda")
        
        if not self.bin_path:
            log_print("      [Lightpanda] ⚠ Warning: 'lightpanda' binary not found in PATH.")
        else:
            log_print(f"      [Lightpanda] Initialised via CLI — {self.bin_path}")

    async def read_pages(self, urls: List[str]) -> List[PageContent]:
        total = len(urls)
        
        if not self.bin_path:
            log_print(f"\n[3/5] ✗ Lightpanda binary missing. Cannot render pages.")
            return []

        log_print(f"\n[3/5] Reading {total} pages via Lightpanda CLI (JS Rendering)...")

        results: List[PageContent] = []
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_task(url, idx):
            async with semaphore:
                try:
                    # Execute: lightpanda fetch <url> --dump html
                    cmd = [self.bin_path, "fetch", url, "--dump", "html"]
                    
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45.0)
                        raw_html = stdout.decode("utf-8", errors="ignore")
                    except asyncio.TimeoutError:
                        try:
                            process.kill()
                        except:
                            pass
                        log_print(f"      [{idx}/{total}] ✗ Timeout for {url}")
                        return None

                    if not raw_html or not raw_html.strip():
                        log_print(f"      [{idx}/{total}] ⚠ Empty response from Lightpanda for: {url}")
                        return None

                    page = extract_page_elements(url, raw_html)
                    log_print(f"      [{idx}/{total}] ✓ Successfully fetched: {url}")
                    return page

                except Exception as e:
                    log_print(f"      [{idx}/{total}] ✗ Lightpanda CLI error for {url}: {e}")
                    return None

        tasks = [fetch_task(url, i+1) for i, url in enumerate(urls)]
        pages = await asyncio.gather(*tasks)
        results = [p for p in pages if p]

        return results

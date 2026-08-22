import logging
import os
import asyncio
import shutil
import httpx
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
    Automatically downloads the binary if missing (optimized for Vercel).
    """

    def __init__(self, concurrency: int = 5):
        self.concurrency = concurrency
        self.bin_path = self._ensure_binary()

    def _ensure_binary(self) -> Optional[str]:
        """Checks for binary and downloads it if missing."""
        # 1. Check system PATH
        path = shutil.which("lightpanda")
        if path:
            return path
        
        # 2. Check local project bin directory
        local_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "lightpanda")
        if os.path.exists(local_bin):
            try:
                os.chmod(local_bin, 0o755)
            except:
                pass
            return local_bin

        # 3. Check Vercel /tmp directory
        tmp_bin = "/tmp/lightpanda"
        if os.path.exists(tmp_bin):
            return tmp_bin

        # 4. Download if missing (Standard for Vercel Hobby/Pro)
        log_print("      [Lightpanda] Binary missing. Downloading engine (150MB)...")
        try:
            url = "https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux"
            with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    with open(tmp_bin, "wb") as f:
                        f.write(resp.content)
                    os.chmod(tmp_bin, 0o755)
                    log_print(f"      [Lightpanda] Engine ready at {tmp_bin}")
                    return tmp_bin
        except Exception as e:
            log_print(f"      [Lightpanda] Download failed: {e}")

        return None

    async def read_pages(self, urls: List[str]) -> List[PageContent]:
        total = len(urls)
        
        if not self.bin_path:
            log_print(f"\n[3/5] ✗ Lightpanda engine not available. JS rendering disabled.")
            # Fallback to simple HTTPX fetch could be added here if needed
            return []

        log_print(f"\n[3/5] Reading {total} pages via Lightpanda (JS Rendering)...")

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
                    log_print(f"      [{idx}/{total}] ✗ Lightpanda engine error for {url}: {e}")
                    return None

        tasks = [fetch_task(url, i+1) for i, url in enumerate(urls)]
        pages = await asyncio.gather(*tasks)
        results = [p for p in pages if p]

        return results

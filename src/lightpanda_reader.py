import logging
import os
import asyncio
import shutil
import httpx
import stat
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
    Prioritizes a build-time bundled binary for Vercel deployment.
    """

    def __init__(self, concurrency: int = 5):
        self.concurrency = concurrency
        self.bin_path = self._ensure_binary()

    def _ensure_binary(self) -> Optional[str]:
        """
        Locates the Lightpanda binary. 
        Order: Bundled (bin/) -> PATH -> Runtime Download (/tmp/)
        """
        # 1. Check for Build-time Bundled Binary (Priority 1)
        # In Vercel, the source files are in /var/task
        project_root = os.getcwd()
        
        # Binary is now placed inside src/lightpanda_bin to ensure bundling
        bundled_bin = os.path.join(project_root, "src", "lightpanda_bin", "lightpanda")
        
        # Fallback to path relative to this file
        file_relative_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lightpanda_bin", "lightpanda")
        
        for path in [bundled_bin, file_relative_bin]:
            if os.path.exists(path):
                self._set_executable(path)
                log_print(f"      [Lightpanda] Using bundled binary at {path}")
                return path

        # 2. Check system PATH (Priority 2 - Local Dev / Custom Environments)
        path_bin = shutil.which("lightpanda")
        if path_bin:
            log_print(f"      [Lightpanda] Using system PATH binary: {path_bin}")
            return path_bin
        
        # 3. Check/Download to /tmp (Priority 3 - Emergency Fallback)
        tmp_bin = "/tmp/lightpanda"
        if os.path.exists(tmp_bin):
            self._set_executable(tmp_bin)
            log_print(f"      [Lightpanda] Using previously downloaded binary at {tmp_bin}")
            return tmp_bin

        # LAST RESORT: Runtime Download (Warning: likely to timeout on Vercel)
        log_print("      [Lightpanda] ⚠ BUNDLED BINARY MISSING. Attempting emergency runtime download to /tmp...")
        log_print("      [Lightpanda] ℹ Note: This fallback usually times out on Vercel. Ensure build step is working.")
        
        try:
            url = "https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux"
            # Using a slightly longer timeout for the fallback attempt
            with httpx.Client(follow_redirects=True, timeout=90.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    with open(tmp_bin, "wb") as f:
                        f.write(resp.content)
                    self._set_executable(tmp_bin)
                    log_print(f"      [Lightpanda] ✓ Emergency download complete at {tmp_bin}")
                    return tmp_bin
                else:
                    log_print(f"      [Lightpanda] ✗ Emergency download failed (HTTP {resp.status_code})")
        except Exception as e:
            log_print(f"      [Lightpanda] ✗ CRITICAL: Emergency binary download failed: {e}")

        log_print("      [Lightpanda] ✗ FATAL: No Lightpanda engine found. JS rendering will be skipped.")
        log_print("      [Lightpanda] 💡 Check build logs for 'Lightpanda Build Step' to debug bundling issues.")
        return None

    def _set_executable(self, path: str):
        """Ensures the file has executable permissions."""
        try:
            st = os.stat(path)
            os.chmod(path, st.st_mode | stat.S_IEXEC)
        except Exception as e:
            log_print(f"      [Lightpanda] Permission fix failed for {path}: {e}")

    async def read_pages(self, urls: List[str]) -> List[PageContent]:
        total = len(urls)
        
        if not self.bin_path:
            log_print(f"\n[3/5] ✗ Lightpanda engine not available. JS rendering disabled.")
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
                        
                        if process.returncode != 0:
                            err = stderr.decode().strip()
                            log_print(f"      [{idx}/{total}] ✗ CLI Error for {url}: {err}")
                            return None

                    except asyncio.TimeoutError:
                        try: process.kill()
                        except: pass
                        log_print(f"      [{idx}/{total}] ✗ Timeout for {url}")
                        return None

                    if not raw_html or not raw_html.strip():
                        log_print(f"      [{idx}/{total}] ⚠ Empty response for: {url}")
                        return None

                    page = extract_page_elements(url, raw_html)
                    log_print(f"      [{idx}/{total}] ✓ Successfully fetched: {url}")
                    return page

                except Exception as e:
                    log_print(f"      [{idx}/{total}] ✗ Engine error for {url}: {e}")
                    return None

        tasks = [fetch_task(url, i+1) for i, url in enumerate(urls)]
        pages = await asyncio.gather(*tasks)
        return [p for p in pages if p]

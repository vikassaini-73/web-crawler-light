"""
Debug Telemetry & Logger Module
Logs and formats comprehensive pipeline execution stats for Discovery, Selection,
Crawling, Extraction, and Fallbacks.
"""

import json
import logging
from typing import Any, Dict, List
from rich.console import Console
from rich.table import Table

console = Console()


class TelemetryLogger:
    """Tracks and reports telemetry stats across the entire crawling pipeline."""

    def __init__(self):
        self.discovery_stats: Dict[str, Any] = {}
        self.selection_stats: List[Dict[str, Any]] = []
        self.crawling_stats: List[Dict[str, Any]] = []
        self.extraction_stats: Dict[str, Any] = {}

    def log_discovery(self, domain: str, sitemaps_found: int, sitemap_urls: int, bfs_urls: int, total_unique: int):
        self.discovery_stats = {
            "domain": domain,
            "sitemaps_found": sitemaps_found,
            "sitemap_urls_count": sitemap_urls,
            "bfs_urls_count": bfs_urls,
            "total_unique_urls": total_unique,
        }
        print("\n" + "=" * 70)
        print("TELEMETRY: [1/4] DISCOVERY BREAKDOWN")
        print("=" * 70)
        print(f"Target Domain:       {domain}")
        print(f"Sitemaps Found:      {sitemaps_found}")
        print(f"Sitemap URLs:        {sitemap_urls}")
        print(f"BFS Discovered URLs: {bfs_urls}")
        print(f"Total Unique URLs:   {total_unique}")

    def log_selection(self, selected_items: List[Any]):
        print("\n" + "=" * 70)
        print("TELEMETRY: [2/4] PAGE SELECTION BREAKDOWN")
        print("=" * 70)
        table = Table(title="Selected High-Priority Pages")
        table.add_column("#", style="dim", width=4)
        table.add_column("Category", style="cyan", width=12)
        table.add_column("Score", style="green", width=8)
        table.add_column("URL", style="white")

        for idx, (url, score, cat) in enumerate(selected_items, 1):
            table.add_row(str(idx), cat.upper(), f"{score:.1f}", url)
            self.selection_stats.append({"url": url, "score": score, "category": cat})

        console.print(table)

    def log_crawling(self, crawled_pages: List[Any]):
        print("\n" + "=" * 70)
        print("TELEMETRY: [3/4] CRAWLING & RENDERING BREAKDOWN")
        print("=" * 70)
        table = Table(title="Rendered Page Results")
        table.add_column("#", style="dim", width=4)
        table.add_column("Status", style="green", width=8)
        table.add_column("HTML Size", style="cyan", width=10)
        table.add_column("Text Size", style="yellow", width=10)
        table.add_column("JSON-LD", style="magenta", width=8)
        table.add_column("URL", style="white")

        for idx, page in enumerate(crawled_pages, 1):
            html_size = len(page.html) if getattr(page, "html", None) else 0
            text_size = len(page.text) if getattr(page, "text", None) else 0
            json_ld_cnt = len(page.json_ld) if getattr(page, "json_ld", None) else 0
            status = getattr(page, "status_code", 200)

            table.add_row(str(idx), str(status), f"{html_size} B", f"{text_size} B", str(json_ld_cnt), page.url)
            self.crawling_stats.append({
                "url": page.url,
                "status": status,
                "html_bytes": html_size,
                "text_bytes": text_size,
                "json_ld_count": json_ld_cnt
            })

        console.print(table)

    def log_extraction(self, company_data: Any):
        print("\n" + "=" * 70)
        print("TELEMETRY: [4/4] EXTRACTION & EVIDENCE BREAKDOWN")
        print("=" * 70)
        table = Table(title="Field Candidates & Evidence")
        table.add_column("Field", style="cyan", width=20)
        table.add_column("Value", style="green", width=30)
        table.add_column("Confidence", style="yellow", width=12)
        table.add_column("Source Method", style="magenta", width=22)

        data_dict = company_data.model_dump()
        fields = [
            "company_name", "legal_name", "brand_name", "registration_number",
            "vat_tax_number", "country", "city", "full_address", "phone",
            "email", "industry", "parent_company"
        ]

        for f in fields:
            val = data_dict.get(f)
            ev = company_data.field_evidence.get(f)
            conf_str = f"{ev.confidence:.2f}" if ev else "0.00"
            method_str = ev.method if ev else "null"
            val_str = str(val)[:28] if val else "[NULL]"
            table.add_row(f, val_str, conf_str, method_str)

        console.print(table)

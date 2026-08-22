import argparse
import asyncio
import json
import os
import sys

# Force unbuffered UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env by searching from src directory up through parent directories
def _load_env_from_parents():
    from dotenv import load_dotenv
    search_dir = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):  # Search up to 6 levels up
        candidate = os.path.join(search_dir, '.env')
        if os.path.isfile(candidate):
            load_dotenv(candidate, override=False)
            return candidate
        search_dir = os.path.dirname(search_dir)
    return None

_env_file = _load_env_from_parents()

from pipeline import CompanyIntelligencePipeline

async def run_cli_research(domain: str, max_pages: int, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    company_data_file = os.path.join(output_dir, "company_data.json")
    
    pipeline = CompanyIntelligencePipeline(max_pages=max_pages, enable_ch=True)
    final_data = None

    async for event in pipeline.run_generator(domain):
        if event["type"] == "log":
            print(event["msg"], flush=True)
        elif event["type"] == "done":
            final_data = event["content"]

    if final_data:
        with open(company_data_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        print(f"\n[Final] Saved to: {company_data_file}")
        
        try:
            from rich.console import Console
            from rich.syntax import Syntax
            console = Console()
            console.print(Syntax(json.dumps(final_data, indent=2, ensure_ascii=False), "json", theme="monokai"))
        except ImportError:
            print(json.dumps(final_data, indent=2, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description="Company Intelligence CLI")
    parser.add_argument("domain", help="Target domain")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    asyncio.run(run_cli_research(args.domain, args.max_pages, args.output_dir))

if __name__ == "__main__":
    main()

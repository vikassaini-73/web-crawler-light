import asyncio
import json
import os
import sys
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
from dotenv import load_dotenv

# Force unbuffered UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure src directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))
load_dotenv(env_path)

from pipeline import CompanyIntelligencePipeline
from url_discovery import _resolve_output_path
from uk import CompaniesHouseClient, UKCompanyResolver, CompaniesHouseMapper

app = FastAPI(title="Company Intelligence Research UI")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        template_path = os.path.join(TEMPLATE_DIR, "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Internal Server Error</h1><p>{str(e)}</p>", status_code=500)


async def run_web_pipeline(domain: str, max_pages: int):
    # Pipeline automatically executes full flow including UK registry if applicable
    pipeline = CompanyIntelligencePipeline(max_pages=max_pages, enable_ch=True)
    final_data = None

    async for event in pipeline.run_generator(domain):
        if event["type"] == "done":
            final_data = event["content"]

        if event["type"] == "log" and "level" not in event:
            event["level"] = "INFO"

        yield f"data: {json.dumps(event)}\n\n"
        await asyncio.sleep(0.01)

    # Save to local history after crawl is complete
    if final_data:
        import time
        if os.getenv("VERCEL") == "1":
            history_dir = _resolve_output_path("output/history")
        else:
            history_dir = os.path.join(os.path.dirname(__file__), "../output/history")
        os.makedirs(history_dir, exist_ok=True)
        print(f"[HISTORY] Saving history to: {history_dir}", flush=True)

        clean_domain = domain.replace('https://', '').replace('http://', '').replace('/', '_').strip('_')
        filename = f"{clean_domain}_{int(time.time())}.json"
        filepath = os.path.join(history_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        print(f"[HISTORY] Saved: {filepath}", flush=True)


@app.get("/crawl")
async def crawl_endpoint(domain: str, max_pages: int = 10):
    return StreamingResponse(run_web_pipeline(domain, max_pages), media_type="text/event-stream")


@app.get("/enrich")
async def enrich_endpoint(
    name: Optional[str] = None,
    reg_no: Optional[str] = None,
    domain: Optional[str] = "",
    postcode: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
):
    """Optional manual re-enrichment endpoint for UK Companies House."""
    try:
        ch_api_key = os.getenv("COMPANIES_HOUSE_API_KEY")
        if not ch_api_key:
            return {"status": "error", "msg": "Companies House API Key missing"}

        ch_client = CompaniesHouseClient(ch_api_key)
        resolver = UKCompanyResolver(ch_client)

        class Proxy:
            def __init__(self, n, r, d, pc, ct, ad):
                self.company_name = n
                self.legal_name = n
                self.registration_number = r
                self.domain = d
                self.postal_code = pc
                self.city = ct
                self.full_address = ad

        res = await resolver.resolve(Proxy(name, reg_no, domain, postcode, city, address))

        if res.matched and res.company_number:
            comp_no = res.company_number
            profile, officers, psc, history, charges, insolvency = await asyncio.gather(
                ch_client.get_company_profile(comp_no),
                ch_client.get_officers(comp_no),
                ch_client.get_psc(comp_no),
                ch_client.get_filing_history(comp_no),
                ch_client.get_charges(comp_no),
                ch_client.get_insolvency(comp_no)
            )

            mapper = CompaniesHouseMapper()
            normalized = mapper.build_normalized_profile(
                profile=profile,
                officers=officers,
                psc=psc,
                filing_history=history,
                charges=charges,
                insolvency=insolvency
            )

            final_enriched_data = {
                "status": "success",
                "company_number": comp_no,
                "legal_name": profile.get("company_name"),
                "resolution": res.to_dict(),
                "normalized": normalized,
                "raw_sources": {
                    "profile": profile,
                    "officers": officers,
                    "psc": psc,
                    "filing_history": history,
                    "charges": charges,
                    "insolvency": insolvency
                }
            }

            return final_enriched_data
        return {"status": res.status, "resolution": res.to_dict()}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

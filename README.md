# Company Domain Crawler

An OSINT tool that extracts verified company identity data from any website domain. It discovers URLs automatically, reads page content via the **Jina AI Reader API**, and enriches results with **UK Companies House** registry data and **Wikipedia** intelligence.

---

## How It Works

```
User enters domain (e.g. stripe.com)
        ↓
URL Discovery — robots.txt, sitemaps, BFS internal links
        ↓
Page Selection — scores and picks best pages (about, contact, legal...)
        ↓
Jina Reader API — reads each page via https://r.jina.ai/<url>
        ↓
Extraction — company name, address, phone, email, VAT, registration no.
        ↓
UK Companies House — official registry verification (UK domains only)
        ↓
Wikipedia — supplementary enrichment and cross-reference
        ↓
Final JSON + Live Web UI
```

---

## Features

- **Automatic URL discovery** — robots.txt, XML sitemaps, BFS internal link crawling
- **Smart page selection** — prioritises about, contact, legal, imprint pages
- **Jina AI Reader** — JavaScript-heavy sites work without a local browser
- **Multi-source extraction** — JSON-LD, HTML tables, meta tags, regex patterns
- **UK Companies House** — auto-detects UK companies and queries official registry
- **Wikipedia enrichment** — verified domain-matched supplementary data
- **Live dashboard** — real-time SSE streaming pipeline console
- **Crawl history** — every result saved as JSON in `output/history/`
- **Vercel ready** — no Chromium binary required

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone https://github.com/vikassaini-73/web-crawler.git
cd web-crawler
python -m venv .venv
.\.venv\Scripts\activate        # Windows
# source .venv/bin/activate     # Mac / Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
copy .env.example .env
```

```env
# Required for UK company registry lookup
COMPANIES_HOUSE_API_KEY=your_key_here

# Optional — get a free key at https://jina.ai/reader for higher rate limits
JINA_API_KEY=your_jina_key_here

# Optional overrides (defaults shown)
# JINA_READER_BASE_URL=https://r.jina.ai
# JINA_CONCURRENCY=3
```

> Get a free Companies House API key at https://developer.company-information.service.gov.uk/
> Get a free Jina API key at https://jina.ai/reader

---

## Usage

### Web Interface (recommended)

```bash
python src/web_app.py
```

Open [http://localhost:8080](http://localhost:8080) and enter any domain.

### CLI

```bash
python src/main.py stripe.com
```

---

## Project Structure

```
company_domain_crawler/
├── src/
│   ├── web_app.py          # FastAPI server, /crawl and /enrich endpoints, SSE
│   ├── pipeline.py         # Main orchestration — discovery → jina → extract → enrich
│   ├── jina_reader.py      # Jina AI Reader API client (page content fetching)
│   ├── url_discovery.py    # robots.txt, sitemap, BFS internal link discovery
│   ├── page_selector.py    # URL scoring and priority selection
│   ├── crawler.py          # PageContent container + HTML parser bridge
│   ├── parser.py           # BeautifulSoup HTML structure parser
│   ├── extractor.py        # Multi-source company data extraction engine
│   ├── models.py           # Pydantic data models (CompanyData, FieldEvidence)
│   ├── validator.py        # Email, phone, postcode, VAT, address validators
│   ├── wikipedia_fallback.py  # Wikipedia API enrichment
│   ├── telemetry.py        # Pipeline execution stats and logging
│   ├── templates/
│   │   └── index.html      # Web UI dashboard
│   └── uk/
│       ├── companies_house.py  # Companies House API client
│       ├── resolver.py         # Company resolution and scoring logic
│       └── mapper.py           # CH data normalisation
├── output/
│   └── history/            # Saved crawl results (JSON)
├── tests/
├── requirements.txt
├── vercel.json
├── .env.example
└── .gitignore
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `COMPANIES_HOUSE_API_KEY` | For UK lookups | — | UK Companies House API key |
| `JINA_API_KEY` | No | _(keyless)_ | Jina Reader key — higher rate limits |
| `JINA_READER_BASE_URL` | No | `https://r.jina.ai` | Override Jina endpoint |
| `JINA_CONCURRENCY` | No | `3` | Max simultaneous Jina requests |
| `HOST` | No | `0.0.0.0` | Server host |
| `PORT` | No | `8080` | Server port |

---

## Deployment

### Vercel

```bash
vercel deploy
```

No Chromium or browser binary required. The Vercel runtime uses only:
- `httpx` for HTTP requests
- Jina Reader API for page content
- `@vercel/python` serverless function

### Local production

```bash
uvicorn src.web_app:app --host 0.0.0.0 --port 8080
```

---

## Extracted Fields

The final JSON includes:

| Field | Source |
|---|---|
| `company_name`, `legal_name`, `brand_name` | Website + Companies House |
| `registration_number`, `vat_tax_number` | Website regex + Companies House |
| `full_address`, `city`, `postal_code`, `country` | Website + Companies House |
| `phone`, `email` | Website |
| `company_status`, `company_type`, `jurisdiction` | Companies House |
| `directors`, `management` | Companies House officers |
| `persons_with_significant_control` | Companies House PSC |
| `filing_history`, `charges`, `insolvency` | Companies House |
| `industry`, `parent_company`, `subsidiaries` | Wikipedia + Website |
| `data_completeness`, `identity_confidence` | Calculated |

---

## Tech Stack

- **Python 3.11+**
- **FastAPI** + **uvicorn** — web server and SSE streaming
- **httpx** — async HTTP (URL discovery + Jina requests)
- **BeautifulSoup4** + **lxml** — HTML parsing
- **Pydantic** — data models and validation
- **Jina AI Reader API** — page content fetching
- **tldextract** — domain parsing
- **rich** — terminal output
n# web-crawler-light

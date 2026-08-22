# Crawlee + spaCy Integration — What Changed

## 1. Jina Reader → Crawlee (new file: `src/crawlee_reader.py`)

**Why**: Jina Reader converted every page to markdown before returning it.
Markdown strips `<script type="application/ld+json">` blocks, meta tags
(`og:site_name`, etc.), and real `<table>` structure — exactly what
`extractor.py`'s highest-confidence methods (`extract_from_json_ld`
@ 0.95–0.98 confidence) need. So the best extraction method in the project
was effectively starved of input.

`CrawleeReader` fetches **raw HTML** directly using Crawlee's `HttpCrawler`
and feeds it straight into the existing `extract_page_elements()` parser —
same `PageContent` output shape as `JinaReader`, zero changes needed in
`extractor.py`, `parser.py`, or anywhere downstream.

**Bonus features you get from Crawlee that Jina didn't give you:**
- Automatic session rotation when a page returns 403/blocked (seen live in
  testing against github.com — one sub-page got blocked, Crawlee rotated
  session and retried automatically, rest of the crawl continued normally)
- No external API dependency, no rate limits, no cost
- Configurable concurrency via `CRAWLEE_CONCURRENCY` env var (default 5)

**Trade-off to know:** some sites serve a JS-challenge/anti-bot page to
plain HTTP requests (seen live against pypi.org in testing) — direct HTML
fetch can't get past that. If you hit sites like this often, add a
`PlaywrightCrawler`-based fallback (Crawlee supports this as a drop-in
alternate crawler class) for those specific domains.

**Swap point**: `src/pipeline.py` — `reader = JinaReader()` →
`reader = CrawleeReader()`. `jina_reader.py` is left in the codebase
untouched in case you want to fall back to it for specific sites.

## 2. spaCy fallback (added to `src/extractor.py`)

**Why**: The existing rule-based extraction (JSON-LD → HTML tables →
regex/meta text → address truncation) is very accurate when a site follows
common patterns, but returns nothing when a page is unusually structured.

**What was added**:
- `extract_from_spacy_fallback(self, pages)` — new method, uses spaCy's
  `en_core_web_sm` NER model to guess `company_name` from plain page text
  when nothing else worked
- Model loads lazily (only once, cached at module level) — no cost if the
  fallback is never triggered
- Confidence is deliberately set low (`0.30`), so it never overrides a real
  JSON-LD/meta/regex signal (those score `0.75–0.98`) — it only wins in
  `select_best_candidates()` when it's the *only* candidate
- Common tech-noise words (`API`, `CLI`, `HTML`, etc.) are filtered out
- `extract_all()` now only calls this fallback if no `company_name`
  candidate exists at all after all rule-based methods ran

Verified in isolated testing: given a page with no JSON-LD, no meta tags,
and no copyright line, the fallback correctly extracted `"Acme Robotics
Corp"` from plain descriptive text with `confidence=0.3`.

## 3. requirements.txt

Added:
```
crawlee>=1.9.0
spacy>=3.8.0
```

You'll also need to download the spaCy language model once:
```bash
python -m spacy download en_core_web_sm
```
(If that command fails in your environment, install the wheel directly:
`pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`)

## Tested end-to-end

Ran `python src/main.py github.com --max-pages 3` successfully:
- `company_name`: "GitHub" (source: `meta_og_site_name`, confidence 0.75)
- `legal_name`: "GitHub, Inc" (source: `legal_text_regex`, matched the
  copyright line — confidence 0.85)
- Pipeline completed with no crashes, one blocked sub-page was auto-retried
  via session rotation and skipped cleanly after exhausting retries

## Nothing else changed

`jina_reader.py`, `parser.py`, `models.py`, jurisdiction detection, registry
resolvers (UK/NY), Wikipedia fallback, telemetry, and the FastAPI web app
are all untouched. This was a targeted, additive change: one new fetching
module, one new fallback method, two new dependencies.

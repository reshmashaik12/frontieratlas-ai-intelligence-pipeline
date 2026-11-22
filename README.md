# FrontierAtlas AI Intelligence Pipeline

Async AI intelligence ingestion for startup and product candidates, research papers, 24-hour news, and jobs. Records retain their fetched source URLs; LLMs add only constrained classifications and do not create source records or fill missing facts.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev,browser]"
py -m playwright install chromium
copy .env.example .env
py -m pytest
py -m src.main --limit 1000
py -m src.main --validate-only
py -m src.scale_benchmark --records 500000
```

Configure API keys in `.env`. A GitHub token improves repository metric reliability and rate limits.

Regenerate `architecture.pdf` after editing `ARCHITECTURE.md` with `py -m pip install -e ".[docs]"` followed by `py -m src.make_architecture_pdf`.

## Implemented

- Async aiohttp crawling with bounded concurrency; LLM providers use retry/backoff policies
- RSS/Atom news and job collection with strict 24-hour filtering
- arXiv paper collection and bounded-concurrency GitHub REST API star resolution
- SQLite deduplicated storage with URL/content fingerprints
- Canonical startup alias resolution
- Optional Gemini Flash → Groq → DeepSeek extraction/classification chain
- Adaptive payload reduction on HTTP 413 and Retry-After-aware 429 retries
- Optional Playwright rendering for explicitly configured JS-rendered sources
- CSV export compatible with Google Sheets import
- Pre-submission audit for row thresholds, nested schemas, provenance URLs, and 24-hour freshness

## Data boundaries

Startup and product records are derived from public GitHub repository metadata, not verified company directories; treat GitHub owners and repositories as candidates only. The current collector labels public GitHub repositories with `pricingModel=FREE`, which is not independently verified commercial pricing. A GitHub token in `.env` is recommended because unauthenticated API quota is limited. Papers with absent or deleted repositories retain a null star count rather than an invented value.

Run `py -m src.main --validate-only` before submission. It reports structural/provenance checks and thresholds; it cannot prove that every source's claims are true or that an external URL remains live. Manually verify the GitHub-derived startup/product candidates before describing them as verified companies/products.

The scale benchmark uses temporary SQLite storage and reserved `.invalid` URLs; it does not add synthetic rows to the database or exports used by ingestion. A local 500,000-row run completed in 47.07 seconds (about 10,622 rows/sec), measuring database writes only—not source acquisition or end-to-end 500k ingestion. The five current job sources are public Greenhouse board APIs configured in `config/sources.yaml`; the live snapshot can still vary with provider availability and posting dates.

The LLM chain runs only when at least one provider API key is configured. It enriches news topics and job classifications only when the returned label is supported by source text; original source fields, URLs, and publication dates remain authoritative. Without API keys the pipeline uses deterministic fields and logs that LLM enrichment was skipped.

For explicitly configured JS-rendered pages, the optional Playwright adapter renders the page and parses `JobPosting` JSON-LD. It detects access challenges and stops; it does not evade CAPTCHA, Cloudflare, rate limits, or other access controls. Install the browser extra and Chromium as shown above. RSS/official API adapters remain preferred.

## Scaling

Run workers behind a queue such as RabbitMQ/Kafka/SQS. Partition URLs by source/hash, use Postgres for primary records and a Redis/Postgres-based idempotency set, and use Neo4j or a graph extension for entity relationships. Store embeddings in pgvector or a managed vector index.

## LLM integration

Set one or more of `GEMINI_API_KEY`, `GROQ_API_KEY`, or `DEEPSEEK_API_KEY` in `.env`. Model IDs can be overridden with `GEMINI_MODEL`, `GROQ_MODEL`, and `DEEPSEEK_MODEL`; defaults use currently configured model IDs. `LLM_DISABLED_PROVIDERS=gemini,deepseek` can skip providers temporarily. Payloads are semantically chunked and HTTP 413 causes chunk-size reduction; HTTP 429 honors `Retry-After` when supplied and otherwise uses exponential backoff with jitter before moving to the next provider. Repeated unavailable providers are cooled down for the remainder of an orchestrator run. Account balance and service availability remain account-dependent.

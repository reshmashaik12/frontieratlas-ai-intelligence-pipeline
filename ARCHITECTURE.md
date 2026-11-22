# FrontierAtlas Technical Architecture

## 1. Scale strategy

**Implemented:** a single asyncio process uses aiohttp for RSS, arXiv, and GitHub REST API sources. GitHub metrics requests have bounded concurrency. An optional Playwright adapter is configured per source for JS-rendered pages and extracts schema.org `JobPosting` JSON-LD.

**Scale path:** stateless asyncio or Playwright workers consume source/discovery tasks from Kafka, SQS, or RabbitMQ. Partition by source and URL hash; isolate API and browser-heavy workers. The current SQLite store is for a single-node trial, not a distributed 500k deployment.

**Write plane:** the local implementation uses SQLite fingerprints and unique source URLs, with bounded 1,000-row transactions so input iterables need not be materialized in memory. At scale, move idempotent writes to Postgres and keep database uniqueness authoritative; daily Parquet exports can feed dashboards and backfills.

**500k scale:** `python -m src.scale_benchmark --records 500000` measured 500,000 isolated synthetic SQLite writes in 47.07 seconds (about 10,622 rows/sec) on the development machine. It uses a temporary database and does not demonstrate source acquisition. Real 500k acquisition still needs paginated source APIs, persisted cursors, queue workers, and load tests against actual provider limits. Keep raw source payloads immutable and canonicalization idempotent.

The current job configuration uses five public Greenhouse board APIs. Their availability and fresh-posting counts vary; only source-dated postings inside the 24-hour window are retained.

## 2. LLM resilience

When API keys are configured, the integrated chain tries Gemini Flash, Groq, then DeepSeek. Model IDs are configurable; defaults reflect IDs discovered in the configured accounts. Text is chunked to 24k characters with overlap. HTTP 413 halves the chunk size down to 1k and retries; HTTP 429 uses `Retry-After` when present, otherwise exponential backoff with jitter. Repeated 401/402/403/404 or 5xx failures disable or cool down that provider for the run, then fail over. News topics and job classifications are only accepted when the labels are supported by literal source text and their declared JSON schema. Without keys, LLM enrichment is explicitly skipped. Provider account balance and live availability are account-dependent.

## 3. Freshness and deduplication

The current feed adapters require parseable RSS/Atom publication dates; the browser job adapter requires JSON-LD `datePosted`. Missing or old dates are rejected rather than guessed. UTC normalization and a 24-hour cutoff are applied before records are exported. SQLite fingerprints and unique source URLs prevent duplicate records locally; distributed nodes should use a shared database uniqueness constraint.

## 4. Storage and graph model

For production, Postgres is the primary system of record, pgvector provides embeddings, and Neo4j or a graph projection stores organization/product/paper relationships. Redis can coordinate rate limits and short-lived leases; object storage can retain raw HTML. The submitted implementation currently uses SQLite and CSV, so these distributed stores are scale recommendations, not implemented services.

## 5. Anti-bot strategy

Prefer official APIs and feeds, respect source terms and rate limits, and use Playwright only for sources explicitly configured for rendering. The browser adapter uses a normal headless Chromium session, detects 401/403/429/503 and common challenge pages, and stops without evasion. It does not rotate identities or proxies, solve CAPTCHAs, or bypass Cloudflare/Datadome. Challenge detection is covered by local tests; live protected sites require authorized access and are not bypass-tested.

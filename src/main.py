import asyncio, json, os, argparse, logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import yaml
from datetime import timedelta

from .storage import Storage
from .feed_crawler import crawl_feeds
from .arxiv import crawl_arxiv
from .github_directory import GitHubDirectoryCollector
from .job_crawler import crawl_job_feeds
from .export import export_csv
from .entity_resolution import EntityResolver
from .llm import (
    LLMOrchestrator,
    enrich_job_classifications,
    enrich_news_topics,
)
from .validation import validate_submission

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

async def run(limit=1000):
    storage = Storage(os.getenv("DATABASE_URL", "data/frontier.db"))
    await storage.initialize()
    await storage.normalize_startup_records()
    run_time = datetime.now(timezone.utc)
    cutoff = (run_time - timedelta(hours=24)).isoformat()
    await storage.purge_older_than("NEWS", cutoff)
    await storage.purge_older_than("JOB", cutoff)

    with open("config/sources.yaml", "r", encoding="utf-8") as source_file:
        source_config = yaml.safe_load(source_file)

    news = await crawl_feeds(source_config["news"], "NEWS")
    jobs = await crawl_job_feeds(source_config["jobs"])
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        logger.warning(
            "GITHUB_TOKEN is not configured; GitHub's unauthenticated API quota may leave some star metrics unavailable"
        )
    if LLMOrchestrator.is_configured():
        orchestrator = LLMOrchestrator()
        news = await enrich_news_topics(news, orchestrator)
        jobs = await enrich_job_classifications(jobs, orchestrator)
    else:
        logger.info("No LLM API key configured; retaining deterministic source-derived classifications")
    papers = await crawl_arxiv(limit=limit, github_token=github_token)
    directory = GitHubDirectoryCollector(github_token)
    products = await directory.search_repositories("topic:artificial-intelligence stars:>100", limit)
    startups = await directory.startup_records(limit)

    for kind, records in (("NEWS", news), ("JOB", jobs)):
        await storage.put_many([
            (
                kind,
                record["source"]["url"],
                json.dumps(record, ensure_ascii=False),
                record["source"]["name"],
                record.get("collectedAt", run_time.isoformat()),
            )
            for record in records
        ])
    await storage.put_many([
        (
            "RESEARCH_PAPER",
            record["content"]["paper_url"],
            json.dumps(record, ensure_ascii=False),
            "arXiv",
            record.get("collectedAt", run_time.isoformat()),
        )
        for record in papers
    ])
    resolver = EntityResolver("config/canonical_entities.json")
    mappings = []
    startup_rows = []
    for record in startups:
        raw = record["content"]["entityName"]
        canonical = resolver.resolve(raw)
        record["content"]["entityName"] = canonical
        mappings.append((raw, canonical, record["source"]["url"]))
        startup_rows.append(
            (
                "STARTUP",
                record["source"]["url"] + "#startup:" + raw,
                json.dumps(record, ensure_ascii=False),
                record["source"]["name"],
                record.get("collectedAt", run_time.isoformat()),
            )
        )
    await storage.put_many(startup_rows)
    product_rows = []
    for record in products:
        raw = record["content"]["startupName"]
        canonical = resolver.resolve(raw)
        record["content"]["startupName"] = canonical
        mappings.append((raw, canonical, record["source"]["url"]))
        product_rows.append(
            (
                "PRODUCT",
                record["source"]["url"],
                json.dumps(record, ensure_ascii=False),
                record["source"]["name"],
                record.get("collectedAt", run_time.isoformat()),
            )
        )
    await storage.put_many(product_rows)
    await storage.put_entity_mappings(mappings)

    entity_mapping_rows = await storage.export_entity_mappings()
    export_csv("data/exports", "entity-mapping-log", entity_mapping_rows)

    for kind in ("STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"):
        rows = await storage.export_rows(kind)
        export_csv("data/exports", kind.lower().replace("_", "-"), rows)

    report = validate_submission(now=datetime.now(timezone.utc))
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise RuntimeError("Submission validation failed; see issues above")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        report = validate_submission()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["passed"] else 1)
    if args.limit < 1:
        parser.error("--limit must be greater than zero")
    asyncio.run(run(args.limit))

import ast
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .freshness import parse_timestamp, within_24h


EXPORTS = {
    "startup.csv": ("STARTUP",),
    "product.csv": ("PRODUCT",),
    "research-paper.csv": ("RESEARCH_PAPER",),
    "job.csv": ("JOB",),
    "news.csv": ("NEWS",),
}
REQUIRED_CONTENT = {
    "startup.csv": ("entityName", "data"),
    "product.csv": ("startupName", "productName", "pricingModel"),
    "research-paper.csv": (
        "title",
        "authors",
        "paper_url",
        "github_url",
        "github_stars",
        "published_date",
    ),
    "job.csv": ("company", "date", "is_remote", "role_family"),
    "news.csv": ("title", "date", "text"),
}
PRICING_MODELS = {"FREE", "FREEMIUM", "PAID", "ENTERPRISE"}


def parse_nested(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ast.literal_eval(value)


def _valid_http_url(value):
    parsed = urlsplit(value or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate_submission(
    exports_dir="data/exports",
    sources_path="config/sources.yaml",
    minimum_records=1000,
    now=None,
):
    now = now or datetime.now(timezone.utc)
    export_dir = Path(exports_dir)
    issues = []
    summary = {}

    for filename, expected_types in EXPORTS.items():
        path = export_dir / filename
        if not path.is_file():
            issues.append(f"Missing export: {filename}")
            summary[filename] = {"rows": 0}
            continue

        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        invalid_sources = 0
        duplicate_sources = 0
        seen_sources = set()
        invalid_schemas = 0
        stale = 0
        github_links = 0
        populated_stars = 0
        for row in rows:
            try:
                record = parse_nested(row.get("content", "{}"))
                source = parse_nested(row.get("source", "{}"))
            except (ValueError, SyntaxError, TypeError):
                invalid_schemas += 1
                continue
            if not isinstance(record, dict) or not isinstance(source, dict):
                invalid_schemas += 1
                continue
            if row.get("recordType") not in expected_types or row.get("schemaVersion") != "1.0":
                invalid_schemas += 1
            collected_at = parse_timestamp(row.get("collectedAt"))
            if not source.get("name") or not collected_at:
                invalid_schemas += 1
            if any(field not in record for field in REQUIRED_CONTENT[filename]):
                invalid_schemas += 1
            if filename == "startup.csv":
                data = record.get("data")
                if not isinstance(data, dict):
                    invalid_schemas += 1
                elif "employeeCount" in data and (
                    not isinstance(data["employeeCount"], int)
                    or isinstance(data["employeeCount"], bool)
                    or data["employeeCount"] < 0
                ):
                    invalid_schemas += 1
            if filename == "research-paper.csv":
                if not isinstance(record.get("authors"), list):
                    invalid_schemas += 1
                if not _valid_http_url(record.get("paper_url")):
                    invalid_schemas += 1
                github_url = record.get("github_url")
                if github_url is not None and not _valid_http_url(github_url):
                    invalid_schemas += 1
                stars = record.get("github_stars")
                if stars is not None and (
                    not isinstance(stars, int) or isinstance(stars, bool) or stars < 0
                ):
                    invalid_schemas += 1
            if filename == "product.csv" and record.get("pricingModel") not in PRICING_MODELS:
                invalid_schemas += 1
            if filename == "job.csv" and not isinstance(record.get("is_remote"), bool):
                invalid_schemas += 1
            source_url = source.get("url")
            if not _valid_http_url(source_url):
                invalid_sources += 1
            elif source_url in seen_sources:
                duplicate_sources += 1
            else:
                seen_sources.add(source_url)

            if filename in ("news.csv", "job.csv"):
                published = parse_timestamp(record.get("date"))
                if not published or not within_24h(published.isoformat(), now):
                    stale += 1
            if filename == "research-paper.csv":
                if record.get("github_url"):
                    github_links += 1
                if isinstance(record.get("github_stars"), int):
                    populated_stars += 1

        summary[filename] = {
            "rows": len(rows),
            "unique_source_urls": len(seen_sources),
            "invalid_source_urls": invalid_sources,
            "duplicate_source_urls": duplicate_sources,
            "schema_issues": invalid_schemas,
            "stale_records": stale,
        }
        if filename in ("startup.csv", "product.csv", "research-paper.csv") and len(rows) < minimum_records:
            issues.append(f"{filename} has {len(rows)} rows; requires {minimum_records}")
        if invalid_sources or invalid_schemas:
            issues.append(f"{filename} contains invalid source/schema fields")
        if duplicate_sources:
            issues.append(f"{filename} contains duplicate source URLs")
        if stale:
            issues.append(f"{filename} contains records outside the 24-hour window")
        if filename == "research-paper.csv":
            summary[filename]["papers_with_github_urls"] = github_links
            summary[filename]["papers_with_star_metrics"] = populated_stars

    mapping_path = export_dir / "entity-mapping-log.csv"
    if not mapping_path.is_file():
        issues.append("Missing export: entity-mapping-log.csv")
        summary["entity-mapping-log.csv"] = {"rows": 0}
    else:
        with mapping_path.open(newline="", encoding="utf-8") as stream:
            mappings = list(csv.DictReader(stream))
        required = {"rawName", "canonicalName", "sourceUrl"}
        invalid = sum(
            not required.issubset(row)
            or not row.get("rawName")
            or not row.get("canonicalName")
            or not _valid_http_url(row.get("sourceUrl"))
            for row in mappings
        )
        summary["entity-mapping-log.csv"] = {
            "rows": len(mappings),
            "invalid_rows": invalid,
        }
        if invalid:
            issues.append("Entity mapping log contains invalid rows")

    source_path = Path(sources_path)
    if not source_path.is_file():
        issues.append(f"Missing source configuration: {sources_path}")
    else:
        with source_path.open(encoding="utf-8") as stream:
            configured = yaml.safe_load(stream) or {}
        news_sources = configured.get("news", [])
        job_sources = configured.get("jobs", [])
        summary["configured_sources"] = {
            "news": len(news_sources),
            "jobs": len(job_sources),
        }
        if len(news_sources) < 5 or len(job_sources) < 5:
            issues.append("Configure at least five news sources and five job sources")

    return {
        "passed": not issues,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "summary": summary,
        "issues": issues,
        "warnings": [
            "GitHub-derived startup and product labels are candidates, not independently verified companies or commercial products.",
            "A source URL is provenance evidence; this validator does not confirm that every external page remains live.",
        ],
    }


if __name__ == "__main__":
    report = validate_submission()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)

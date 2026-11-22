import csv
import json
from datetime import datetime, timezone, timedelta

from src.validation import validate_submission


def write_rows(path, rows):
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_submission_validator_checks_thresholds_sources_and_freshness(tmp_path):
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    fresh_date = (now - timedelta(hours=2)).isoformat()
    collected_at = now.isoformat()
    source = {"name": "Source", "url": "https://example.com/item"}
    exports = {
        "startup.csv": [{
            "recordType": "STARTUP", "schemaVersion": "1.0",
            "source": json.dumps(source),
            "content": json.dumps({"entityName": "Example", "data": {}}),
            "collectedAt": collected_at,
        }],
        "product.csv": [{
            "recordType": "PRODUCT", "schemaVersion": "1.0",
            "source": json.dumps(source), "content": json.dumps({
                "startupName": "Example", "productName": "Tool", "pricingModel": "FREE",
            }),
            "collectedAt": collected_at,
        }],
        "research-paper.csv": [{
            "recordType": "RESEARCH_PAPER", "schemaVersion": "1.0",
            "source": json.dumps(source), "content": json.dumps({
                "title": "Paper", "authors": [], "paper_url": source["url"],
                "github_url": None, "github_stars": None, "published_date": fresh_date,
            }),
            "collectedAt": collected_at,
        }],
        "news.csv": [{
            "recordType": "NEWS", "schemaVersion": "1.0",
            "source": json.dumps(source), "content": json.dumps({
                "title": "News", "date": fresh_date, "text": "Story",
            }),
            "collectedAt": collected_at,
        }],
        "job.csv": [{
            "recordType": "JOB", "schemaVersion": "1.0",
            "source": json.dumps(source), "content": json.dumps({
                "company": "Example", "date": fresh_date,
                "is_remote": False, "role_family": "Engineering",
            }),
            "collectedAt": collected_at,
        }],
    }
    for filename, rows in exports.items():
        write_rows(tmp_path / filename, rows)
    write_rows(tmp_path / "entity-mapping-log.csv", [{
        "rawName": "Example", "canonicalName": "Example", "sourceUrl": source["url"],
    }])

    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        "news:\n" + "".join(f"  - name: News {i}\n    url: https://news{i}.example/feed\n" for i in range(5))
        + "jobs:\n" + "".join(f"  - name: Jobs {i}\n    url: https://jobs{i}.example/feed\n" for i in range(5)),
        encoding="utf-8",
    )

    report = validate_submission(tmp_path, sources_path, minimum_records=1, now=now)
    assert report["passed"] is True
    assert report["summary"]["configured_sources"] == {"news": 5, "jobs": 5}

    exports["news.csv"][0]["content"] = json.dumps({
        "title": "News",
        "date": (now - timedelta(days=2)).isoformat(),
        "text": "Story",
    })
    write_rows(tmp_path / "news.csv", exports["news.csv"])
    report = validate_submission(tmp_path, sources_path, minimum_records=1, now=now)
    assert report["passed"] is False
    assert any("24-hour" in issue for issue in report["issues"])

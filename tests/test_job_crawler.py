import asyncio
import json
from datetime import datetime, timezone

from src import job_crawler


def test_greenhouse_api_uses_configured_company_and_source_job_url(monkeypatch):
    now = datetime.now(timezone.utc)
    payload = {
        "jobs": [{
            "title": "Machine Learning Engineer",
            "absolute_url": "https://boards.greenhouse.io/togetherai/jobs/123",
            "updated_at": now.isoformat(),
            "content": "<p>Build reliable AI systems.</p>",
            "location": {"name": "Remote"},
        }]
    }

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def text(self):
            return json.dumps(payload)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, timeout):
            return Response()

    monkeypatch.setattr(job_crawler.aiohttp, "ClientSession", Session)
    records = asyncio.run(job_crawler.crawl_job_feeds([{
        "name": "Together AI Jobs",
        "company": "Together AI",
        "url": "https://boards-api.greenhouse.io/v1/boards/togetherai/jobs",
    }]))

    assert len(records) == 1
    assert records[0]["content"]["company"] == "Together AI"
    assert records[0]["source"]["url"] == payload["jobs"][0]["absolute_url"]
    assert records[0]["collectedAt"]

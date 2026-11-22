import asyncio
import aiohttp
from urllib.parse import quote
from datetime import datetime, timezone


class GitHubDirectoryCollector:
    """Source-backed startup/product directory derived from GitHub public API."""

    def __init__(self, token=None, concurrency=8):
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "FrontierAtlas-Ingestion/1.0"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.semaphore = asyncio.Semaphore(concurrency)

    async def search_repositories(self, query: str, limit: int):
        records = []
        async with aiohttp.ClientSession(headers=self.headers) as session:
            page = 1
            while len(records) < limit:
                per_page = min(100, limit - len(records))
                url = f"https://api.github.com/search/repositories?q={quote(query)}&sort=stars&order=desc&per_page={per_page}&page={page}"
                async with self.semaphore:
                    async with session.get(url, timeout=30) as response:
                        if response.status == 403:
                            break
                        response.raise_for_status()
                        payload = await response.json()
                items = payload.get("items", [])
                if not items:
                    break
                for item in items:
                    owner = item.get("owner") or {}
                    owner_name = owner.get("login")
                    owner_url = owner.get("html_url")
                    repo_url = item.get("html_url")
                    records.append({
                        "schemaVersion": "1.0",
                        "recordType": "PRODUCT",
                        "source": {"name": "GitHub", "url": repo_url},
                        "content": {
                            "startupName": owner_name,
                            "productName": item.get("name"),
                            "pricingModel": "FREE",
                            "githubStars": item.get("stargazers_count"),
                            "description": item.get("description"),
                        },
                        "collectedAt": datetime.now(timezone.utc).isoformat(),
                    })
                page += 1
                await asyncio.sleep(0.25)
        return records

    async def startup_records(self, limit: int, token=None):
        queries = (
            "topic:artificial-intelligence stars:>100",
            "topic:machine-learning stars:>100",
            "topic:llm stars:>100",
            "topic:generative-ai stars:>100",
        )
        groups = await asyncio.gather(*(self.search_repositories(query, max(limit, 300)) for query in queries))
        seen = set()
        startups = []
        for products in groups:
            for product in products:
                name = product["content"].get("startupName")
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                owner_url = "/".join(product["source"]["url"].split("/")[:5])
                startups.append({
                    "schemaVersion": "1.0",
                    "recordType": "STARTUP",
                    "source": {"name": "GitHub", "url": owner_url},
                    "content": {
                        "entityName": name,
                        "data": {"productSourceUrl": product["source"]["url"]},
                    },
                    "collectedAt": datetime.now(timezone.utc).isoformat(),
                })
                if len(startups) >= limit:
                    return startups
        return startups

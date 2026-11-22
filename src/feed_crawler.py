import asyncio, aiohttp, feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from .freshness import parse_timestamp, within_24h


async def crawl_feeds(sources, record_type="NEWS"):
    records = []
    async with aiohttp.ClientSession() as session:
        async def one(source):
            try:
                async with session.get(source["url"], timeout=30) as response:
                    if response.status != 200:
                        return []
                    xml = await response.text()
            except Exception:
                return []
            feed = feedparser.parse(xml)
            found = []
            for entry in feed.entries:
                published = entry.get("published") or entry.get("updated")
                published_at = parse_timestamp(published)
                if not within_24h(published_at.isoformat() if published_at else None):
                    continue
                url = entry.get("link")
                if not url:
                    continue
                try:
                    async with session.get(url, timeout=30) as response:
                        html = await response.text() if response.status == 200 else ""
                except Exception:
                    html = ""
                soup = BeautifulSoup(html, "lxml") if html else None
                text = soup.get_text(" ", strip=True)[:50_000] if soup else entry.get("summary", "")
                found.append({"schemaVersion":"1.0","recordType":record_type,
                              "source":{"name":source["name"],"url":url},
                              "content":{"title":entry.get("title",""),
                                         "date":published_at.isoformat(),
                                         "text":text,"published_url":url},
                              "collectedAt":datetime.now(timezone.utc).isoformat()})
            return found
        groups = await asyncio.gather(*(one(source) for source in sources))
        for group in groups:
            records.extend(group)
    return records

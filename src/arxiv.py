import asyncio, aiohttp, feedparser
import re
from urllib.parse import urlencode
from datetime import datetime, timezone
from dateutil import parser as date_parser
from .research import GitHubMetrics


def github_repository_url(entry) -> str | None:
    candidates = [link.href for link in entry.links if "github.com" in link.href.lower()]
    candidates.extend(
        re.findall(
            r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            entry.summary,
            flags=re.IGNORECASE,
        )
    )
    for candidate in candidates:
        candidate = candidate.strip(" \t\r\n<>()[]{}'\".,;")
        if GitHubMetrics.repository_api_url(candidate):
            return candidate
    return None


async def crawl_arxiv(limit=1200, github_token=None):
    github = GitHubMetrics(github_token)
    records = []
    async with aiohttp.ClientSession() as session:
        start = 0
        while len(records) < limit:
            page_size = min(200, limit - len(records))
            query = urlencode({"search_query":"cat:cs.AI OR cat:cs.LG", "start":start,
                               "max_results":page_size, "sortBy":"submittedDate", "sortOrder":"descending"})
            url = f"https://export.arxiv.org/api/query?{query}"
            async with session.get(url, timeout=60) as response:
                response.raise_for_status()
                text = await response.text()
            feed = feedparser.parse(text)
            if not feed.entries:
                break
            page_records = []
            repo_urls = []
            for entry in feed.entries:
                github_url = github_repository_url(entry)
                published_at = date_parser.parse(entry.published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                page_records.append({"schemaVersion":"1.0","recordType":"RESEARCH_PAPER",
                                "source":{"name":"arXiv","url":entry.link},
                                "content":{"title":entry.title.strip(),
                                           "authors":[a.name for a in entry.authors],
                                           "paper_url":entry.link,
                                           "github_url":github_url,
                                           "github_stars":None,
                                           "published_date":published_at.astimezone(timezone.utc).isoformat()},
                                "collectedAt":datetime.now(timezone.utc).isoformat()})
                repo_urls.append(github_url)
            metrics = await asyncio.gather(
                *(github.stars(repo_url, session) if repo_url else asyncio.sleep(0, result=None)
                  for repo_url in repo_urls)
            )
            for record, stars in zip(page_records, metrics):
                record["content"]["github_stars"] = stars
                records.append(record)
            start += len(feed.entries)
    return records[:limit]

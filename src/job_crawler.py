import asyncio
import aiohttp
import feedparser
import json
import logging
from bs4 import BeautifulSoup
from dateutil import parser
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from .browser import AccessChallengeError, render_page
from .freshness import within_24h


logger = logging.getLogger(__name__)


def _walk_jsonld(value):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_jsonld(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)


def parse_rendered_job_postings(html, source, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    soup = BeautifulSoup(html, "lxml")
    records = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_jsonld(payload):
            item_type = item.get("@type", [])
            if isinstance(item_type, str):
                item_type = [item_type]
            if not any(str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1] == "JobPosting"
                       for value in item_type):
                continue
            try:
                date = parser.parse(item["datePosted"]) if item.get("datePosted") else None
            except (ValueError, TypeError, OverflowError):
                logger.info("Skipping JobPosting with invalid datePosted from %s", source["url"])
                continue
            if not date:
                continue
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            date = date.astimezone(timezone.utc)
            if date < cutoff or date > now + timedelta(minutes=5):
                continue

            organization = item.get("hiringOrganization") or {}
            company = organization.get("name")
            job_url = urljoin(source["url"], item.get("url") or source["url"])
            if not company or not job_url:
                continue
            description = BeautifulSoup(item.get("description", ""), "lxml").get_text(" ", strip=True)
            location_type = str(item.get("jobLocationType") or "").upper()
            location = item.get("jobLocation") or []
            if isinstance(location, dict):
                location = [location]
            locations = []
            for place in location:
                address = place.get("address") or {}
                name = ", ".join(
                    part for part in (
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    ) if part
                )
                if name:
                    locations.append(name)
            content_text = f"{item.get('title', '')} {description} {' '.join(locations)}"
            is_remote = (
                "TELECOMMUTE" in location_type
                or "remote" in content_text.lower()
                or "work from home" in content_text.lower()
            )
            title = item.get("title", "")
            lowered_title = title.lower()
            role_family = (
                "Engineering"
                if any(term in lowered_title for term in ("engineer", "developer", "software"))
                else "Research"
                if any(term in lowered_title for term in ("research", "scientist"))
                else "Other"
            )
            records.append({
                "schemaVersion": "1.0",
                "recordType": "JOB",
                "source": {"name": source["name"], "url": job_url},
                "content": {
                    "company": company,
                    "date": date.isoformat(),
                    "is_remote": is_remote,
                    "role_family": role_family,
                    "title": title,
                    "location": "; ".join(locations),
                    "text": description[:50000],
                },
                "collectedAt": now.isoformat(),
            })
    return records


async def crawl_job_feeds(sources, limit=5000):
    records = []
    async with aiohttp.ClientSession() as session:
        async def one(source):
            found = []
            if source.get("render_js"):
                try:
                    html = await render_page(source["url"])
                    return parse_rendered_job_postings(html, source)
                except AccessChallengeError as exc:
                    logger.warning("%s", exc)
                    return found
                except Exception:
                    logger.exception("Rendered job source failed: %s", source["url"])
                    return found
            try:
                async with session.get(source["url"], timeout=30) as response:
                    if response.status != 200:
                        logger.warning("Job source returned HTTP %s: %s", response.status, source["url"])
                        return found
                    text = await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.exception("Job source request failed: %s", source["url"])
                return found

            if "boards-api.greenhouse.io" in source["url"]:
                import json
                try:
                    payload = json.loads(text)
                    entries = [{
                        "title": job.get("title", ""),
                        "link": job.get("absolute_url"),
                        "published": job.get("updated_at") or job.get("first_published"),
                        "summary": job.get("content", ""),
                        "location": ((job.get("location") or {}).get("name", "")),
                    } for job in payload.get("jobs", [])]
                except Exception:
                    entries = []
            else:
                feed = feedparser.parse(text)
                entries = [{"title": e.get("title", ""), "link": e.get("link"),
                            "published": e.get("published") or e.get("updated"),
                            "summary": e.get("summary", ""),
                            "location": e.get("remoteok_location") or ""} for e in feed.entries]

            for entry in entries:
                if not entry["link"]:
                    continue
                published = entry["published"]
                try:
                    date = parser.parse(published) if published else None
                    if date and date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    if not date or not within_24h(date.isoformat()):
                        continue
                except Exception:
                    continue

                soup = BeautifulSoup(entry["summary"] or "", "lxml")
                text_content = soup.get_text(" ", strip=True)
                location = str(entry["location"] or "").lower()
                is_remote = "remote" in location or "anywhere" in location
                found.append({
                    "schemaVersion": "1.0",
                    "recordType": "JOB",
                    "source": {"name": source["name"], "url": entry["link"]},
                    "content": {
                        "company": source.get("company")
                        or source["name"].removesuffix(" Jobs"),
                        "date": date.isoformat(),
                        "is_remote": is_remote,
                        "role_family": "Engineering" if any(k in entry["title"].lower() for k in ("engineer", "developer", "research")) else "Other",
                        "title": entry["title"],
                        "location": entry["location"],
                        "text": text_content[:50000],
                    },
                    "collectedAt": datetime.now(timezone.utc).isoformat(),
                })
            return found

        groups = await asyncio.gather(*(one(source) for source in sources))
        for group in groups:
            records.extend(group)
            if len(records) >= limit:
                break
    return records[:limit]

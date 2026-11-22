import asyncio
import logging
from typing import Optional
from urllib.parse import urlsplit

import aiohttp


logger = logging.getLogger(__name__)


class GitHubMetrics:
    def __init__(self, token: Optional[str] = None, concurrency: int = 4):
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "FrontierAtlas-Ingestion/1.0",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.semaphore = asyncio.Semaphore(concurrency)
        self._cache: dict[str, Optional[int]] = {}
        self._inflight: dict[str, asyncio.Task[Optional[int]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def repository_api_url(repo_url: str) -> Optional[str]:
        parsed = urlsplit(repo_url.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in ("github.com", "www.github.com"):
            return None

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None
        owner, repository = parts[:2]
        if repository.endswith(".git"):
            repository = repository[:-4]
        if not owner or not repository:
            return None
        return f"https://api.github.com/repos/{owner}/{repository}"

    async def stars(self, repo_url: str, session: aiohttp.ClientSession) -> Optional[int]:
        api_url = self.repository_api_url(repo_url)
        if not api_url:
            logger.warning("Skipping malformed or non-GitHub repository URL: %s", repo_url)
            return None

        async with self._lock:
            if api_url in self._cache:
                return self._cache[api_url]
            task = self._inflight.get(api_url)
            if task is None:
                task = asyncio.create_task(
                    self._fetch_stars(api_url, repo_url, session)
                )
                self._inflight[api_url] = task
        return await task

    async def _fetch_stars(
        self, api_url: str, repo_url: str, session: aiohttp.ClientSession
    ) -> Optional[int]:
        for attempt in range(3):
            try:
                async with self.semaphore:
                    async with session.get(
                        api_url,
                        headers=self.headers,
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as response:
                        if response.status == 200:
                            value = (await response.json()).get("stargazers_count")
                            result = value if isinstance(value, int) else None
                            await self._cache_result(api_url, result)
                            return result
                        if response.status in (403, 429):
                            remaining = response.headers.get("X-RateLimit-Remaining")
                            retry_after = response.headers.get("Retry-After")
                            if response.status == 429 and retry_after and attempt < 2:
                                try:
                                    delay = min(30.0, max(0.0, float(retry_after)))
                                except ValueError:
                                    delay = 1.0 * (2**attempt)
                                await asyncio.sleep(delay)
                                continue
                            reason = "rate limit exhausted" if remaining == "0" else "request denied"
                            logger.warning(
                                "GitHub metrics unavailable for %s (%s; HTTP %s)",
                                repo_url,
                                reason,
                                response.status,
                            )
                            await self._cache_result(api_url, None)
                            return None
                        if response.status == 404:
                            logger.info("GitHub repository no longer exists: %s", repo_url)
                            await self._cache_result(api_url, None)
                            return None
                        if response.status < 500 or attempt == 2:
                            logger.warning(
                                "GitHub metrics request failed for %s (HTTP %s)",
                                repo_url,
                                response.status,
                            )
                            await self._cache_result(api_url, None)
                            return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    logger.warning("GitHub metrics request failed for %s: %s", repo_url, exc)
                    await self._cache_result(api_url, None)
                    return None
            await asyncio.sleep(0.5 * (2**attempt))
        await self._cache_result(api_url, None)
        return None

    async def _cache_result(self, api_url: str, result: Optional[int]) -> None:
        async with self._lock:
            self._cache[api_url] = result
            self._inflight.pop(api_url, None)

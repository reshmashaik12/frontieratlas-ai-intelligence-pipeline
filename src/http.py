import aiohttp, asyncio
from tenacity import retry, stop_after_attempt, wait_random_exponential


class AsyncFetcher:
    def __init__(self, concurrency=20):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.headers = {"User-Agent": "FrontierAtlas-Research/1.0 (+source-preserving ingestion)"}

    @retry(stop=stop_after_attempt(5), wait=wait_random_exponential(min=1, max=30), reraise=True)
    async def get(self, session, url):
        async with self.semaphore:
            async with session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status in (403, 429, 503):
                    raise aiohttp.ClientResponseError(response.request_info, response.history,
                                                      status=response.status, message="anti-bot/rate-limit")
                response.raise_for_status()
                return await response.text()

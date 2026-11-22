import logging
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)


class AccessChallengeError(RuntimeError):
    pass


def access_challenge_reason(status: int | None, title: str, body: str) -> str | None:
    if status is not None and status >= 400:
        return f"HTTP {status}"
    sample = f"{title}\n{body[:12000]}".lower()
    markers = (
        "verify you are human",
        "checking your browser",
        "just a moment...",
        "captcha",
        "cf-chl-",
        "/cdn-cgi/challenge-platform",
        "datadome",
        "access denied",
    )
    return next((marker for marker in markers if marker in sample), None)


async def render_page(url: str, timeout_ms: int = 30_000) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Invalid browser URL: {url}")
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install the browser extra and Chromium first."
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightTimeoutError:
                await page.wait_for_timeout(1_000)
            status = response.status if response else None
            title = await page.title()
            body = await page.locator("body").inner_text(timeout=5000)
            challenge = access_challenge_reason(status, title, body)
            if challenge:
                raise AccessChallengeError(
                    f"Stopped browser ingestion for {parsed.hostname}: {challenge}"
                )
            return await page.content()
        finally:
            await browser.close()

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from datetime import datetime, timezone
from src.browser import AccessChallengeError, access_challenge_reason, render_page
from src.job_crawler import parse_rendered_job_postings


def test_access_challenge_is_detected_and_not_bypassed():
    assert access_challenge_reason(403, "", "") == "HTTP 403"
    assert access_challenge_reason(
        200, "Just a moment...", "Checking your browser"
    ) is not None
    assert access_challenge_reason(200, "Careers", "Job listings") is None


def test_rendered_job_parser_keeps_only_recent_source_dated_jobs():
    now = datetime.now(timezone.utc)
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Research Scientist",
      "datePosted": "2026-11-21T00:00:00Z",
      "url": "https://jobs.example/research",
      "hiringOrganization": {"name": "Example AI"},
      "jobLocationType": "TELECOMMUTE",
      "description": "<p>Build research systems.</p>"
    }
    </script>
    """
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    records = parse_rendered_job_postings(
        html, {"name": "Example", "url": "https://example.com/careers"}, now
    )
    assert len(records) == 1
    assert records[0]["content"]["company"] == "Example AI"
    assert records[0]["content"]["is_remote"] is True
    assert records[0]["source"]["url"] == "https://jobs.example/research"


def test_render_page_closes_browser_after_access_challenge(monkeypatch):
    class Page:
        async def goto(self, *args, **kwargs):
            return SimpleNamespace(status=403)

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        async def title(self):
            return "Access denied"

        def locator(self, selector):
            return SimpleNamespace(inner_text=lambda **kwargs: asyncio.sleep(0, result=""))

    class Browser:
        closed = False

        async def new_page(self):
            return Page()

        async def close(self):
            self.closed = True

    browser = Browser()

    class Chromium:
        async def launch(self, **kwargs):
            return browser

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return None

    fake_module = ModuleType("playwright.async_api")
    fake_module.async_playwright = lambda: Manager()
    fake_module.TimeoutError = asyncio.TimeoutError
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    with pytest.raises(AccessChallengeError):
        asyncio.run(render_page("https://jobs.example/careers"))
    assert browser.closed is True

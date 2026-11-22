import asyncio
from types import SimpleNamespace

from src import arxiv
from src.research import GitHubMetrics


def test_arxiv_crawl_attaches_live_github_stars(monkeypatch):
    entry = SimpleNamespace(
        links=[SimpleNamespace(href="https://github.com/example/repo")],
        summary="Reference implementation: https://github.com/example/repo",
        published="2026-11-21T10:00:00Z",
        title="A source-backed paper",
        authors=[SimpleNamespace(name="Author")],
        link="https://arxiv.org/abs/1234.56789",
    )
    monkeypatch.setattr(
        arxiv.feedparser,
        "parse",
        lambda _text: SimpleNamespace(entries=[entry]),
    )

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def text(self):
            return "<feed/>"

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return Response()

    class Metrics:
        repository_api_url = staticmethod(GitHubMetrics.repository_api_url)

        def __init__(self, token):
            assert token == "test-token"

        async def stars(self, repo_url, _session):
            assert repo_url == "https://github.com/example/repo"
            return 42

    monkeypatch.setattr(arxiv.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(arxiv, "GitHubMetrics", Metrics)
    records = asyncio.run(arxiv.crawl_arxiv(limit=1, github_token="test-token"))

    assert records[0]["content"]["github_url"] == "https://github.com/example/repo"
    assert records[0]["content"]["github_stars"] == 42
    assert records[0]["source"]["url"] == "https://arxiv.org/abs/1234.56789"


def test_arxiv_repository_url_extraction_trims_wrapping_punctuation():
    entry = SimpleNamespace(
        links=[],
        summary="Code: (https://github.com/example/repo).",
    )
    assert arxiv.github_repository_url(entry) == "https://github.com/example/repo"

import asyncio

from src.research import GitHubMetrics


def test_repository_api_url_normalizes_git_and_extra_path():
    assert GitHubMetrics.repository_api_url(
        "https://github.com/owner/repo.git/tree/main"
    ) == "https://api.github.com/repos/owner/repo"


def test_repository_api_url_rejects_non_github_hosts():
    assert GitHubMetrics.repository_api_url("https://example.com/owner/repo") is None


def test_stars_reads_official_api_metric():
    class Response:
        status = 200
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self):
            return {"stargazers_count": 321}

    class Session:
        def get(self, url, **kwargs):
            assert url == "https://api.github.com/repos/owner/repo"
            return Response()

    assert asyncio.run(
        GitHubMetrics().stars("https://github.com/owner/repo", Session())
    ) == 321

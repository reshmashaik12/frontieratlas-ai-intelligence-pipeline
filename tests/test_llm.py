import asyncio

import pytest

from src.llm import (
    LLMOrchestrator,
    PayloadTooLarge,
    ProviderHTTPError,
    RateLimited,
    enrich_job_classifications,
    enrich_news_topics,
    validate_json_schema,
)


def test_llm_falls_back_to_next_configured_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")

    class FakeOrchestrator(LLMOrchestrator):
        async def _call_provider(self, adapter, key, chunk, schema):
            if adapter == "gemini":
                raise RuntimeError("provider unavailable")
            return {"role_family": "Engineering"}

    result = asyncio.run(FakeOrchestrator().extract("Software Engineer", {}))
    assert result == {"role_family": "Engineering"}


def test_unavailable_provider_is_disabled_for_the_orchestrator(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")

    class FakeOrchestrator(LLMOrchestrator):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def _call_provider(self, adapter, key, chunk, schema):
            self.calls.append(adapter)
            if adapter == "gemini":
                raise ProviderHTTPError(adapter, 404, "model unavailable")
            return {"ok": True}

    orchestrator = FakeOrchestrator()
    assert asyncio.run(orchestrator.extract("source", {})) == {"ok": True}
    assert asyncio.run(orchestrator.extract("source", {})) == {"ok": True}
    assert orchestrator.calls.count("gemini") == 1


def test_llm_chain_skips_failed_providers_until_a_provider_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_DISABLED_PROVIDERS", "")
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(key, f"test-{key.lower()}")

    class FakeOrchestrator(LLMOrchestrator):
        async def _call_provider(self, adapter, key, chunk, schema):
            if adapter == "gemini":
                raise ProviderHTTPError(adapter, 503, "temporarily unavailable")
            if adapter == "groq":
                raise ProviderHTTPError(adapter, 402, "account unavailable")
            return {"ok": True}

    orchestrator = FakeOrchestrator()
    assert asyncio.run(orchestrator.extract("source", {})) == {"ok": True}
    assert orchestrator.disabled_providers == {"groq"}
    assert "gemini" in orchestrator.provider_cooldowns


def test_llm_rejects_output_not_matching_requested_schema():
    with pytest.raises(ValueError):
        validate_json_schema(
            {"role_family": "Invented"},
            {
                "type": "object",
                "properties": {"role_family": {"type": "string", "enum": ["Other"]}},
                "required": ["role_family"],
                "additionalProperties": False,
            },
        )


def test_llm_rechunks_after_413(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")

    class FakeOrchestrator(LLMOrchestrator):
        initial_chunk_chars = 24_000
        minimum_chunk_chars = 1_000

        async def _call_provider(self, adapter, key, chunk, schema):
            if len(chunk) > 12_000:
                raise PayloadTooLarge("too large")
            return {"text": "source-backed"}

    result = asyncio.run(
        FakeOrchestrator().extract("x" * 30_000, {"type": "object"})
    )
    assert result == {"text": "source-backed"}


def test_llm_retries_429_and_honors_retry_after(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    orchestrator = LLMOrchestrator()
    attempts = 0
    waits = []

    async def call_provider(adapter, key, chunk, schema):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimited("limited", retry_after=0)
        return {"ok": True}

    async def backoff(attempt, retry_after=None):
        waits.append(retry_after)

    orchestrator._call_provider = call_provider
    orchestrator._backoff = backoff
    assert asyncio.run(orchestrator.extract("source", {})) == {"ok": True}
    assert attempts == 2
    assert waits == [0]


def test_job_classification_only_accepts_source_supported_values():
    class FakeOrchestrator:
        async def extract(self, text, schema):
            return {"role_family": "Research", "is_remote": True}

    records = [{
        "source": {"url": "https://jobs.example/job"},
        "content": {
            "title": "Research Scientist",
            "location": "New York",
            "text": "Work in our New York office.",
            "role_family": "Other",
            "is_remote": False,
        },
    }]
    result = asyncio.run(enrich_job_classifications(records, FakeOrchestrator()))
    assert result[0]["content"]["role_family"] == "Research"
    assert result[0]["content"]["is_remote"] is False


def test_news_topics_are_rejected_without_source_evidence():
    class FakeOrchestrator:
        async def extract(self, text, schema):
            return {"topics": ["Research", "Funding"]}

    records = [{
        "source": {"url": "https://news.example/article"},
        "content": {
            "title": "New research paper released",
            "text": "The research team published a paper.",
        },
    }]
    result = asyncio.run(enrich_news_topics(records, FakeOrchestrator()))
    assert result[0]["content"]["topics"] == ["Research"]

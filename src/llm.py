import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from .chunking import semantic_chunks


logger = logging.getLogger(__name__)


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        for field in schema.get("required", []):
            if field not in value:
                raise ValueError(f"{path}.{field} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = value.keys() - properties.keys()
            if extras:
                raise ValueError(f"{path} contains unsupported fields: {sorted(extras)}")
        for field, field_schema in properties.items():
            if field in value and value[field] is not None:
                validate_json_schema(value[field], field_schema, f"{path}.{field}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, f"{path}[{index}]")
    elif expected == "string" and not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    elif expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{path} must be an integer")
    elif expected == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        raise ValueError(f"{path} must be a number")
    elif expected == "null" and value is not None:
        raise ValueError(f"{path} must be null")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed enum value")


class PayloadTooLarge(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderHTTPError(RuntimeError):
    def __init__(self, adapter: str, status: int, message: str):
        super().__init__(f"{adapter} HTTP {status}: {message}")
        self.adapter = adapter
        self.status = status


class LLMOrchestrator:
    providers = (
        ("Gemini Flash", "GEMINI_API_KEY", "gemini"),
        ("Groq", "GROQ_API_KEY", "groq"),
        ("DeepSeek", "DEEPSEEK_API_KEY", "deepseek"),
    )
    initial_chunk_chars = 24_000
    minimum_chunk_chars = 1_000
    rate_limit_attempts = 5

    def __init__(self):
        self.disabled_providers = {
            name.strip().lower()
            for name in os.getenv("LLM_DISABLED_PROVIDERS", "").split(",")
            if name.strip()
        }
        self.provider_cooldowns: dict[str, float] = {}

    @classmethod
    def is_configured(cls) -> bool:
        disabled = {
            name.strip().lower()
            for name in os.getenv("LLM_DISABLED_PROVIDERS", "").split(",")
            if name.strip()
        }
        return any(
            os.getenv(env_key) and adapter not in disabled
            for _, env_key, adapter in cls.providers
        )

    async def extract(self, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("Empty extraction payload")

        errors = []
        chunk_limit = self.initial_chunk_chars
        configured = [
            (name, os.getenv(key), adapter)
            for name, key, adapter in self.providers
            if os.getenv(key)
            and adapter not in self.disabled_providers
            and self.provider_cooldowns.get(adapter, 0) <= time.monotonic()
        ]
        if not configured:
            raise RuntimeError("No LLM provider API key is configured")

        while chunk_limit >= self.minimum_chunk_chars:
            chunks = semantic_chunks(text, max_chars=chunk_limit)
            for provider_name, api_key, adapter in configured:
                if (
                    adapter in self.disabled_providers
                    or self.provider_cooldowns.get(adapter, 0) > time.monotonic()
                ):
                    continue
                extracted: dict[str, Any] = {}
                try:
                    for chunk in chunks:
                        result = await self._request_with_retries(
                            adapter, api_key, chunk, schema
                        )
                        if not isinstance(result, dict):
                            raise ValueError(f"{provider_name} returned non-object JSON")
                        for name, value in result.items():
                            if value is None:
                                continue
                            if isinstance(value, list) and isinstance(extracted.get(name), list):
                                extracted[name] = list(dict.fromkeys(extracted[name] + value))
                            else:
                                extracted[name] = value
                    return extracted
                except PayloadTooLarge as exc:
                    errors.append(f"{provider_name}: payload too large")
                    logger.warning("%s rejected extraction payload; reducing chunk size", provider_name)
                    chunk_limit //= 2
                    break
                except RateLimited as exc:
                    errors.append(f"{provider_name}: rate limit exhausted")
                    cooldown = max(30.0, exc.retry_after or 0.0)
                    self.provider_cooldowns[adapter] = time.monotonic() + cooldown
                    logger.warning("%s rate limit exhausted; cooling down provider", provider_name)
                except ProviderHTTPError as exc:
                    errors.append(f"{provider_name}: HTTP {exc.status}")
                    if exc.status in (401, 402, 403, 404):
                        self.disabled_providers.add(adapter)
                    elif exc.status >= 500:
                        self.provider_cooldowns[adapter] = time.monotonic() + 30.0
                    logger.warning("%s unavailable (HTTP %s); trying the next provider", provider_name, exc.status)
                except Exception as exc:
                    errors.append(f"{provider_name}: {exc}")
                    logger.warning("%s extraction failed; trying the next provider: %s", provider_name, exc)
            else:
                break

        detail = "; ".join(errors) or "all provider keys are unavailable"
        raise RuntimeError(f"LLM extraction failed: {detail}")

    async def _request_with_retries(
        self, adapter: str, key: str, chunk: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        last_error: RateLimited | None = None
        for attempt in range(self.rate_limit_attempts):
            try:
                return await self._call_provider(adapter, key, chunk, schema)
            except RateLimited as exc:
                last_error = exc
                if attempt + 1 == self.rate_limit_attempts:
                    break
                await self._backoff(attempt, exc.retry_after)
        if last_error:
            raise last_error
        raise RuntimeError("Provider retry loop ended unexpectedly")

    async def _backoff(self, attempt: int, retry_after: float | None = None):
        delay = min(30.0, float(retry_after)) if retry_after is not None else min(30.0, 2**attempt)
        await asyncio.sleep(delay + random.random())

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    async def _call_provider(
        self, adapter: str, key: str, chunk: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = (
            "Extract only facts explicitly supported by the source text. "
            "Use null for absent values; do not infer missing facts. "
            "Return strict JSON matching this schema: "
            + json.dumps(schema)
            + "\nSOURCE TEXT:\n"
            + chunk
        )
        if adapter == "gemini":
            model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
        elif adapter == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
        elif adapter == "deepseek":
            url = "https://api.deepseek.com/chat/completions"
            payload = {
                "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
        else:
            raise ValueError(f"Unknown LLM adapter: {adapter}")

        headers = {"Authorization": f"Bearer {key}"} if adapter != "gemini" else {}
        target = url + (f"?key={key}" if adapter == "gemini" else "")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                target, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                body = await response.text()
                if response.status == 413:
                    raise PayloadTooLarge(body[:500])
                if response.status == 429:
                    raise RateLimited(
                        body[:500],
                        self._retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if response.status >= 400:
                    raise ProviderHTTPError(adapter, response.status, body[:500])
                data = json.loads(body)

        if adapter == "gemini":
            result_text = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            result_text = data["choices"][0]["message"]["content"]
        result = json.loads(result_text)
        if not isinstance(result, dict):
            raise ValueError(f"{adapter} returned non-object JSON")
        validate_json_schema(result, schema)
        return result


ROLE_EVIDENCE = {
    "Engineering": ("engineer", "engineering", "developer", "software", "swe"),
    "Research": ("research", "scientist"),
    "Product": ("product manager", "product management"),
    "Design": ("designer", "design"),
    "Sales": ("sales", "account executive", "business development"),
    "Marketing": ("marketing", "communications"),
    "Data": ("data scientist", "data engineer", "analytics"),
    "Operations": ("operations", "recruiting", "human resources"),
}
REMOTE_EVIDENCE = ("remote", "telecommute", "work from home", "distributed")
ONSITE_EVIDENCE = ("on-site", "onsite", "in-office", "in office", "on site")
NEWS_TOPIC_EVIDENCE = {
    "Funding": ("funding", "raised", "investment", "series a", "series b", "valuation"),
    "Product": ("product", "launch", "launched", "release", "released", "announced"),
    "Research": ("research", "paper", "study", "benchmark", "arxiv"),
    "Policy": ("policy", "regulation", "law", "government", "legislation"),
    "AI Models": ("model", "llm", "gpt", "foundation model", "language model"),
    "Jobs": ("job", "hiring", "layoff", "career", "recruit"),
}


async def enrich_job_classifications(
    records: list[dict[str, Any]], orchestrator: LLMOrchestrator
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(4)
    schema = {
        "type": "object",
        "properties": {
            "role_family": {
                "type": "string",
                "enum": [*ROLE_EVIDENCE, "Other"],
            },
            "is_remote": {"type": "boolean"},
        },
        "required": ["role_family", "is_remote"],
        "additionalProperties": False,
    }

    async def classify(record: dict[str, Any]) -> None:
        content = record.get("content", {})
        source_text = " ".join(
            str(content.get(field) or "")
            for field in ("title", "location", "text")
        )
        source_text = source_text[:12_000]
        if not source_text.strip():
            return
        async with semaphore:
            try:
                extracted = await orchestrator.extract(source_text, schema)
            except RuntimeError as exc:
                logger.warning(
                    "LLM classification unavailable for %s; keeping source-derived fields: %s",
                    record.get("source", {}).get("url"),
                    exc,
                )
                return

        evidence = source_text.casefold()
        role = extracted.get("role_family")
        if role in ROLE_EVIDENCE and any(
            term in evidence for term in ROLE_EVIDENCE[role]
        ):
            content["role_family"] = role
        remote = extracted.get("is_remote")
        if remote is True and any(term in evidence for term in REMOTE_EVIDENCE):
            content["is_remote"] = True
        elif remote is False and any(term in evidence for term in ONSITE_EVIDENCE):
            content["is_remote"] = False

    await asyncio.gather(*(classify(record) for record in records))
    return records


async def enrich_news_topics(
    records: list[dict[str, Any]], orchestrator: LLMOrchestrator
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(4)
    schema = {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {"type": "string", "enum": list(NEWS_TOPIC_EVIDENCE)},
            }
        },
        "required": ["topics"],
        "additionalProperties": False,
    }

    async def classify(record: dict[str, Any]) -> None:
        content = record.get("content", {})
        source_text = " ".join(
            str(content.get(field) or "") for field in ("title", "text")
        )
        source_text = source_text[:8_000]
        if not source_text.strip():
            return
        async with semaphore:
            try:
                extracted = await orchestrator.extract(source_text, schema)
            except RuntimeError as exc:
                logger.warning(
                    "LLM topic extraction unavailable for %s; leaving topics unset: %s",
                    record.get("source", {}).get("url"),
                    exc,
                )
                return

        evidence = source_text.casefold()
        topics = extracted.get("topics")
        if not isinstance(topics, list):
            return
        content["topics"] = [
            topic
            for topic in dict.fromkeys(topics)
            if topic in NEWS_TOPIC_EVIDENCE
            and any(term in evidence for term in NEWS_TOPIC_EVIDENCE[topic])
        ]

    await asyncio.gather(*(classify(record) for record in records))
    return records

"""A fake OpenAI SDK and settings for LLM tests; no test ever reaches the network."""

from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from eingang.config import Settings
from llm import client


@dataclass
class FakeSdk:
    """Serves queued responses (or raises queued errors) and records each request."""

    replies: list[Any] = field(default_factory=list)  # boundary: openai, fake responses
    requests: list[dict[str, Any]] = field(default_factory=list)  # boundary: openai kwargs

    @property
    def responses(self) -> "FakeSdk":
        return self

    @property
    def with_raw_response(self) -> "FakeSdk":
        return self

    def parse(self, **kwargs: Any) -> Any:  # boundary: openai, responses.with_raw_response.parse
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(
    parsed: BaseModel | None,
    *,
    status: str = "completed",
    input_tokens: int = 1000,
    cached: int = 200,
    output: int = 100,
) -> SimpleNamespace:
    """A raw response: its JSON body, and `parse()` as the SDK's (None parses as cut-off text)."""
    body = {
        "status": status,
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens": output,
        },
    }

    def parse() -> SimpleNamespace:
        if parsed is None:  # the SDK raises like this on JSON cut off at the token limit
            TypeAdapter(dict[str, str]).validate_json('{"invoice_number": "RE-')
        return SimpleNamespace(output_parsed=parsed)

    return SimpleNamespace(http_response=SimpleNamespace(json=lambda: body), parse=parse)


def enabled_settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {  # boundary: pydantic-settings, mixed field types
        "LLM_ENABLED": True,
        "OPENAI_API_KEY": "test-key",
        "OPENAI_MODEL": "test-model",
        "OPENAI_PRICE_INPUT_PER_MTOK": Decimal("0.10"),
        "OPENAI_PRICE_CACHED_INPUT_PER_MTOK": Decimal("0.01"),
        "OPENAI_PRICE_OUTPUT_PER_MTOK": Decimal("0.40"),
        "LLM_SPENT_ELSEWHERE_USD": Decimal("0.00"),
        **overrides,
    }
    return Settings(_env_file=None, **values)  # never the developer's .env


def install(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> FakeSdk:
    """Switch the LLM on with test prices and route every call to a fresh fake SDK."""
    configured = enabled_settings(**overrides)
    fake = FakeSdk()
    monkeypatch.setattr(client, "get_settings", lambda: configured)
    monkeypatch.setattr(client, "sdk_factory", lambda _settings: fake)
    return fake

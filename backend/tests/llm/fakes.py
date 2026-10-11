"""A fake OpenAI SDK and settings for LLM tests; no test ever reaches the network."""

from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

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

    def parse(self, **kwargs: Any) -> Any:  # boundary: openai, responses.parse
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(
    parsed: BaseModel,
    *,
    status: str = "completed",
    input_tokens: int = 1000,
    cached: int = 200,
    output: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        output_parsed=parsed,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens=output,
        ),
    )


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

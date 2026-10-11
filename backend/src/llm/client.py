"""The one door to the LLM (HANDOFF "LLM usage and budget"). Nothing else imports `openai`.

Every call goes: switched off or over budget → refused; identical request cached → free;
otherwise one Responses API call with Structured Outputs. Each outcome writes exactly one
ledger row, and the ledger never holds prompt or response text. The SDK never retries
(`max_retries=0`); retries happen only through Temporal, so each attempt is budgeted.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import openai
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from pydantic import BaseModel, ValidationError

from accounts.models import Organization
from eingang.config import Settings, get_settings
from eingang.temporal_errors import BudgetExceededError, PermanentError
from llm import budget
from llm.models import LlmCache, LlmCall
from llm.prompts import Prompt

SDK_TIMEOUT_SECONDS = 90
# One lock for every budgeted call: the check and the ledger row of a call happen while no
# other call can pass the same check (calls are rare; serialising them is cheap).
BUDGET_LOCK_KEY = 0x6C6C6D  # "llm"

logger = logging.getLogger(__name__)


def _default_sdk(settings: Settings) -> Any:  # boundary: the SDK client (or a test fake)
    return openai.OpenAI(
        api_key=settings.OPENAI_API_KEY, timeout=SDK_TIMEOUT_SECONDS, max_retries=0
    )


# Tests replace this with a fake that serves recorded responses.
sdk_factory: Callable[[Settings], Any] = _default_sdk  # boundary: SDK client


@dataclass(frozen=True)
class Request[T: BaseModel]:
    purpose: str  # extract, compare, explain, eval
    prompt: Prompt
    data: str  # the untrusted input, already fenced
    output: type[T]
    max_output_tokens: int
    organization: Organization | None = None

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "developer", "content": self.prompt.text},
            {"role": "user", "content": self.data},
        ]

    def request_hash(self, model: str) -> str:
        """SHA-256 of model + prompt version + the exact input messages + the output schema."""
        payload = json.dumps(
            {
                "model": model,
                "prompt_version": self.prompt.label,
                "messages": self.messages(),
                "schema": self.output.model_json_schema(),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


AnyRequest = Request[Any]  # boundary: pydantic, a request for any output schema


def _ledger(request: AnyRequest, settings: Settings, status: str, **values: object) -> None:
    organization = request.organization
    LlmCall.objects.create(
        organization=organization,
        is_public=organization is not None and organization.is_sandbox,
        purpose=request.purpose,
        model=settings.OPENAI_MODEL or None,
        prompt_version=request.prompt.label,
        status=status,
        **values,
    )


def _count_sandbox_call(organization: Organization | None) -> None:
    if organization is not None and organization.is_sandbox:
        Organization.objects.filter(id=organization.id).update(
            llm_calls_used=F("llm_calls_used") + 1
        )


def call[T: BaseModel](request: Request[T]) -> T:
    """The parsed response, or BudgetExceededError / PermanentError / an SDK error to retry.

    The ledger row of a refused or failed call must survive the exception, so the
    transaction (which holds the budget lock) commits first and the error is raised after.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BUDGET_LOCK_KEY])
        outcome = _attempt(request, get_settings())
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _attempt[T: BaseModel](request: Request[T], settings: Settings) -> T | BaseException:
    if not settings.LLM_ENABLED:
        _ledger(request, settings, LlmCall.Status.REFUSED_DISABLED)
        return BudgetExceededError("disabled")
    request_hash = request.request_hash(settings.OPENAI_MODEL)
    cached = LlmCache.objects.filter(request_hash=request_hash).first()
    if cached is not None:
        _ledger(request, settings, LlmCall.Status.OK, cache_hit=True)
        return request.output.model_validate(cached.response_json)
    prices = budget.Prices.from_settings(settings)
    estimated = budget.estimate(
        prices, request.prompt.text + request.data, request.max_output_tokens
    )
    reason = budget.refusal(settings, request.organization, estimated)
    if reason is not None:
        _ledger(request, settings, LlmCall.Status.REFUSED_BUDGET)
        return BudgetExceededError(reason)
    return _call_api(request, settings, prices, request_hash)


def _call_api[T: BaseModel](
    request: Request[T], settings: Settings, prices: budget.Prices, request_hash: str
) -> T | BaseException:
    arguments: dict[str, Any] = {  # boundary: SDK keyword arguments
        "model": settings.OPENAI_MODEL,
        "input": request.messages(),
        "text_format": request.output,
        "max_output_tokens": request.max_output_tokens,
        "store": False,
    }
    if settings.OPENAI_REASONING_EFFORT:
        arguments["reasoning"] = {"effort": settings.OPENAI_REASONING_EFFORT}
    started = time.monotonic()
    try:
        # The raw response: its status and usage are read before parsing, because the SDK
        # raises while parsing an answer that was cut off, and that answer is still paid for.
        raw = sdk_factory(settings).responses.with_raw_response.parse(**arguments)
    except openai.BadRequestError as error:
        _failed(request, settings, started)
        return PermanentError(f"The LLM request was refused: {error.code or 'bad request'}")
    except openai.APIError as error:
        # Timeouts, rate limits, server errors: recorded, then retried by Temporal (at most
        # two attempts for LLM activities).
        _failed(request, settings, started)
        return error
    body = raw.http_response.json()  # boundary: json
    usage = body.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    try:
        parsed = _without_nul(request.output, raw.parse().output_parsed)
    except ValidationError:
        parsed = None
    complete = body.get("status") == "completed" and isinstance(parsed, request.output)
    _ledger(
        request,
        settings,
        LlmCall.Status.OK if complete else LlmCall.Status.ERROR,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        cost=budget.cost(prices, input_tokens, cached_tokens, output_tokens),
        latency_ms=_since(started),
    )
    _count_sandbox_call(request.organization)
    if not complete or not isinstance(parsed, request.output):
        # The cost is on record; an incomplete or unparsed answer is never used.
        return PermanentError("The LLM response was incomplete or did not match the schema.")
    try:
        with transaction.atomic():  # a savepoint: a failed cache write keeps the ledger row
            LlmCache.objects.get_or_create(
                request_hash=request_hash,
                defaults={"response_json": parsed.model_dump(mode="json")},
            )
    except DatabaseError:
        logger.warning("An LLM answer for %s could not be cached", request.purpose)
    return parsed


def _without_nul[T: BaseModel](output: type[T], parsed: T | None) -> T | None:
    """The answer with NUL characters removed: PostgreSQL stores none in text or jsonb.

    The model can write one: quoting evidence, it wrote a NUL followed by "A0" where the
    document had a no-break space (U+00A0).
    """
    if not isinstance(parsed, output):
        return parsed
    dumped = parsed.model_dump(mode="json")
    cleaned = _strip_nul(dumped)
    return parsed if cleaned == dumped else output.model_validate(cleaned)


def _strip_nul(value: object) -> object:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_nul(item) for key, item in value.items()}
    return value


def _failed(request: AnyRequest, settings: Settings, started: float) -> None:
    _ledger(request, settings, LlmCall.Status.ERROR, latency_ms=_since(started))
    _count_sandbox_call(request.organization)


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def spent_total() -> Decimal:
    """This database's ledger total (`uv run poe llm-spend`)."""
    return budget.spent()

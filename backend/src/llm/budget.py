"""Budgets and costs (HANDOFF "Costs are computed, not guessed" and "Budgets").

Every limit is checked before a call, against the ledger of this database. The estimate
overestimates on purpose: `ceil(len(prompt) / 3)` input tokens plus `max_output_tokens`.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from django.db.models import Sum

from accounts.models import Organization
from eingang import clock
from eingang.config import Settings
from llm.models import LlmCall

RefusalReason = Literal[
    "disabled", "budget_lifetime", "budget_monthly", "budget_daily_public", "sandbox_limit"
]
MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Prices:
    input: Decimal
    cached_input: Decimal
    output: Decimal

    @classmethod
    def from_settings(cls, settings: Settings) -> "Prices":
        return cls(
            input=settings.OPENAI_PRICE_INPUT_PER_MTOK or Decimal(0),
            cached_input=settings.OPENAI_PRICE_CACHED_INPUT_PER_MTOK or Decimal(0),
            output=settings.OPENAI_PRICE_OUTPUT_PER_MTOK or Decimal(0),
        )


def cost(prices: Prices, input_tokens: int, cached_tokens: int, output_tokens: int) -> Decimal:
    """The spec's formula; output tokens include reasoning tokens. Six decimal places."""
    uncached = max(input_tokens - cached_tokens, 0)
    total = (
        uncached * prices.input
        + cached_tokens * prices.cached_input
        + output_tokens * prices.output
    ) / MILLION
    return total.quantize(COST_PLACES, rounding=ROUND_HALF_UP)


def estimate(prices: Prices, prompt_text: str, max_output_tokens: int) -> Decimal:
    return cost(prices, math.ceil(len(prompt_text) / 3), 0, max_output_tokens)


def spent(**filters: object) -> Decimal:
    """The ledger's total cost, optionally filtered."""
    total: Decimal | None = LlmCall.objects.filter(**filters).aggregate(total=Sum("cost"))["total"]
    return total if total is not None else Decimal(0)


def _day_start(moment: datetime) -> datetime:
    utc = moment.astimezone(UTC)
    return datetime(utc.year, utc.month, utc.day, tzinfo=UTC)


def _month_start(moment: datetime) -> datetime:
    utc = moment.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def _calls_used(organization: Organization | None) -> int:
    """The counter as stored now: a loaded organisation may predate calls made since."""
    if organization is None:
        return 0
    return Organization.objects.values_list("llm_calls_used", flat=True).get(id=organization.id)


def refusal(
    settings: Settings, organization: Organization | None, estimated: Decimal
) -> RefusalReason | None:
    """The first limit this call would break, or None when it may run.

    Order: switched off, the per-sandbox cap, then the money limits from the widest down.
    """
    if not settings.LLM_ENABLED:
        return "disabled"
    public = organization is not None and organization.is_sandbox
    if public and _calls_used(organization) >= settings.LLM_MAX_CALLS_PER_SANDBOX:
        return "sandbox_limit"
    elsewhere = settings.LLM_SPENT_ELSEWHERE_USD or Decimal(0)
    if spent() + elsewhere + estimated > settings.LLM_BUDGET_USD_LIFETIME:
        return "budget_lifetime"
    now = clock.now()
    if spent(created_at__gte=_month_start(now)) + estimated > settings.LLM_BUDGET_USD_MONTHLY:
        return "budget_monthly"
    if public:
        today = spent(
            created_at__gte=_day_start(now),
            created_at__lt=_day_start(now) + timedelta(days=1),
            is_public=True,
        )
        if today + estimated > settings.LLM_BUDGET_USD_DAILY_PUBLIC:
            return "budget_daily_public"
    return None

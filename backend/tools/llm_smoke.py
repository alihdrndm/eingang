"""The one smoke call of the LLM milestone: record a response fixture for each LLM feature.

Run as `uv run poe llm-smoke` with LLM_ENABLED=true for this run only. It prints the model,
its prices and the estimated maximum cost, asks for `yes` (or takes `--yes`), then makes
exactly three calls through the LLM client (budgeted, cached and ledgered):

- extract: the stored text of sample S08 (a plain PDF), cut like the activity cuts it;
- compare: the stored text of sample S10 (a hybrid PDF whose visible gross differs);
- explain: rule BR-DE-15 with its official message from sample S05's validation report.

Each parsed answer and its usage go to `tests/fixtures/recorded/<feature>.json`, which the
recorded-fixture tests replay. The prompt text is never written there.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from live_llm import (
    BACKEND_DIR,
    SAMPLES_DIR,
    Ask,
    Caller,
    Outcome,
    confirm,
    enabled_settings,
    report_cost,
)

from eingang.config import Settings
from invoices.extraction import TEXT_LIMIT
from llm import requests
from llm.client import AnyRequest

FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures" / "recorded"
EXPLAINED_RULE = "BR-DE-15"


def _precomputed(samples_dir: Path, sample_id: str) -> dict[str, Any]:  # boundary: JSON file
    path = samples_dir / "precomputed" / f"{sample_id}.json"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))  # boundary: json
    return loaded


def smoke_requests(samples_dir: Path) -> dict[str, AnyRequest]:
    """The three requests, by feature name, built exactly as the activities build them."""
    s08 = _precomputed(samples_dir, "S08")
    s10 = _precomputed(samples_dir, "S10")
    s05 = _precomputed(samples_dir, "S05")
    issue = next(
        item for item in s05["validation"]["issues"] if item.get("rule_id") == EXPLAINED_RULE
    )
    return {
        "extract": requests.extraction_request(str(s08["text"])[:TEXT_LIMIT]),
        "compare": requests.comparison_request(str(s10["text"])[:TEXT_LIMIT]),
        "explain": requests.explanation_request(
            EXPLAINED_RULE, str(issue.get("message", "")), str(issue.get("source", ""))
        ),
    }


def fixture(
    feature: str,
    request: AnyRequest,
    outcome: Outcome[Any],  # boundary: pydantic, any output schema
    settings: Settings,
) -> dict[str, Any]:  # boundary: JSON document
    """What is recorded: the model's answer and the usage, never the prompt."""
    row = outcome.row
    return {
        "feature": feature,
        "prompt_version": request.prompt.label,
        "model": settings.OPENAI_MODEL,
        "reasoning_effort": settings.OPENAI_REASONING_EFFORT or None,
        "max_output_tokens": request.max_output_tokens,
        "usage": {
            "status": "completed",  # the client returns an answer only for a completed response
            "input_tokens": row.input_tokens,
            "cached_tokens": row.cached_tokens,
            "output_tokens": row.output_tokens,
            "cache_hit": row.cache_hit,
            "cost_usd": str(row.cost),
        },
        "response": outcome.answer.model_dump(mode="json"),
    }


def run(
    *,
    assume_yes: bool,
    samples_dir: Path = SAMPLES_DIR,
    fixtures_dir: Path = FIXTURES_DIR,
    ask: Ask = input,
) -> int:
    settings = enabled_settings()
    if settings is None:
        return 2
    planned = smoke_requests(samples_dir)
    if not confirm(settings, list(planned.values()), assume_yes=assume_yes, ask=ask):
        return 1
    caller = Caller()
    failures = 0
    for feature, request in planned.items():
        outcome = caller.call(request)
        if isinstance(outcome, str):
            print(f"{feature}: FAILED: {outcome}", file=sys.stderr)
            failures += 1
            continue
        row = outcome.row
        fits = row.output_tokens <= request.max_output_tokens
        print(
            f"{feature}: completed, {row.input_tokens} input ({row.cached_tokens} cached) / "
            f"{row.output_tokens} output tokens of max {request.max_output_tokens}"
            f"{' (cache hit)' if row.cache_hit else ''}, cost {row.cost:.6f} USD"
        )
        if not fits:
            print(f"{feature}: FAILED: the output exceeds max_output_tokens", file=sys.stderr)
            failures += 1
            continue
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        path = fixtures_dir / f"{feature}.json"
        document = fixture(feature, request, outcome, settings)
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"{feature}: wrote {path}")
    report_cost(caller)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="do not ask before calling")
    parser.add_argument("--samples-dir", type=Path, default=SAMPLES_DIR)
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR)
    args = parser.parse_args()
    return run(assume_yes=args.yes, samples_dir=args.samples_dir, fixtures_dir=args.fixtures_dir)


if __name__ == "__main__":
    sys.exit(main())

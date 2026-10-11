"""The sample precompute tool, on copies of the precomputed JSON and the fake SDK only."""

import json
from pathlib import Path

import live_llm
import precompute_samples
import pytest

from llm.schemas import ComparedValues, ExtractedInvoice
from tests.llm import fakes

pytestmark = pytest.mark.django_db
PRECOMPUTED = Path(__file__).resolve().parents[3] / "samples" / "precomputed"


def install(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> fakes.FakeSdk:
    """The fake SDK, and the tool reading the same (fake) settings as the LLM client."""
    configured = fakes.enabled_settings(**overrides)
    monkeypatch.setattr(live_llm, "get_settings", lambda: configured)
    return fakes.install(monkeypatch, **overrides)


@pytest.fixture
def samples(tmp_path: Path) -> Path:
    """Copies of S08.json and S10.json as before the precompute (never the real files)."""
    (tmp_path / "precomputed").mkdir()
    for sample_id in ("S08", "S10"):
        data = json.loads((PRECOMPUTED / f"{sample_id}.json").read_text(encoding="utf-8"))
        data.pop("extraction", None)
        data.pop("comparison", None)
        (tmp_path / "precomputed" / f"{sample_id}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


def extracted(**values: object) -> ExtractedInvoice:
    empty: dict[str, object] = dict.fromkeys(ExtractedInvoice.model_fields)
    empty.update(tax_breakdown=[], lines=[])
    return ExtractedInvoice.model_validate({**empty, **values})


def compared(**values: object) -> ComparedValues:
    return ComparedValues.model_validate({**dict.fromkeys(ComparedValues.model_fields), **values})


def load(samples: Path, sample_id: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(
        (samples / "precomputed" / f"{sample_id}.json").read_text(encoding="utf-8")
    )
    return loaded


def never_asked(_prompt: str) -> str:
    raise AssertionError("--yes must not ask")


def test_refuses_while_the_llm_is_off(
    monkeypatch: pytest.MonkeyPatch, samples: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sdk = install(monkeypatch, LLM_ENABLED=False)
    assert precompute_samples.run(assume_yes=True, samples_dir=samples, ask=never_asked) == 2
    assert "LLM_ENABLED is not true" in capsys.readouterr().err
    assert sdk.requests == []
    assert "extraction" not in load(samples, "S08")


def test_asks_and_aborts_without_yes(
    monkeypatch: pytest.MonkeyPatch, samples: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sdk = install(monkeypatch)
    assert precompute_samples.run(assume_yes=False, samples_dir=samples, ask=lambda _: "") == 1
    assert "Estimated maximum cost" in capsys.readouterr().out
    assert sdk.requests == []
    assert "extraction" not in load(samples, "S08")


def test_writes_the_two_answers_then_skips_them_until_forced(
    monkeypatch: pytest.MonkeyPatch, samples: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sdk = install(monkeypatch)
    sdk.replies += [
        fakes.reply(extracted(invoice_number="2026-1043")),
        fakes.reply(compared(gross_total="1190.00")),
    ]
    assert precompute_samples.run(assume_yes=True, samples_dir=samples, ask=never_asked) == 0
    s08 = load(samples, "S08")
    s10 = load(samples, "S10")
    assert s08["extraction"] == {
        "prompt_version": "extract_invoice.v1",
        "answer": extracted(invoice_number="2026-1043").model_dump(mode="json"),
    }
    assert s10["comparison"] == {
        "prompt_version": "compare_pdf_xml.v1",
        "answer": compared(gross_total="1190.00").model_dump(mode="json"),
    }
    original = json.loads((PRECOMPUTED / "S08.json").read_text(encoding="utf-8"))
    original.pop("extraction", None)
    assert {key: s08[key] for key in original} == original  # the rest is kept
    assert [request["max_output_tokens"] for request in sdk.requests] == [2500, 600]
    out = capsys.readouterr().out
    assert "Estimated maximum cost" in out
    assert "Actual cost of this run" in out

    # Run again: both keys exist, so nothing is called.
    assert precompute_samples.run(assume_yes=True, samples_dir=samples, ask=never_asked) == 0
    assert "Nothing to precompute" in capsys.readouterr().out
    assert len(sdk.requests) == 2

    # --force asks again; identical requests come from the cache, so the SDK is not called.
    assert (
        precompute_samples.run(assume_yes=True, force=True, samples_dir=samples, ask=never_asked)
        == 0
    )
    assert len(sdk.requests) == 2
    assert load(samples, "S08")["extraction"] == s08["extraction"]


def test_force_rewrites_an_existing_answer(monkeypatch: pytest.MonkeyPatch, samples: Path) -> None:
    path = samples / "precomputed" / "S10.json"
    data = load(samples, "S10")
    data["comparison"] = {"prompt_version": "compare_pdf_xml.v0", "answer": {}}
    path.write_text(json.dumps(data), encoding="utf-8")
    sdk = install(monkeypatch)
    sdk.replies += [
        fakes.reply(extracted()),
        fakes.reply(compared(invoice_number="SA/26/1160")),
    ]
    assert (
        precompute_samples.run(assume_yes=True, force=True, samples_dir=samples, ask=never_asked)
        == 0
    )
    comparison = load(samples, "S10")["comparison"]
    assert isinstance(comparison, dict)
    assert comparison["prompt_version"] == "compare_pdf_xml.v1"
    assert comparison["answer"]["invoice_number"] == "SA/26/1160"

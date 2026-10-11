"""Replay the responses recorded at the M5 smoke call (`uv run poe llm-smoke`).

Each recorded answer is served by the fake SDK to the real activity, so the parsing, the
post-processing and the stored result are tested against what the chosen model really
returned. No test reaches the network.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from accounts.models import Organization
from eingang import clock, storage
from eingang.workflows import contracts as c
from invoices import activities
from invoices.models import Document, Invoice, RuleExplanation, ValidationReport
from llm.schemas import (
    FIX_HINT_MAX,
    PLAIN_TEXT_MAX,
    ComparedValues,
    ExtractedInvoice,
    RuleExplanationOut,
)
from sandbox.services import sample_buyer
from tests.factories import make_document
from tests.llm import fakes

pytestmark = pytest.mark.django_db
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "recorded"
SAMPLES = Path(__file__).resolve().parents[3] / "samples"
EXTRACT = FIXTURES / "extract.json"
COMPARE = FIXTURES / "compare.json"
EXPLAIN = FIXTURES / "explain.json"


@pytest.fixture(autouse=True)
def _keep_the_test_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inside the test transaction Django would treat the connection as stale and close it.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)


@pytest.fixture
def buyer(db: None) -> Organization:
    sample = sample_buyer()
    return Organization.objects.create(name=sample.name, slug="sample-buyer", vat_id=sample.vat_id)


def recorded(path: Path) -> dict[str, Any]:  # boundary: JSON fixture
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))  # boundary: json
    return loaded


def replay(
    sdk: fakes.FakeSdk, path: Path, output: type[BaseModel]
) -> dict[str, Any]:  # boundary: json
    fixture = recorded(path)
    usage = fixture["usage"]
    sdk.replies.append(
        fakes.reply(
            output.model_validate(fixture["response"]),
            status=usage["status"],
            input_tokens=usage["input_tokens"],
            cached=usage["cached_tokens"],
            output=usage["output_tokens"],
        )
    )
    return fixture


def stored(organization: Organization, sample: str) -> c.DocumentRef:
    """A processing document whose file is the sample, detected as the workflow does."""
    document = make_document(
        organization, status=Document.Status.PROCESSING, kind=Document.Kind.XML, format_label=""
    )
    is_pdf = sample.endswith(".pdf")
    document.original_filename = sample
    document.content_type = "application/pdf" if is_pdf else "application/xml"
    document.storage_key = f"orgs/{organization.id}/documents/{document.id}/{sample}"
    document.save()
    storage.write(document.storage_key, (SAMPLES / sample).read_bytes())
    ref = c.DocumentRef(document_id=document.id)
    activities.detect_document(ref)
    return ref


def test_recorded_extraction_of_S08(buyer: Organization, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = fakes.install(monkeypatch)
    ref = stored(buyer, "S08-2026-1043.pdf")
    fixture = replay(sdk, EXTRACT, ExtractedInvoice)
    assert fixture["usage"]["output_tokens"] <= fixture["max_output_tokens"] == 2500
    activities.extract_with_llm(ref)
    invoice = Invoice.objects.get(document_id=ref.document_id)
    assert invoice.prompt_version == fixture["prompt_version"] == "extract_invoice.v1"
    assert invoice.invoice_number == "2026-1043"
    assert invoice.field_confidence["invoice_number"] == "high"
    assert str(invoice.gross_total) == "1547.00"
    assert invoice.field_confidence["gross_total"] == "high"


def test_recorded_comparison_of_S10(buyer: Organization, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = fakes.install(monkeypatch)
    ref = stored(buyer, "S10-SA-26-1160.pdf")
    fixture = replay(sdk, COMPARE, ComparedValues)
    assert fixture["usage"]["output_tokens"] <= fixture["max_output_tokens"] == 600
    result = activities.compare_pdf_to_xml(ref)
    assert result.compared
    differences = {difference.field: difference for difference in result.differences}
    # S10's visible PDF shows 1,190.00 EUR; its XML says 1200.00 (gross and payable).
    assert set(differences) <= {"gross_total", "payable_amount"}
    gross = differences["gross_total"]
    assert (gross.xml, gross.pdf) == ("1200.00", "1190.00")


def test_recorded_explanation_of_BR_DE_15(
    buyer: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = fakes.install(monkeypatch)
    ref = stored(buyer, "S05-RE-2026-0413.xml")
    s05 = json.loads((SAMPLES / "precomputed" / "S05.json").read_text(encoding="utf-8"))
    issue = next(item for item in s05["validation"]["issues"] if item["rule_id"] == "BR-DE-15")
    ValidationReport.objects.create(
        document_id=ref.document_id,
        status="invalid",
        engine="test",
        xsd_ok=True,
        issues=[issue],
        fatal_count=1,
        warning_count=0,
        ran_at=clock.now(),
    )
    fixture = replay(sdk, EXPLAIN, RuleExplanationOut)
    assert fixture["usage"]["output_tokens"] <= fixture["max_output_tokens"] == 400
    assert activities.explain_rules(ref) == 1
    saved = RuleExplanation.objects.get(rule_id="BR-DE-15")
    assert saved.prompt_version == fixture["prompt_version"] == "explain_rule.v1"
    assert 0 < len(saved.plain_text) <= PLAIN_TEXT_MAX
    assert 0 < len(saved.fix_hint) <= FIX_HINT_MAX

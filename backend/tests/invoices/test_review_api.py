"""Review endpoints: edit, resolve, mark-reviewed, decision, send-back, reopen, retry, delete."""

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pytest_django import DjangoCaptureOnCommitCallbacks

from accounts.models import Organization, User
from eingang import temporal_client
from invoices import review
from invoices.models import Approval, Check, Document, Event, Invoice, InvoiceLine
from suppliers.models import SupplierIban
from tests.conftest import ApiClient, MakeUser

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response
from tests.factories import add_check, add_iban, make_document, make_invoice, make_supplier

pytestmark = pytest.mark.django_db
Signals = list[tuple[UUID, str, str]]
Status = Document.Status
NOTE = "Checked with the supplier by phone."


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch) -> Signals:
    sent: Signals = []
    monkeypatch.setattr(
        temporal_client,
        "signal",
        lambda document_id, workflow_id, name: sent.append((document_id, workflow_id, name)),
    )
    return sent


def document_in(
    organization: Organization, status: str, *, reviewed_by: User | None = None
) -> Document:
    document = make_document(organization, status=status, reviewed_by=reviewed_by)
    Document.objects.filter(id=document.id).update(workflow_id=f"invoice-{document.id}")
    document.refresh_from_db()
    return document


def url(document: Document, action: str) -> str:
    return f"/api/v1/documents/{document.id}/{action}"


# --- PATCH …/invoice -------------------------------------------------------------------


def test_edit_marks_changed_fields_edited_and_logs_them(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("accountant")
    document = document_in(organization, Status.NEEDS_REVIEW)
    invoice = make_invoice(document, number="RE-1")
    Invoice.objects.filter(id=invoice.id).update(
        extraction_method=Invoice.ExtractionMethod.LLM,
        field_confidence={"invoice_number": "low", "gross_total": "high"},
    )
    response = api.unsafe(
        "patch",
        url(document, "invoice"),
        data={"invoice_number": "RE-2", "gross_total": "1190.00", "due_date": "2026-04-01"},
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["invoice"]["invoice_number"] == "RE-2"
    assert body["invoice"]["extraction_method"] == "llm"  # never changed by an edit
    invoice.refresh_from_db()
    assert invoice.field_confidence == {
        "invoice_number": "edited",
        "gross_total": "high",  # sent unchanged, so not edited
        "due_date": "edited",
    }
    events = Event.objects.filter(document=document, type="invoice.fields_edited")
    assert {event.data["field"]: event.data for event in events} == {
        "invoice_number": {"field": "invoice_number", "old": "RE-1", "new": "RE-2"},
        "due_date": {"field": "due_date", "old": None, "new": "2026-04-01"},
    }


def test_edit_removes_the_low_confidence_check_of_the_edited_field(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    invoice = make_invoice(document)
    Invoice.objects.filter(id=invoice.id).update(field_confidence={"invoice_number": "low"})
    Check.objects.create(
        document=document,
        check_id="C13",
        code="LOW_CONFIDENCE",
        severity="block",
        message="Low confidence: invoice number.",
        details={"field": "invoice_number"},
    )
    response = api.unsafe("patch", url(document, "invoice"), data={"invoice_number": "RE-9"})
    assert response.status_code == 200
    assert not Check.objects.filter(document=document, check_id="C13").exists()


def test_edit_keeps_the_extraction_refusal_check(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document)
    Check.objects.create(
        document=document,
        check_id="C12",
        code="EXTRACTION_UNAVAILABLE",
        severity="block",
        message="old text",
        details={"reason": "disabled"},
    )
    api.unsafe("patch", url(document, "invoice"), data={"invoice_number": "RE-9"})
    assert Check.objects.get(document=document, check_id="C12").details == {"reason": "disabled"}


def test_edit_logs_iban_values_masked_only(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document, iban="DE89370400440532013000")
    api.unsafe("patch", url(document, "invoice"), data={"payee_iban": "DE02120300000000202051"})
    event = Event.objects.get(document=document, type="invoice.fields_edited")
    assert event.data == {
        "field": "payee_iban",
        "change": "changed",
        "old": "DE…3000",
        "new": "DE…2051",
    }
    assert "370400440532" not in str(event.data)
    assert "120300000000" not in str(event.data)


def test_edit_logs_a_cleared_iban_as_none(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document, iban="DE89370400440532013000")
    api.unsafe("patch", url(document, "invoice"), data={"payee_iban": ""})
    event = Event.objects.get(document=document, type="invoice.fields_edited")
    assert event.data["old"] == "DE…3000"
    assert event.data["new"] is None


@pytest.mark.parametrize(
    ("value", "masked"),
    [(None, None), ("", None), ("DE12", "…"), ("NL91ABNA0417164300", "NL…4300")],
)
def test_mask_iban(value: str | None, masked: str | None) -> None:
    assert review.mask_iban(value) == masked


def test_edit_keeps_the_replaced_lines_in_the_event(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    invoice = make_invoice(document)
    InvoiceLine.objects.create(
        invoice=invoice, position=1, line_id="1", description="Kabel", quantity=Decimal("2"),
        unit_code="H87", unit_price=Decimal("10"), net_amount=Decimal("20.00"),
        tax_category="S", tax_rate=Decimal("19"),
    )  # fmt: skip
    lines = [{"description": "Montage", "quantity": "1", "net_amount": "80.00"}]
    response = api.unsafe("patch", url(document, "invoice"), data={"lines": lines})
    assert response.status_code == 200
    event = Event.objects.get(document=document, type="invoice.fields_edited")
    assert event.data["field"] == "lines"
    assert (event.data["old"], event.data["new"]) == (1, 1)
    [old] = event.data["old_lines"]
    assert old == {
        "position": 1,
        "line_id": "1",
        "description": "Kabel",
        "quantity": old["quantity"],
        "unit_code": "H87",
        "unit_price": old["unit_price"],
        "net_amount": "20.00",
        "tax_category": "S",
        "tax_rate": old["tax_rate"],
    }
    assert Decimal(old["quantity"]) == 2
    assert Decimal(old["unit_price"]) == 10
    assert Decimal(old["tax_rate"]) == 19
    [new] = event.data["new_lines"]
    assert new["position"] == 1
    assert new["description"] == "Montage"
    assert Decimal(new["net_amount"]) == Decimal("80.00")
    assert new["line_id"] is None


def test_edit_replaces_the_lines(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document)
    lines = [
        {"description": "Kabel", "quantity": "2", "unit_price": "10", "net_amount": "20.00"},
        {"description": "Montage", "net_amount": "80.00"},
    ]
    response = api.unsafe("patch", url(document, "invoice"), data={"lines": lines})
    assert response.status_code == 200
    assert [line["position"] for line in response.json()["lines"]] == [1, 2]
    assert response.json()["lines"][1]["description"] == "Montage"


def test_INVALID_TRANSITION_edit_outside_review(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.AWAITING_APPROVAL)
    make_invoice(document)
    response = api.unsafe("patch", url(document, "invoice"), data={"invoice_number": "X"})
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_FORBIDDEN_ROLE_approver_cannot_edit(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("approver")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document)
    response = api.unsafe("patch", url(document, "invoice"), data={"invoice_number": "X"})
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_ROLE"


# --- POST /checks/{id}/resolve ---------------------------------------------------------


def resolve(api: ApiClient, check: Check, note: str = NOTE) -> "Response":
    return api.unsafe("post", f"/api/v1/checks/{check.id}/resolve", data={"note": note})


def test_resolve_records_who_when_and_the_note(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("accountant")
    document = document_in(organization, Status.NEEDS_REVIEW)
    check = add_check(document, "C04", "warn")
    response = resolve(api, check)
    assert response.status_code == 200
    body = response.json()
    assert body["resolution_note"] == NOTE
    assert body["resolved_by_name"] == "Accountant"
    assert body["resolve"] == {
        "enabled": False,
        "reason_code": "CHECK_NOT_RESOLVABLE",
        "reason": "Already resolved.",
    }
    assert Event.objects.filter(document=document, type="check.resolved").count() == 1


def test_resolving_C05_confirms_the_supplier_iban(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    supplier = make_supplier(organization)
    first = make_invoice(make_document(organization), supplier=supplier, iban="DE1")
    add_iban(supplier, "DE1", first)
    document = document_in(organization, Status.NEEDS_REVIEW)
    invoice = make_invoice(document, supplier=supplier, iban="DE2")
    add_iban(supplier, "DE2", invoice)
    response = resolve(api, add_check(document, "C05", "block"))
    assert response.status_code == 200
    entry = SupplierIban.objects.get(supplier=supplier, iban="DE2")
    assert entry.confirmation_note == NOTE
    assert entry.confirmed_by is not None
    assert SupplierIban.objects.get(supplier=supplier, iban="DE1").confirmed_at is None


def test_VALIDATION_FAILED_resolve_note_too_short(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    check = add_check(document_in(organization, Status.NEEDS_REVIEW))
    response = resolve(api, check, "ok")
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "note"


@pytest.mark.parametrize("severity", ["info", "resolved"])
def test_CHECK_NOT_RESOLVABLE(
    signed_in: Callable[[str], ApiClient], organization: Organization, severity: str
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    check = add_check(document, "C11", "info" if severity == "info" else "warn")
    if severity == "resolved":
        resolve(api, check)
    response = resolve(api, check)
    assert response.status_code == 409
    assert response.json()["code"] == "CHECK_NOT_RESOLVABLE"


def test_INVALID_TRANSITION_resolve_outside_review(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    check = add_check(document_in(organization, Status.AWAITING_APPROVAL))
    response = resolve(api, check)
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_FORBIDDEN_ROLE_only_an_admin_accepts_a_failed_validation(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("accountant")
    check = add_check(document_in(organization, Status.NEEDS_REVIEW), "C15", "block")
    response = resolve(api, check)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_ROLE"


def test_resolve_another_organisations_check_is_not_found(
    signed_in: Callable[[str], ApiClient], other_organization: Organization
) -> None:
    api = signed_in("admin")
    check = add_check(document_in(other_organization, Status.NEEDS_REVIEW))
    assert resolve(api, check).status_code == 404


# --- Status actions --------------------------------------------------------------------


def test_mark_reviewed_moves_on_and_signals_after_commit(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    signals: Signals,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    api = signed_in("accountant")
    document = document_in(organization, Status.NEEDS_REVIEW)
    with django_capture_on_commit_callbacks(execute=True):
        response = api.unsafe("post", url(document, "mark-reviewed"))
    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
    assert signals == [(document.id, document.workflow_id, "reviewed")]


def test_BLOCKING_CHECKS_lists_the_open_blocking_checks(
    signed_in: Callable[[str], ApiClient], organization: Organization, signals: Signals
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    check = add_check(document, "C05", "block")
    add_check(document, "C04", "warn")
    response = api.unsafe("post", url(document, "mark-reviewed"))
    assert response.status_code == 409
    assert response.json()["code"] == "BLOCKING_CHECKS"
    assert response.json()["checks"] == [str(check.id)]
    assert signals == []


def test_FOUR_EYES_the_reviewer_cannot_decide(
    api: ApiClient, organization: Organization, make_user: MakeUser
) -> None:
    admin = make_user("admin")
    api.sign_in(admin)
    document = document_in(organization, Status.AWAITING_APPROVAL, reviewed_by=admin)
    response = api.unsafe("post", url(document, "decision"), data={"decision": "approved"})
    assert response.status_code == 403
    assert response.json()["code"] == "FOUR_EYES"


def test_decision_approves_and_records_the_approval(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    signals: Signals,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    api = signed_in("approver")
    document = document_in(organization, Status.AWAITING_APPROVAL)
    with django_capture_on_commit_callbacks(execute=True):
        response = api.unsafe("post", url(document, "decision"), data={"decision": "approved"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert Approval.objects.get(document=document).decision == "approved"
    assert signals == [(document.id, document.workflow_id, "decided")]


def test_VALIDATION_FAILED_reject_needs_a_comment(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("approver")
    document = document_in(organization, Status.AWAITING_APPROVAL)
    response = api.unsafe("post", url(document, "decision"), data={"decision": "rejected"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "comment"


def test_FORBIDDEN_ROLE_accountant_cannot_decide(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("accountant")
    document = document_in(organization, Status.AWAITING_APPROVAL)
    response = api.unsafe("post", url(document, "decision"), data={"decision": "approved"})
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_ROLE"


def test_send_back_then_reopen_after_a_rejection(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    signals: Signals,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    api = signed_in("admin")
    waiting = document_in(organization, Status.AWAITING_APPROVAL)
    rejected = document_in(organization, Status.REJECTED)
    with django_capture_on_commit_callbacks(execute=True):
        sent = api.unsafe("post", url(waiting, "send-back"), data={"comment": "Wrong cost centre."})
        reopened = api.unsafe("post", url(rejected, "reopen"))
    assert sent.json()["status"] == reopened.json()["status"] == "needs_review"
    assert [name for _, _, name in signals] == ["sent_back", "reopened"]


def test_INVALID_TRANSITION_reopen_of_a_waiting_document(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    response = api.unsafe("post", url(document_in(organization, Status.NEEDS_REVIEW), "reopen"))
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_retry_moves_to_processing_and_starts_with_a_retry_signal(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = signed_in("accountant")
    retried: list[UUID] = []
    monkeypatch.setattr(temporal_client, "retry_processing", retried.append)
    document = document_in(organization, Status.FAILED)
    response = api.unsafe("post", url(document, "retry"))
    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert retried == [document.id]


def test_TEMPORAL_UNAVAILABLE_retry_leaves_the_document_failed(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = signed_in("accountant")

    def unavailable(document_id: UUID) -> None:
        raise temporal_client.TemporalUnavailableError

    monkeypatch.setattr(temporal_client, "retry_processing", unavailable)
    document = document_in(organization, Status.FAILED)
    Document.objects.filter(id=document.id).update(failure_reason="Corrupt PDF.")
    response = api.unsafe("post", url(document, "retry"))
    assert response.status_code == 503
    assert response.json()["code"] == "TEMPORAL_UNAVAILABLE"
    document.refresh_from_db()
    assert document.status == Status.FAILED
    assert document.failure_reason == "Corrupt PDF."


def test_delete_signals_the_workflow(
    signed_in: Callable[[str], ApiClient],
    organization: Organization,
    signals: Signals,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    with django_capture_on_commit_callbacks(execute=True):
        response = api.unsafe("delete", f"/api/v1/documents/{document.id}")
    assert response.status_code == 204
    assert signals == [(document.id, document.workflow_id, "deleted")]


def test_edit_stores_iban_and_vat_id_compacted(
    signed_in: Callable[[str], ApiClient], organization: Organization
) -> None:
    api = signed_in("admin")
    document = document_in(organization, Status.NEEDS_REVIEW)
    make_invoice(document)
    api.unsafe(
        "patch",
        url(document, "invoice"),
        data={"payee_iban": "de89 3704 0044 0532 0130 00", "seller_vat_id": "de 123456789"},
    )
    invoice = Invoice.objects.get(document=document)
    assert (invoice.payee_iban, invoice.seller_vat_id) == ("DE89370400440532013000", "DE123456789")

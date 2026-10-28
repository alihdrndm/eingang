"""What people do to a document: edit fields, resolve checks, move it through review.

Each function changes the database in one transaction and signals the document's workflow
only after the commit (HANDOFF "Who changes the database?"); a lost signal is repaired by
the daily maintenance.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import partial

from django.db import transaction

from accounts.models import User
from eingang import clock, temporal_client
from eingang.problem import ProblemError
from eingang.workflows import contracts as c
from einvoice.fields import compact_upper
from invoices import checks
from invoices.actions import check_resolvable
from invoices.models import Check, Document, Event, Invoice, InvoiceLine
from invoices.status import forbidden_role, transition
from suppliers.matching import match_supplier
from suppliers.models import SupplierIban

Status = Document.Status

# Every scalar CanonicalInvoice field a person may correct (HTTP API: PATCH …/invoice).
EDITABLE_FIELDS = (
    "invoice_number", "type_code", "issue_date", "due_date", "currency", "buyer_reference",
    "order_reference", "seller_name", "seller_vat_id", "seller_tax_number", "seller_street",
    "seller_postcode", "seller_city", "seller_country_code", "seller_email", "buyer_name",
    "buyer_vat_id", "buyer_tax_number", "buyer_street", "buyer_postcode", "buyer_city",
    "buyer_country_code", "buyer_email", "payee_iban", "payee_bic", "payment_terms",
    "line_total", "allowance_total", "charge_total", "net_total", "tax_total", "gross_total",
    "prepaid_amount", "payable_amount",
)  # fmt: skip
LINE_FIELDS = (
    "line_id", "description", "quantity", "unit_code", "unit_price", "net_amount",
    "tax_category", "tax_rate",
)  # fmt: skip
# Stored upper-case without spaces (section 2), like the parsed values they are compared to.
COMPACTED_FIELDS = frozenset({"payee_iban", "payee_bic", "seller_vat_id", "buyer_vat_id"})
# The audit trail stores these values masked (`mask_iban`), never in full.
MASKED_VALUES = frozenset({"payee_iban"})
# Shorter values keep nothing of their content when masked.
MASK_MIN_LENGTH = 8
NOTE_MIN, NOTE_MAX = 5, 500

FieldValue = str | int | Decimal | date | None


def _after_commit(document: Document, name: str) -> None:
    transaction.on_commit(partial(temporal_client.signal, document.id, document.workflow_id, name))


def not_editable() -> ProblemError:
    return ProblemError(
        409,
        "INVALID_TRANSITION",
        "Not possible now",
        "Only invoices that need review can be changed.",
    )


def _logged(value: FieldValue) -> str | int | None:
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def mask_iban(value: str | int | None) -> str | None:
    """`DE…3000`: the country code and the last four characters, never the whole IBAN."""
    if value is None or value == "":
        return None
    text = str(value)
    if len(text) < MASK_MIN_LENGTH:
        return "…"
    return f"{text[:2]}…{text[-4:]}"


# One edited field and its event data besides the field name.
Change = tuple[str, dict[str, object]]


# --- Editing ------------------------------------------------------------------------


def edit_invoice(
    document: Document,
    user: User,
    fields: Mapping[str, FieldValue],
    lines: Sequence[Mapping[str, FieldValue]] | None,
) -> None:
    """Apply a person's corrections; changed fields become `edited`; checks run again."""
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        invoice = Invoice.objects.filter(document=document).first()
        if document.status != Status.NEEDS_REVIEW or invoice is None:
            raise not_editable()
        changed = _apply_fields(invoice, fields)
        if lines is not None:
            changed.append(_replace_lines(invoice, lines))
        confidence = dict(invoice.field_confidence or {})
        for name, _data in changed:
            confidence[name] = "edited"
        invoice.field_confidence = confidence
        invoice.save()
        for name, data in changed:
            Event.objects.create(
                organization=document.organization,
                document=document,
                actor=user,
                type=Event.Type.INVOICE_FIELDS_EDITED,
                data={"field": name, **data},
            )
        if changed:
            match_supplier(invoice)
            checks.recheck(document)


def _apply_fields(invoice: Invoice, fields: Mapping[str, FieldValue]) -> list[Change]:
    """Set the changed fields; each change keeps its old and new value (GoBD)."""
    changed: list[Change] = []
    for name in EDITABLE_FIELDS:
        if name not in fields:
            continue
        old: FieldValue = getattr(invoice, name)
        new = fields[name]
        if name in COMPACTED_FIELDS and isinstance(new, str):
            new = compact_upper(new)  # as section 2 stores them, so the IBAN history matches
        if old == new:
            continue
        setattr(invoice, name, new)
        if name in MASKED_VALUES:
            data: dict[str, object] = {
                "change": "changed",
                "old": mask_iban(_logged(old)),
                "new": mask_iban(_logged(new)),
            }
        else:
            data = {"old": _logged(old), "new": _logged(new)}
        changed.append((name, data))
    return changed


def _logged_line(position: int, line: Mapping[str, FieldValue]) -> dict[str, object]:
    return {"position": position, **{name: _logged(line.get(name)) for name in LINE_FIELDS}}


def _replace_lines(invoice: Invoice, lines: Sequence[Mapping[str, FieldValue]]) -> Change:
    """Lines are replaced as a whole list; positions are renumbered from 1.

    The event keeps both lists in full, because the deleted rows are gone afterwards and
    a correction must leave the original recognisable (GoBD, section 146(4) AO).
    """
    old_lines = [
        {"position": line.position, **{name: _logged(getattr(line, name)) for name in LINE_FIELDS}}
        for line in invoice.lines.order_by("position")
    ]
    invoice.lines.all().delete()
    InvoiceLine.objects.bulk_create(
        InvoiceLine(
            invoice=invoice,
            position=position,
            **{name: line.get(name) for name in LINE_FIELDS},
        )
        for position, line in enumerate(lines, start=1)
    )
    new_lines = [_logged_line(position, line) for position, line in enumerate(lines, start=1)]
    return "lines", {
        "old": len(old_lines),
        "new": len(new_lines),
        "old_lines": old_lines,
        "new_lines": new_lines,
    }


# --- Checks -------------------------------------------------------------------------


def _note(note: str) -> str:
    text = note.strip()
    if not NOTE_MIN <= len(text) <= NOTE_MAX:
        raise ProblemError(
            422,
            "VALIDATION_FAILED",
            "Validation failed",
            "One or more fields are invalid.",
            errors=[
                {
                    "path": "note",
                    "code": "length",
                    "message": f"Write {NOTE_MIN} to {NOTE_MAX} characters.",
                }
            ],
        )
    return text


def resolve_check(check: Check, user: User, note: str) -> Check:
    """Resolve a `block` or `warn` check with a note; C05 also confirms the IBAN."""
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=check.document_id)
        check = Check.objects.select_for_update().get(pk=check.pk)
        verdict = check_resolvable(check, document, user)
        if not verdict.enabled:
            if verdict.reason_code == "FORBIDDEN_ROLE":
                raise forbidden_role(user.role)
            if verdict.reason_code == "INVALID_TRANSITION":
                raise not_editable()
            raise ProblemError(
                409, "CHECK_NOT_RESOLVABLE", "Can't be resolved", verdict.reason or ""
            )
        text = _note(note)
        moment = clock.now()
        check.resolved_by = user
        check.resolved_at = moment
        check.resolution_note = text
        check.save(update_fields=["resolved_by", "resolved_at", "resolution_note", "updated_at"])
        if check.check_id == "C05":
            _confirm_iban(document, user, text, moment)
        Event.objects.create(
            organization=document.organization,
            document=document,
            actor=user,
            type=Event.Type.CHECK_RESOLVED,
            data={"check_id": check.check_id, "code": check.code, "note": text},
        )
    return check


def _confirm_iban(document: Document, user: User, note: str, moment: datetime) -> None:
    invoice = Invoice.objects.filter(document=document).first()
    if invoice is None or invoice.supplier_id is None or not invoice.payee_iban:
        return
    SupplierIban.objects.filter(
        supplier_id=invoice.supplier_id, iban=invoice.payee_iban, confirmed_at__isnull=True
    ).update(confirmed_by=user, confirmed_at=moment, confirmation_note=note)


# --- Status actions -----------------------------------------------------------------


def mark_reviewed(document: Document, user: User) -> None:
    with transaction.atomic():
        transition(document, Status.AWAITING_APPROVAL, user)
        _after_commit(document, c.SIG_REVIEWED)


def decide(document: Document, user: User, decision: str, comment: str | None) -> None:
    with transaction.atomic():
        transition(document, decision, user, comment=comment)
        _after_commit(document, c.SIG_DECIDED)


def send_back(document: Document, user: User, comment: str) -> None:
    with transaction.atomic():
        transition(document, Status.NEEDS_REVIEW, user, comment=comment)
        _after_commit(document, c.SIG_SENT_BACK)


def reopen(document: Document, user: User) -> None:
    with transaction.atomic():
        transition(document, Status.NEEDS_REVIEW, user)
        _after_commit(document, c.SIG_REOPENED)


def retry(document: Document, user: User) -> None:
    """`failed → processing`, committed, then signal-with-start `retry`.

    The workflow re-reads the status when the signal arrives, so the transition must be
    committed first. If Temporal can't be reached the document goes back to `failed`
    (a system transition) and the caller answers 503 TEMPORAL_UNAVAILABLE.
    """
    reason = document.failure_reason
    transition(document, Status.PROCESSING, user)
    try:
        temporal_client.retry_processing(document.id)
    except temporal_client.TemporalUnavailableError:
        transition(document, Status.FAILED, None, failure_reason=reason)
        raise


def mark_deleted(document: Document, user: User) -> None:
    with transaction.atomic():
        document.deleted_at = clock.now()
        document.save(update_fields=["deleted_at", "updated_at"])
        Event.objects.create(
            organization=document.organization,
            document=document,
            actor=user,
            type=Event.Type.DOCUMENT_DELETED,
            data={},
        )
        _after_commit(document, c.SIG_DELETED)

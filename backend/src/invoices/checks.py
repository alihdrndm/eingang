"""Eingang's business checks C01-C16 (HANDOFF section 8).

`evaluate` decides which checks apply to a document; `apply_findings` makes the `checks`
table match. They run after extraction and again after every edit of invoice fields. A
check that no longer applies is deleted unless it was resolved: resolved checks stay for
the audit trail.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, QuerySet
from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from stdnum import iban as stdnum_iban
from stdnum.eu import vat as stdnum_vat

from eingang import clock
from einvoice.fields import compact_upper
from einvoice.model import CREDIT_NOTE_TYPE_CODES, is_credit_note_type
from invoices.models import Check, Document, Event, Invoice, ValidationReport
from suppliers.models import SupplierIban
from suppliers.trust import first_invoice_id, is_trusted

Severity = Check.Severity

TOLERANCE = Decimal("0.01")
# BR-CO-17 in the vendored EN 16931 Schematron accepts a row's tax within one currency unit
# of taxable * rate / 100, because suppliers that round per line drift by a few cents.
BREAKDOWN_ROW_TOLERANCE = Decimal(1)
CENT = Decimal("0.01")
SIMILARITY_THRESHOLD = 80

# Source of the e-invoicing mandate dates (HANDOFF "Product brief").
MANDATE_SOURCE_URL = (
    "https://ec.europa.eu/digital-building-blocks/sites/spaces/DIGITAL/pages/467108886/"
    "eInvoicing+in+Germany"
)

# Exact texts from HANDOFF "LLM features in Eingang", "Unavailable reasons".
_BUDGET_USED_UP = (
    "Automatic extraction is paused: this installation's AI budget is used up. "
    "Enter the fields by hand."
)
UNAVAILABLE_REASON_TEXTS: dict[str, str] = {
    "disabled": "Automatic extraction is switched off. Enter the fields by hand.",
    "budget_lifetime": _BUDGET_USED_UP,
    "budget_monthly": _BUDGET_USED_UP,
    "budget_daily_public": (
        "Automatic extraction is unavailable today: the demo's AI budget for today is used up. "
        "Enter the fields by hand."
    ),
    "sandbox_limit": "This sandbox has used its 3 automatic extractions. Enter the fields by hand.",
    "error": "Automatic extraction failed. Enter the fields by hand, or retry later.",
}

BANK_DETAILS_CHANGED_MESSAGE = (
    "The bank account differs from earlier invoices from this supplier. Confirm the change by "
    "phone, using a number you already had — not one printed on this invoice."
)
NO_TEXT_LAYER_MESSAGE = "This PDF is a scan with no text. Enter the fields by hand."


@dataclass(frozen=True)
class CheckContext:
    """What the checks need that is not stored in the database."""

    # C09: each difference is {"field", "xml", "pdf"} (section 6).
    pdf_xml_differences: list[dict[str, str]] = field(default_factory=list)
    # C12: one of the keys of UNAVAILABLE_REASON_TEXTS, or None when extraction was not refused.
    extraction_unavailable_reason: str | None = None
    # C08: today in the organisation's time zone (Europe/Berlin).
    today: date = field(default_factory=clock.today)


@dataclass(frozen=True)
class Finding:
    check_id: str
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


def evaluate(document: Document, context: CheckContext) -> list[Finding]:
    """Every check that applies to the document now, in check-ID order."""
    invoice = (
        Invoice.objects.filter(document=document)
        .select_related("supplier", "organization", "document")
        .first()
    )
    report = ValidationReport.objects.filter(document=document).first()
    findings: list[Finding] = []
    if invoice is not None:
        for invoice_check in _INVOICE_CHECKS_BEFORE_C09:
            findings.extend(invoice_check(invoice, context))
    findings.extend(_c09_pdf_xml_mismatch(context))
    findings.extend(_c10_no_text_layer(document))
    if invoice is not None:
        findings.extend(_c11_not_an_einvoice(document, invoice))
    findings.extend(_c12_extraction_unavailable(context))
    if invoice is not None:
        findings.extend(_c13_low_confidence(invoice))
        findings.extend(_c14_addressed_to_someone_else(document, invoice))
    findings.extend(_c15_validation_failed(report))
    if invoice is not None:
        findings.extend(_c16_foreign_currency(invoice))
    return findings


def apply_findings(document: Document, findings: list[Finding]) -> None:
    """Make the document's checks match `findings`.

    New findings are created (with a `check.created` event), unresolved checks that still
    apply get the current message and details, unresolved checks that no longer apply are
    deleted, and resolved checks are never touched. A finding whose check exists resolved
    is not created again. Running it twice with the same findings changes nothing.
    """
    with transaction.atomic():
        existing = list(Check.objects.select_for_update().filter(document=document))
        resolved_keys = {_key_of_check(check) for check in existing if check.resolved_at}
        unresolved: dict[tuple[str, str | None], Check] = {}
        stale: list[Check] = []
        for check in existing:
            if check.resolved_at is not None:
                continue
            key = _key_of_check(check)
            if key in unresolved:
                stale.append(check)  # a second unresolved check for the same finding
            else:
                unresolved[key] = check

        wanted: set[tuple[str, str | None]] = set()
        for finding in findings:
            key = _key_of_finding(finding)
            if key in wanted:
                continue
            wanted.add(key)
            current = unresolved.get(key)
            if current is None:
                if key not in resolved_keys:
                    _create(document, finding)
            elif current.message != finding.message or current.details != finding.details:
                current.message = finding.message
                current.details = finding.details
                current.save(update_fields=["message", "details", "updated_at"])

        gone = [check for key, check in unresolved.items() if key not in wanted]
        stale.extend(gone)
        if stale:
            Check.objects.filter(id__in=[check.id for check in stale]).delete()
        # A finding that no longer applies leaves a trace, so a removed blocking check (a C05
        # after an IBAN edit, say) can be told apart from one that was never raised.
        for check in gone:
            Event.objects.create(
                organization_id=document.organization_id,
                document=document,
                actor=None,
                type=Event.Type.CHECK_CLEARED,
                data={"check_id": check.check_id, "code": check.code},
            )


def _create(document: Document, finding: Finding) -> None:
    Check.objects.create(
        document=document,
        check_id=finding.check_id,
        code=finding.code,
        severity=finding.severity,
        message=finding.message,
        details=finding.details,
    )
    Event.objects.create(
        organization_id=document.organization_id,
        document=document,
        actor=None,
        type=Event.Type.CHECK_CREATED,
        data={"check_id": finding.check_id},
    )


def _key_of_finding(finding: Finding) -> tuple[str, str | None]:
    return _key(finding.check_id, finding.details)


def _key_of_check(check: Check) -> tuple[str, str | None]:
    details: object = check.details
    return _key(check.check_id, details)


def _key(check_id: str, details: object) -> tuple[str, str | None]:
    """C13 exists once per field; every other check once per document."""
    if check_id != "C13" or not isinstance(details, dict):
        return (check_id, None)
    value = details.get("field")
    return (check_id, value if isinstance(value, str) else None)


# --- helpers -----------------------------------------------------------------------------


def _earlier_invoices(invoice: Invoice, *, with_deleted: bool = False) -> QuerySet[Invoice]:
    """Other invoices of the organisation received before this one, by default non-deleted.

    "Received earlier" means an earlier `received_at`, ties broken by document id.
    """
    document = invoice.document
    earlier = Q(document__received_at__lt=document.received_at) | Q(
        document__received_at=document.received_at, document__id__lt=document.id
    )
    invoices = Invoice.objects.filter(organization_id=invoice.organization_id)
    if not with_deleted:
        invoices = invoices.filter(document__deleted_at__isnull=True)
    return (
        invoices.filter(earlier)
        .exclude(id=invoice.id)
        .order_by("document__received_at", "document__id")
    )


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _differs(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > TOLERANCE


def _line_sum(invoice: Invoice) -> Decimal | None:
    """Σ line net amounts; None when there are no lines or any line has no net amount."""
    amounts = list(invoice.lines.values_list("net_amount", flat=True))
    if not amounts or any(amount is None for amount in amounts):
        return None
    return sum((amount for amount in amounts if amount is not None), Decimal(0))


def _breakdown_rows(invoice: Invoice) -> list[object]:
    rows: object = invoice.tax_breakdown
    return rows if isinstance(rows, list) else []


def _breakdown_sum(invoice: Invoice, key: str) -> Decimal | None:
    """Σ of one breakdown amount; None without a breakdown or with an unreadable row.

    An empty breakdown is "not given" (for example on a plain-PDF extraction), not a sum of
    zero, so it does not fake a difference (docs/DECISIONS.md).
    """
    rows = _breakdown_rows(invoice)
    if not rows:
        return None
    total = Decimal(0)
    for row in rows:
        amount = _decimal(row.get(key)) if isinstance(row, dict) else None
        if amount is None:
            return None
        total += amount
    return total


def _breakdown_row_problems(invoice: Invoice) -> list[str]:
    """BR-CO-17: each row's tax is its taxable amount * rate / 100, rounded to the cent,
    within `BREAKDOWN_ROW_TOLERANCE` like the official rule.

    A row without a rate, taxable amount or tax amount is skipped.
    """
    problems: list[str] = []
    for row in _breakdown_rows(invoice):
        if not isinstance(row, dict):
            continue
        rate = _decimal(row.get("rate"))
        taxable = _decimal(row.get("taxable_amount"))
        tax = _decimal(row.get("tax_amount"))
        if rate is None or taxable is None or tax is None:
            continue
        expected = (taxable * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        if abs(abs(expected) - abs(tax)) >= BREAKDOWN_ROW_TOLERANCE:
            problems.append(
                f"in the VAT breakdown, {rate.quantize(CENT)}% of {_money(taxable)} is "
                f"{_money(expected)}, but the tax amount is {_money(tax)}"
            )
    return problems


# --- C01 to C08 -----------------------------------------------------------------------------


def _c01_arithmetic(invoice: Invoice, context: CheckContext) -> list[Finding]:
    problems: list[tuple[str, str]] = []

    line_sum = _line_sum(invoice)
    line_total = invoice.line_total
    if line_sum is not None and line_total is not None and _differs(line_sum, line_total):
        problems.append(
            (
                "line_total",
                f"the lines add up to {_money(line_sum)}, "
                f"but the line total is {_money(line_total)}",
            )
        )

    net, tax, gross = invoice.net_total, invoice.tax_total, invoice.gross_total
    if line_total is not None and net is not None:
        # BR-CO-13. No document-level allowance or charge total means none (EN 16931). Both
        # are amounts, but an AI-read "Rabatt -50,00" keeps its printed minus sign.
        allowances = abs(invoice.allowance_total or Decimal(0))
        charges = abs(invoice.charge_total or Decimal(0))
        expected_net = line_total - allowances + charges
        if _differs(expected_net, net):
            problems.append(
                (
                    "net_total",
                    f"line total minus allowances plus charges is {_money(expected_net)}, "
                    f"but the net total is {_money(net)}",
                )
            )

    if net is not None and tax is not None and gross is not None and _differs(net + tax, gross):
        problems.append(
            (
                "gross_total",
                f"net total plus tax total is {_money(net + tax)}, "
                f"but the gross total is {_money(gross)}",
            )
        )

    breakdown_sum = _breakdown_sum(invoice, "tax_amount")
    if breakdown_sum is not None and tax is not None and _differs(breakdown_sum, tax):
        problems.append(
            (
                "tax_total",
                f"the VAT breakdown adds up to {_money(breakdown_sum)}, "
                f"but the tax total is {_money(tax)}",
            )
        )

    taxable_sum = _breakdown_sum(invoice, "taxable_amount")
    if taxable_sum is not None and net is not None and _differs(taxable_sum, net):
        problems.append(
            (
                "tax_breakdown",
                f"the taxable amounts in the VAT breakdown add up to {_money(taxable_sum)}, "
                f"but the net total is {_money(net)}",
            )
        )

    problems.extend(("tax_breakdown", text) for text in _breakdown_row_problems(invoice))

    prepaid, payable = invoice.prepaid_amount, invoice.payable_amount
    if (
        gross is not None
        and prepaid is not None
        and payable is not None
        and _differs(gross - prepaid, payable)
    ):
        problems.append(
            (
                "payable_amount",
                f"gross total minus prepaid amount is {_money(gross - prepaid)}, "
                f"but the amount payable is {_money(payable)}",
            )
        )

    if not problems:
        return []
    severity = (
        Severity.BLOCK
        if invoice.extraction_method == Invoice.ExtractionMethod.LLM
        else Severity.WARN
    )
    message = "The amounts don't add up: " + "; ".join(text for _, text in problems) + "."
    return [
        Finding(
            check_id="C01",
            code="ARITHMETIC",
            severity=severity,
            message=message,
            details={"fields": list(dict.fromkeys(name for name, _ in problems))},
        )
    ]


def _c02_duplicate_number(invoice: Invoice, context: CheckContext) -> list[Finding]:
    if invoice.supplier_id is None or not invoice.normalised_number:
        return []
    other = (
        _earlier_invoices(invoice)
        .filter(supplier_id=invoice.supplier_id, normalised_number=invoice.normalised_number)
        .first()
    )
    if other is None:
        return []
    return [
        Finding(
            check_id="C02",
            code="DUPLICATE_NUMBER",
            severity=Severity.BLOCK,
            message=(
                "An invoice from this supplier with the same number was received earlier. "
                "Open the earlier invoice to compare."
            ),
            details={"document_id": str(other.document_id)},
        )
    ]


def _c03_duplicate_similar(invoice: Invoice, context: CheckContext) -> list[Finding]:
    if invoice.supplier_id is None or invoice.gross_total is None or invoice.issue_date is None:
        return []
    window = invoice.organization.duplicate_window_days
    candidates = _earlier_invoices(invoice).filter(
        supplier_id=invoice.supplier_id,
        gross_total=invoice.gross_total,
        issue_date__isnull=False,
    )
    # A credit note for an invoice often repeats its gross total; only compare like with like.
    if is_credit_note_type(invoice.type_code):
        candidates = candidates.filter(type_code__in=CREDIT_NOTE_TYPE_CODES)
    else:
        candidates = candidates.exclude(type_code__in=CREDIT_NOTE_TYPE_CODES)
    for other in candidates:
        if other.issue_date is None or other.normalised_number == invoice.normalised_number:
            continue
        if abs((other.issue_date - invoice.issue_date).days) <= window:
            return [
                Finding(
                    check_id="C03",
                    code="DUPLICATE_SIMILAR",
                    severity=Severity.WARN,
                    message=(
                        "An invoice from this supplier with the same gross total and an issue "
                        f"date within {window} days was received earlier, under a different "
                        "number. Open the earlier invoice to compare."
                    ),
                    details={"document_id": str(other.document_id)},
                )
            ]
    return []


def _c04_new_supplier(invoice: Invoice, context: CheckContext) -> list[Finding]:
    supplier = invoice.supplier
    if supplier is None:
        return []
    if _earlier_invoices(invoice).filter(supplier_id=supplier.id).exists():
        return []
    return [
        Finding(
            check_id="C04",
            code="NEW_SUPPLIER",
            severity=Severity.INFO,
            message=(
                f"First invoice from {supplier.name}. There are no earlier invoices from this "
                "supplier to compare the bank account with."
            ),
        )
    ]


def _c05_bank_details_changed(invoice: Invoice, context: CheckContext) -> list[Finding]:
    supplier = invoice.supplier
    if not invoice.payee_iban or supplier is None:
        return []
    # A deleted earlier invoice still counts, so deleting it can't make this one the first.
    if not _earlier_invoices(invoice, with_deleted=True).filter(supplier_id=supplier.id).exists():
        return []
    entry = SupplierIban.objects.filter(supplier=supplier, iban=invoice.payee_iban).first()
    if entry is not None and is_trusted(entry, first_invoice_id(supplier)):
        return []
    return [
        Finding(
            check_id="C05",
            code="BANK_DETAILS_CHANGED",
            severity=Severity.BLOCK,
            message=BANK_DETAILS_CHANGED_MESSAGE,
        )
    ]


def _c06_iban_invalid(invoice: Invoice, context: CheckContext) -> list[Finding]:
    if not invoice.payee_iban or stdnum_iban.is_valid(invoice.payee_iban):
        return []
    return [
        Finding(
            check_id="C06",
            code="IBAN_INVALID",
            severity=Severity.BLOCK,
            message="The IBAN is not valid. Check it against the invoice.",
        )
    ]


def _c07_vat_id_invalid(invoice: Invoice, context: CheckContext) -> list[Finding]:
    if not invoice.seller_vat_id or stdnum_vat.is_valid(invoice.seller_vat_id):
        return []
    return [
        Finding(
            check_id="C07",
            code="VAT_ID_INVALID",
            severity=Severity.WARN,
            message="The seller's VAT ID is not valid. Check it against the invoice.",
        )
    ]


def _c08_overdue(invoice: Invoice, context: CheckContext) -> list[Finding]:
    if invoice.due_date is None or invoice.due_date >= context.today:
        return []
    # Nothing is owed on a credit note or when nothing is left to pay.
    nothing_to_pay = invoice.payable_amount is not None and invoice.payable_amount <= 0
    if is_credit_note_type(invoice.type_code) or nothing_to_pay:
        return []
    return [
        Finding(
            check_id="C08",
            code="OVERDUE",
            severity=Severity.INFO,
            message=f"The invoice was due on {invoice.due_date.isoformat()}.",
        )
    ]


_INVOICE_CHECKS_BEFORE_C09: tuple[Callable[[Invoice, CheckContext], list[Finding]], ...] = (
    _c01_arithmetic,
    _c02_duplicate_number,
    _c03_duplicate_similar,
    _c04_new_supplier,
    _c05_bank_details_changed,
    _c06_iban_invalid,
    _c07_vat_id_invalid,
    _c08_overdue,
)


# --- C09 to C16 -----------------------------------------------------------------------------


def _c09_pdf_xml_mismatch(context: CheckContext) -> list[Finding]:
    differences = context.pdf_xml_differences
    if not differences:
        return []
    fields = ", ".join(difference.get("field", "") for difference in differences)
    return [
        Finding(
            check_id="C09",
            code="PDF_XML_MISMATCH",
            severity=Severity.WARN,
            message=(
                f"The visible PDF shows different values from the embedded XML: {fields}. "
                "The XML is the authoritative part of the invoice."
            ),
            details={"differences": [dict(difference) for difference in differences]},
        )
    ]


def _c10_no_text_layer(document: Document) -> list[Finding]:
    if document.kind != Document.Kind.PDF_NO_TEXT:
        return []
    return [
        Finding(
            check_id="C10",
            code="NO_TEXT_LAYER",
            severity=Severity.BLOCK,
            message=NO_TEXT_LAYER_MESSAGE,
        )
    ]


_NOT_AN_EINVOICE_BY_KIND: dict[str, str] = {
    Document.Kind.PDF_TEXT: "This is a plain PDF",
    Document.Kind.PDF_NO_TEXT: "This is a scanned PDF",
    Document.Kind.LEGACY_ZUGFERD1: "This is a legacy ZUGFeRD 1 invoice",
    Document.Kind.HYBRID_PDF_UNSUPPORTED: "This is an unsupported hybrid PDF",
}


def _c11_not_an_einvoice(document: Document, invoice: Invoice) -> list[Finding]:
    if invoice.is_einvoice:
        return []
    case = _NOT_AN_EINVOICE_BY_KIND.get(document.kind or "")
    if case is None:
        case = f"This invoice has the profile {invoice.profile or 'UNKNOWN'}"
    message = (
        f"{case}, which is not an e-invoice. Suppliers may still send such invoices during "
        "the transition period. Source of the mandate dates: European Commission, "
        f'"eInvoicing in Germany" ({MANDATE_SOURCE_URL}).'
    )
    return [
        Finding(
            check_id="C11",
            code="NOT_AN_EINVOICE",
            severity=Severity.INFO,
            message=message,
            details={"source_url": MANDATE_SOURCE_URL},
        )
    ]


def _c12_extraction_unavailable(context: CheckContext) -> list[Finding]:
    reason = context.extraction_unavailable_reason
    if reason is None:
        return []
    return [
        Finding(
            check_id="C12",
            code="EXTRACTION_UNAVAILABLE",
            severity=Severity.BLOCK,
            message=UNAVAILABLE_REASON_TEXTS[reason],
            details={"reason": reason},
        )
    ]


def _c13_low_confidence(invoice: Invoice) -> list[Finding]:
    confidence: object = invoice.field_confidence
    if not isinstance(confidence, dict):
        return []
    low_fields = sorted(
        name for name, level in confidence.items() if isinstance(name, str) and level == "low"
    )
    return [
        Finding(
            check_id="C13",
            code="LOW_CONFIDENCE",
            severity=Severity.BLOCK,
            message=(
                f"Low confidence: {name.replace('_', ' ')}. Check the value against the "
                "document and correct it, or resolve the check with a note."
            ),
            details={"field": name},
        )
        for name in low_fields
    ]


def _c14_addressed_to_someone_else(document: Document, invoice: Invoice) -> list[Finding]:
    organization = invoice.organization
    message: str | None = None
    if invoice.buyer_vat_id:
        if organization.vat_id and compact_upper(organization.vat_id) != compact_upper(
            invoice.buyer_vat_id
        ):
            message = (
                "The buyer's VAT ID on this invoice differs from your organisation's VAT ID. "
                "The invoice may be addressed to someone else."
            )
    elif (
        invoice.buyer_name
        and fuzz.token_set_ratio(invoice.buyer_name, organization.name, processor=default_process)
        < SIMILARITY_THRESHOLD
    ):
        message = (
            "The buyer's name on this invoice does not match your organisation's name. "
            "The invoice may be addressed to someone else."
        )
    if message is None:
        return []
    return [
        Finding(
            check_id="C14",
            code="ADDRESSED_TO_SOMEONE_ELSE",
            severity=Severity.WARN,
            message=message,
        )
    ]


def _c15_validation_failed(report: ValidationReport | None) -> list[Finding]:
    if report is None or report.status != ValidationReport.Status.INVALID:
        return []
    count = report.fatal_count
    issues = "issue" if count == 1 else "issues"
    return [
        Finding(
            check_id="C15",
            code="VALIDATION_FAILED",
            severity=Severity.BLOCK,
            message=(
                f"Validation found {count} fatal {issues}, so the invoice can't go to approval "
                "as it is. Ask the supplier for a corrected invoice, or accept it anyway with "
                "a note."
            ),
            details={"fatal_count": count},
        )
    ]


def _c16_foreign_currency(invoice: Invoice) -> list[Finding]:
    if not invoice.currency or invoice.currency == "EUR":
        return []
    return [
        Finding(
            check_id="C16",
            code="FOREIGN_CURRENCY",
            severity=Severity.INFO,
            message=f"The invoice is in {invoice.currency}, not EUR.",
        )
    ]


def stored_context(document: Document) -> CheckContext:
    """The processing-time context, rebuilt from the document's C09 and C12 checks.

    The PDF comparison and the extraction refusal happen only while processing; a later
    re-run (after an edit) keeps what they found instead of dropping those checks.
    """
    differences: list[dict[str, str]] = []
    reason: str | None = None
    for check in Check.objects.filter(document=document, check_id__in=["C09", "C12"]):
        details: object = check.details
        if not isinstance(details, dict):
            continue
        stored_differences = details.get("differences")
        if check.check_id == "C09" and isinstance(stored_differences, list):
            differences = [dict(item) for item in stored_differences if isinstance(item, dict)]
        stored_reason = details.get("reason")
        if check.check_id == "C12" and isinstance(stored_reason, str):
            reason = stored_reason
    return CheckContext(pdf_xml_differences=differences, extraction_unavailable_reason=reason)


def recheck(document: Document) -> None:
    """Run every check again with the stored context (after a person edits the invoice)."""
    apply_findings(document, evaluate(document, stored_context(document)))

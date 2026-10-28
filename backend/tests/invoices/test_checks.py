from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounts.models import Organization
from eingang import clock
from invoices.checks import (
    MANDATE_SOURCE_URL,
    CheckContext,
    Finding,
    apply_findings,
    evaluate,
)
from invoices.models import Check, Document, Event, Invoice, InvoiceLine, ValidationReport
from suppliers.matching import match_supplier
from suppliers.models import SupplierIban
from tests.factories import RECEIVED, make_document

pytestmark = pytest.mark.django_db

ORG_VAT_ID = "DE291746055"  # the `organization` fixture's VAT ID
IBAN = "DE89370400440532013000"
OTHER_IBAN = "DE02120300000000202051"
TODAY = date(2026, 3, 10)
CONTEXT = CheckContext(today=TODAY)


def invoice_on(
    organization: Organization,
    *,
    minutes: int,
    kind: str = Document.Kind.XML,
    match: bool = True,
    **fields: object,
) -> Invoice:
    """A consistent XRechnung invoice addressed to the organisation; `fields` override it."""
    document = make_document(organization, received_minutes=minutes, kind=kind)
    values: dict[str, object] = {
        "extraction_method": Invoice.ExtractionMethod.XML,
        "is_einvoice": True,
        "profile": "XRECHNUNG",
        "invoice_number": f"RE-2026-{minutes:04d}",
        "issue_date": date(2026, 3, 1),
        "due_date": date(2026, 3, 31),
        "currency": "EUR",
        "seller_name": "Elektro Kessler GmbH",
        "buyer_name": "Holzwerk Brandt GmbH",
        "buyer_vat_id": ORG_VAT_ID,
        "net_total": Decimal("1000.00"),
        "tax_total": Decimal("190.00"),
        "gross_total": Decimal("1190.00"),
        "payable_amount": Decimal("1190.00"),
        "tax_breakdown": [
            {"category": "S", "rate": "19.00", "taxable_amount": "1000.00", "tax_amount": "190.00"}
        ],
        **fields,
    }
    invoice = Invoice.objects.create(document=document, organization=organization, **values)
    if match:
        match_supplier(invoice)
    return invoice


def run(invoice: Invoice, context: CheckContext = CONTEXT) -> list[Finding]:
    return evaluate(invoice.document, context)


def ids(findings: list[Finding]) -> list[str]:
    return [finding.check_id for finding in findings]


def only(findings: list[Finding], check_id: str) -> Finding:
    matching = [finding for finding in findings if finding.check_id == check_id]
    assert len(matching) == 1, ids(findings)
    return matching[0]


def add_lines(invoice: Invoice, *amounts: str | None) -> None:
    for position, amount in enumerate(amounts, start=1):
        InvoiceLine.objects.create(
            invoice=invoice,
            position=position,
            net_amount=None if amount is None else Decimal(amount),
        )


def add_report(document: Document, status: str, fatal_count: int = 0) -> None:
    ValidationReport.objects.create(
        document=document,
        status=status,
        engine="test",
        fatal_count=fatal_count,
        ran_at=RECEIVED,
    )


def test_a_consistent_first_invoice_only_gets_C04(organization: Organization) -> None:
    assert ids(run(invoice_on(organization, minutes=1))) == ["C04"]


def test_context_today_defaults_to_the_clock(fixed_clock: clock.FixedClock) -> None:
    assert CheckContext().today == fixed_clock.today()


# --- C01 ---------------------------------------------------------------------------------


def test_C01_line_sum_differs_from_line_total(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, line_total=Decimal("1000.02"))
    add_lines(invoice, "600.00", "400.00")
    finding = only(run(invoice), "C01")
    assert finding.code == "ARITHMETIC"
    assert finding.details == {"fields": ["line_total"]}
    assert "1000.00" in finding.message
    assert "1000.02" in finding.message


def test_C01_llm_is_block_xml_is_warn(organization: Organization) -> None:
    xml = invoice_on(organization, minutes=1, gross_total=Decimal("1200.00"))
    llm = invoice_on(
        organization,
        minutes=2,
        gross_total=Decimal("1200.00"),
        extraction_method=Invoice.ExtractionMethod.LLM,
    )
    manual = invoice_on(
        organization,
        minutes=3,
        gross_total=Decimal("1200.00"),
        extraction_method=Invoice.ExtractionMethod.MANUAL,
    )
    assert only(run(xml), "C01").severity == "warn"
    assert only(run(llm), "C01").severity == "block"
    assert only(run(manual), "C01").severity == "warn"


def test_C01_difference_of_one_cent_is_allowed(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        line_total=Decimal("1000.01"),
        gross_total=Decimal("1190.01"),
        payable_amount=Decimal("1190.00"),
        prepaid_amount=Decimal("0.00"),
        tax_breakdown=[{"category": "S", "rate": "19.00", "tax_amount": "190.01"}],
    )
    add_lines(invoice, "1000.00")
    assert "C01" not in ids(run(invoice))


def test_C01_difference_of_two_cents_is_not(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, gross_total=Decimal("1190.02"))
    finding = only(run(invoice), "C01")
    assert finding.details == {"fields": ["gross_total"]}


def test_C01_breakdown_sum_differs_from_tax_total(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        tax_breakdown=[
            {"category": "S", "rate": "19.00", "taxable_amount": "500.00", "tax_amount": "95.00"},
            {"category": "S", "rate": "19.00", "taxable_amount": "500.00", "tax_amount": "94.00"},
        ],
    )
    assert only(run(invoice), "C01").details == {"fields": ["tax_total"]}


def test_C01_payable_differs_from_gross_minus_prepaid(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        prepaid_amount=Decimal("190.00"),
        payable_amount=Decimal("1190.00"),
    )
    finding = only(run(invoice), "C01")
    assert finding.details == {"fields": ["payable_amount"]}


def test_C01_lists_every_failed_comparison(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        line_total=Decimal("5.00"),
        gross_total=Decimal("2000.00"),
        tax_breakdown=[{"tax_amount": "1.00"}],
        prepaid_amount=Decimal("0.00"),
    )
    add_lines(invoice, "1.00")
    finding = only(run(invoice), "C01")
    assert finding.details == {
        "fields": ["line_total", "gross_total", "tax_total", "payable_amount"]
    }


def test_C01_an_empty_breakdown_is_not_compared(organization: Organization) -> None:
    # No breakdown means "not given", not a sum of zero (docs/DECISIONS.md).
    invoice = invoice_on(organization, minutes=1, tax_breakdown=[])
    assert "C01" not in {finding.check_id for finding in run(invoice)}


def test_C01_skips_none_fields(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        net_total=None,
        tax_total=None,
        line_total=None,
        prepaid_amount=None,
        gross_total=Decimal("5.00"),
        payable_amount=Decimal("1.00"),
        tax_breakdown=[{"tax_amount": "1.00"}],
    )
    add_lines(invoice, "7.00")
    assert "C01" not in ids(run(invoice))


def test_C01_skips_the_line_sum_without_lines(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, line_total=Decimal("999.00"))
    assert "C01" not in ids(run(invoice))


def test_C01_skips_the_line_sum_when_a_line_has_no_amount(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, line_total=Decimal("999.00"))
    add_lines(invoice, "1.00", None)
    assert "C01" not in ids(run(invoice))


def test_C01_an_empty_invoice_has_nothing_to_compare(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        net_total=None,
        tax_total=None,
        gross_total=None,
        payable_amount=None,
    )
    assert "C01" not in ids(run(invoice))


# --- C02 ---------------------------------------------------------------------------------


def test_C02_same_normalised_number_received_earlier(organization: Organization) -> None:
    earlier = invoice_on(organization, minutes=1, invoice_number="RE-2026/0412")
    later = invoice_on(organization, minutes=2, invoice_number="re 2026 0412")
    finding = only(run(later), "C02")
    assert finding.code == "DUPLICATE_NUMBER"
    assert finding.severity == "block"
    assert finding.details == {"document_id": str(earlier.document_id)}
    assert "C02" not in ids(run(earlier))


def test_C02_ties_in_received_at_are_broken_by_id(organization: Organization) -> None:
    first = invoice_on(organization, minutes=5, invoice_number="RE-1")
    second = invoice_on(organization, minutes=5, invoice_number="RE-1")
    assert first.document.received_at == second.document.received_at
    assert first.document_id < second.document_id
    assert only(run(second), "C02").details == {"document_id": str(first.document_id)}
    assert "C02" not in ids(run(first))


def test_C02_ignores_deleted_documents(organization: Organization) -> None:
    earlier = invoice_on(organization, minutes=1, invoice_number="RE-1")
    later = invoice_on(organization, minutes=2, invoice_number="RE-1")
    Document.objects.filter(id=earlier.document_id).update(deleted_at=RECEIVED)
    assert "C02" not in ids(run(later))


def test_C02_needs_the_same_supplier(organization: Organization) -> None:
    invoice_on(organization, minutes=1, invoice_number="RE-1", seller_name="Druckerei Sommer")
    later = invoice_on(organization, minutes=2, invoice_number="RE-1")
    assert "C02" not in ids(run(later))


def test_C02_stays_within_the_organisation(
    organization: Organization, other_organization: Organization
) -> None:
    invoice_on(other_organization, minutes=1, invoice_number="RE-1")
    later = invoice_on(organization, minutes=2, invoice_number="RE-1")
    assert "C02" not in ids(run(later))


# --- C03 ---------------------------------------------------------------------------------


def test_C03_same_gross_within_the_window_with_another_number(
    organization: Organization,
) -> None:
    earlier = invoice_on(organization, minutes=1, issue_date=date(2026, 3, 1))
    later = invoice_on(organization, minutes=2, issue_date=date(2026, 3, 31))
    finding = only(run(later), "C03")
    assert finding.code == "DUPLICATE_SIMILAR"
    assert finding.severity == "warn"
    assert finding.details == {"document_id": str(earlier.document_id)}
    assert "C03" not in ids(run(earlier))


def test_C03_not_outside_the_window(organization: Organization) -> None:
    invoice_on(organization, minutes=1, issue_date=date(2026, 3, 1))
    later = invoice_on(organization, minutes=2, issue_date=date(2026, 4, 1))
    assert "C03" not in ids(run(later))


def test_C03_uses_the_organisation_window(organization: Organization) -> None:
    organization.duplicate_window_days = 5
    organization.save()
    invoice_on(organization, minutes=1, issue_date=date(2026, 3, 1))
    later = invoice_on(organization, minutes=2, issue_date=date(2026, 3, 7))
    assert "C03" not in ids(run(later))


def test_C03_not_for_a_different_gross_or_the_same_number(organization: Organization) -> None:
    invoice_on(organization, minutes=1, invoice_number="RE-1")
    same_number = invoice_on(organization, minutes=2, invoice_number="RE-1")
    other_gross = invoice_on(
        organization,
        minutes=3,
        gross_total=Decimal("1190.01"),
        payable_amount=Decimal("1190.01"),
        net_total=Decimal("1000.01"),
    )
    assert ids(run(same_number)) == ["C02"]
    assert "C03" not in ids(run(other_gross))


def test_C03_a_credit_note_does_not_match_an_invoice(organization: Organization) -> None:
    invoice_on(organization, minutes=1, type_code=380)
    credit_note = invoice_on(organization, minutes=2, type_code=381)
    assert "C03" not in ids(run(credit_note))
    later_invoice = invoice_on(organization, minutes=3, type_code=None)
    assert only(run(later_invoice), "C03").details == {
        "document_id": str(Invoice.objects.get(type_code=380).document_id)
    }


def test_C03_an_invoice_does_not_match_an_earlier_credit_note(organization: Organization) -> None:
    invoice_on(organization, minutes=1, type_code=261)
    invoice = invoice_on(organization, minutes=2, type_code=None)
    assert "C03" not in ids(run(invoice))


def test_C03_credit_notes_match_each_other(organization: Organization) -> None:
    earlier = invoice_on(organization, minutes=1, type_code=381)
    later = invoice_on(organization, minutes=2, type_code=396)
    assert only(run(later), "C03").details == {"document_id": str(earlier.document_id)}


def test_C03_ignores_deleted_documents(organization: Organization) -> None:
    earlier = invoice_on(organization, minutes=1)
    later = invoice_on(organization, minutes=2)
    Document.objects.filter(id=earlier.document_id).update(deleted_at=RECEIVED)
    assert "C03" not in ids(run(later))


# --- C04 ---------------------------------------------------------------------------------


def test_C04_first_invoice_from_the_supplier(organization: Organization) -> None:
    first = invoice_on(organization, minutes=1)
    second = invoice_on(organization, minutes=2, gross_total=Decimal("5.00"))
    finding = only(run(first), "C04")
    assert finding.code == "NEW_SUPPLIER"
    assert finding.severity == "info"
    assert finding.message.startswith("First invoice from Elektro Kessler GmbH.")
    assert "C04" not in ids(run(second))


def test_C04_not_without_a_supplier(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, seller_name=None)
    assert invoice.supplier_id is None
    assert "C04" not in ids(run(invoice))


def test_C04_deleted_earlier_documents_do_not_count(organization: Organization) -> None:
    earlier = invoice_on(organization, minutes=1)
    later = invoice_on(organization, minutes=2)
    Document.objects.filter(id=earlier.document_id).update(deleted_at=RECEIVED)
    assert "C04" in ids(run(later))


# --- C05 ---------------------------------------------------------------------------------


def test_C05_untrusted_iban_after_earlier_invoices(organization: Organization) -> None:
    invoice_on(organization, minutes=1, payee_iban=IBAN)
    changed = invoice_on(organization, minutes=2, payee_iban=OTHER_IBAN)
    finding = only(run(changed), "C05")
    assert finding.code == "BANK_DETAILS_CHANGED"
    assert finding.severity == "block"
    assert finding.message == (
        "The bank account differs from earlier invoices from this supplier. Confirm the change "
        "by phone, using a number you already had — not one printed on this invoice."
    )


def test_C05_not_for_the_iban_of_the_first_invoice(organization: Organization) -> None:
    first = invoice_on(organization, minutes=1, payee_iban=IBAN)
    same = invoice_on(organization, minutes=2, payee_iban=IBAN)
    assert "C05" not in ids(run(first))
    assert "C05" not in ids(run(same))


def test_C05_not_for_a_confirmed_iban(organization: Organization) -> None:
    invoice_on(organization, minutes=1, payee_iban=IBAN)
    changed = invoice_on(organization, minutes=2, payee_iban=OTHER_IBAN)
    SupplierIban.objects.filter(iban=OTHER_IBAN).update(confirmed_at=RECEIVED)
    assert "C05" not in ids(run(changed))


def test_C05_not_on_the_first_invoice_or_without_iban(organization: Organization) -> None:
    first = invoice_on(organization, minutes=1, payee_iban=OTHER_IBAN)
    without = invoice_on(organization, minutes=2)
    assert "C05" not in ids(run(first))
    assert "C05" not in ids(run(without))


def test_C05_fires_when_the_first_invoice_had_no_iban(organization: Organization) -> None:
    invoice_on(organization, minutes=1)
    later = invoice_on(organization, minutes=2, payee_iban=IBAN)
    assert "C05" in ids(run(later))


# --- C06 to C08 --------------------------------------------------------------------------


def test_C06_invalid_iban(organization: Organization) -> None:
    invalid = invoice_on(organization, minutes=1, payee_iban="DE89370400440532013001")
    valid = invoice_on(organization, minutes=2, payee_iban=IBAN, seller_name="Andere AG")
    finding = only(run(invalid), "C06")
    assert finding.code == "IBAN_INVALID"
    assert finding.severity == "block"
    assert "C06" not in ids(run(valid))


def test_C07_invalid_seller_vat_id(organization: Organization) -> None:
    invalid = invoice_on(organization, minutes=1, seller_vat_id="DE123456789")
    valid = invoice_on(organization, minutes=2, seller_vat_id="DE100000016")
    finding = only(run(invalid), "C07")
    assert finding.code == "VAT_ID_INVALID"
    assert finding.severity == "warn"
    assert "C07" not in ids(run(valid))


def test_C07_not_without_a_seller_vat_id(organization: Organization) -> None:
    assert "C07" not in ids(run(invoice_on(organization, minutes=1, seller_vat_id=None)))


def test_C08_due_date_before_today(organization: Organization) -> None:
    overdue = invoice_on(organization, minutes=1, due_date=TODAY - timedelta(days=1))
    due_today = invoice_on(organization, minutes=2, due_date=TODAY, seller_name="B GmbH")
    no_date = invoice_on(organization, minutes=3, due_date=None, seller_name="C GmbH")
    finding = only(run(overdue), "C08")
    assert finding.code == "OVERDUE"
    assert finding.severity == "info"
    assert "C08" not in ids(run(due_today))
    assert "C08" not in ids(run(no_date))


def test_C08_uses_the_context_date(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, due_date=TODAY)
    later = CheckContext(today=TODAY + timedelta(days=1))
    assert "C08" in ids(run(invoice, later))


@pytest.mark.parametrize("type_code", [381, 261, 396])
def test_C08_not_for_a_credit_note(organization: Organization, type_code: int) -> None:
    due = TODAY - timedelta(days=1)
    credit_note = invoice_on(organization, minutes=1, due_date=due, type_code=type_code)
    assert "C08" not in ids(run(credit_note))


@pytest.mark.parametrize("payable", ["0.00", "-10.00"])
def test_C08_not_when_nothing_is_left_to_pay(organization: Organization, payable: str) -> None:
    paid = invoice_on(
        organization,
        minutes=1,
        due_date=TODAY - timedelta(days=1),
        prepaid_amount=Decimal("1190.00") - Decimal(payable),
        payable_amount=Decimal(payable),
    )
    assert "C08" not in ids(run(paid))


def test_C08_still_fires_without_a_payable_amount(organization: Organization) -> None:
    invoice = invoice_on(
        organization, minutes=1, due_date=TODAY - timedelta(days=1), payable_amount=None
    )
    assert "C08" in ids(run(invoice))


# --- C09 to C12 --------------------------------------------------------------------------


def test_C09_pdf_differs_from_xml(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, kind=Document.Kind.HYBRID_PDF)
    differences = [{"field": "gross_total", "xml": "1200.00", "pdf": "1190.00"}]
    finding = only(run(invoice, CheckContext(pdf_xml_differences=differences, today=TODAY)), "C09")
    assert finding.code == "PDF_XML_MISMATCH"
    assert finding.severity == "warn"
    assert finding.details == {"differences": differences}
    assert "gross_total" in finding.message
    assert "C09" not in ids(run(invoice))


def test_C10_scan_without_text_layer(organization: Organization) -> None:
    scan = invoice_on(
        organization,
        minutes=1,
        kind=Document.Kind.PDF_NO_TEXT,
        extraction_method=Invoice.ExtractionMethod.MANUAL,
        is_einvoice=False,
    )
    finding = only(run(scan), "C10")
    assert finding.code == "NO_TEXT_LAYER"
    assert finding.severity == "block"
    assert finding.message == "This PDF is a scan with no text. Enter the fields by hand."
    assert "C10" not in ids(run(invoice_on(organization, minutes=2, seller_name="B GmbH")))


@pytest.mark.parametrize(
    ("kind", "profile", "named"),
    [
        (Document.Kind.PDF_TEXT, None, "plain PDF"),
        (Document.Kind.PDF_NO_TEXT, None, "scanned PDF"),
        (Document.Kind.LEGACY_ZUGFERD1, None, "legacy ZUGFeRD 1"),
        (Document.Kind.HYBRID_PDF_UNSUPPORTED, None, "unsupported hybrid PDF"),
        (Document.Kind.HYBRID_PDF, "MINIMUM", "profile MINIMUM"),
        (Document.Kind.HYBRID_PDF, "BASIC WL", "profile BASIC WL"),
        (Document.Kind.XML, "UNKNOWN", "profile UNKNOWN"),
    ],
)
def test_C11_not_an_einvoice_names_the_case(
    organization: Organization, kind: str, profile: str | None, named: str
) -> None:
    invoice = invoice_on(organization, minutes=1, kind=kind, profile=profile, is_einvoice=False)
    finding = only(run(invoice), "C11")
    assert finding.code == "NOT_AN_EINVOICE"
    assert finding.severity == "info"
    assert named in finding.message
    assert MANDATE_SOURCE_URL in finding.message
    assert finding.details == {"source_url": MANDATE_SOURCE_URL}


def test_C11_not_for_an_einvoice(organization: Organization) -> None:
    assert "C11" not in ids(run(invoice_on(organization, minutes=1)))


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("disabled", "Automatic extraction is switched off. Enter the fields by hand."),
        (
            "budget_lifetime",
            "Automatic extraction is paused: this installation's AI budget is used up. "
            "Enter the fields by hand.",
        ),
        (
            "budget_monthly",
            "Automatic extraction is paused: this installation's AI budget is used up. "
            "Enter the fields by hand.",
        ),
        (
            "budget_daily_public",
            "Automatic extraction is unavailable today: the demo's AI budget for today is used "
            "up. Enter the fields by hand.",
        ),
        (
            "sandbox_limit",
            "This sandbox has used its 3 automatic extractions. Enter the fields by hand.",
        ),
        ("error", "Automatic extraction failed. Enter the fields by hand, or retry later."),
    ],
)
def test_C12_extraction_unavailable(organization: Organization, reason: str, text: str) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        kind=Document.Kind.PDF_TEXT,
        extraction_method=Invoice.ExtractionMethod.LLM,
    )
    context = CheckContext(extraction_unavailable_reason=reason, today=TODAY)
    finding = only(run(invoice, context), "C12")
    assert finding.code == "EXTRACTION_UNAVAILABLE"
    assert finding.severity == "block"
    assert finding.message == text
    assert finding.details == {"reason": reason}
    assert "C12" not in ids(run(invoice))


# --- C13 to C16 --------------------------------------------------------------------------


def test_C13_one_check_per_low_confidence_field(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        extraction_method=Invoice.ExtractionMethod.LLM,
        field_confidence={
            "seller_vat_id": "low",
            "invoice_number": "high",
            "due_date": "medium",
            "payee_iban": "edited",
            "issue_date": "low",
        },
    )
    findings = [finding for finding in run(invoice) if finding.check_id == "C13"]
    assert [finding.details for finding in findings] == [
        {"field": "issue_date"},
        {"field": "seller_vat_id"},
    ]
    assert {finding.code for finding in findings} == {"LOW_CONFIDENCE"}
    assert {finding.severity for finding in findings} == {"block"}


def test_C14_buyer_vat_id_differs(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, buyer_vat_id="DE100000016")
    finding = only(run(invoice), "C14")
    assert finding.code == "ADDRESSED_TO_SOMEONE_ELSE"
    assert finding.severity == "warn"


def test_C14_same_vat_id_in_another_spelling(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1, buyer_vat_id="de 291 746 055")
    assert "C14" not in ids(run(invoice))


def test_C14_buyer_name_differs_without_buyer_vat_id(organization: Organization) -> None:
    other = invoice_on(organization, minutes=1, buyer_vat_id=None, buyer_name="Bäckerei Lindner")
    ours = invoice_on(
        organization,
        minutes=2,
        buyer_vat_id=None,
        buyer_name="Holzwerk Brandt GmbH Leipzig",
        seller_name="B GmbH",
    )
    assert "C14" in ids(run(other))
    assert "C14" not in ids(run(ours))


def test_C14_with_neither_buyer_vat_id_nor_buyer_name_fires_nothing(
    organization: Organization,
) -> None:
    invoice = invoice_on(organization, minutes=1, buyer_vat_id=None, buyer_name=None)
    assert "C14" not in ids(run(invoice))


def test_C14_buyer_vat_id_without_organisation_vat_id(organization: Organization) -> None:
    organization.vat_id = None
    organization.save()
    invoice = invoice_on(
        organization, minutes=1, buyer_vat_id="DE100000016", buyer_name="Bäckerei Lindner"
    )
    assert "C14" not in ids(run(invoice))


def test_C15_validation_failed_counts_fatal_issues(organization: Organization) -> None:
    invoice = invoice_on(organization, minutes=1)
    add_report(invoice.document, ValidationReport.Status.INVALID, fatal_count=2)
    finding = only(run(invoice), "C15")
    assert finding.code == "VALIDATION_FAILED"
    assert finding.severity == "block"
    assert "2 fatal issues" in finding.message
    assert finding.details == {"fatal_count": 2}


@pytest.mark.parametrize(
    "status",
    [
        ValidationReport.Status.VALID,
        ValidationReport.Status.WARNINGS,
        ValidationReport.Status.NOT_APPLICABLE,
    ],
)
def test_C15_not_unless_invalid(organization: Organization, status: str) -> None:
    invoice = invoice_on(organization, minutes=1)
    add_report(invoice.document, status)
    assert "C15" not in ids(run(invoice))


def test_C16_foreign_currency(organization: Organization) -> None:
    usd = invoice_on(organization, minutes=1, currency="USD")
    none = invoice_on(organization, minutes=2, currency=None, seller_name="B GmbH")
    finding = only(run(usd), "C16")
    assert finding.code == "FOREIGN_CURRENCY"
    assert finding.severity == "info"
    assert "USD" in finding.message
    assert "C16" not in ids(run(none))


def test_without_an_invoice_only_document_checks_run(organization: Organization) -> None:
    document = make_document(organization, kind=Document.Kind.PDF_NO_TEXT)
    add_report(document, ValidationReport.Status.INVALID, fatal_count=1)
    context = CheckContext(extraction_unavailable_reason="disabled", today=TODAY)
    assert ids(evaluate(document, context)) == ["C10", "C12", "C15"]


# --- apply_findings ----------------------------------------------------------------------


def finding(check_id: str = "C08", message: str = "Due.", **details: object) -> Finding:
    return Finding(check_id, "CODE", "warn", message, dict(details))


def created_events(document: Document) -> list[object]:
    return list(
        Event.objects.filter(document=document, type=Event.Type.CHECK_CREATED).values_list(
            "data", flat=True
        )
    )


def test_apply_findings_creates_checks_with_events(organization: Organization) -> None:
    document = make_document(organization)
    apply_findings(document, [finding("C08"), finding("C13", field="due_date")])
    checks = Check.objects.filter(document=document).order_by("check_id")
    assert [(c.check_id, c.code, c.severity, c.message) for c in checks] == [
        ("C08", "CODE", "warn", "Due."),
        ("C13", "CODE", "warn", "Due."),
    ]
    assert checks[1].details == {"field": "due_date"}
    assert sorted(created_events(document), key=str) == [
        {"check_id": "C08"},
        {"check_id": "C13"},
    ]
    event = Event.objects.filter(document=document).first()
    assert event is not None
    assert event.actor_id is None
    assert event.organization_id == organization.id


def test_apply_findings_updates_unresolved_checks(organization: Organization) -> None:
    document = make_document(organization)
    apply_findings(document, [finding("C09", "Old.", differences=[])])
    apply_findings(document, [finding("C09", "New.", differences=[{"field": "x"}])])
    check = Check.objects.get(document=document)
    assert check.message == "New."
    assert check.details == {"differences": [{"field": "x"}]}
    assert len(created_events(document)) == 1


def test_apply_findings_deletes_unresolved_checks_that_no_longer_apply(
    organization: Organization,
) -> None:
    document = make_document(organization)
    apply_findings(
        document,
        [finding("C08"), finding("C13", field="due_date"), finding("C13", field="issue_date")],
    )
    apply_findings(document, [finding("C13", field="issue_date")])
    remaining = list(Check.objects.filter(document=document).values_list("check_id", "details"))
    assert remaining == [("C13", {"field": "issue_date"})]


def test_apply_findings_keeps_resolved_checks(organization: Organization) -> None:
    document = make_document(organization)
    apply_findings(document, [finding("C05", "Original.")])
    Check.objects.filter(document=document).update(
        resolved_at=RECEIVED, resolution_note="Confirmed by phone."
    )

    apply_findings(document, [])  # no longer applies: kept
    apply_findings(document, [finding("C05", "Changed.")])  # applies again: not re-created
    check = Check.objects.get(document=document)
    assert check.message == "Original."
    assert check.resolved_at == RECEIVED
    assert len(created_events(document)) == 1


def test_apply_findings_is_idempotent(organization: Organization) -> None:
    document = make_document(organization)
    findings = [finding("C04"), finding("C13", field="due_date"), finding("C13", field="iban")]
    apply_findings(document, findings)
    before = list(Check.objects.filter(document=document).values().order_by("id"))
    apply_findings(document, findings)
    after = list(Check.objects.filter(document=document).values().order_by("id"))
    assert after == before
    assert len(created_events(document)) == 3


def test_evaluate_and_apply_after_an_edit(organization: Organization) -> None:
    invoice = invoice_on(
        organization,
        minutes=1,
        extraction_method=Invoice.ExtractionMethod.LLM,
        field_confidence={"due_date": "low"},
        due_date=TODAY - timedelta(days=3),
    )
    apply_findings(invoice.document, run(invoice))
    assert sorted(Check.objects.values_list("check_id", flat=True)) == ["C04", "C08", "C13"]

    invoice.field_confidence = {"due_date": "edited"}
    invoice.due_date = TODAY + timedelta(days=3)
    invoice.save()
    apply_findings(invoice.document, run(invoice))
    assert list(Check.objects.values_list("check_id", flat=True)) == ["C04"]


def test_C14_buyer_name_comparison_ignores_case(organization: Organization) -> None:
    invoice = invoice_on(
        organization, minutes=1, buyer_vat_id=None, buyer_name=organization.name.upper()
    )
    assert "C14" not in {finding.check_id for finding in run(invoice)}

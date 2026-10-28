"""Storing a canonical invoice on a document (sections 2 and 9).

Shared by the `parse_structured` activity and the sandbox seed, so both write exactly the
same rows. Light on purpose: no validation, PDF or LLM code.
"""

from typing import Any

from django.db import models, transaction

from einvoice.model import CanonicalInvoice
from invoices.extraction import Extraction
from invoices.models import Document, Invoice, InvoiceLine

PARTY_FIELDS = (
    "name",
    "vat_id",
    "tax_number",
    "street",
    "postcode",
    "city",
    "country_code",
    "email",
)
MONEY_FIELDS = (
    "line_total", "allowance_total", "charge_total", "net_total", "tax_total", "gross_total",
    "prepaid_amount", "payable_amount",
)  # fmt: skip


def invoice_columns(invoice: CanonicalInvoice) -> dict[str, Any]:  # boundary: model fields
    columns: dict[str, Any] = {  # boundary: model fields
        "invoice_number": invoice.invoice_number,
        "type_code": invoice.type_code,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "buyer_reference": invoice.buyer_reference,
        "order_reference": invoice.order_reference,
        "payee_iban": invoice.payee_iban,
        "payee_bic": invoice.payee_bic,
        "payment_terms": invoice.payment_terms,
        "notes": invoice.notes,
        "tax_breakdown": [row.model_dump(mode="json") for row in invoice.tax_breakdown],
    }
    for party_name, party in (("seller", invoice.seller), ("buyer", invoice.buyer)):
        for field in PARTY_FIELDS:
            columns[f"{party_name}_{field}"] = getattr(party, field)
    for field in MONEY_FIELDS:
        columns[field] = getattr(invoice, field)
    return columns


def _fitting(model: type[models.Model], values: dict[str, Any]) -> dict[str, Any]:
    """The values, with each text longer than its column replaced by None.

    The XML can carry values the columns can't hold (a three-letter country code, an
    overlong BIC); storing them would fail the whole activity. The LLM path stores such a
    value as None too (`extraction.fits_column`).
    """
    fitted = dict(values)
    for name, value in values.items():
        limit: object = getattr(model._meta.get_field(name), "max_length", None)
        if isinstance(value, str) and isinstance(limit, int) and len(value) > limit:
            fitted[name] = None
    return fitted


def save_structured(
    document: Document,
    canonical: CanonicalInvoice,
    *,
    syntax: str | None,
    profile: str | None,
    spec_id: str | None,
    is_einvoice: bool,
) -> Invoice:
    """Create or replace the document's invoice (`extraction_method = xml`) and its lines."""
    with transaction.atomic():
        invoice, _created = Invoice.objects.update_or_create(
            document=document,
            defaults={
                "organization": document.organization,
                "syntax": syntax,
                "profile": profile,
                "is_einvoice": is_einvoice,
                "extraction_method": Invoice.ExtractionMethod.XML,
                **_fitting(Invoice, {"spec_id": spec_id, **invoice_columns(canonical)}),
            },
        )
        invoice.lines.all().delete()
        InvoiceLine.objects.bulk_create(
            [
                InvoiceLine(
                    invoice=invoice, position=position, **_fitting(InvoiceLine, line.model_dump())
                )
                for position, line in enumerate(canonical.lines, start=1)
            ]
        )
    return invoice


def save_empty(document: Document, *, is_einvoice: bool = False) -> Invoice:
    """An invoice without data, for a person to fill in (scans, refused extraction)."""
    invoice, _created = Invoice.objects.get_or_create(
        document=document,
        defaults={
            "organization": document.organization,
            "extraction_method": Invoice.ExtractionMethod.MANUAL,
            "is_einvoice": is_einvoice,
        },
    )
    return invoice


def save_extracted(
    document: Document, extraction: Extraction, *, truncated: bool, prompt_version: str
) -> Invoice:
    """Create or replace the invoice read by the LLM (`extraction_method = llm`) and its lines.

    Not an e-invoice: the data comes from the visible text, graded field by field.
    """
    with transaction.atomic():
        invoice, _created = Invoice.objects.update_or_create(
            document=document,
            defaults={
                "organization": document.organization,
                "is_einvoice": False,
                "extraction_method": Invoice.ExtractionMethod.LLM,
                "field_confidence": extraction.confidence,
                "field_evidence": extraction.evidence,
                "text_truncated": truncated,
                "prompt_version": prompt_version,
                **extraction.columns,
            },
        )
        invoice.lines.all().delete()
        InvoiceLine.objects.bulk_create(
            [
                InvoiceLine(invoice=invoice, position=position, **line.model_dump())
                for position, line in enumerate(extraction.lines, start=1)
            ]
        )
    return invoice

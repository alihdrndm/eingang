"""UBL 2.1 Invoice / CreditNote → CanonicalInvoice (XPath table in HANDOFF section 2)."""

import re
from datetime import date
from decimal import Decimal

from lxml import etree

from einvoice import namespaces as ns
from einvoice.errors import InvoiceParseError, UnsupportedFileError
from einvoice.fields import (
    clean,
    compact_upper,
    decimal_at,
    required_text,
    text,
    to_date,
    to_decimal,
    to_int,
)
from einvoice.model import CanonicalInvoice, Line, Party, TaxBreakdown
from einvoice.xmlsafe import parse_xml

NS = ns.UBL_NS
DATE = "%Y-%m-%d"
# xsd:date may carry a time zone ("2026-10-01Z", "2026-10-01+02:00"); the day is kept as written.
_DATE_TIMEZONE = re.compile(r"(?<=^\d{4}-\d{2}-\d{2})(?:Z|[+-]\d{2}:\d{2})$")
TOTALS = "cac:LegalMonetaryTotal"
SUPPLIER = "cac:AccountingSupplierParty/cac:Party"
CUSTOMER = "cac:AccountingCustomerParty/cac:Party"


def _date(root: etree._Element, path: str, field: str) -> date | None:
    value = text(root, path, NS)
    if value is not None:
        value = _DATE_TIMEZONE.sub("", value)
    return to_date(value, DATE, field, path)


def _party(root: etree._Element, base: str, *, with_tax_number: bool) -> Party:
    vat_id = None
    tax_number = None
    for scheme in root.findall(f"{base}/cac:PartyTaxScheme", namespaces=NS):
        company_id = text(scheme, "cbc:CompanyID", NS)
        if text(scheme, "cac:TaxScheme/cbc:ID", NS) == "VAT":
            vat_id = vat_id or company_id
        elif with_tax_number:
            tax_number = tax_number or company_id
    address = f"{base}/cac:PostalAddress"
    return Party(
        name=text(root, f"{base}/cac:PartyLegalEntity/cbc:RegistrationName", NS),
        vat_id=compact_upper(vat_id),
        tax_number=tax_number,
        street=text(root, f"{address}/cbc:StreetName", NS),
        postcode=text(root, f"{address}/cbc:PostalZone", NS),
        city=text(root, f"{address}/cbc:CityName", NS),
        country_code=text(root, f"{address}/cac:Country/cbc:IdentificationCode", NS),
        email=text(root, f"{base}/cac:Contact/cbc:ElectronicMail", NS),
    )


def _tax_total(root: etree._Element, currency: str) -> Decimal | None:
    # BT-110 is the TaxAmount in the document currency; a second TaxTotal may carry BT-111.
    path = "cac:TaxTotal/cbc:TaxAmount"
    for amount in root.findall(path, namespaces=NS):
        if amount.get("currencyID") in (None, currency):
            return to_decimal(clean(amount.text), "tax_total", path)
    return None


def _tax_breakdown(root: etree._Element, currency: str) -> list[TaxBreakdown]:
    rows = []
    for tax_total in root.findall("cac:TaxTotal", namespaces=NS):
        amount = tax_total.find("cbc:TaxAmount", namespaces=NS)
        if amount is not None and amount.get("currencyID") not in (None, currency):
            continue
        for subtotal in tax_total.findall("cac:TaxSubtotal", namespaces=NS):
            rows.append(
                TaxBreakdown(
                    category=text(subtotal, "cac:TaxCategory/cbc:ID", NS),
                    rate=decimal_at(subtotal, "cac:TaxCategory/cbc:Percent", NS, "tax_breakdown"),
                    taxable_amount=decimal_at(subtotal, "cbc:TaxableAmount", NS, "tax_breakdown"),
                    tax_amount=decimal_at(subtotal, "cbc:TaxAmount", NS, "tax_breakdown"),
                )
            )
    return rows


def _lines(root: etree._Element, credit_note: bool) -> list[Line]:
    line_tag, quantity_tag = (
        ("cac:CreditNoteLine", "cbc:CreditedQuantity")
        if credit_note
        else ("cac:InvoiceLine", "cbc:InvoicedQuantity")
    )
    lines = []
    for element in root.findall(line_tag, namespaces=NS):
        quantity = element.find(quantity_tag, namespaces=NS)
        tax = "cac:Item/cac:ClassifiedTaxCategory"
        lines.append(
            Line(
                line_id=text(element, "cbc:ID", NS),
                description=text(element, "cac:Item/cbc:Name", NS),
                quantity=decimal_at(element, quantity_tag, NS, "lines.quantity"),
                unit_code=quantity.get("unitCode") if quantity is not None else None,
                unit_price=decimal_at(element, "cac:Price/cbc:PriceAmount", NS, "lines.unit_price"),
                net_amount=decimal_at(element, "cbc:LineExtensionAmount", NS, "lines.net_amount"),
                tax_category=text(element, f"{tax}/cbc:ID", NS),
                tax_rate=decimal_at(element, f"{tax}/cbc:Percent", NS, "lines.tax_rate"),
            )
        )
    return lines


def parse_ubl_root(root: etree._Element) -> CanonicalInvoice:
    if root.tag not in (ns.UBL_INVOICE_ROOT, ns.UBL_CREDIT_NOTE_ROOT):
        raise UnsupportedFileError("not a UBL invoice or credit note")
    credit_note = root.tag == ns.UBL_CREDIT_NOTE_ROOT
    currency = required_text(root, "cbc:DocumentCurrencyCode", NS, "currency")
    type_path = "cbc:CreditNoteTypeCode" if credit_note else "cbc:InvoiceTypeCode"
    due_path = "cac:PaymentMeans/cbc:PaymentDueDate" if credit_note else "cbc:DueDate"
    gross_path = f"{TOTALS}/cbc:TaxInclusiveAmount"
    gross_total = decimal_at(root, gross_path, NS, "gross_total")
    if gross_total is None:
        raise InvoiceParseError("gross_total", gross_path)
    seller = _party(root, SUPPLIER, with_tax_number=True)
    if seller.name is None:
        raise InvoiceParseError(
            "seller.name", f"{SUPPLIER}/cac:PartyLegalEntity/cbc:RegistrationName"
        )
    account = "cac:PaymentMeans/cac:PayeeFinancialAccount"

    def total(element: str, field: str) -> Decimal | None:
        return decimal_at(root, f"{TOTALS}/cbc:{element}", NS, field)

    return CanonicalInvoice.model_validate(
        {
            "invoice_number": required_text(root, "cbc:ID", NS, "invoice_number"),
            "type_code": to_int(text(root, type_path, NS), "type_code", type_path),
            "issue_date": _required_date(root, "cbc:IssueDate", "issue_date"),
            "due_date": _date(root, due_path, "due_date"),
            "currency": currency,
            "buyer_reference": text(root, "cbc:BuyerReference", NS),
            "order_reference": text(root, "cac:OrderReference/cbc:ID", NS),
            "seller": seller,
            "buyer": _party(root, CUSTOMER, with_tax_number=False),
            "payee_iban": compact_upper(text(root, f"{account}/cbc:ID", NS)),
            "payee_bic": compact_upper(
                text(root, f"{account}/cac:FinancialInstitutionBranch/cbc:ID", NS)
            ),
            "payment_terms": text(root, "cac:PaymentTerms/cbc:Note", NS),
            "line_total": total("LineExtensionAmount", "line_total"),
            "allowance_total": total("AllowanceTotalAmount", "allowance_total"),
            "charge_total": total("ChargeTotalAmount", "charge_total"),
            "net_total": total("TaxExclusiveAmount", "net_total"),
            "tax_total": _tax_total(root, currency),
            "gross_total": gross_total,
            "prepaid_amount": total("PrepaidAmount", "prepaid_amount"),
            "payable_amount": total("PayableAmount", "payable_amount"),
            "tax_breakdown": _tax_breakdown(root, currency),
            "lines": _lines(root, credit_note),
            "notes": _notes(root),
        }
    )


def _notes(root: etree._Element) -> list[str]:
    notes = []
    for note in root.findall("cbc:Note", namespaces=NS):
        value = (note.text or "").strip()
        if value:
            notes.append(value)
    return notes


def _required_date(root: etree._Element, path: str, field: str) -> date:
    value = _date(root, path, field)
    if value is None:
        raise InvoiceParseError(field, path)
    return value


def parse_ubl(xml: bytes) -> CanonicalInvoice:
    return parse_ubl_root(parse_xml(xml))

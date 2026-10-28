from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from einvoice import namespaces as ns
from einvoice.errors import InvoiceParseError, UnsupportedFileError
from einvoice.model import CanonicalInvoice, Line, Party, TaxBreakdown
from einvoice.parse_cii import parse_cii, parse_cii_root
from einvoice.parse_ubl import parse_ubl, parse_ubl_root
from einvoice.xmlsafe import parse_xml

FIXTURES = Path(__file__).parents[1] / "fixtures" / "invoices"
UBL = (FIXTURES / "invoice.ubl.xml").read_bytes()
CII = (FIXTURES / "invoice.cii.xml").read_bytes()
CREDIT_NOTE = (FIXTURES / "creditnote.ubl.xml").read_bytes()

EXPECTED = CanonicalInvoice(
    invoice_number="RE-2026-0412",
    type_code=380,
    issue_date=date(2026, 3, 2),
    due_date=date(2026, 4, 1),
    currency="EUR",
    buyer_reference="04011000-12345-34",
    order_reference="PO-778",
    seller=Party(
        name="Elektro Kessler GmbH",
        vat_id="DE123456788",
        tax_number="231/456/78901",
        street="Werkstraße 4",
        postcode="04109",
        city="Leipzig",
        country_code="DE",
        email="rechnung@kessler.example",
    ),
    buyer=Party(
        name="Holzwerk Brandt GmbH",
        vat_id="DE987654321",
        street="Holzweg 1",
        postcode="04103",
        city="Leipzig",
        country_code="DE",
    ),
    payee_iban="DE89370400440532013000",
    payee_bic="COBADEFF370",
    payment_terms="Zahlbar innerhalb 30 Tagen netto",
    line_total=Decimal("1000"),
    allowance_total=Decimal("0"),
    charge_total=Decimal("0"),
    net_total=Decimal("1000"),
    tax_total=Decimal("190"),
    gross_total=Decimal("1190"),
    prepaid_amount=Decimal("100"),
    payable_amount=Decimal("1090"),
    tax_breakdown=[
        TaxBreakdown(
            category="S",
            rate=Decimal("19"),
            taxable_amount=Decimal("1000"),
            tax_amount=Decimal("190"),
        )
    ],
    lines=[
        Line(
            line_id="1",
            description="Elektroinstallation",
            quantity=Decimal("8"),
            unit_code="HUR",
            unit_price=Decimal("75"),
            net_amount=Decimal("600"),
            tax_category="S",
            tax_rate=Decimal("19"),
        ),
        Line(
            line_id="2",
            description="Kabel NYM 3x1,5",
            quantity=Decimal("2.5"),
            unit_code="H87",
            unit_price=Decimal("160"),
            net_amount=Decimal("400"),
            tax_category="S",
            tax_rate=Decimal("19"),
        ),
    ],
    notes=["Lieferung gemäß Auftrag.", "Zweite Bemerkung"],
)


def test_ubl_maps_every_field() -> None:
    assert parse_ubl(UBL) == EXPECTED


def test_cii_maps_every_field() -> None:
    assert parse_cii(CII) == EXPECTED


def test_tax_total_is_the_amount_in_the_document_currency() -> None:
    # Both fixtures also carry a BT-111 tax total in USD, which must be ignored.
    assert parse_ubl(UBL).tax_total == Decimal("190.00")
    assert parse_cii(CII).tax_total == Decimal("190.00")


def test_iban_and_vat_ids_are_upper_cased_without_spaces() -> None:
    invoice = parse_ubl(UBL)
    assert invoice.payee_iban == "DE89370400440532013000"
    assert invoice.seller.vat_id == "DE123456788"


def test_ubl_credit_note_uses_its_own_elements() -> None:
    invoice = parse_ubl(CREDIT_NOTE)
    assert invoice.type_code == 381
    assert invoice.is_credit_note
    assert invoice.due_date == date(2026, 3, 12)
    assert invoice.lines[0].quantity == Decimal("7.0000")
    assert invoice.lines[0].description == "Ordner A4, zurückgegeben"
    assert invoice.tax_total == Decimal("9.31")
    assert invoice.buyer.vat_id is None


def remove(xml: bytes, xpath: str, namespaces: dict[str, str]) -> bytes:
    root = parse_xml(xml)
    for element in root.findall(xpath, namespaces=namespaces):
        parent = element.getparent()
        assert parent is not None
        parent.remove(element)
    return etree.tostring(root)


def empty(xml: bytes, xpath: str, namespaces: dict[str, str]) -> bytes:
    root = parse_xml(xml)
    for element in root.findall(xpath, namespaces=namespaces):
        element.text = "  "
    return etree.tostring(root)


UBL_REQUIRED = [
    ("invoice_number", "cbc:ID"),
    ("issue_date", "cbc:IssueDate"),
    ("currency", "cbc:DocumentCurrencyCode"),
    ("seller.name", ".//cac:AccountingSupplierParty//cbc:RegistrationName"),
    ("gross_total", ".//cbc:TaxInclusiveAmount"),
]
CII_REQUIRED = [
    ("invoice_number", "rsm:ExchangedDocument/ram:ID"),
    ("issue_date", ".//ram:IssueDateTime"),
    ("currency", ".//ram:InvoiceCurrencyCode"),
    ("seller.name", ".//ram:SellerTradeParty/ram:Name"),
    ("gross_total", ".//ram:GrandTotalAmount"),
]


@pytest.mark.parametrize(("field", "xpath"), UBL_REQUIRED)
def test_ubl_missing_required_field_raises_invoice_parse_error(field: str, xpath: str) -> None:
    with pytest.raises(InvoiceParseError) as error:
        parse_ubl(remove(UBL, xpath, ns.UBL_NS))
    assert error.value.field == field


@pytest.mark.parametrize(("field", "xpath"), CII_REQUIRED)
def test_cii_missing_required_field_raises_invoice_parse_error(field: str, xpath: str) -> None:
    with pytest.raises(InvoiceParseError) as error:
        parse_cii(remove(CII, xpath, ns.CII_NS))
    assert error.value.field == field


def test_present_but_empty_element_is_none() -> None:
    assert parse_ubl(empty(UBL, ".//cbc:BuyerReference", ns.UBL_NS)).buyer_reference is None
    assert parse_cii(empty(CII, ".//ram:BuyerReference", ns.CII_NS)).buyer_reference is None


def test_empty_required_element_counts_as_missing() -> None:
    with pytest.raises(InvoiceParseError, match="invoice_number"):
        parse_ubl(empty(UBL, "cbc:ID", ns.UBL_NS))


def test_bad_amount_is_a_parse_error() -> None:
    root = parse_xml(UBL)
    root.findall(".//cbc:PayableAmount", namespaces=ns.UBL_NS)[0].text = "12,50"
    with pytest.raises(InvoiceParseError, match="payable_amount is not a number"):
        parse_ubl(etree.tostring(root))


def test_infinite_amount_is_a_parse_error() -> None:
    root = parse_xml(CII)
    root.findall(".//ram:DuePayableAmount", namespaces=ns.CII_NS)[0].text = "Infinity"
    with pytest.raises(InvoiceParseError, match="not a number"):
        parse_cii(etree.tostring(root))


def test_bad_date_is_a_parse_error() -> None:
    root = parse_xml(CII)
    issue = ".//ram:IssueDateTime/udt:DateTimeString"
    root.findall(issue, namespaces=ns.CII_NS)[0].text = "2026-03-02"
    with pytest.raises(InvoiceParseError, match="issue_date is not a valid date"):
        parse_cii(etree.tostring(root))


@pytest.mark.parametrize("suffix", ["Z", "+02:00", "-05:00", "+14:00"])
def test_ubl_date_with_a_time_zone_keeps_the_day(suffix: str) -> None:
    root = parse_xml(UBL)
    root.findall("cbc:IssueDate", namespaces=ns.UBL_NS)[0].text = f"2026-03-02{suffix}"
    root.findall("cbc:DueDate", namespaces=ns.UBL_NS)[0].text = f"2026-04-01{suffix}"
    invoice = parse_ubl(etree.tostring(root))
    assert (invoice.issue_date, invoice.due_date) == (date(2026, 3, 2), date(2026, 4, 1))


def test_ubl_credit_note_due_date_with_a_time_zone() -> None:
    root = parse_xml(CREDIT_NOTE)
    root.findall(".//cbc:PaymentDueDate", namespaces=ns.UBL_NS)[0].text = "2026-03-12Z"
    assert parse_ubl(etree.tostring(root)).due_date == date(2026, 3, 12)


@pytest.mark.parametrize("value", ["2026-03-02T10:00:00", "2026-03-02+2:00", "2026-03-02 Z"])
def test_ubl_date_with_anything_else_after_the_day_is_a_parse_error(value: str) -> None:
    root = parse_xml(UBL)
    root.findall("cbc:IssueDate", namespaces=ns.UBL_NS)[0].text = value
    with pytest.raises(InvoiceParseError, match="issue_date is not a valid date"):
        parse_ubl(etree.tostring(root))


def test_bad_type_code_is_a_parse_error() -> None:
    root = parse_xml(UBL)
    root.findall(".//cbc:InvoiceTypeCode", namespaces=ns.UBL_NS)[0].text = "x"
    with pytest.raises(InvoiceParseError, match="type_code"):
        parse_ubl(etree.tostring(root))


def test_wrong_root_is_refused() -> None:
    with pytest.raises(UnsupportedFileError):
        parse_ubl_root(parse_xml(CII))
    with pytest.raises(UnsupportedFileError):
        parse_cii_root(parse_xml(UBL))


def test_invoice_without_optional_parts_parses() -> None:
    stripped = UBL
    for xpath in [
        ".//cac:InvoiceLine",
        ".//cac:TaxTotal",
        ".//cac:PaymentMeans",
        ".//cac:AccountingCustomerParty",
        ".//cbc:Note",
    ]:
        stripped = remove(stripped, xpath, ns.UBL_NS)
    invoice = parse_ubl(stripped)
    assert invoice.lines == []
    assert invoice.tax_total is None
    assert invoice.tax_breakdown == []
    assert invoice.payee_iban is None
    assert invoice.buyer == Party()
    assert invoice.notes == []


def test_tax_number_is_read_for_the_seller_only() -> None:
    root = parse_xml(CII)
    buyer = root.findall(".//ram:BuyerTradeParty", namespaces=ns.CII_NS)[0]
    registration = etree.SubElement(buyer, f"{{{ns.RAM}}}SpecifiedTaxRegistration")
    etree.SubElement(registration, f"{{{ns.RAM}}}ID", schemeID="FC").text = "999/999/99999"
    assert parse_cii(etree.tostring(root)).buyer.tax_number is None


def test_whitespace_only_tax_total_is_none() -> None:
    ubl = empty(UBL, "cac:TaxTotal/cbc:TaxAmount", ns.UBL_NS)
    cii = empty(CII, ".//ram:TaxTotalAmount", ns.CII_NS)
    assert parse_ubl(ubl).tax_total is None
    assert parse_cii(cii).tax_total is None

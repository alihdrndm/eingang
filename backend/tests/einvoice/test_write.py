from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from einvoice import namespaces as ns
from einvoice.detect import Profile, Syntax, detect
from einvoice.model import CanonicalInvoice, Line, Party, TaxBreakdown
from einvoice.parse_cii import parse_cii
from einvoice.parse_ubl import parse_ubl
from einvoice.write import (
    PEPPOL_BILLING_PROCESS,
    XRECHNUNG_3_ID,
    ElectronicAddress,
    WriteOptions,
    to_cii,
    to_ubl,
)
from einvoice.xmlsafe import parse_xml
from tests.einvoice.test_parse import EXPECTED

OPTIONS = WriteOptions(
    seller_contact_name="Petra Kessler",
    seller_contact_phone="+49 341 123456",
    seller_electronic_address=ElectronicAddress("rechnung@kessler.example"),
    buyer_electronic_address=ElectronicAddress("eingang@brandt.example"),
    tax_exemption_reasons={"AE": "Reverse charge"},
)


def test_write_ubl_round_trips_the_full_fixture() -> None:
    assert parse_ubl(to_ubl(EXPECTED, OPTIONS)) == EXPECTED


def test_write_cii_round_trips_the_full_fixture() -> None:
    assert parse_cii(to_cii(EXPECTED, OPTIONS, XRECHNUNG_3_ID)) == EXPECTED


def test_written_files_are_detected_with_their_profile() -> None:
    ubl = detect(to_ubl(EXPECTED, OPTIONS), "a.xml")
    cii = detect(to_cii(EXPECTED, OPTIONS, "urn:cen.eu:en16931:2017"), "a.xml")
    assert (ubl.syntax, ubl.profile, ubl.profile_version) == (Syntax.UBL, Profile.XRECHNUNG, "3.0")
    assert (cii.syntax, cii.profile) == (Syntax.CII, Profile.EN16931)


def test_ubl_options_are_written() -> None:
    root = parse_xml(to_ubl(EXPECTED, OPTIONS))
    supplier = "cac:AccountingSupplierParty/cac:Party"
    assert root.findtext(f"{supplier}/cac:Contact/cbc:Name", namespaces=ns.UBL_NS) == (
        "Petra Kessler"
    )
    assert root.findtext(f"{supplier}/cac:Contact/cbc:Telephone", namespaces=ns.UBL_NS) == (
        "+49 341 123456"
    )
    endpoint = root.find(f"{supplier}/cbc:EndpointID", namespaces=ns.UBL_NS)
    assert endpoint is not None
    assert (endpoint.text, endpoint.get("schemeID")) == ("rechnung@kessler.example", "EM")
    assert root.findtext(
        "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", namespaces=ns.UBL_NS
    ) == ("eingang@brandt.example")
    assert root.findtext("cac:PaymentMeans/cbc:PaymentMeansCode", namespaces=ns.UBL_NS) == "58"


def test_cii_options_are_written() -> None:
    root = parse_xml(to_cii(EXPECTED, OPTIONS, XRECHNUNG_3_ID))
    seller = (
        "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty"
    )
    contact = f"{seller}/ram:DefinedTradeContact"
    assert root.findtext(f"{contact}/ram:PersonName", namespaces=ns.CII_NS) == "Petra Kessler"
    assert root.findtext(
        f"{contact}/ram:TelephoneUniversalCommunication/ram:CompleteNumber", namespaces=ns.CII_NS
    ) == ("+49 341 123456")
    uri = root.find(f"{seller}/ram:URIUniversalCommunication/ram:URIID", namespaces=ns.CII_NS)
    assert uri is not None
    assert (uri.text, uri.get("schemeID")) == ("rechnung@kessler.example", "EM")


def reverse_charge() -> CanonicalInvoice:
    return EXPECTED.model_copy(
        update={
            "tax_breakdown": [
                TaxBreakdown(
                    category="AE",
                    rate=Decimal("0"),
                    taxable_amount=Decimal("640"),
                    tax_amount=Decimal("0"),
                )
            ],
            "lines": [
                Line(line_id="1", description="Design", tax_category="AE", tax_rate=Decimal("0"))
            ],
        }
    )


def test_exemption_reason_is_written_for_the_categories_given() -> None:
    ubl = parse_xml(to_ubl(reverse_charge(), OPTIONS))
    reasons = ubl.findall(".//cbc:TaxExemptionReason", namespaces=ns.UBL_NS)
    assert [reason.text for reason in reasons] == ["Reverse charge", "Reverse charge"]
    cii = parse_xml(to_cii(reverse_charge(), OPTIONS, XRECHNUNG_3_ID))
    reasons = cii.findall(".//ram:ExemptionReason", namespaces=ns.CII_NS)
    assert [reason.text for reason in reasons] == ["Reverse charge", "Reverse charge"]


def test_type_code_381_is_written_as_a_ubl_credit_note() -> None:
    credit_note = EXPECTED.model_copy(update={"type_code": 381})
    written = to_ubl(credit_note, OPTIONS)
    assert parse_xml(written).tag == ns.UBL_CREDIT_NOTE_ROOT
    assert parse_ubl(written) == credit_note


@pytest.mark.parametrize("code", [261, 396])
def test_other_credit_note_codes_are_written_as_a_ubl_credit_note(code: int) -> None:
    credit_note = EXPECTED.model_copy(update={"type_code": code})
    written = to_ubl(credit_note, OPTIONS)
    assert parse_xml(written).tag == ns.UBL_CREDIT_NOTE_ROOT
    assert parse_ubl(written) == credit_note


def test_missing_type_code_is_written_as_commercial_invoice_380() -> None:
    invoice = EXPECTED.model_copy(update={"type_code": None})
    assert parse_ubl(to_ubl(invoice, WriteOptions())).type_code == 380
    assert parse_cii(to_cii(invoice, WriteOptions(), XRECHNUNG_3_ID)).type_code == 380


# --- Property test: parse(write(invoice)) == invoice ---------------------------------

# Characters a parser gives back unchanged: no control characters (XML forbids most, and
# turns \r into \n), and values never start or end with whitespace (parsers strip it).
_TEXT_ALPHABET = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po", "Pd"),
    whitelist_characters="äöüßÄÖÜ&<>\"'",
    # No-break spaces: str.strip removes them, so a value could change on re-reading.
    blacklist_characters="\u00a0\u2007\u202f",
)
texts = st.text(_TEXT_ALPHABET, min_size=1, max_size=30).map(str.strip).filter(bool)
codes = st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=2, max_size=22)
dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))


def decimals(places: int) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=Decimal("-999999.99"),
        max_value=Decimal("999999.99"),
        places=places,
        allow_nan=False,
        allow_infinity=False,
    )


def optional[T](strategy: st.SearchStrategy[T]) -> st.SearchStrategy[T | None]:
    return st.none() | strategy


parties = st.builds(
    Party,
    name=optional(texts),
    vat_id=optional(codes),
    tax_number=st.none(),  # BT-32 exists for the seller only
    street=optional(texts),
    postcode=optional(texts),
    city=optional(texts),
    country_code=optional(st.sampled_from(["DE", "FR", "AT", "NL"])),
    email=optional(texts),
)
tax_rows = st.builds(
    TaxBreakdown,
    category=optional(st.sampled_from(["S", "Z", "E", "AE", "K", "G", "O"])),
    rate=optional(decimals(2)),
    taxable_amount=optional(decimals(2)),
    tax_amount=optional(decimals(2)),
)
lines = st.builds(
    Line,
    line_id=optional(texts),
    description=optional(texts),
    quantity=optional(decimals(4)),
    unit_code=optional(st.sampled_from(["H87", "HUR", "C62", "KGM"])),
    unit_price=optional(decimals(6)),
    net_amount=optional(decimals(2)),
    tax_category=optional(st.sampled_from(["S", "Z", "AE"])),
    tax_rate=optional(decimals(2)),
)
invoices = st.builds(
    CanonicalInvoice,
    invoice_number=texts,
    type_code=st.sampled_from([380, 381, 384, 389, 326, 261, 396]),
    issue_date=dates,
    due_date=optional(dates),
    currency=st.sampled_from(["EUR", "USD", "CHF"]),
    buyer_reference=optional(texts),
    order_reference=optional(texts),
    seller=st.builds(
        Party,
        name=texts,
        vat_id=optional(codes),
        tax_number=optional(texts),
        street=optional(texts),
        postcode=optional(texts),
        city=optional(texts),
        country_code=optional(st.sampled_from(["DE", "FR"])),
        email=optional(texts),
    ),
    buyer=parties,
    payee_iban=optional(codes),
    payee_bic=optional(codes),
    payment_terms=optional(texts),
    line_total=optional(decimals(2)),
    allowance_total=optional(decimals(2)),
    charge_total=optional(decimals(2)),
    net_total=optional(decimals(2)),
    tax_total=optional(decimals(2)),
    gross_total=decimals(2),
    prepaid_amount=optional(decimals(2)),
    payable_amount=optional(decimals(2)),
    tax_breakdown=st.lists(tax_rows, max_size=3),
    lines=st.lists(lines, max_size=3),
    notes=st.lists(texts, max_size=3),
)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(invoices)
def test_round_trip_ubl(invoice: CanonicalInvoice) -> None:
    assert parse_ubl(to_ubl(invoice, OPTIONS)) == invoice


@settings(max_examples=200, deadline=None, derandomize=True)
@given(invoices)
def test_round_trip_cii(invoice: CanonicalInvoice) -> None:
    assert parse_cii(to_cii(invoice, OPTIONS, XRECHNUNG_3_ID)) == invoice


def test_business_process_bt_23_is_written_in_both_syntaxes() -> None:
    ubl = parse_xml(to_ubl(EXPECTED, OPTIONS))
    assert ubl.findtext("cbc:ProfileID", namespaces=ns.UBL_NS) == PEPPOL_BILLING_PROCESS
    cii = parse_xml(to_cii(EXPECTED, OPTIONS, XRECHNUNG_3_ID))
    path = (
        "rsm:ExchangedDocumentContext/ram:BusinessProcessSpecifiedDocumentContextParameter/ram:ID"
    )
    assert cii.findtext(path, namespaces=ns.CII_NS) == PEPPOL_BILLING_PROCESS


def test_business_process_can_be_left_out() -> None:
    root = parse_xml(to_ubl(EXPECTED, WriteOptions(business_process=None)))
    assert root.find("cbc:ProfileID", namespaces=ns.UBL_NS) is None

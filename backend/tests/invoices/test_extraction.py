"""Post-processing of LLM-read values and the PDF-versus-XML comparison (sections 5 and 6)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from einvoice.model import CanonicalInvoice, Line, Party
from invoices import extraction
from invoices.extraction import (
    HIGH,
    LOW,
    MEDIUM,
    amounts_in,
    assess,
    collapse,
    compare,
    dates_in,
    parse_amount,
    parse_date,
    parse_number,
    post_process,
    prepare_text,
)
from invoices.persist import invoice_columns
from llm.schemas import (
    ComparedValues,
    ExtractedInvoice,
    ExtractedLine,
    ExtractedTaxRow,
    RuleExplanationOut,
)

TODAY = date(2026, 10, 9)
IBAN = "DE89370400440532013000"
VAT_ID = "DE123456788"

# --- the text the model reads ---------------------------------------------------------------


def test_prepare_text_prefixes_each_page() -> None:
    text, truncated = prepare_text(["first", "second"])

    assert text == "--- page 1 ---\nfirst\n--- page 2 ---\nsecond"
    assert truncated is False


def test_prepare_text_keeps_only_the_first_six_pages() -> None:
    text, truncated = prepare_text([f"p{number}" for number in range(1, 9)])

    assert "--- page 6 ---\np6" in text
    assert "page 7" not in text
    assert truncated is False


def test_prepare_text_cuts_at_12000_characters_and_says_so() -> None:
    exact, exact_truncated = prepare_text(["x" * (12_000 - len("--- page 1 ---\n"))])
    long, long_truncated = prepare_text(["x" * 20_000])

    assert len(exact) == 12_000
    assert exact_truncated is False
    assert len(long) == 12_000
    assert long_truncated is True


def test_prepare_text_of_no_pages_is_empty() -> None:
    assert prepare_text([]) == ("", False)


# --- amounts --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234.56", Decimal("1234.56")),
        ("-12.500", Decimal("-12.5")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("1234,56", Decimal("1234.56")),
        ("1234", Decimal(1234)),
        ("1.234.567,89", Decimal("1234567.89")),
        ("1,234,567.89", Decimal("1234567.89")),
        ("1.234.567", Decimal(1234567)),
        ("1 234,56", Decimal("1234.56")),
        ("1\u00a0234,56", Decimal("1234.56")),
        ("1'234.56", Decimal("1234.56")),
        ("1 234", Decimal(1234)),
        # Ambiguous: one mark followed by three digits is read as thousands...
        ("1,234", Decimal(1234)),
        ("1.234 EUR", Decimal(1234)),
        # ...but a bare dot-decimal is read as written, the format the model returns.
        ("1.234", Decimal("1.234")),
        # ...unless the first group starts with 0 or is longer than three digits.
        ("0,125", Decimal("0.125")),
        ("1234,567", Decimal("1234.567")),
        ("1,23", Decimal("1.23")),
        ("1.5", Decimal("1.5")),
        ("1.234,56 €", Decimal("1234.56")),
        ("€ 1,234.56", Decimal("1234.56")),
        ("EUR 1.234,56", Decimal("1234.56")),
        ("1.234,56 EUR", Decimal("1234.56")),
        ("-1.234,56", Decimal("-1234.56")),
        ("\u22121.234,56", Decimal("-1234.56")),
        ("1.234,56-", Decimal("-1234.56")),
        ("(1.234,56)", Decimal("-1234.56")),
        ("- 12,50 €", Decimal("-12.50")),
    ],
)
def test_parse_amount_reads_german_and_english_formats(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    "text", [None, "", "abc", "1.234.56", "1,234.567.8", "12a", "1.234,56,78", "12.34,56", "€", "-"]
)
def test_parse_amount_rejects_what_is_not_an_amount(text: str | None) -> None:
    assert parse_amount(text) is None


def test_amounts_in_reads_every_amount_of_a_snippet() -> None:
    found = amounts_in("Netto 1.000,00 EUR, MwSt 19 % 190,00 EUR")

    assert {Decimal("1000.00"), Decimal("190.00"), Decimal(19)} <= found


def test_amounts_in_keeps_both_readings_of_an_ambiguous_number() -> None:
    assert {Decimal(1234), Decimal("1.234")} <= amounts_in("Total 1.234")


def test_amounts_in_separates_a_position_number_from_the_amount() -> None:
    assert Decimal("100.00") in amounts_in("Pos 1 100,00")


def test_amounts_in_applies_a_leading_or_trailing_minus() -> None:
    assert Decimal("-50.00") in amounts_in("Gutschrift -50,00 EUR")
    assert Decimal("-50.00") in amounts_in("Gutschrift 50,00- EUR")
    assert Decimal("-50.00") not in amounts_in("Gutschrift 50,00 EUR")


@pytest.mark.parametrize(
    ("text", "places", "expected"),
    [
        ("19 %", 2, Decimal(19)),
        ("19,00%", 2, Decimal("19.00")),
        ("2.5", 4, Decimal("2.5")),
        ("1.23456", 4, None),
        ("12.345678", 6, Decimal("12.345678")),
        ("2 Stk", 4, None),
        (None, 2, None),
    ],
)
def test_parse_number(text: str | None, places: int, expected: Decimal | None) -> None:
    assert parse_number(text, places) == expected


# --- dates ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-31", date(2026, 10, 31)),
        ("2026/10/31", date(2026, 10, 31)),
        ("31.10.2026", date(2026, 10, 31)),
        ("1.2.2026", date(2026, 2, 1)),
        ("31.10.26", date(2026, 10, 31)),
        ("31/10/2026", date(2026, 10, 31)),
        ("31-10-2026", date(2026, 10, 31)),
        # Ambiguous: slash dates are day first...
        ("03/04/2026", date(2026, 4, 3)),
        # ...unless only month first is a date.
        ("10/31/2026", date(2026, 10, 31)),
        ("October 31, 2026", date(2026, 10, 31)),
        ("Oct. 31st, 2026", date(2026, 10, 31)),
        ("31 October 2026", date(2026, 10, 31)),
        ("31. Oktober 2026", date(2026, 10, 31)),
        ("31. Okt. 2026", date(2026, 10, 31)),
        ("3. März 2026", date(2026, 3, 3)),
        ("3. MAERZ 2026", date(2026, 3, 3)),
        ("  31.10.2026  ", date(2026, 10, 31)),
    ],
)
def test_parse_date_reads_german_and_english_formats(text: str, expected: date) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [None, "", "31.13.2026", "10.31.2026", "2026-02-30", "32/13/2026", "Rechnung", "31 Foo 2026"],
)
def test_parse_date_rejects_what_is_not_a_date(text: str | None) -> None:
    assert parse_date(text) is None


def test_dates_in_reads_every_date_and_both_readings_of_a_slash_date() -> None:
    found = dates_in("Rechnungsdatum 03/04/2026, fällig am 1. Mai 2026")

    assert found == {date(2026, 4, 3), date(2026, 3, 4), date(2026, 5, 1)}


def test_dates_in_reads_a_dotted_date_day_first_only() -> None:
    assert dates_in("Datum: 03.04.2026") == {date(2026, 4, 3)}


# --- evidence and confidence ----------------------------------------------------------------

PAGE = """--- page 1 ---
Muster GmbH   Hauptstr. 1
Rechnungsnummer:   RE-2026-001
Rechnungsdatum: 15.03.2026
IBAN: DE89 3704 0044
0532 0130 00
Betrag 1.190,00 EUR
"""


def test_collapse_joins_whitespace_runs() -> None:
    assert collapse("  a \n\t b  ") == "a b"


def test_evidence_found_ignores_whitespace_differences() -> None:
    page = collapse(PAGE)

    assert extraction.evidence_found("Rechnungsnummer: RE-2026-001", page)
    assert extraction.evidence_found("IBAN: DE89 3704 0044 0532 0130 00", page)
    assert not extraction.evidence_found("Rechnungsnummer: RE-2026-002", page)
    assert not extraction.evidence_found("   ", page)
    assert not extraction.evidence_found(None, page)


def _assess(name: str, raw: str, evidence: str | None) -> extraction.FieldResult:
    return assess(name, raw, evidence, collapse(PAGE), TODAY)


def test_high_when_found_valid_and_readable() -> None:
    result = _assess("issue_date", "2026-03-15", "Rechnungsdatum: 15.03.2026")

    assert result == extraction.FieldResult(value=date(2026, 3, 15), confidence=HIGH)


def test_medium_when_the_value_cannot_be_read_from_the_evidence() -> None:
    result = _assess("issue_date", "2026-03-16", "Rechnungsdatum: 15.03.2026")

    assert result.confidence == MEDIUM
    assert result.value == date(2026, 3, 16)


def test_low_when_the_evidence_is_not_in_the_text() -> None:
    assert _assess("issue_date", "2026-03-15", "Datum: 15.03.2026").confidence == LOW
    assert _assess("issue_date", "2026-03-15", None).confidence == LOW


def test_low_when_the_validator_fails_even_with_good_evidence() -> None:
    page = collapse("Rechnungsdatum: 15.03.1999")
    result = assess("issue_date", "1999-03-15", "Rechnungsdatum: 15.03.1999", page, TODAY)

    assert result == extraction.FieldResult(value=date(1999, 3, 15), confidence=LOW)


def test_amount_high_with_german_evidence_and_stored_to_the_cent() -> None:
    result = _assess("gross_total", "1190.0", "Betrag 1.190,00 EUR")

    assert result.confidence == HIGH
    assert result.value == Decimal("1190.00")
    assert str(result.value) == "1190.00"


def test_amount_with_three_decimals_is_low_and_not_stored() -> None:
    page = collapse("Betrag 1190,005")
    result = assess("gross_total", "1190.005", "Betrag 1190,005", page, TODAY)

    assert result == extraction.FieldResult(value=None, confidence=LOW)


def test_unreadable_value_is_low_and_not_stored() -> None:
    result = _assess("gross_total", "about a thousand", "Betrag 1.190,00 EUR")

    assert result == extraction.FieldResult(value=None, confidence=LOW)


def test_iban_is_compacted_and_read_across_a_line_break() -> None:
    result = _assess("payee_iban", "DE89 3704 0044 0532 0130 00", "DE89 3704 0044 0532 0130 00")

    assert result == extraction.FieldResult(value=IBAN, confidence=HIGH)


def test_iban_with_a_bad_checksum_is_low_but_kept_for_check_c06() -> None:
    page = collapse("IBAN DE00370400440532013000")
    result = assess(
        "payee_iban", "DE00370400440532013000", "IBAN DE00370400440532013000", page, TODAY
    )

    assert result == extraction.FieldResult(value="DE00370400440532013000", confidence=LOW)


def test_vat_id_validator_and_reread() -> None:
    page = collapse("USt-IdNr.: DE 123 456 788 / bad DE123456789")

    good = assess("seller_vat_id", "de123456788", "USt-IdNr.: DE 123 456 788", page, TODAY)
    bad = assess("seller_vat_id", "DE123456789", "bad DE123456789", page, TODAY)

    assert good == extraction.FieldResult(value=VAT_ID, confidence=HIGH)
    assert bad.confidence == LOW


def test_bic_validator() -> None:
    page = collapse("BIC: COBADEFF370 and BIC: 12345")

    assert assess("payee_bic", "COBADEFF370", "BIC: COBADEFF370", page, TODAY).confidence == HIGH
    assert assess("payee_bic", "12345", "BIC: 12345", page, TODAY).confidence == LOW


@pytest.mark.parametrize(
    ("raw", "evidence", "value", "confidence"),
    [
        ("EUR", "Währung: EUR", "EUR", HIGH),
        ("eur", "Betrag 10,00 €", "EUR", HIGH),
        ("€", "Betrag 10,00 €", "EUR", HIGH),
        ("EUR", "Alle Beträge in Euro", "EUR", HIGH),
        ("USD", "Total $10.00", "USD", HIGH),
        ("USD", "Betrag 10,00 €", "USD", MEDIUM),
        ("EUR", "Betrag 10,00", "EUR", MEDIUM),
        ("XYZ", "Currency XYZ", "XYZ", LOW),
        ("EURO", "Currency EURO", None, LOW),
    ],
)
def test_currency(raw: str, evidence: str, value: str | None, confidence: str) -> None:
    result = assess("currency", raw, evidence, collapse(evidence), TODAY)

    assert result == extraction.FieldResult(value=value, confidence=confidence)


def test_country_code() -> None:
    page = collapse("Land: DE / Germany")

    assert assess("seller_country_code", "de", "Land: DE", page, TODAY).confidence == HIGH
    germany = assess("seller_country_code", "Germany", "Germany", page, TODAY)
    assert germany == extraction.FieldResult(value=None, confidence=LOW)


def test_text_reread_is_case_and_space_insensitive() -> None:
    result = _assess("invoice_number", "re-2026-001", "Rechnungsnummer:   RE-2026-001")

    assert result == extraction.FieldResult(value="re-2026-001", confidence=HIGH)


def test_text_longer_than_its_column_is_low_and_not_stored() -> None:
    name = "Muster GmbH " * 50
    result = assess("seller_name", name, name, collapse(name), TODAY)

    assert result == extraction.FieldResult(value=None, confidence=LOW)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (date(2000, 1, 1), True),
        (date(1999, 12, 31), False),
        (date(2027, 11, 13), True),  # today + 400 days
        (date(2027, 11, 14), False),
    ],
)
def test_date_range_is_2000_to_today_plus_400_days(value: date, valid: bool) -> None:
    assert extraction.DATE.is_valid(value, TODAY) is valid


def test_fits_column_accepts_dates() -> None:
    assert extraction.fits_column("issue_date", date(2026, 1, 1))


# --- post_process ---------------------------------------------------------------------------


def _extracted(**values: Any) -> ExtractedInvoice:  # boundary: test data
    fields: dict[str, Any] = dict.fromkeys(ExtractedInvoice.model_fields)  # boundary: test data
    fields.update(tax_breakdown=[], lines=[])
    fields.update(values)
    return ExtractedInvoice(**fields)


def test_post_process_grades_values_and_leaves_null_fields_out() -> None:
    extracted = _extracted(
        invoice_number="RE-2026-001",
        invoice_number_evidence="Rechnungsnummer:   RE-2026-001",
        issue_date="2026-03-15",
        issue_date_evidence="Rechnungsdatum: 15.03.2026",
        gross_total="1190.00",
        gross_total_evidence=None,
        seller_name="  ",
        seller_name_evidence="Muster GmbH",
        payee_iban="null",
        payee_iban_evidence=None,
    )

    result = post_process(extracted, PAGE, today=TODAY)

    assert result.confidence == {
        "invoice_number": HIGH,
        "issue_date": HIGH,
        "gross_total": LOW,
    }
    assert result.evidence == {
        "invoice_number": "Rechnungsnummer:   RE-2026-001",
        "issue_date": "Rechnungsdatum: 15.03.2026",
        "gross_total": None,
    }
    assert result.columns["invoice_number"] == "RE-2026-001"
    assert result.columns["issue_date"] == date(2026, 3, 15)
    assert result.columns["gross_total"] == Decimal("1190.00")
    assert result.columns["seller_name"] is None
    assert result.columns["payee_iban"] is None  # the text "null" is no value
    assert result.columns["due_date"] is None


def test_post_process_has_the_same_columns_as_a_structured_invoice() -> None:
    canonical = CanonicalInvoice(
        invoice_number="1",
        issue_date=date(2026, 1, 1),
        currency="EUR",
        seller=Party(name="Muster GmbH"),
        gross_total=Decimal(1),
    )

    result = post_process(_extracted(), "", today=TODAY)

    assert result.columns.keys() == invoice_columns(canonical).keys()
    assert result.columns["tax_breakdown"] == []
    assert result.lines == []


def test_post_process_reads_today_from_the_clock(fixed_clock: object) -> None:
    # The shared fixed clock stands at 2026-03-10; 2027-04-14 is 400 days later.
    page = "Fällig 15.04.2027"
    extracted = _extracted(due_date="2027-04-15", due_date_evidence="Fällig 15.04.2027")

    assert post_process(extracted, page).confidence == {"due_date": LOW}


def test_post_process_parses_tax_rows_and_lines() -> None:
    extracted = _extracted(
        tax_breakdown=[
            ExtractedTaxRow(category="S", rate="19%", taxable_amount="1000.00", tax_amount="190"),
            ExtractedTaxRow(category=" ", rate=None, taxable_amount=None, tax_amount=None),
        ],
        lines=[
            ExtractedLine(
                description="  Beratung \n März ",
                quantity="10",
                unit_price="100.00",
                net_amount="1.000,00",
                tax_rate="19 %",
            ),
            ExtractedLine(
                description=None, quantity="x", unit_price=None, net_amount=None, tax_rate=None
            ),
        ],
    )

    result = post_process(extracted, "", today=TODAY)

    assert result.columns["tax_breakdown"] == [
        {"category": "S", "rate": "19.00", "taxable_amount": "1000.00", "tax_amount": "190.00"}
    ]
    assert result.lines == [
        Line(
            description="Beratung März",
            quantity=Decimal(10),
            unit_price=Decimal(100),
            net_amount=Decimal(1000),
            tax_rate=Decimal(19),
        )
    ]


# --- section 6 ------------------------------------------------------------------------------


def _xml_invoice(**values: Any) -> CanonicalInvoice:  # boundary: test data
    fields: dict[str, Any] = {  # boundary: test data
        "invoice_number": "RE-2026-001",
        "issue_date": date(2026, 3, 15),
        "currency": "EUR",
        "seller": Party(name="Muster GmbH"),
        "gross_total": Decimal("1190.00"),
        "payable_amount": Decimal("1190.00"),
        "payee_iban": IBAN,
    }
    fields.update(values)
    return CanonicalInvoice(**fields)


def _pdf(**values: str | None) -> ComparedValues:
    fields: dict[str, str | None] = dict.fromkeys(ComparedValues.model_fields)
    fields.update(values)
    return ComparedValues(**fields)


def test_compare_finds_no_difference_when_values_agree_after_normalisation() -> None:
    pdf = _pdf(
        invoice_number="re-2026- 001",
        issue_date="15.03.2026",
        gross_total="1.190,00 €",
        payable_amount="1190",
        payee_iban="de89 3704 0044 0532 0130 00",
    )

    assert compare(_xml_invoice(), pdf) == []


def test_compare_lists_every_differing_field_with_both_values() -> None:
    pdf = _pdf(
        invoice_number="RE-2026-002",
        issue_date="2026-03-16",
        gross_total="1190.01",
        payable_amount="1.000,00",
        payee_iban="DE02120300000000202051",
    )

    assert compare(_xml_invoice(), pdf) == [
        {"field": "invoice_number", "xml": "RE-2026-001", "pdf": "RE-2026-002"},
        {"field": "issue_date", "xml": "2026-03-15", "pdf": "2026-03-16"},
        {"field": "gross_total", "xml": "1190.00", "pdf": "1190.01"},
        {"field": "payable_amount", "xml": "1190.00", "pdf": "1000.00"},
        {"field": "payee_iban", "xml": IBAN, "pdf": "DE02120300000000202051"},
    ]


def test_compare_ignores_values_not_found_unreadable_or_missing_from_the_xml() -> None:
    pdf = _pdf(issue_date="sometime in March", payable_amount="999.00")

    assert compare(_xml_invoice(payable_amount=None), pdf) == []


def test_compare_with_page_text_ignores_values_whose_evidence_is_not_there() -> None:
    pdf = _pdf(
        gross_total="999.00",
        gross_total_evidence="Brutto 999,00",
        payable_amount="998.00",
        payable_amount_evidence="Zahlbetrag 998,00",
    )

    differences = compare(_xml_invoice(), pdf, page_text="Zahlbetrag   998,00")

    assert differences == [{"field": "payable_amount", "xml": "1190.00", "pdf": "998.00"}]


# --- output schemas -------------------------------------------------------------------------


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:  # boundary: JSON schema
    found: list[dict[str, Any]] = [schema]  # boundary: JSON schema
    for definition in schema.get("$defs", {}).values():
        found.append(definition)
    return found


@pytest.mark.parametrize("model", [ExtractedInvoice, ComparedValues, RuleExplanationOut])
def test_schemas_are_strict_structured_outputs(model: type[ExtractedInvoice]) -> None:
    schema = model.model_json_schema()

    for definition in _objects(schema):
        assert definition["type"] == "object"
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
        for prop in definition["properties"].values():
            assert "default" not in prop
            assert "maxLength" not in prop


def test_extracted_invoice_caps_its_lists() -> None:
    properties = ExtractedInvoice.model_json_schema()["properties"]

    assert properties["tax_breakdown"]["maxItems"] == 5
    assert properties["lines"]["maxItems"] == 30


def test_every_header_field_has_an_evidence_partner() -> None:
    names = set(ExtractedInvoice.model_fields) - {"tax_breakdown", "lines"}
    values = {name for name in names if not name.endswith("_evidence")}

    assert values == set(extraction.HEADER_FIELDS)
    assert names == values | {f"{name}_evidence" for name in values}
    assert set(ComparedValues.model_fields) == {
        *extraction.COMPARED_FIELDS,
        *(f"{name}_evidence" for name in extraction.COMPARED_FIELDS),
    }


def test_rule_explanation_is_clipped_to_the_stored_limits() -> None:
    long = RuleExplanationOut(plain_text="word " * 100, fix_hint="x" * 250)
    short = RuleExplanationOut(plain_text="  Fine  as is. ", fix_hint="Fix it.")

    clipped = long.clipped()

    assert len(clipped.plain_text) <= 300
    assert clipped.plain_text.endswith("word…")
    assert len(clipped.fix_hint) == 200
    assert short.clipped() == RuleExplanationOut(plain_text="Fine as is.", fix_hint="Fix it.")

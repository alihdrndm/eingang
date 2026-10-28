"""The CSV formatting rules of HANDOFF section 11."""

from datetime import date
from decimal import Decimal

import pytest

from exports import csv_format as fmt


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_injection_guard_quotes_each_leading_character(prefix: str) -> None:
    assert fmt.text(f"{prefix}SUM(A1:A9)") == f"'{prefix}SUM(A1:A9)"


def test_ordinary_and_empty_text_is_unchanged() -> None:
    assert fmt.text("Elektro Kessler GmbH") == "Elektro Kessler GmbH"
    assert fmt.text("RE-2026-001") == "RE-2026-001"  # only a leading character matters
    assert fmt.text(None) == ""
    assert fmt.text("") == ""


def test_money_has_two_places_and_a_decimal_comma() -> None:
    assert fmt.money(Decimal("1190")) == "1190,00"
    assert fmt.money(Decimal("0.005")) == "0,01"  # ROUND_HALF_UP
    assert fmt.money(Decimal("1234567.5")) == "1234567,50"  # no thousands separator
    assert fmt.money(None) == ""


def test_credit_note_money_is_negative() -> None:
    assert fmt.money(Decimal("1190.00"), credit_note=True) == "-1190,00"
    assert fmt.money(Decimal("0.00"), credit_note=True) == "0,00"  # never "-0,00"


def test_numbers_keep_their_stored_places() -> None:
    assert fmt.number(Decimal("2.5000")) == "2,5000"
    assert fmt.number(Decimal("12.345000")) == "12,345000"
    assert fmt.number(None) == ""


def test_dates_are_day_month_year() -> None:
    assert fmt.day(date(2026, 3, 1)) == "01.03.2026"
    assert fmt.day(None) == ""


def test_e_invoice_is_ja_or_nein() -> None:
    assert fmt.yes_no(True) == "ja"
    assert fmt.yes_no(False) == "nein"
    assert fmt.yes_no(None) == ""


def test_credit_note_is_type_381() -> None:
    assert fmt.is_credit_note(381)
    assert not fmt.is_credit_note(380)
    assert not fmt.is_credit_note(None)


@pytest.mark.parametrize("code", [261, 262, 396, 532])
def test_other_credit_note_codes_are_credit_notes(code: int) -> None:
    assert fmt.is_credit_note(code)
    assert fmt.money(Decimal("10"), credit_note=fmt.is_credit_note(code)) == "-10,00"


def test_file_has_bom_semicolons_and_crlf() -> None:
    data = fmt.write_csv(["A", "B"], [["1,50", "x;y"]])
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8-sig") == 'A;B\r\n1,50;"x;y"\r\n'

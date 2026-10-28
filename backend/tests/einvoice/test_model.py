from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from einvoice.model import CanonicalInvoice, Line, Party, TaxBreakdown


def minimal(**changes: object) -> CanonicalInvoice:
    values: dict[str, object] = {
        "invoice_number": "RE-1",
        "issue_date": date(2026, 3, 1),
        "currency": "EUR",
        "seller": Party(name="Elektro Kessler GmbH"),
        "gross_total": Decimal("1190"),
    }
    values.update(changes)
    return CanonicalInvoice.model_validate(values)


def test_only_the_five_required_fields_are_needed() -> None:
    invoice = minimal()
    assert invoice.gross_total == Decimal("1190.00")
    assert invoice.due_date is None
    assert invoice.buyer == Party()
    assert invoice.lines == []


@pytest.mark.parametrize("field", ["invoice_number", "issue_date", "currency", "gross_total"])
def test_required_fields_are_enforced(field: str) -> None:
    values = minimal().model_dump()
    del values[field]
    with pytest.raises(ValidationError):
        CanonicalInvoice.model_validate(values)


def test_seller_name_is_required() -> None:
    with pytest.raises(ValidationError, match=r"seller\.name"):
        minimal(seller=Party(vat_id="DE123456789"))


def test_money_rounds_half_up_to_two_places() -> None:
    assert minimal(gross_total=Decimal("2.345")).gross_total == Decimal("2.35")
    assert minimal(net_total=Decimal("-2.345")).net_total == Decimal("-2.35")


def test_line_precisions() -> None:
    line = Line(
        quantity=Decimal("1.23456"),
        unit_price=Decimal("0.1234565"),
        net_amount=Decimal("10"),
        tax_rate=Decimal("19"),
    )
    assert line.quantity == Decimal("1.2346")
    assert line.unit_price == Decimal("0.123457")
    assert str(line.net_amount) == "10.00"
    assert str(line.tax_rate) == "19.00"


def test_tax_breakdown_precision() -> None:
    row = TaxBreakdown(category="S", rate=Decimal("7"), taxable_amount=Decimal("100.005"))
    assert str(row.rate) == "7.00"
    assert row.taxable_amount == Decimal("100.01")


def test_models_are_frozen() -> None:
    invoice = minimal()
    with pytest.raises(ValidationError):
        invoice.invoice_number = "other"  # type: ignore[misc]  # proving immutability


def test_floats_are_never_needed_but_strings_parse_exactly() -> None:
    assert minimal(gross_total="0.10").gross_total == Decimal("0.10")


def test_credit_note_flag() -> None:
    assert minimal(type_code=381).is_credit_note
    assert not minimal(type_code=380).is_credit_note
    assert not minimal().is_credit_note


@pytest.mark.parametrize("code", [81, 83, 261, 262, 296, 308, 381, 396, 420, 458, 532])
def test_credit_note_flag_for_every_en16931_credit_note_code(code: int) -> None:
    assert minimal(type_code=code).is_credit_note


@pytest.mark.parametrize("code", [326, 380, 384, 389, 751, 875, 876, 877])
def test_invoice_codes_are_not_credit_notes(code: int) -> None:
    assert not minimal(type_code=code).is_credit_note

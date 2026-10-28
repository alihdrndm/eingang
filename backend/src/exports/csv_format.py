"""Formatting rules of the CSV exports (HANDOFF section 11), as small pure functions.

German spreadsheet conventions: UTF-8 with BOM, `;` between cells, decimal comma, dates
`DD.MM.YYYY`. Text cells that a spreadsheet would read as a formula get a leading `'`.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from einvoice.model import is_credit_note_type

MONEY_PLACES = Decimal("0.01")
# A spreadsheet treats a cell starting with one of these as a formula (or strips it).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def text(value: str | None) -> str:
    """A text cell; empty when unset, guarded against spreadsheet formula injection."""
    if not value:
        return ""
    return f"'{value}" if value.startswith(FORMULA_PREFIXES) else value


def _decimal(value: Decimal) -> str:
    if value == 0:
        value = abs(value)  # never write "-0,00"
    return format(value, "f").replace(".", ",")


def money(value: Decimal | None, *, credit_note: bool = False) -> str:
    """An amount with two places and a decimal comma; negative on credit notes."""
    if value is None:
        return ""
    amount = value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
    return _decimal(-amount if credit_note else amount)


def number(value: Decimal | None) -> str:
    """A quantity, price or rate exactly as stored, with a decimal comma."""
    if value is None:
        return ""
    return _decimal(value)


def day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else ""


def yes_no(value: bool | None) -> str:
    if value is None:
        return ""
    return "ja" if value else "nein"


def is_credit_note(type_code: int | None) -> bool:
    return is_credit_note_type(type_code)


def write_csv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    """The whole file: BOM, `;` separator, CRLF line ends (what Excel expects)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")

"""The canonical invoice: one shape for UBL, CII and LLM-extracted invoices.

Money is Decimal with 2 places, quantities 4, unit prices 6, rates 2, always
rounded ROUND_HALF_UP. Never float.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator


def _quantizer(places: int) -> AfterValidator:
    exponent = Decimal(1).scaleb(-places)

    def quantize(value: Decimal) -> Decimal:
        return value.quantize(exponent, rounding=ROUND_HALF_UP)

    return AfterValidator(quantize)


Money = Annotated[Decimal, _quantizer(2)]
Rate = Annotated[Decimal, _quantizer(2)]
Quantity = Annotated[Decimal, _quantizer(4)]
UnitPrice = Annotated[Decimal, _quantizer(6)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Party(_Frozen):
    name: str | None = None
    vat_id: str | None = None
    tax_number: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    country_code: str | None = None
    email: str | None = None


class TaxBreakdown(_Frozen):
    category: str | None = None
    rate: Rate | None = None
    taxable_amount: Money | None = None
    tax_amount: Money | None = None


class Line(_Frozen):
    line_id: str | None = None
    description: str | None = None
    quantity: Quantity | None = None
    unit_code: str | None = None
    unit_price: UnitPrice | None = None
    net_amount: Money | None = None
    tax_category: str | None = None
    tax_rate: Rate | None = None


class CanonicalInvoice(_Frozen):
    invoice_number: str
    type_code: int | None = None
    issue_date: date
    due_date: date | None = None
    currency: str
    buyer_reference: str | None = None
    order_reference: str | None = None
    seller: Party
    buyer: Party = Party()
    payee_iban: str | None = None
    payee_bic: str | None = None
    payment_terms: str | None = None
    line_total: Money | None = None
    allowance_total: Money | None = None
    charge_total: Money | None = None
    net_total: Money | None = None
    tax_total: Money | None = None
    gross_total: Money
    prepaid_amount: Money | None = None
    payable_amount: Money | None = None
    tax_breakdown: list[TaxBreakdown] = []
    lines: list[Line] = []
    notes: list[str] = []

    @model_validator(mode="after")
    def _seller_has_a_name(self) -> "CanonicalInvoice":
        # seller.name is one of the five required fields; Party is shared with the buyer,
        # whose name is optional, so the rule lives here.
        if not self.seller.name:
            raise ValueError("seller.name is required")
        return self

    @property
    def is_credit_note(self) -> bool:
        return is_credit_note_type(self.type_code)


# BT-3 codes that make a document a credit note: the UNTDID 1001 credit-note codes of the
# EN 16931 code list, as Peppol BIS Billing 3.0 lists them for a UBL CreditNote.
CREDIT_NOTE_TYPE_CODES = frozenset({81, 83, 261, 262, 296, 308, 381, 396, 420, 458, 532})


def is_credit_note_type(type_code: int | None) -> bool:
    return type_code in CREDIT_NOTE_TYPE_CODES

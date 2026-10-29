"""When a supplier's bank account is trusted (HANDOFF section 10, "Suppliers").

A supplier IBAN is trusted when it was on the supplier's first invoice or a person has
confirmed it. The API reports each invoice's IBAN as `known` (trusted, first invoice),
`confirmed` (trusted, confirmed by a person), `new` (not trusted) or nothing (no IBAN).
"""

from typing import Literal
from uuid import UUID

from suppliers.models import Supplier, SupplierIban

IbanStatus = Literal["known", "confirmed", "new"]


def first_invoice_id(supplier: Supplier) -> UUID | None:
    """The supplier's earliest received invoice, ties broken by document id; None when that
    invoice was deleted.

    Deleted invoices still count when finding the first one, so deleting it never makes a
    later invoice, and its bank account, "first". A deleted first invoice confers no trust:
    every IBAN of the supplier then needs a person's confirmation.
    """
    first = (
        supplier.invoices.order_by("document__received_at", "document__id")
        .values_list("id", "document__deleted_at")
        .first()
    )
    if first is None or first[1] is not None:
        return None
    return first[0]


def iban_status(entry: SupplierIban, first_invoice: UUID | None) -> IbanStatus:
    if entry.first_seen_invoice_id == first_invoice:
        return "known"
    if entry.confirmed_at is not None:
        return "confirmed"
    return "new"


def is_trusted(entry: SupplierIban, first_invoice: UUID | None) -> bool:
    return iban_status(entry, first_invoice) != "new"

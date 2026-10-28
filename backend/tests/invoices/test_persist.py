from datetime import date
from decimal import Decimal

import pytest

from accounts.models import Organization
from einvoice.model import CanonicalInvoice, Line, Party
from invoices.models import Invoice
from invoices.persist import save_structured
from tests.factories import make_document

pytestmark = pytest.mark.django_db


def canonical(**fields: object) -> CanonicalInvoice:
    values: dict[str, object] = {
        "invoice_number": "RE-1",
        "issue_date": date(2026, 3, 1),
        "currency": "EUR",
        "seller": Party(name="Elektro Kessler GmbH", country_code="DE"),
        "buyer": Party(name="Holzwerk Brandt GmbH", country_code="DE"),
        "payee_bic": "COBADEFFXXX",
        "gross_total": Decimal("119.00"),
        "lines": [Line(line_id="1", unit_code="H87", tax_category="S")],
        **fields,
    }
    return CanonicalInvoice.model_validate(values)


def save(organization: Organization, invoice: CanonicalInvoice, spec_id: str = "urn:x") -> Invoice:
    return save_structured(
        make_document(organization),
        invoice,
        syntax="ubl",
        profile="XRECHNUNG",
        spec_id=spec_id,
        is_einvoice=True,
    )


def test_save_structured_keeps_values_that_fit(organization: Organization) -> None:
    invoice = save(organization, canonical())
    invoice.refresh_from_db()
    assert (invoice.seller_country_code, invoice.buyer_country_code) == ("DE", "DE")
    assert invoice.payee_bic == "COBADEFFXXX"
    assert invoice.spec_id == "urn:x"
    line = invoice.lines.get()
    assert (line.line_id, line.unit_code, line.tax_category) == ("1", "H87", "S")


def test_save_structured_stores_values_longer_than_their_column_as_none(
    organization: Organization,
) -> None:
    overlong = canonical(
        seller=Party(name="Elektro Kessler GmbH", country_code="DEU"),
        buyer=Party(name="Holzwerk Brandt GmbH", country_code="Germany"),
        payee_bic="COBADEFFXXX1",
        currency="EURO",
        lines=[Line(line_id="1" * 101, unit_code="X" * 17, tax_category="S" * 9)],
    )
    invoice = save(organization, overlong, spec_id="urn:" + "x" * 300)
    invoice.refresh_from_db()
    assert (invoice.seller_country_code, invoice.buyer_country_code) == (None, None)
    assert invoice.payee_bic is None
    assert invoice.currency is None
    assert invoice.spec_id is None
    assert invoice.seller_name == "Elektro Kessler GmbH"
    assert invoice.gross_total == Decimal("119.00")
    line = invoice.lines.get()
    assert (line.line_id, line.unit_code, line.tax_category) == (None, None, None)

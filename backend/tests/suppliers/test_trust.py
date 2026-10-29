import pytest

from accounts.models import Organization
from suppliers.trust import first_invoice_id, iban_status, is_trusted
from tests.factories import RECEIVED, add_iban, make_document, make_invoice, make_supplier

pytestmark = pytest.mark.django_db

KNOWN = "DE89370400440532013000"
NEW = "DE02120300000000202051"


def test_iban_on_the_first_invoice_is_known(organization: Organization) -> None:
    supplier = make_supplier(organization)
    first = make_invoice(make_document(organization, received_minutes=1), supplier=supplier)
    make_invoice(make_document(organization, received_minutes=2), supplier=supplier)
    entry = add_iban(supplier, KNOWN, first)
    assert first_invoice_id(supplier) == first.id
    assert iban_status(entry, first.id) == "known"
    assert is_trusted(entry, first.id)


def test_a_later_iban_is_new_until_confirmed(organization: Organization) -> None:
    supplier = make_supplier(organization)
    first = make_invoice(make_document(organization, received_minutes=1), supplier=supplier)
    later = make_invoice(make_document(organization, received_minutes=2), supplier=supplier)
    entry = add_iban(supplier, NEW, later)
    assert iban_status(entry, first.id) == "new"
    assert not is_trusted(entry, first.id)
    entry.confirmed_at = RECEIVED
    entry.save()
    assert iban_status(entry, first.id) == "confirmed"


def test_a_deleted_first_invoice_confers_no_trust(organization: Organization) -> None:
    # Deleting the first invoice must not promote the next one (and its IBAN) to "first".
    supplier = make_supplier(organization)
    deleted = make_document(organization, received_minutes=1)
    make_invoice(deleted, supplier=supplier)
    deleted.deleted_at = RECEIVED
    deleted.save()
    second = make_invoice(make_document(organization, received_minutes=2), supplier=supplier)
    entry = add_iban(supplier, NEW, second)
    assert first_invoice_id(supplier) is None
    assert iban_status(entry, first_invoice_id(supplier)) == "new"

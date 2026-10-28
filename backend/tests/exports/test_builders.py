"""The export files: column order, formatting, credit notes and the ZIP bundle."""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.storage import storages
from django.test import override_settings

from accounts.models import Organization
from eingang import storage
from exports import builders
from invoices.models import Document, Invoice, InvoiceLine, ValidationReport
from tests.conftest import MakeUser
from tests.factories import add_approval, add_check, make_document, make_invoice

pytestmark = pytest.mark.django_db
MOMENT = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _temporary_storage(tmp_path: Path) -> Iterator[None]:
    documents = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(tmp_path), "allow_overwrite": True},
    }
    with override_settings(STORAGES={**storages.backends, "documents": documents}):
        yield


def approved_invoice(
    organization: Organization, make_user: MakeUser, *, type_code: int = 380
) -> Document:
    document = make_document(organization, status=Document.Status.APPROVED)
    invoice = make_invoice(document, number="RE-2026-001")
    Invoice.objects.filter(id=invoice.id).update(
        type_code=type_code,
        due_date=date(2026, 3, 31),
        seller_vat_id="DE123456789",
        payee_iban="DE89370400440532013000",
        net_total=Decimal("1000.00"),
        tax_total=Decimal("190.00"),
        payable_amount=Decimal("1190.00"),
    )
    InvoiceLine.objects.create(
        invoice=invoice, position=1, description="=HYPERLINK(\"x\")", quantity=Decimal("2.5"),
        unit_code="H87", unit_price=Decimal("400"), net_amount=Decimal("1000.00"),
        tax_category="S", tax_rate=Decimal("19.00"),
    )  # fmt: skip
    ValidationReport.objects.create(
        document=document, status="valid", engine="test", xsd_ok=True, issues=[],
        ran_at=MOMENT,
    )  # fmt: skip
    add_check(document, "C04", "info")
    add_approval(document, make_user("approver"))
    return document


def loaded(document: Document) -> list[Document]:
    return list(builders.export_queryset(Document.objects.filter(id=document.id)))


def rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))


def test_invoices_csv_columns_and_values(organization: Organization, make_user: MakeUser) -> None:
    document = approved_invoice(organization, make_user)
    data = builders.csv_invoices(loaded(document))
    assert data.startswith(b"\xef\xbb\xbf")
    header, row = rows(data)
    assert header == [
        "Belegdatum", "Rechnungsnummer", "Lieferant", "USt-IdNr", "IBAN", "Netto", "USt",
        "Brutto", "Zahlbetrag", "Währung", "Fällig", "Format", "E-Rechnung", "Validierung",
        "Freigegeben von", "Freigegeben am", "Eingang-ID",
    ]  # fmt: skip
    assert row == [
        "01.03.2026", "RE-2026-001", "Elektro Kessler GmbH", "DE123456789",
        "DE89370400440532013000", "1000,00", "190,00", "1190,00", "1190,00", "EUR",
        "31.03.2026", "XRechnung · UBL", "ja", "valid", "Approver", "02.03.2026",
        str(document.id),
    ]  # fmt: skip
    assert b"\r\n" in data
    assert ";" in data.decode("utf-8-sig").splitlines()[0]


def test_lines_csv_columns_and_formula_guard(
    organization: Organization, make_user: MakeUser
) -> None:
    document = approved_invoice(organization, make_user)
    data = builders.csv_lines(loaded(document))
    assert data.startswith(b"\xef\xbb\xbf")
    header, row = rows(data)
    assert header == [
        "Eingang-ID", "Rechnungsnummer", "Position", "Beschreibung", "Menge", "Einheit",
        "Einzelpreis", "Netto", "Steuersatz",
    ]  # fmt: skip
    assert row == [
        str(document.id), "RE-2026-001", "1", "'=HYPERLINK(\"x\")", "2,5000", "H87",
        "400,000000", "1000,00", "19,00",
    ]  # fmt: skip


def test_credit_note_amounts_are_negative(organization: Organization, make_user: MakeUser) -> None:
    documents = loaded(approved_invoice(organization, make_user, type_code=381))
    _, row = rows(builders.csv_invoices(documents))
    assert row[5:9] == ["-1000,00", "-190,00", "-1190,00", "-1190,00"]
    _, line = rows(builders.csv_lines(documents))
    assert line[7] == "-1000,00"
    assert line[4] == "2,5000"  # quantities and unit prices keep their sign


def test_filenames_use_the_moment() -> None:
    assert builders.filename("csv_invoices", MOMENT) == "eingang-invoices-20260310-0930.csv"
    assert builders.filename("csv_lines", MOMENT) == "eingang-lines-20260310-0930.csv"
    assert builders.filename("zip_bundle", MOMENT) == "eingang-bundle-20260310-0930.zip"


def test_safe_name_keeps_only_safe_characters() -> None:
    assert builders.safe_name("../Rechnung März 2026.pdf") == "_Rechnung_M_rz_2026.pdf"
    assert builders.safe_name("..") == "original"


def test_zip_bundle_contents(organization: Organization, make_user: MakeUser) -> None:
    document = approved_invoice(organization, make_user, type_code=381)
    Document.objects.filter(id=document.id).update(original_filename="Rechnung 1.xml")
    storage.write(document.storage_key, b"<Invoice/>")
    documents = loaded(document)
    archive = zipfile.ZipFile(io.BytesIO(builders.zip_bundle(documents, MOMENT)))
    assert sorted(archive.namelist()) == sorted(
        [
            "eingang-invoices-20260310-0930.csv",
            "eingang-lines-20260310-0930.csv",
            f"originals/{document.id}-Rechnung_1.xml",
            f"reports/{document.id}.json",
            "SHA256SUMS",
        ]
    )
    assert archive.read("eingang-invoices-20260310-0930.csv") == builders.csv_invoices(documents)
    assert archive.read(f"originals/{document.id}-Rechnung_1.xml") == b"<Invoice/>"
    report = json.loads(archive.read(f"reports/{document.id}.json"))
    assert report["invoice"]["invoice_number"] == "RE-2026-001"
    assert report["invoice"]["gross_total"] == "1190.00"  # money as strings, as stored
    assert report["invoice"]["lines"][0]["net_amount"] == "1000.00"
    assert report["validation"]["status"] == "valid"
    assert [check["check_id"] for check in report["checks"]] == ["C04"]
    assert report["approval"]["decision"] == "approved"
    assert report["approval"]["decided_by"] == "Approver"


def test_zip_bundle_report_names_the_receipt(
    organization: Organization, make_user: MakeUser
) -> None:
    document = approved_invoice(organization, make_user)
    storage.write(document.storage_key, b"<Invoice/>")
    archive = zipfile.ZipFile(io.BytesIO(builders.zip_bundle(loaded(document), MOMENT)))
    report = json.loads(archive.read(f"reports/{document.id}.json"))
    assert report["sha256"] == document.sha256
    assert report["received_at"] == document.received_at.astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )
    assert report["source"] == "upload"


def test_zip_bundle_manifest_covers_every_file(
    organization: Organization, make_user: MakeUser
) -> None:
    first = approved_invoice(organization, make_user)
    second = make_document(organization, status=Document.Status.APPROVED)
    storage.write(first.storage_key, b"<Invoice/>")
    storage.write(second.storage_key, b"%PDF-1.7 second")
    documents = list(
        builders.export_queryset(Document.objects.filter(id__in=[first.id, second.id]))
    )
    archive = zipfile.ZipFile(io.BytesIO(builders.zip_bundle(documents, MOMENT)))
    manifest = archive.read("SHA256SUMS").decode("utf-8")
    assert manifest.endswith("\n")
    sums: dict[str, str] = {}
    for line in manifest.splitlines():
        # `sha256sum` format: 64 lower-case hex digits, two spaces, the path.
        assert re.fullmatch(r"[0-9a-f]{64}  \S.*", line), line
        digest, path = line.split("  ", 1)
        sums[path] = digest
    assert sorted(sums) == sorted(set(archive.namelist()) - {"SHA256SUMS"})
    for path, digest in sums.items():
        assert hashlib.sha256(archive.read(path)).hexdigest() == digest
    assert len(sums) == 6  # two CSVs, two originals, two reports

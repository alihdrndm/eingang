"""`fetch_mail` against a real database, with the IMAP server replaced by a fake."""

import hashlib
import imaplib
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import ClassVar

import pytest
from django.core.files.storage import storages
from django.test import override_settings

from accounts.models import Organization
from eingang import mailbox_activities, storage
from eingang.config import Settings
from eingang.mailbox_activities import fetch_mail
from eingang.temporal_errors import PermanentError
from eingang.workflows import contracts as c
from invoices.models import Document, Event

pytestmark = pytest.mark.django_db

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
PDF = (SAMPLES / "S03-SA-26-1187.pdf").read_bytes()
XML = (SAMPLES / "S01-RE-2026-0412.xml").read_bytes()
LIMIT = 100_000
SENDER = "rechnung@lieferant.example"


@pytest.fixture(autouse=True)
def _temporary_storage(tmp_path: Path) -> Iterator[None]:
    documents = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(tmp_path), "allow_overwrite": True},
    }
    with override_settings(STORAGES={**storages.backends, "documents": documents}):
        yield


@pytest.fixture(autouse=True)
def _keep_test_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    # The test runs inside a transaction; closing "old" connections would close it.
    monkeypatch.setattr(mailbox_activities, "close_old_connections", lambda: None)


def use_settings(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> None:
    settings = Settings(
        _env_file=None,
        MAILBOX_ENABLED=enabled,
        MAILBOX_HOST="imap.example.invalid",
        MAILBOX_USER="inbox@example.invalid",
        MAILBOX_PASSWORD="not-a-real-password",  # noqa: S106 - test value
        MAILBOX_ORG_SLUG="holzwerk-brandt",
        MAX_UPLOAD_BYTES=LIMIT,
    )
    monkeypatch.setattr(mailbox_activities, "get_settings", lambda: settings)


@dataclass
class Stored:
    raw: bytes
    flags: set[str] = field(default_factory=set)


class FakeImap:
    """Serves `messages` by UID; never opens a socket."""

    messages: ClassVar[dict[str, Stored]] = {}
    fetched: ClassVar[list[str]] = []
    refuse_login: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        assert (host, port) == ("imap.example.invalid", 993)
        self.logged_out = False

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if self.refuse_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"Logged in"]

    def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        assert mailbox == "INBOX"
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command: str, *args: str | bytes) -> tuple[str, list[object]]:
        if command == "SEARCH":
            assert args == ("UNSEEN",)
            unseen = [uid for uid, item in self.messages.items() if "\\Seen" not in item.flags]
            return "OK", [" ".join(unseen).encode()]
        uid = str(args[0])
        message = self.messages[uid]
        if command == "FETCH":
            self.fetched.append(str(args[1]))
            if args[1] == "(RFC822.SIZE)":
                return "OK", [f"{uid} (UID {uid} RFC822.SIZE {len(message.raw)})".encode()]
            assert args[1] == "(BODY.PEEK[])"
            return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(message.raw)}}}".encode(), message.raw)]
        assert (command, args[1:]) == ("STORE", ("+FLAGS", "(\\Seen)"))
        message.flags.add("\\Seen")
        return "OK", [b""]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b""]


def mail(*attachments: tuple[str, str, bytes], sender: str = SENDER) -> bytes:
    message = EmailMessage()
    message["From"] = f"Lieferant GmbH <{sender}>"
    message["To"] = "eingang@example.invalid"
    message["Subject"] = "Rechnung"
    message.set_content("Anbei die Rechnung.")
    for filename, mime, data in attachments:
        maintype, subtype = mime.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch, organization: Organization) -> type[FakeImap]:
    FakeImap.messages = {
        "1": Stored(mail(("rechnung.pdf", "application/pdf", PDF))),
        "2": Stored(
            mail(
                ("rechnung.xml", "application/xml", XML),
                ("notiz.txt", "text/plain", b"Bitte zahlen."),
            )
        ),
        "3": Stored(mail()),
    }
    FakeImap.fetched = []
    FakeImap.refuse_login = False
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    use_settings(monkeypatch)
    return FakeImap


def test_stores_pdf_and_xml_attachments_as_email_documents(
    imap: type[FakeImap], organization: Organization
) -> None:
    result = fetch_mail()

    documents = Document.objects.filter(organization=organization).order_by("received_at", "id")
    assert set(result.document_ids) == {document.id for document in documents}
    assert sorted(document.content_type for document in documents) == [
        "application/pdf",
        "application/xml",
    ]
    for document in documents:
        assert document.source == Document.Source.EMAIL
        assert document.sender_email == SENDER
        assert document.status == Document.Status.RECEIVED
        assert document.workflow_id == c.process_invoice_workflow_id(document.id)
        assert storage.read(document.storage_key) in (PDF, XML)
        event = Event.objects.get(document=document)
        assert event.type == Event.Type.DOCUMENT_RECEIVED
        assert event.actor is None
        assert event.data == {"source": "email", "size_bytes": document.size_bytes}
    pdf = documents.get(content_type="application/pdf")
    assert pdf.original_filename == "rechnung.pdf"
    assert pdf.sha256 == hashlib.sha256(PDF).hexdigest()


def test_marks_every_message_seen_and_only_peeks_at_bodies(imap: type[FakeImap]) -> None:
    fetch_mail()
    assert all("\\Seen" in item.flags for item in imap.messages.values())
    assert set(imap.fetched) == {"(RFC822.SIZE)", "(BODY.PEEK[])"}


def test_a_second_run_finds_nothing_new(imap: type[FakeImap], organization: Organization) -> None:
    fetch_mail()
    assert fetch_mail() == c.MailboxResult(document_ids=[])
    assert Document.objects.filter(organization=organization).count() == 2


def test_an_attachment_already_stored_is_skipped(
    imap: type[FakeImap], organization: Organization
) -> None:
    fetch_mail()
    imap.messages["4"] = Stored(mail(("kopie.pdf", "application/pdf", PDF)))
    assert fetch_mail() == c.MailboxResult(document_ids=[])
    assert Document.objects.filter(organization=organization).count() == 2
    assert "\\Seen" in imap.messages["4"].flags


def test_an_oversized_attachment_is_refused(
    imap: type[FakeImap], organization: Organization
) -> None:
    too_large = b"%PDF-1.7\n" + b"0" * LIMIT
    imap.messages = {"9": Stored(mail(("gross.pdf", "application/pdf", too_large)))}
    assert fetch_mail() == c.MailboxResult(document_ids=[])
    assert not Document.objects.filter(organization=organization).exists()
    assert "\\Seen" in imap.messages["9"].flags


def test_an_oversized_message_is_not_downloaded(
    imap: type[FakeImap], organization: Organization
) -> None:
    attachments = [
        (f"teil-{n}.pdf", "application/pdf", b"%PDF-" + bytes([n]) * LIMIT) for n in range(4)
    ]
    imap.messages = {"9": Stored(mail(*attachments))}
    assert fetch_mail() == c.MailboxResult(document_ids=[])
    assert imap.fetched == ["(RFC822.SIZE)"]
    assert "\\Seen" in imap.messages["9"].flags


def test_an_xml_with_a_doctype_is_refused(imap: type[FakeImap], organization: Organization) -> None:
    hostile = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        + XML.split(b"?>", 1)[1]
    )
    imap.messages = {"9": Stored(mail(("boese.xml", "application/xml", hostile)))}
    assert fetch_mail() == c.MailboxResult(document_ids=[])
    assert not Document.objects.filter(organization=organization).exists()


def test_disabled_mailbox_is_a_permanent_error(
    imap: type[FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    use_settings(monkeypatch, enabled=False)
    with pytest.raises(PermanentError, match="disabled"):
        fetch_mail()


def test_unknown_organisation_is_a_permanent_error(
    imap: type[FakeImap], organization: Organization
) -> None:
    Organization.objects.filter(id=organization.id).update(slug="anders")
    with pytest.raises(PermanentError, match="MAILBOX_ORG_SLUG"):
        fetch_mail()


def test_refused_login_is_a_permanent_error(imap: type[FakeImap]) -> None:
    imap.refuse_login = True
    with pytest.raises(PermanentError, match="refused"):
        fetch_mail()
    assert not any("\\Seen" in item.flags for item in imap.messages.values())

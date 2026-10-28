"""Receiving uploaded files (HANDOFF "Uploads", "Sandbox storage quota").

Size is enforced while the request body is read, never after the whole file is in memory;
the type is decided by content only (magic bytes for PDF, a safe parse for XML). This
module runs in the API process, so it uses einvoice's safe XML parser and namespaces only,
never the PDF or validation libraries (import-linter: api-stays-light).
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.db import IntegrityError, transaction
from django.db.models import Sum

from accounts.models import Organization, User
from eingang import clock, storage
from eingang.config import get_settings
from eingang.problem import ProblemError
from eingang.workflows.contracts import process_invoice_workflow_id
from einvoice import namespaces
from einvoice.errors import UnsupportedFileError
from einvoice.xmlsafe import parse_xml, strip_leading
from invoices.models import LIVE_SHA256_CONSTRAINT, Document, Event

MAX_FILES_PER_REQUEST = 10
# Multipart boundaries and headers around the files; generous, it only bounds the body.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
INVOICE_ROOTS = frozenset(
    {namespaces.UBL_INVOICE_ROOT, namespaces.UBL_CREDIT_NOTE_ROOT, namespaces.CII_ROOT}
)


def file_too_large() -> ProblemError:
    limit_mb = get_settings().MAX_UPLOAD_BYTES / (1024 * 1024)
    return ProblemError(
        413, "FILE_TOO_LARGE", "File too large", f"Each file may be at most {limit_mb:g} MB."
    )


def unsupported_file(reason: str) -> ProblemError:
    return ProblemError(
        415,
        "UNSUPPORTED_FILE",
        "Unsupported file",
        f"Only PDF and XML invoices (UBL or CII) are accepted: {reason}.",
    )


def too_many_files() -> ProblemError:
    return ProblemError(
        400,
        "TOO_MANY_FILES",
        "Too many files",
        f"Send at most {MAX_FILES_PER_REQUEST} files per request.",
    )


def sandbox_upload_limit(detail: str) -> ProblemError:
    return ProblemError(429, "SANDBOX_UPLOAD_LIMIT", "Sandbox upload limit reached", detail)


class SizeLimitedUploadHandler(FileUploadHandler):
    """Keeps each file in memory, but stops reading as soon as one exceeds the limit."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.too_large = False
        self._buffer = BytesIO()
        self._size = 0

    def new_file(self, *args: Any, **kwargs: Any) -> None:  # boundary: django handler API
        super().new_file(*args, **kwargs)
        self._buffer = BytesIO()
        self._size = 0

    def receive_data_chunk(self, raw_data: bytes, start: int) -> None:
        self._size += len(raw_data)
        if self._size > self.limit:
            self.too_large = True
            # Stop storing, but let Django read the rest of the body so the client gets the 413
            # response rather than a reset connection behind a proxy.
            raise StopUpload(connection_reset=False)
        self._buffer.write(raw_data)

    def file_complete(self, file_size: int) -> InMemoryUploadedFile:
        self._buffer.seek(0)
        return InMemoryUploadedFile(
            file=self._buffer,
            field_name=self.field_name,
            name=self.file_name,
            content_type=self.content_type,
            size=file_size,
            charset=self.charset,
            content_type_extra=self.content_type_extra,
        )


def check_request_size(content_length: str | None) -> None:
    """Refuse a body larger than ten maximum-size files before reading any of it."""
    limit = get_settings().MAX_UPLOAD_BYTES * MAX_FILES_PER_REQUEST + MULTIPART_OVERHEAD_BYTES
    try:
        length = int(content_length or 0)
    except ValueError:
        length = 0
    if length > limit:
        raise file_too_large()


@dataclass(frozen=True)
class FileType:
    extension: str
    content_type: str


def file_type(data: bytes) -> FileType:
    """PDF by its magic bytes; XML when it parses safely and its root is an invoice."""
    if data.startswith(b"%PDF-"):
        return FileType("pdf", "application/pdf")
    if not strip_leading(data).startswith(b"<"):
        raise unsupported_file("the file is neither PDF nor XML")
    try:
        root = parse_xml(data)
    except UnsupportedFileError as error:
        raise unsupported_file(error.reason) from error
    if root.tag not in INVOICE_ROOTS:
        raise unsupported_file("the XML is not a UBL or CII invoice")
    return FileType("xml", "application/xml")


def check_sandbox_quota(organization: Organization, new_files: int, new_bytes: int) -> None:
    if not organization.is_sandbox:
        return
    config = get_settings()
    uploaded = Document.objects.filter(
        organization=organization, source=Document.Source.UPLOAD
    ).count()
    if uploaded + new_files > config.SANDBOX_MAX_UPLOADS:
        raise sandbox_upload_limit(
            f"A sandbox can hold at most {config.SANDBOX_MAX_UPLOADS} uploaded files."
        )
    stored = (
        Document.objects.filter(organization__kind=Organization.Kind.SANDBOX).aggregate(
            total=Sum("size_bytes")
        )["total"]
        or 0
    )
    if stored + new_bytes > config.SANDBOX_STORAGE_BUDGET_BYTES:
        raise sandbox_upload_limit(
            "The demo's storage is full right now. Please try again tomorrow."
        )


@dataclass(frozen=True)
class Duplicate:
    filename: str
    existing_document_id: str


@dataclass(frozen=True)
class UploadResult:
    created: list[Document]
    duplicates: list[Duplicate]


def is_duplicate(organization: Organization, sha256: str) -> bool:
    """An identical file the organisation still has (deleted documents do not count)."""
    return Document.objects.filter(
        organization=organization, sha256=sha256, deleted_at__isnull=True
    ).exists()


def _is_live_duplicate(error: IntegrityError) -> bool:
    """The insert lost a race for `documents_org_sha256_live_uniq`, and nothing else."""
    diag = getattr(error.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None) == LIVE_SHA256_CONSTRAINT


def store_document(
    organization: Organization,
    name: str,
    data: bytes,
    kind: FileType,
    *,
    sha256: str,
    source: str,
    actor: User | None,
    sender_email: str | None = None,
) -> Document:
    """Save one checked file as a `received` document with its file and `document.received`.

    The workflow ID is set here so the maintenance can start the document if starting its
    workflow fails. Used by the upload API and by the mailbox intake.
    """
    document = Document(
        organization=organization,
        source=source,
        original_filename=name[:255],
        content_type=kind.content_type,
        size_bytes=len(data),
        sha256=sha256,
        received_at=clock.now(),
        status=Document.Status.RECEIVED,
        sender_email=sender_email,
    )
    document.storage_key = storage.original_key(
        organization.id, document.id, sha256, kind.extension
    )
    document.workflow_id = process_invoice_workflow_id(document.id)
    document.save()
    storage.write(document.storage_key, data)
    Event.objects.create(
        organization=organization,
        document=document,
        actor=actor,
        type=Event.Type.DOCUMENT_RECEIVED,
        data={"source": source, "size_bytes": len(data)},
    )
    return document


@transaction.atomic
def store_uploads(user: User, files: list[tuple[str, bytes]]) -> UploadResult:
    """Store new files as `received` documents; exact duplicates are reported, not stored."""
    organization = user.organization
    typed = [(name, data, file_type(data)) for name, data in files]
    created: list[Document] = []
    duplicates: list[tuple[str, int, str]] = []  # (filename, size, sha256)
    new_files: dict[str, tuple[str, bytes, FileType]] = {}  # sha256 -> first copy
    for name, data, kind in typed:
        sha256 = hashlib.sha256(data).hexdigest()
        if is_duplicate(organization, sha256) or sha256 in new_files:
            duplicates.append((name, len(data), sha256))
        else:
            new_files[sha256] = (name, data, kind)
    check_sandbox_quota(
        organization, len(new_files), sum(len(item[1]) for item in new_files.values())
    )
    for sha256, (name, data, kind) in new_files.items():
        try:
            # A savepoint, so a lost race leaves the other files of the request intact.
            with transaction.atomic():
                document = store_document(
                    organization,
                    name,
                    data,
                    kind,
                    sha256=sha256,
                    source=Document.Source.UPLOAD,
                    actor=user,
                )
        except IntegrityError as error:
            if not _is_live_duplicate(error):
                raise
            # A parallel request stored the same file after the duplicate check. The insert
            # failed before the file was written, and the key holds this document's own id,
            # so nothing is left in storage.
            duplicates.append((name, len(data), sha256))
            continue
        created.append(document)
    reported = []
    for name, size, sha256 in duplicates:
        existing = Document.objects.get(
            organization=organization, sha256=sha256, deleted_at__isnull=True
        )
        Event.objects.create(
            organization=organization,
            document=existing,
            actor=user,
            type=Event.Type.DOCUMENT_DUPLICATE_UPLOAD,
            data={"size_bytes": size},
        )
        reported.append(Duplicate(filename=name, existing_document_id=str(existing.id)))
    return UploadResult(created=created, duplicates=reported)

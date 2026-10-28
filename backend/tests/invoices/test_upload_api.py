from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import override_settings
from django.test.client import MULTIPART_CONTENT

from accounts.models import Organization
from eingang import storage, temporal_client
from eingang.config import Settings
from invoices import middleware as invoices_middleware
from invoices import uploads
from invoices.models import Document, Event
from tests.conftest import ApiClient, MakeUser
from tests.fixtures import xml as xml_fixtures
from tests.fixtures.pdfs import LONG_TEXT, text_pdf

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response

pytestmark = pytest.mark.django_db
SAMPLES = Path(__file__).resolve().parents[3] / "samples"
S01 = (SAMPLES / "S01-RE-2026-0412.xml").read_bytes()


@pytest.fixture(autouse=True)
def _temporary_storage(tmp_path: Path) -> Iterator[None]:
    documents = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(tmp_path), "allow_overwrite": True},
    }
    with override_settings(STORAGES={**storages.backends, "documents": documents}):
        yield


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """Documents handed to Temporal; no real Temporal is needed."""
    calls: list[UUID] = []
    monkeypatch.setattr(temporal_client, "start_processing", calls.append)
    return calls


def with_settings(monkeypatch: pytest.MonkeyPatch, **values: int) -> None:
    changed = Settings(_env_file=None, **values)  # type: ignore[arg-type]  # test override
    monkeypatch.setattr(uploads, "get_settings", lambda: changed)
    monkeypatch.setattr(invoices_middleware, "get_settings", lambda: changed)


def upload(api: ApiClient, *files: tuple[str, bytes]) -> "Response":
    return api.unsafe(
        "post",
        "/api/v1/documents",
        data={"files": [SimpleUploadedFile(name, data) for name, data in files]},
        content_type=MULTIPART_CONTENT,
    )


def test_xml_upload_is_stored_and_started(
    signed_in: Callable[[str], ApiClient], started: list[UUID]
) -> None:
    response = upload(signed_in("accountant"), ("rechnung.xml", S01))
    assert response.status_code == 201
    body = response.json()
    assert body["duplicates"] == []
    (summary,) = body["created"]
    assert summary["status"] == "received"
    assert summary["original_filename"] == "rechnung.xml"
    document = Document.objects.get(id=summary["id"])
    assert document.content_type == "application/xml"
    assert document.workflow_id == f"invoice-{document.id}"
    assert document.storage_key.endswith(f"/{document.sha256}.xml")
    assert "rechnung" not in document.storage_key  # never the original name
    assert storage.read(document.storage_key) == S01
    assert started == [document.id]
    assert Event.objects.filter(document=document, type="document.received").exists()


def test_pdf_upload_is_accepted_by_content(
    signed_in: Callable[[str], ApiClient], started: list[UUID]
) -> None:
    response = upload(signed_in("admin"), ("scan.xml", text_pdf([LONG_TEXT])))
    assert response.status_code == 201
    document = Document.objects.get(id=response.json()["created"][0]["id"])
    assert document.content_type == "application/pdf"


def test_duplicate_upload_is_reported_not_stored(
    signed_in: Callable[[str], ApiClient], started: list[UUID]
) -> None:
    api = signed_in("accountant")
    first = upload(api, ("a.xml", S01)).json()["created"][0]["id"]
    response = upload(api, ("again.xml", S01))
    assert response.status_code == 201
    assert response.json() == {
        "created": [],
        "duplicates": [{"filename": "again.xml", "existing_document_id": first}],
    }
    assert Document.objects.count() == 1
    assert Event.objects.filter(type="document.duplicate_upload").count() == 1


def test_duplicate_within_one_request(
    signed_in: Callable[[str], ApiClient], started: list[UUID]
) -> None:
    body = upload(signed_in("accountant"), ("a.xml", S01), ("b.xml", S01)).json()
    assert len(body["created"]) == 1
    assert body["duplicates"] == [
        {"filename": "b.xml", "existing_document_id": body["created"][0]["id"]}
    ]


def test_a_deleted_document_does_not_count_as_duplicate(
    signed_in: Callable[[str], ApiClient], started: list[UUID]
) -> None:
    api = signed_in("accountant")
    first = upload(api, ("a.xml", S01)).json()["created"][0]["id"]
    Document.objects.filter(id=first).update(deleted_at="2026-03-01T00:00:00Z")
    assert len(upload(api, ("a.xml", S01)).json()["created"]) == 1


def test_concurrent_duplicate_upload_is_reported_not_500(
    signed_in: Callable[[str], ApiClient],
    started: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api = signed_in("accountant")
    first = upload(api, ("a.xml", S01)).json()["created"][0]["id"]
    # Simulate the race: the pre-check misses the parallel insert, the unique index sees it.
    monkeypatch.setattr(uploads, "is_duplicate", lambda organization, sha256: False)
    other = text_pdf([LONG_TEXT])
    response = upload(api, ("again.xml", S01), ("new.pdf", other))
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["duplicates"] == [{"filename": "again.xml", "existing_document_id": first}]
    (created,) = body["created"]  # the other file of the request is still stored
    assert created["original_filename"] == "new.pdf"
    assert Document.objects.count() == 2
    assert Event.objects.filter(type="document.duplicate_upload").count() == 1
    # The losing insert wrote no file: only the two documents' own originals exist.
    stored = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    expected = sorted(tmp_path / document.storage_key for document in Document.objects.all())
    assert stored == expected


@pytest.mark.parametrize(
    ("constraint", "duplicate"),
    [("documents_org_sha256_live_uniq", True), ("documents_pkey", False), (None, False)],
)
def test_only_the_live_sha256_constraint_counts_as_duplicate(
    constraint: str | None, duplicate: bool
) -> None:
    class DriverError(Exception):  # stands in for psycopg's error and its diagnostics
        diag = SimpleNamespace(constraint_name=constraint)

    error = IntegrityError("duplicate key")
    error.__cause__ = DriverError()
    assert uploads._is_live_duplicate(error) is duplicate


def test_FILE_TOO_LARGE_one_file_over_the_limit(
    signed_in: Callable[[str], ApiClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    with_settings(monkeypatch, MAX_UPLOAD_BYTES=1024)
    response = upload(signed_in("accountant"), ("big.xml", S01))
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"
    assert Document.objects.count() == 0


def test_FILE_TOO_LARGE_body_refused_before_reading(
    signed_in: Callable[[str], ApiClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    with_settings(monkeypatch, MAX_UPLOAD_BYTES=10)
    api = signed_in("accountant")
    response = api.unsafe(
        "post",
        "/api/v1/documents",
        data={"files": [SimpleUploadedFile("a.xml", b"x" * 200_000)]},
        content_type=MULTIPART_CONTENT,
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("photo.pdf", b"\x89PNG\r\n\x1a\n" + b"0" * 100),
        ("notes.xml", b"plain text, not xml"),
        ("other.xml", b"<order xmlns='urn:example'/>"),
        ("broken.xml", b"<Invoice><unclosed></Invoice>"),
        ("zugferd1.xml", xml_fixtures.zugferd1()),
    ],
)
def test_UNSUPPORTED_FILE_decided_by_content(
    name: str, data: bytes, signed_in: Callable[[str], ApiClient]
) -> None:
    response = upload(signed_in("accountant"), (name, data))
    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_FILE"
    assert Document.objects.count() == 0


def test_UNSUPPORTED_FILE_xxe_upload_is_refused(signed_in: Callable[[str], ApiClient]) -> None:
    payload = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><Invoice>&x;</Invoice>'
    response = upload(signed_in("accountant"), ("x.xml", payload))
    assert response.status_code == 415
    assert "passwd" not in response.content.decode()


def test_TOO_MANY_FILES_more_than_ten(signed_in: Callable[[str], ApiClient]) -> None:
    files = [(f"f{number}.xml", xml_fixtures.ubl(f"spec-{number}")) for number in range(11)]
    response = upload(signed_in("accountant"), *files)
    assert response.status_code == 400
    assert response.json()["code"] == "TOO_MANY_FILES"


def test_VALIDATION_FAILED_without_files(signed_in: Callable[[str], ApiClient]) -> None:
    response = signed_in("accountant").unsafe(
        "post", "/api/v1/documents", data={}, content_type=MULTIPART_CONTENT
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "files"


@pytest.mark.parametrize("role", ["approver", "viewer"])
def test_FORBIDDEN_ROLE_upload(role: str, signed_in: Callable[[str], ApiClient]) -> None:
    response = upload(signed_in(role), ("a.xml", S01))
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_ROLE"


def test_NOT_AUTHENTICATED_upload(api: ApiClient) -> None:
    assert upload(api, ("a.xml", S01)).json()["code"] == "NOT_AUTHENTICATED"


def test_TEMPORAL_UNAVAILABLE_keeps_the_upload(
    signed_in: Callable[[str], ApiClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(document_id: UUID) -> None:
        raise temporal_client.TemporalUnavailableError

    monkeypatch.setattr(temporal_client, "start_processing", unavailable)
    response = upload(signed_in("accountant"), ("a.xml", S01))
    assert response.status_code == 503
    assert response.json()["code"] == "TEMPORAL_UNAVAILABLE"
    # The only recovery is the daily maintenance, so the message must not promise minutes.
    assert "03:10 UTC" in response.json()["detail"]
    document = Document.objects.get()
    assert document.status == "received"
    assert document.workflow_id == f"invoice-{document.id}"


def sandbox_admin(make_user: MakeUser) -> ApiClient:
    sandbox = Organization.objects.create(name="Sandbox", slug="sb-up", kind="sandbox")
    api = ApiClient()
    api.sign_in(make_user("admin", org=sandbox))
    return api


def test_SANDBOX_UPLOAD_LIMIT_files_per_sandbox(
    make_user: MakeUser, monkeypatch: pytest.MonkeyPatch, started: list[UUID]
) -> None:
    with_settings(monkeypatch, SANDBOX_MAX_UPLOADS=1)
    api = sandbox_admin(make_user)
    assert upload(api, ("a.xml", S01)).status_code == 201
    response = upload(api, ("b.xml", xml_fixtures.ubl()))
    assert response.status_code == 429
    assert response.json()["code"] == "SANDBOX_UPLOAD_LIMIT"


def test_SANDBOX_UPLOAD_LIMIT_storage_budget(
    make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_settings(monkeypatch, SANDBOX_STORAGE_BUDGET_BYTES=100)
    response = upload(sandbox_admin(make_user), ("a.xml", S01))
    assert response.status_code == 429
    assert response.json()["code"] == "SANDBOX_UPLOAD_LIMIT"


def test_quota_does_not_apply_to_standard_organisations(
    signed_in: Callable[[str], ApiClient], monkeypatch: pytest.MonkeyPatch, started: list[UUID]
) -> None:
    with_settings(monkeypatch, SANDBOX_MAX_UPLOADS=0, SANDBOX_STORAGE_BUDGET_BYTES=0)
    assert upload(signed_in("accountant"), ("a.xml", S01)).status_code == 201

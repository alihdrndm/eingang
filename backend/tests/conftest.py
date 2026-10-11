"""Fixtures shared by the API tests."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn

import pytest
from django.core.cache import cache
from django.core.files.storage import storages
from django.test import Client, override_settings

from accounts.models import Organization, User
from eingang import clock
from eingang.config import Settings
from llm import client as llm_client

if TYPE_CHECKING:
    # Only django-stubs defines this type; at run time the client returns an HttpResponse.
    from django.test.client import _MonkeyPatchedWSGIResponse as Response

PASSWORD = "correct horse battery"  # noqa: S105 - test fixture
NOW = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fast_password_hashing() -> Iterator[None]:
    # Production hashing is slow on purpose; tests only need a working hasher.
    with override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]):
        yield


@pytest.fixture(autouse=True)
def _temporary_document_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    # No test writes into the development storage folder.
    documents = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(tmp_path_factory.mktemp("documents")), "allow_overwrite": True},
    }
    with override_settings(STORAGES={**storages.backends, "documents": documents}):
        yield


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # The LLM client never reads the developer's .env (which may switch it on with a real
    # key) and never builds the real SDK; tests that call it install a fake (tests/llm).
    test_settings = Settings(_env_file=None)
    monkeypatch.setattr(llm_client, "get_settings", lambda: test_settings)

    def no_sdk(_settings: Settings) -> NoReturn:
        raise AssertionError("A test reached the real OpenAI SDK; install tests.llm.fakes.")

    monkeypatch.setattr(llm_client, "sdk_factory", no_sdk)


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    # Throttle counters live in the cache; each test starts with none.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fixed_clock() -> Iterator[clock.FixedClock]:
    fixed = clock.FixedClock(NOW)
    previous = clock.get_clock()
    clock.set_clock(fixed)
    yield fixed
    clock.set_clock(previous)


@pytest.fixture
def organization(db: None) -> Organization:
    return Organization.objects.create(
        name="Holzwerk Brandt GmbH", slug="holzwerk-brandt", vat_id="DE291746055"
    )


@pytest.fixture
def other_organization(db: None) -> Organization:
    return Organization.objects.create(name="Andere GmbH", slug="andere")


MakeUser = Callable[..., User]


@pytest.fixture
def make_user(organization: Organization) -> MakeUser:
    def make(
        role: str = "admin", *, org: Organization | None = None, email: str | None = None
    ) -> User:
        target = org or organization
        address = email or f"{role}-{target.slug}@example.invalid"
        return User.objects.create_user(
            address, PASSWORD, organization=target, role=role, name=role.title()
        )

    return make


class ApiClient(Client):
    """A test client that behaves like the SPA: fetches the CSRF token and sends it."""

    def __init__(self) -> None:
        super().__init__(enforce_csrf_checks=True)

    def csrf(self) -> str:
        self.get("/api/v1/auth/csrf")
        return self.cookies["csrftoken"].value

    def unsafe(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: object
    ) -> "Response":
        """A POST/PATCH/DELETE with the CSRF token, as the SPA sends it."""
        all_headers = {**(headers or {}), "X-CSRFToken": self.csrf()}
        kwargs.setdefault("content_type", "application/json")  # the SPA always sends JSON
        call = getattr(self, method)
        response: Response = call(path, headers=all_headers, **kwargs)
        return response

    def sign_in(self, user: User) -> None:
        self.force_login(user)


@pytest.fixture
def api() -> ApiClient:
    return ApiClient()


@pytest.fixture
def signed_in(api: ApiClient, make_user: MakeUser) -> Callable[[str], ApiClient]:
    def sign_in(role: str = "admin") -> ApiClient:
        api.sign_in(make_user(role))
        return api

    return sign_in

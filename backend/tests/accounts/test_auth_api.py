from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client, override_settings

from accounts.models import Organization
from eingang import clock
from tests.conftest import PASSWORD, ApiClient, MakeUser

pytestmark = pytest.mark.django_db


def test_csrf_endpoint_sets_the_cookie(api: ApiClient) -> None:
    response = api.get("/api/v1/auth/csrf")
    assert response.status_code == 204
    assert "csrftoken" in response.cookies


def test_login_returns_the_session_and_signs_in(api: ApiClient, make_user: MakeUser) -> None:
    user = make_user("accountant")
    response = api.unsafe(
        "post",
        "/api/v1/auth/login",
        data={"email": user.email.upper(), "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "accountant"
    assert body["user"] == {"id": str(user.id), "email": user.email, "name": "Accountant"}
    assert body["organization"]["vat_id"] == "DE291746055"
    assert "expires_at" not in body["organization"]  # unset optional fields are omitted
    assert api.get("/api/v1/auth/me").status_code == 200


def test_INVALID_CREDENTIALS_wrong_password(api: ApiClient, make_user: MakeUser) -> None:
    user = make_user()
    response = api.unsafe(
        "post",
        "/api/v1/auth/login",
        data={"email": user.email, "password": "wrong"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_INVALID_CREDENTIALS_inactive_user(api: ApiClient, make_user: MakeUser) -> None:
    user = make_user()
    user.is_active = False
    user.save()
    response = api.unsafe(
        "post",
        "/api/v1/auth/login",
        data={"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_login_needs_the_csrf_token(make_user: MakeUser) -> None:
    user = make_user()
    client = Client(enforce_csrf_checks=True)
    response = client.post(
        "/api/v1/auth/login",
        data={"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_VALIDATION_FAILED_login_without_password(api: ApiClient) -> None:
    response = api.unsafe(
        "post", "/api/v1/auth/login", data={"email": "x"}, content_type="application/json"
    )
    assert response.status_code == 422
    paths = {error["path"] for error in response.json()["errors"]}
    assert paths == {"email", "password"}


def test_NOT_AUTHENTICATED_me_without_a_session(api: ApiClient) -> None:
    response = api.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_logout_ends_the_session(signed_in: Callable[[str], ApiClient]) -> None:
    api = signed_in("viewer")
    assert api.unsafe("post", "/api/v1/auth/logout").status_code == 204
    assert api.get("/api/v1/auth/me").status_code == 401


def test_CSRF_FAILED_signed_in_post_without_token(signed_in: Callable[[str], ApiClient]) -> None:
    api = signed_in("admin")
    response = api.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_SANDBOX_EXPIRED_after_expires_at(
    api: ApiClient, make_user: MakeUser, fixed_clock: clock.FixedClock
) -> None:
    sandbox = Organization.objects.create(
        name="Sandbox",
        slug="sandbox-1",
        kind="sandbox",
        expires_at=fixed_clock.now() + timedelta(hours=1),
    )
    api.sign_in(make_user("admin", org=sandbox))
    assert api.get("/api/v1/auth/me").status_code == 200
    fixed_clock.moment += timedelta(hours=2)
    response = api.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SANDBOX_EXPIRED"
    fixed_clock.moment -= timedelta(hours=2)
    # The session was flushed: going back in time does not restore it.
    assert api.get("/api/v1/auth/me").json()["code"] == "NOT_AUTHENTICATED"


def test_RATE_LIMITED_login_after_ten_attempts_per_minute(api: ApiClient) -> None:
    token = api.csrf()
    for _ in range(10):
        api.post(
            "/api/v1/auth/login",
            data={"email": "nobody@example.invalid", "password": "x"},
            content_type="application/json",
            headers={"X-CSRFToken": token},
        )
    response = api.post(
        "/api/v1/auth/login",
        data={"email": "nobody@example.invalid", "password": "x"},
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert int(response["Retry-After"]) > 0


def test_client_supplied_forwarded_for_does_not_change_the_throttle_key(api: ApiClient) -> None:
    token = api.csrf()
    for number in range(11):
        response = api.post(
            "/api/v1/auth/login",
            data={"email": "nobody@example.invalid", "password": "x"},
            content_type="application/json",
            headers={"X-CSRFToken": token, "X-Forwarded-For": f"203.0.113.{number}"},
        )
    assert response.status_code == 429


PROXY_SECRET = "s3cret"  # noqa: S105 - test value


def _login_burst(api: ApiClient, headers: Callable[[int], dict[str, str]]) -> int:
    """Eleven failed logins, one over the limit; the status of the last one."""
    token = api.csrf()
    for number in range(11):
        response = api.post(
            "/api/v1/auth/login",
            data={"email": "nobody@example.invalid", "password": "x"},
            content_type="application/json",
            headers={"X-CSRFToken": token, **headers(number)},
        )
    return response.status_code


def test_the_vercel_middleware_address_is_the_throttle_key_with_the_secret(api: ApiClient) -> None:
    with override_settings(PROXY_SHARED_SECRET=PROXY_SECRET):
        status = _login_burst(
            api,
            lambda number: {
                "X-Eingang-Proxy-Secret": PROXY_SECRET,
                "X-Eingang-Client-IP": f"203.0.113.{number}",
            },
        )
    assert status == 400  # wrong password, not limited: eleven visitors, one attempt each


@pytest.mark.parametrize("secret_sent", ["wrong", ""], ids=["wrong-secret", "no-secret"])
def test_a_forwarded_address_without_the_secret_does_not_change_the_throttle_key(
    api: ApiClient, secret_sent: str
) -> None:
    with override_settings(PROXY_SHARED_SECRET=PROXY_SECRET):
        status = _login_burst(
            api,
            lambda number: {
                "X-Eingang-Proxy-Secret": secret_sent,
                "X-Eingang-Client-IP": f"203.0.113.{number}",
            },
        )
    assert status == 429


def test_the_forwarded_address_is_ignored_when_no_secret_is_configured(api: ApiClient) -> None:
    with override_settings(PROXY_SHARED_SECRET=""):
        status = _login_burst(
            api,
            lambda number: {
                "X-Eingang-Proxy-Secret": "",
                "X-Eingang-Client-IP": f"203.0.113.{number}",
            },
        )
    assert status == 429


def test_one_forwarded_visitor_is_still_limited(api: ApiClient) -> None:
    with override_settings(PROXY_SHARED_SECRET=PROXY_SECRET):
        status = _login_burst(
            api,
            lambda _: {
                "X-Eingang-Proxy-Secret": PROXY_SECRET,
                "X-Eingang-Client-IP": "203.0.113.7",
            },
        )
    assert status == 429

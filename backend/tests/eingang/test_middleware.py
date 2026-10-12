import logging
import uuid

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from eingang.middleware import DEFAULT_HTML_CSP, RequestContextMiddleware
from eingang.problem import PROBLEM_CONTENT_TYPE


def raising_view(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("internal detail that must not leak")


def plain_form_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse(status=204)


urlpatterns = [path("boom", raising_view), path("form", plain_form_view)]


def test_request_id_is_echoed_when_supplied(client: Client) -> None:
    response = client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert response["x-request-id"] == "abc-123"


def test_request_id_is_generated_as_uuid7_when_missing(client: Client) -> None:
    response = client.get("/healthz")
    assert uuid.UUID(response["x-request-id"]).version == 7


def test_unsafe_request_id_is_replaced(client: Client) -> None:
    response = client.get("/healthz", headers={"x-request-id": "bad id\nwith newline"})
    assert uuid.UUID(response["x-request-id"]).version == 7


def test_request_log_line_has_no_query_string(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="eingang.request"):
        client.get("/healthz?name=Anna+Weber", headers={"x-request-id": "req-1"})
    lines = [record for record in caplog.records if record.name == "eingang.request"]
    assert len(lines) == 1
    message = lines[0].getMessage()
    assert message.startswith("GET /healthz 200 ")
    assert "Anna" not in message
    assert lines[0].request_id == "req-1"  # type: ignore[attr-defined]  # set by RequestIdFilter


def test_unknown_route_is_problem_not_found(client: Client) -> None:
    response = client.get("/no/such/route")
    assert response.status_code == 404
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["code"] == "NOT_FOUND"
    assert response.json()["instance"] == response["x-request-id"]


@pytest.mark.urls("tests.eingang.test_middleware")
def test_unhandled_exception_is_problem_internal_without_details() -> None:
    client = Client(raise_request_exception=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["code"] == "INTERNAL"
    assert "internal detail" not in response.content.decode()


def test_security_headers(client: Client) -> None:
    response = client.get("/healthz")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["X-Frame-Options"] == "DENY"


def test_disallowed_host_is_problem_malformed_request(client: Client) -> None:
    response = client.get("/healthz", headers={"host": "evil.example"})
    assert response.status_code == 400
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["code"] == "MALFORMED_REQUEST"


@pytest.mark.urls("tests.eingang.test_middleware")
def test_csrf_failure_is_problem_csrf_failed() -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.post("/form")
    assert response.status_code == 403
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["code"] == "CSRF_FAILED"


def test_html_responses_get_a_restrictive_csp_by_default() -> None:
    response = HttpResponse("<p>hi</p>", content_type="text/html")
    wrapped = RequestContextMiddleware(lambda request: response)
    result = wrapped(RequestFactory().get("/somewhere"))
    assert result["Content-Security-Policy"] == DEFAULT_HTML_CSP


def test_docs_page_sends_its_own_csp_without_inline_script(client: Client) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    policy = response["Content-Security-Policy"]
    assert policy.startswith("default-src 'none'")
    assert "unsafe-inline" not in policy.split("script-src", 1)[1].split(";", 1)[0]
    assert "swagger-ui-dist@5.33.1" in response.content.decode()


PROXY_SECRET = "s3cret"  # noqa: S105 - test value


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("required", "headers", "status"),
    [
        (True, {}, 403),
        (True, {"X-Eingang-Proxy-Secret": "guess"}, 403),
        (True, {"X-Eingang-Proxy-Secret": PROXY_SECRET}, 200),
        (False, {}, 200),
    ],
    ids=["required-no-secret", "required-wrong-secret", "required-secret", "not-required"],
)
def test_PROXY_REQUIRED_api_answers_only_through_the_web_app_when_required(
    client: Client, required: bool, headers: dict[str, str], status: int
) -> None:
    with override_settings(PROXY_SECRET_REQUIRED=required, PROXY_SHARED_SECRET=PROXY_SECRET):
        response = client.get("/api/v1/accuracy", headers=headers)
    assert response.status_code == status
    if status == 403:
        assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
        assert response.json()["code"] == "PROXY_REQUIRED"


def test_health_checks_stay_open_when_the_proxy_is_required(client: Client) -> None:
    with override_settings(PROXY_SECRET_REQUIRED=True, PROXY_SHARED_SECRET=PROXY_SECRET):
        assert client.get("/healthz").status_code == 200

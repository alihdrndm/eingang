"""Request id, the one request log line, and problem+json for errors outside DRF views."""

import logging
import re
import time
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from uuid_utils.compat import uuid7

from eingang import problem
from eingang.log import request_id_var
from eingang.throttles import from_proxy

logger = logging.getLogger("eingang.request")

REQUEST_ID_HEADER = "x-request-id"
# Any HTML the API serves gets this unless its view sets a policy of its own.
DEFAULT_HTML_CSP = "default-src 'none'; frame-ancestors 'none'"
# A client-supplied id is echoed and logged, so only accept a short, harmless token.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _request_id_from(request: HttpRequest) -> str:
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    if _VALID_REQUEST_ID.fullmatch(supplied):
        return supplied
    return str(uuid7())


class RequestContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _request_id_from(request)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            if response.status_code >= 400 and not _is_problem(response):
                # Errors produced by Django itself (unknown route, disallowed Host, debug
                # pages) are HTML; every error the API sends must be problem+json.
                response = problem.for_status(response.status_code)
            if _is_html(response) and "Content-Security-Policy" not in response:
                response["Content-Security-Policy"] = DEFAULT_HTML_CSP
            response[REQUEST_ID_HEADER] = request_id
            duration_ms = round((time.perf_counter() - started) * 1000)
            # request.path never includes the query string, which can contain names.
            logger.info(
                "%s %s %s %dms",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
            )
            return response
        finally:
            request_id_var.reset(token)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse:
        logger.exception("Unhandled exception in %s %s", request.method, request.path)
        return problem.internal_error()


def _is_problem(response: HttpResponse) -> bool:
    return response.get("Content-Type", "").startswith(problem.PROBLEM_CONTENT_TYPE)


def _is_html(response: HttpResponse) -> bool:
    return response.get("Content-Type", "").startswith("text/html")


class ProxyOnlyMiddleware:
    """With PROXY_SECRET_REQUIRED, /api answers only requests from the Vercel middleware.

    Otherwise anyone could deploy a copy of the frontend whose rewrite points at this API;
    its requests would arrive from changing Vercel addresses, past the per-IP limits.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            settings.PROXY_SECRET_REQUIRED
            and request.path.startswith("/api/")
            and not from_proxy(request.META)
        ):
            return problem.problem_response(
                403,
                "PROXY_REQUIRED",
                "Use the web app",
                "This API only answers requests made through its own web app.",
            )
        return self.get_response(request)

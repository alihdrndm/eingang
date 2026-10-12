"""Rate limits (HANDOFF "Rate limits"): per client IP, per user, and for the sandbox.

The client IP comes from DRF's `get_ident`, which trusts exactly `NUM_PROXIES` hops of
`X-Forwarded-For` (settings: TRUSTED_PROXY_HOPS). A client cannot pick its own key by
sending that header itself.

Requests through the Vercel site arrive from a different Vercel address each time, so no
hop count finds the visitor there. The frontend's Vercel middleware sends the visitor's
address in `X-Eingang-Client-IP` together with PROXY_SHARED_SECRET; that address is used
only when the secret matches, so a direct request cannot choose its key with it either.
"""

import hmac
import ipaddress
from typing import TYPE_CHECKING

from django.conf import settings
from rest_framework.request import Request

if TYPE_CHECKING:
    # DRF imports these classes while rest_framework.views is still loading.
    from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle


def forwarded_client_ip(request: Request) -> str | None:
    """The visitor's address sent by the Vercel middleware, or None if it is not trusted."""
    secret: str = settings.PROXY_SHARED_SECRET
    sent = request.META.get("HTTP_X_EINGANG_PROXY_SECRET", "")
    if not secret or not hmac.compare_digest(sent.encode(), secret.encode()):
        return None
    try:
        return str(ipaddress.ip_address(request.META.get("HTTP_X_EINGANG_CLIENT_IP", "").strip()))
    except ValueError:
        return None


class _PerClientIp(SimpleRateThrottle):
    def get_ident(self, request: Request) -> str:
        return forwarded_client_ip(request) or super().get_ident(request)

    def get_cache_key(self, request: Request, view: "APIView") -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class GeneralThrottle(_PerClientIp):
    """120 requests per minute per client IP, on every endpoint."""

    scope = "general"


class LoginThrottle(_PerClientIp):
    scope = "login"


class SandboxPerIpThrottle(_PerClientIp):
    scope = "sandbox"


class UploadThrottle(SimpleRateThrottle):
    """30 uploads per minute per signed-in user."""

    scope = "uploads"

    def get_cache_key(self, request: Request, view: "APIView") -> str | None:
        user_id = getattr(request.user, "pk", None)
        if user_id is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": str(user_id)}

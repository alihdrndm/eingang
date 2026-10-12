"""Django settings. Every value comes from `eingang.config`, never from os.environ."""

import logging.config
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from eingang.config import get_settings
from eingang.log import logging_config

config = get_settings()

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/

SECRET_KEY = config.DJANGO_SECRET_KEY
DEBUG = config.DJANGO_DEBUG
ALLOWED_HOSTS = config.allowed_hosts
CSRF_TRUSTED_ORIGINS = [config.FRONTEND_ORIGIN]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "rest_framework",
    "drf_spectacular",
    "eingang",
    "accounts",
    "invoices",
    "suppliers",
    "exports",
    "llm",
    "sandbox",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "eingang.middleware.RequestContextMiddleware",
    "invoices.middleware.UploadLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "eingang.urls"
WSGI_APPLICATION = "eingang.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]


def _database_from_url(url: str) -> dict[str, object]:
    parts = urlsplit(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        # Bounds a hung connection attempt; /readyz gives up after 2 s on its own.
        "OPTIONS": {"connect_timeout": 5},
        # One test database per test run (process), so parallel runs never drop each
        # other's database; pytest-django creates and drops it.
        "TEST": {"NAME": f"eingang_test_{os.getpid()}"},
    }


DATABASES = {"default": _database_from_url(config.DATABASE_URL)}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"

LOCAL_MEDIA_DIR = BASE_DIR / ".local-media"
# The committed sandbox sample set (manifest, files, precomputed data).
SAMPLES_DIR = BASE_DIR.parent / "samples"
# The headline evaluation numbers served by GET /accuracy (bundled into the API image).
EVALS_LATEST = BASE_DIR.parent / "evals" / "latest.json"


def _documents_storage() -> dict[str, object]:
    if config.STORAGE_BACKEND == "s3":
        return {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": config.S3_BUCKET,
                "endpoint_url": config.S3_ENDPOINT_URL,
                "region_name": config.S3_REGION or None,
                "access_key": config.S3_ACCESS_KEY_ID,
                "secret_key": config.S3_SECRET_ACCESS_KEY,
                "addressing_style": "path",  # Supabase Storage needs path-style URLs
                "default_acl": None,
                "file_overwrite": True,
                "querystring_auth": True,
            },
        }
    return {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(LOCAL_MEDIA_DIR), "allow_overwrite": True},
    }


STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "documents": _documents_storage(),
}

# Security headers (HTTP API conventions).
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 12 * 60 * 60
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = "eingang.problem.csrf_failure"
# A trailing-slash redirect would be a non-2xx response that is not problem+json.
APPEND_SLASH = False

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "eingang.problem.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.authentication.SessionAuthentication"],
    # Every endpoint needs a session unless its view says otherwise (auth, sandbox, health).
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.AnyMember"],
    "DEFAULT_THROTTLE_CLASSES": ["eingang.throttles.GeneralThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "general": "120/min",
        "login": "10/min",
        "uploads": "30/min",
        "sandbox": "5/hour",
    },
    # Proxy hops in front of the API (Vercel, Railway); measured at deployment.
    "NUM_PROXIES": config.TRUSTED_PROXY_HOPS,
    # `?format=` is the document list's format-label filter, not a renderer choice.
    "URL_FORMAT_OVERRIDE": None,
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# The frontend's Vercel middleware sends the visitor's address with this secret (rate limits).
PROXY_SHARED_SECRET = config.PROXY_SHARED_SECRET

SWAGGER_UI_VERSION = "5.33.1"
SPECTACULAR_SETTINGS = {
    "TITLE": "Eingang API",
    "DESCRIPTION": "Inbox for supplier invoices: validation, extraction, checks and approval.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Three different `status` fields; each gets its own enum name in the generated types.
    "ENUM_NAME_OVERRIDES": {
        "DocumentStatusEnum": "invoices.models.Document.Status",
        "ValidationStatusEnum": ["valid", "warnings", "invalid", "not_applicable"],
        "IbanStatusEnum": ["known", "confirmed", "new"],
    },
    # Pinned instead of the default "@latest", so /docs loads a known build.
    "SWAGGER_UI_DIST": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}",
    "SWAGGER_UI_FAVICON_HREF": (
        f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/favicon-32x32.png"
    ),
}

LOGGING_CONFIG = None
logging.config.dictConfig(logging_config(json_logs=not DEBUG))

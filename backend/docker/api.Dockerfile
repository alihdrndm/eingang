# syntax=docker/dockerfile:1
# API image. Build context: the repository root, so the image can include samples/ and
# evals/latest.json. The last stage is the production image.

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# No worker extra: saxonche, pdfplumber, pypdf and factur-x stay out of the API image.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/backend/.venv/bin:$PATH \
    PYTHONPATH=/app/backend/src \
    PORT=8010
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app/backend
COPY --from=builder /app/backend/.venv .venv
COPY backend/manage.py backend/pyproject.toml ./
COPY backend/prompts prompts
COPY backend/vendor vendor
COPY backend/src src
# settings.SAMPLES_DIR and settings.EVALS_LATEST resolve next to backend/, as in the repository.
COPY samples /app/samples
COPY evals/latest.json /app/evals/latest.json
# Local file storage (STORAGE_BACKEND=local); a named volume mounted here inherits the owner.
RUN mkdir .local-media && chown app:app .local-media
USER app
EXPOSE 8010
# One process with threads; no access log, because query strings can contain names.
CMD ["sh", "-c", "exec gunicorn eingang.wsgi --workers 1 --threads 4 --bind \"[::]:${PORT}\""]

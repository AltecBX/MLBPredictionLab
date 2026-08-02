# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# Copied here, before the USER switch, and never chmod-ed: the repository stores
# it executable, COPY preserves that, and a chmod after dropping to an unprivileged
# user fails with "Operation not permitted" — which CI caught.
COPY entrypoint.sh ./

RUN useradd --create-home --uid 10001 jerry && chown -R jerry /srv
USER jerry

# The host assigns the port. Render sets PORT and fails the deploy when nothing
# is listening on it, so a hard-coded 8000 would never come up.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Migrations run at start so a deploy is a single step -- but they are attempted
# rather than required. See entrypoint.sh: a database that wakes slower than the
# web service must not be able to keep the API from existing.
CMD ["./entrypoint.sh"]

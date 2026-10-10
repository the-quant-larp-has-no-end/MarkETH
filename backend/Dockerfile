# syntax=docker/dockerfile:1

# MarkETH backend image: the API, the outbox relay and the Alembic migrations (SPEC §9, A.10).
# compose.yaml runs all three from this one image and only changes the command.
#
#   prod  runtime dependencies only, source baked in (the default target)
#   dev   adds the dev dependency group; compose.override.yaml mounts the source over /app
#
# The virtualenv lives in /opt/venv, outside /app, so the dev bind mount cannot hide it and
# the host's own backend/.venv (built for another OS) is never used.

FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.14-slim AS base
# PYTHONPATH: the project is not an installed package, and console scripts such as
# `alembic` do not put the working directory on the import path the way `python -m` does.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /usr/local/bin/uv
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app
WORKDIR /app
EXPOSE 8000
# No access log of uvicorn's: its line holds the query string of each request. The proxy
# in front writes the access log of the stack and leaves the query out.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]

# Dependencies come from the lockfile alone, so this layer is rebuilt only when it changes.
FROM base AS runtime-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev

FROM runtime-deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen
COPY . .
USER app

FROM runtime-deps AS prod
COPY . .
USER app

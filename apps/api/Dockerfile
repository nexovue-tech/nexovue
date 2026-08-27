# Production image for the control-plane API.
FROM python:3.13-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies resolve from the lockfile before any source is copied, so editing
# code does not re-resolve the environment.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/scheduler/pyproject.toml apps/scheduler/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package nexovue-api

COPY apps/api apps/api
COPY packages/contracts/gen/python packages/contracts/gen/python
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package nexovue-api

FROM python:3.13-slim-trixie
WORKDIR /app
# Only the virtualenv and the source cross over; uv and the caches stay behind.
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/apps/api /app/apps/api
COPY --from=build /app/packages /app/packages
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 nexovue
USER nexovue
EXPOSE 8000
CMD ["uvicorn", "nexovue_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

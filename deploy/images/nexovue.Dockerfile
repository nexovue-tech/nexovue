# The `nexovue` image (ADR-019): the API, the scheduler and the console's built files, in one
# environment. The role is the command — `nexovue api`, `nexovue scheduler-beat`, `nexovue setup`,
# `nexovue web-assets <dir>` (apps/api/src/nexovue_api/roles.py) — never a different image.
#
# Built from the repository root: docker build -f deploy/images/nexovue.Dockerfile .

# ---- the console ------------------------------------------------------------------------------
FROM node:26.12.0-trixie-slim AS console
RUN npm install --global corepack@latest && corepack enable
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/ui/package.json packages/ui/
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

COPY packages/ui packages/ui
COPY apps/web apps/web
# The setup pages are documentation first; the build turns them into console routes
# (apps/web/build/setup-docs-plugin.ts).
COPY docs/product/setup docs/product/setup
RUN pnpm --filter @nexovue/web build

# ---- the Python environment -------------------------------------------------------------------
FROM python:3.13-slim-trixie AS python
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies resolve from the lockfile before any source is copied, so editing code does not
# re-resolve the environment. Every workspace member's metadata: `--frozen` resolves the whole lock.
COPY pyproject.toml uv.lock LICENSE ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/scheduler/pyproject.toml apps/scheduler/
COPY packages/py-platform/pyproject.toml packages/py-platform/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-packages --no-install-workspace

COPY apps/api apps/api
COPY apps/scheduler apps/scheduler
COPY packages/py-platform packages/py-platform
COPY packages/contracts/gen/python packages/contracts/gen/python
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --all-packages

# ---- the image --------------------------------------------------------------------------------
FROM python:3.13-slim-trixie
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="nexovue" \
      org.opencontainers.image.description="nexovue: the API, the scheduler and the console" \
      org.opencontainers.image.source="https://github.com/nexovue-tech/nexovue" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

WORKDIR /app
# Only the environment, the sources and the console cross over; uv, node and the caches stay behind.
COPY --from=python /app/.venv /app/.venv
COPY --from=python /app/apps/api /app/apps/api
COPY --from=python /app/apps/scheduler /app/apps/scheduler
COPY --from=python /app/packages /app/packages
COPY --from=console /app/apps/web/dist /srv/web
# What /system/info reports (nexovue_api.buildinfo), so an answer names the build behind it.
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    NEXOVUE_VERSION="${VERSION}" BUILD_REVISION="${REVISION}" BUILD_TIME="${CREATED}"

RUN useradd --system --uid 10001 nexovue
USER nexovue
EXPOSE 8000
ENTRYPOINT ["nexovue"]

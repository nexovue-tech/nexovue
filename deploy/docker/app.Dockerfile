# The combined control-plane image: FastAPI, Celery Beat and a Celery worker in
# one container, supervised.
#
# It carries Chromium because the worker drains the browser queue, which is why
# this image is large and why the container needs shm_size and ~2.5GB.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends supervisor \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/scheduler/pyproject.toml apps/scheduler/
RUN uv sync --frozen --no-dev --package nexovue-api --package nexovue-scheduler

COPY apps/api apps/api
COPY apps/scheduler apps/scheduler
COPY packages/contracts/gen/python packages/contracts/gen/python

COPY deploy/supervisor/app.conf /etc/supervisor/conf.d/app.conf

EXPOSE 8000
# supervisord is PID 1 so a dead child is restarted rather than silently absent.
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf", "-n"]

# Development image for the Python services (api, scheduler). The source tree
# and the uv-managed virtualenv are bind-mounted; this image carries only the
# interpreter and the tooling.
FROM python:3.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /usr/local/bin/

# watchmedo restarts Celery workers on edit. It is a dev-image tool only and is
# deliberately absent from apps/scheduler/pyproject.toml.
RUN pip install --no-cache-dir "watchdog[watchmedo]==6.0.0"

ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

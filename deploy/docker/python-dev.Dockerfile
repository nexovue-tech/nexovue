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


# Amazon's RDS CA bundle, for door 4b's certificate verification. asyncpg cannot
# use the system trust store and RDS certificates are signed by Amazon's own
# authority, so `postgres_connect.rds_tls_context` refuses to connect without
# this file rather than falling back to an unverified session.
#
# Baked into the image rather than mounted: a poller whose TLS depends on a
# volume being present is one that fails in a way nobody attributes to a volume.
# The bundle rotates, so this layer is what gets rebuilt when it does.
ADD https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /etc/ssl/certs/rds-global-bundle.pem
RUN chmod 0644 /etc/ssl/certs/rds-global-bundle.pem

WORKDIR /workspace

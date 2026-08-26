# api — control plane (Python, FastAPI)

Serves the web app. Owns Postgres. Reads ClickHouse. Never touches the ingest path.

**`data/clickhouse/base.py` is the tenancy chokepoint.** Every query is built through it and the tenant filter is injected there — not by callers. The "no raw SQL outside `data/`" rule is what makes that enforceable.

Domain packages hold `router.py` (HTTP only) · `schemas.py` · `service.py` (logic, no HTTP, no SQL text) · `queries.py` (SQL) · `dependencies.py` · `exceptions.py`.

Routers raise domain exceptions and never build HTTP responses. One handler maps them to RFC 9457 problem+json.

Routes are versioned: `/api/v1/…`.

---
name: api-domain-package
description: Add or change a domain package in apps/api (FastAPI control plane). Use when adding an endpoint, a service, or any Postgres or ClickHouse read in the API.
---

# API domain package

Read `apps/api/CLAUDE.md` first. This is how to obey it.

## Layout — every domain package

| file | holds | never holds |
|---|---|---|
| `router.py` | HTTP only: paths, params, status | logic, SQL |
| `schemas.py` | pydantic request/response | logic |
| `service.py` | the decision | HTTP, SQL text |
| `queries.py` | SQL | decisions |

Routes are `/api/v1/…`. Routers raise domain exceptions from
`errors/exceptions.py` and never build an HTTP response — one handler maps
them to RFC 9457 problem+json.

## Tenancy — the invariant

**All ClickHouse access goes through `data/clickhouse/base.py`.** It refuses
a query without the tenant binding, and the binding comes from the session,
never from a caller value. A router that could build its own repository is a
router that could read another tenant.

**For Postgres, tenancy goes in the statement, not in a check after it:**

```python
select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id)
```

Another org's row then returns nothing, and "not yours" is indistinguishable
from "does not exist". A `SELECT` then an `if row.org_id != org_id` is a
different thing and leaks existence.

`apps/api/tests/test_tenant_chokepoint_structure.py` AST-parses every module
and derives its table list from the migrations directory, so it will catch a
new table that is not guarded. Keep it green.

## Audit

Security-relevant actions record to `audit_log` via `audit/service.py`, in
the **service** layer so the record exists whichever caller triggered it. The
entry joins the caller's transaction — action and record commit together.

The single exception is an action that fails: a refused sign-in has nothing
to be atomic with, and the exception would roll its record back, so it
commits for itself. See `auth/service._record_refusal`.

Never record a credential, a hash, a token, or `api_keys.key_prefix`.

## Tests

Two kinds, both required for anything touching tenancy:

- **Offline** — a fake store that answers from a statement's *bound values*,
  so a query that loses its `org_id` predicate finds nothing and fails.
- **Live** (`*_live.py`) — the same behaviour against real Postgres, inside a
  transaction that rolls back.

A live suite must **fail** when the stack is down, never skip. Use
`require_stack` from `tests/live.py`.

## Before finishing

`uv run ruff check --fix apps/api && uv run ruff format apps/api`, then the
API tests. Alembic migrations live in `apps/api/migrations/versions/` and
need a working `downgrade`, proven.

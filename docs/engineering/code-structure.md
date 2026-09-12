# Code structure standard

Where code goes and what each file is for. Like the rest of these standards, this is read off the
repository rather than proposed for it. `coding-standards.md` covers how code is written; this
covers where it lives.

## The repository

`apps/` holds the five deployable services, `packages/` the code more than one of them uses,
`deploy/` configuration, `tools/` things run against a stack rather than shipped to customers.
Code that two apps need moves to `packages/` rather than being copied.

## An API domain package

`apps/api/src/nexovue_api/<domain>/` is the unit. Four files carry the same role in every domain,
so a reader who knows one knows all of them:

| File | Role | Rule |
|---|---|---|
| `router.py` | HTTP only | Reads the request, calls a service, shapes the response. No business rules, no SQL. |
| `service.py` | Business logic | Where the decisions live. Knows nothing about HTTP. |
| `queries.py` | Data access | The only place SQL is written. Tenant-scoped Postgres queries take `org_id`. |
| `schemas.py` | The wire contract | Request and response models, validated at the edge. |

`dependencies.py`, `exceptions.py`, and domain-specific modules (`cursor.py`, `search.py`,
`tail.py`, `targets.py`) appear when a domain needs them. A new domain starts with the four and
adds only what it uses.

The direction is one-way: `router → service → queries`. A router that imports `queries` has skipped
the layer that holds the rules, and the next reader will not know where to look for them.

## A web feature folder

`apps/web/src/features/<feature>/` follows its own consistent shape: `<Name>Screen.tsx` for the
route component (22 of them), other components in `PascalCase.tsx`, one `api.ts` per feature for
its calls, and pure logic in `kebab-case.ts` with its test colocated as `kebab-case.test.ts`. Hooks
are `use-<thing>.ts`.

Pure logic lives in its own file precisely so it can be tested without rendering anything — which
is why the test count sits beside the logic files rather than the screens.

## A Go package

`internal/<area>/` with one file per concern, named for the concern rather than the type
(`gates.go`, `origin.go`, `handler.go`, `codec.go`; `masker.go`, `rules.go`, `card.go`). Tests sit
next to the file they cover as `<file>_test.go`. Worker pipeline stages each get a package under
`internal/pipeline/`, which is what makes the stage order legible from the directory listing.

## The patterns already in use

These are the names for what is here, useful when discussing a change:

- **Service** — `service.py`. Business logic kept out of the HTTP layer.
- **Repository** — `queries.py` per domain, and `TenantScopedRepository` in
  `data/clickhouse/base.py`, which refuses any statement that does not bind a tenant.
- **Adapter (ports and adapters)** — `billing/processor.py` is a `Protocol`: the port every payment
  leaves through. `razorpay.py` is one implementation. Nothing above the port knows which ran.
- **Dependency injection** — `deps.py`, via FastAPI `Depends`. Handlers declare what they need;
  tests override the same declarations, which is how the offline suite runs without stores.
- **Middleware** — `middleware/`: correlation, errors, rate limit, request log. Cross-cutting work
  that would otherwise be repeated in every handler.
- **Factory** — `session_factory` and `create_app(settings)`. Construction in one place, so tests
  build the same app the process does.

Two on most pattern lists that this repository deliberately does not have: there is no **singleton**
— shared resources are built once in the lifespan and handed out by injection, which gives one
instance without a global — and no **facade**, because the service layer already is the simple
interface over the complicated part. Add a pattern when a problem calls for it, never to complete a
list.

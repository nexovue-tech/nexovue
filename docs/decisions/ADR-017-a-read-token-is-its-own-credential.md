# ADR-017: A read token is its own credential, and the read API is the routes that accept it

## Status

Accepted 2026-09-19 — founder decision, with the F26 + F31 + F36 design (`docs/features/team-and-api-access/design.md`).

## Context

F36 lets a customer's machines read their data: CI deploy gates, warehouse pulls, and an MCP server for
AI assistants (spec D6). Two credentials exist today. **Ingest keys** (`nvk_live_`, `api_keys`) are
project-scoped and write-only; the receiver reads their table on its hot path. **Session tokens** are
handed only to a browser that typed a password; the console API reads them from a cookie or a Bearer
header (`auth/sessions.py:269-276`).

The feature catalogue weighed three shapes: scopes on `api_keys`, a separate read key, and personal
access tokens. Every vendor researched for the spec separates the credential that writes from the one
that reads, and the failure small teams hit is CI breaking when the person whose token it used leaves.

A second question decides how safe the API is to publish: which routes may a token reach? The console's
API changes whenever a screen does.

## Decision

1. **API tokens are a third credential kind**: table `api_tokens`, prefix `nvt_live_`, SHA-256 hashed,
   owned by the organization (not a person), scoped to one project or all, **read-only**, always
   expiring (30/90/365 days). The receiver never accepts one; the API never accepts an ingest key.
2. **The read API is exactly the set of routes that depend on `CurrentReader`**, the one dependency that
   accepts a token. Every other route depends on `CurrentIdentity`, which refuses a token. A structural
   test enumerates the `CurrentReader` routes against a checked-in list, and that list is what the
   documentation publishes and what the API promises to keep stable under `/api/v1`.
3. **A token outside its project scope gets the same 404 as a project that does not exist.**
4. **The MCP server is mounted in the API process**, stateless, authenticated by the same token, and its
   tools call the same service functions as the `CurrentReader` routes — it can read nothing a token's
   endpoints cannot.

## Consequences

- The receiver's key lookup and cache are untouched.
- Putting a route on the public API is a deliberate, reviewed change — a new dependency and a line in a
  checked-in list — not a side effect of adding a `GET`.
- A route moved onto the surface commits nexovue to its response shape; breaking it later needs a
  `/api/v2`.
- Personal tokens, write tokens and OAuth for MCP remain possible as later additions to `api_tokens` and
  `CurrentReader`, without changing this shape.

## Rejected

- **Scopes on `api_keys`** — one row meaning both "may write telemetry" and "may read data", on the
  receiver's hot path.
- **Personal access tokens** — they die with their creator (spec D3).
- **Every `GET` readable by a token** — the console's endpoints would become a public contract by
  accident (spec D8).
- **OAuth 2.1 for the MCP server now** — an authorization server of its own; Bearer headers are accepted
  by the clients that matter today (spec D13).

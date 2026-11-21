# ADR-031: A dashboard is a checked document of fixed panel kinds

## Status

Accepted 2026-11-02 by the founder with the F20 design ([`design.md`](../features/dashboards/design.md),
*Proposed ADRs*, the first; spec D1, D4, D5); filed at implementation, 2026-11-08 (F20 plan Step 2, T2.5).

## Context

The tenant guarantee rests on every ClickHouse statement being a module constant that binds the tenant
(`test_tenant_chokepoint_structure.py`), so no statement is built at runtime. Rivals with a query language
accept what that costs. Nexovue has one developer, and a dashboard must belong to an organization yet show
staging and production alike without a copy per environment.

## Decision

1. A dashboard is one JSON document on one `dashboards` row: `schema_version`, refresh, default range, a
   service picker, up to 5 `https` links, and an ordered list of panels of **fixed kinds**, each with checked
   settings. Title and description are columns, not part of the definition.
2. `dashboards/definition.py` is the only way a definition enters: one Pydantic union on `kind`, with
   `extra="forbid"` throughout, holding the spec's allowed-values table and its caps (30 panels, 64 KiB, a
   worst case of 40 scans, refused at save). A save, a draft, a file and a template pass the same check.
   Reading is forgiving: a stored panel the code no longer knows reads `unknown_kind`, and the page draws.
3. A panel kind compiles to at most one scan key naming an existing service function and its bound
   arguments (`dashboards/compile.py`). No document, file, AI or setting can introduce SQL.
4. A dashboard belongs to the organization and is shown against a project the reader names. Services and,
   later, checks and SLOs are stored by name and resolved in that project at read time.

## Consequences

- Isolation stays provable by the structure test, cost stays bounded by the cap, and every number comes
  from the same statement as the screen it came from.
- A question no kind answers needs a new kind, which is code. Rivals answer more.
- **Harder to reverse after the first customer:** the document's format, hence `schema_version`. Stored
  documents would then need upgrading by a plain function at read time.
- A name resolved per project means a panel can read `not_in_project`, and a renamed check breaks its panel
  visibly.

## Rejected

A query builder compiling to SQL, and free-form SQL or PromQL: each fails the structure test and needs its
own safety proof. A child table per panel: a reorder touches many rows, and export reassembles them. Ids for
checks and SLOs: a dashboard would not work across projects or in an exported file.

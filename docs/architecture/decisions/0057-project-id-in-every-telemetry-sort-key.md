# 0057 — `project_id` in every telemetry sort key, not just spans

- **Status:** Accepted
- **Extends:** [0049](0049-project-id-in-the-spans-sort-key.md) — same argument, remaining tables
- **Date:** 2026-08-27

## Context

[0049](0049-project-id-in-the-spans-sort-key.md) put `project_id` second in the `spans` sort key
because [0042](0042-tenancy-model-organization-and-project.md) makes a project an *environment*:
every query filters it, and without it staging and production rows interleave in the same granules,
so every production read pulls staging off disk to discard it.

It applied that fix to `spans` alone and recorded the rest as a follow-up — `logs` and `metrics`
were to be revisited "once their query patterns are real". M2 makes them real. P1-22 writes exact
pre-sampling counters into `metrics` at one row per service, endpoint and minute, and P1-23 creates
the per-minute rollup that dashboards read.

0049 also recorded the constraint that decides the timing: the change is free only before data
exists. Both tables were still empty.

## Decision

`project_id` second in the sort key of `logs`, `metrics` and the new `endpoint_stats_1m` rollup, as
it already is in `spans`. Applied by `CREATE OR REPLACE TABLE`, which is atomic and a single
statement — the migration runner sends one statement per file.

`endpoint_stats_1m` is built with it from the start rather than corrected later.

## Consequences

Every telemetry table now prunes to one environment on the same two leading columns, so one mental
model covers all of them and a query written for one table is right for the others.

The cost was a drop-and-recreate on two empty tables — effectively nothing, which is exactly why it
happened now. Had it waited until either table held customer data it would have meant rebuilding a
populated table, which is a migration with downtime rather than a schema edit. That asymmetry is
the reason this was worth interrupting M2's first hour for.

M3's log pipeline inherits the correct shape and does not have to repeat the conversation.

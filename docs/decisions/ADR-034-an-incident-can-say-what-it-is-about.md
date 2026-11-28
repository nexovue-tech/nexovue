# ADR-034: An incident can say what it is about

## Status

Accepted 2026-11-02 with the F19 design ([`design.md`](../features/slo-tracking/design.md), *Proposed ADRs*,
the second), which the founder approved; filed with its columns, 2026-11-08 (F19 plan Step 4, T4.1, Alembic
`0058`). The number is the next free one at filing; the coordinator may renumber it at commit.

## Context

An incident's identity is its rule and its subject key, held single by the partial unique index on
`(rule_id, subject_key)` over open rows (`incidents.py`, ADR-013). A reliability target's burn must be keyed by
its target, `slo:<id>`, or two targets on one service would share one open incident. But linking an incident to
a finding compared the subject key with `service:<name>` (`incidents._open`, `project_job._LINK_INCIDENTS`), and
F19's withholding (Step 5) must also compare a burn's type and endpoint with the other subject's.

## Decision

`incidents` gains four typed, nullable columns that say what an incident concerns, apart from its identity:
`about_key` (`service:<name>`, or `check:<id>` for a check target naming no service), `about_signal`
(`availability` or `latency`, checked), `about_endpoint`, and `about_check_id`. Null for every kind that does not
need them. Links, withholding and PagerDuty's shared alert read these columns.

## Why

A typed column is compared by the database and cannot silently fail to match the way a name inside JSON can. The
subject key stays the identity, so the one-incident rule is unchanged.

## Consequences

- F19 Step 4 adds the columns (`0058`); Step 5 writes and reads them (`Subject`'s about fields, `_open`'s link,
  `_LINK_INCIDENTS`, `_SAME_NEWS`, `_PIN_KEY`).
- Another kind may adopt them later with no change to its identity.
- `about_check_id` has no foreign key: the check an incident concerned stays a fact after the check is deleted,
  as `detail.check_id` does.

## Rejected

- Matching `detail->>'service'`: untyped, and one typo from never linking.
- Joining `slo_targets` from the alerting module: couples F21's module to F19's table, and the target may be
  deleted while its incident is history.

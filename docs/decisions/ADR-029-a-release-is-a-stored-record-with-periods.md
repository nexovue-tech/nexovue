# ADR-029: A release is a stored record with periods, fed by two sources, with measured beating told

## Status

Accepted 2026-11-02 with the F28 design ([`design.md`](../features/deployment-release-tracking/design.md),
*Proposed ADRs*, the second), which the founder approved; filed with plan Step 1b, 2026-11-08, which builds
the record and its sync from telemetry. The second source, the deploy webhook, writes to it from Step 2.

## Context

Facts about a change reach nexovue from two doors: a signed sender (CI, through the deploy webhook) and
telemetry (`service.version` on spans, counted in `service_versions_1m`, ADR-025). They can disagree, and they
age differently:

- **The rollup expires.** `service_versions_1m` keeps at most 35 days (`000038`'s TTL). A first-seen time read
  from it would drift forward as rows expire, and a release would vanish with its rows.
- **A version only ever reported has no rollup row at all,** so a view over the rollup could not show it
  without joining two stores on every list request.
- **Versions come back.** v2.0, v2.1, then a rollback to v2.0: with one row and a start that moves, after the
  rollback both rows start after anything that happened between, so "the release before this finding" names
  something older or nothing, and a rollback compares against the wrong side (F28 D15).
- **F3's evidence needs ids that do not change,** and the record must outlive its numbers.

## Decision

1. **`releases` holds one row per project, service and version,** updated from both doors (Postgres migration
   `0054`). It keeps the first and last time it was seen and reported, and `last_activity_at`, the latest
   *arrival* of a report or a count, which decides retention, so a sender's claimed time cannot shorten it.
2. **`release_periods` holds one row per stretch in service.** A past period only ever gains an end. A version
   that returns gets a new period, read as "came back". At most one period per release is open at a time.
3. **A start prefers what was counted** (F28 FR42). Once counted, a period's start is its first counted minute,
   and a report never moves it; before any traffic it is the reported time. The first version counted on a
   service, with no report before it, is flagged already running: it is not a release, and is never "the
   release before" anything (FR43).
4. **Telemetry fills it through one sync,** `sweep:releases` and its per-project job on the capped `releases`
   queue (`nexovue_scheduler/releases/sync.py`), walking each service's minutes in order up to the settled minute,
   so a return inside one read is seen. The deploy webhook writes in the same transaction as the deploy, from
   Step 2. Both take row locks in (service, version) order.
5. **Reported and seen stay distinct** on every screen and fact (BR3). A seen version is never called a deploy.
6. **A release never opens, closes, mutes or moves a finding** (ADR-022 stands; F28 BR12). Detection and the alert
   evaluation sweeps never reach the sync (`test_detection_isolation.py`).

## Consequences

- Stable ids, history that does not change, coverage that can be stated from Postgres alone, and a record
  outliving its numbers: the comparison then reads "numbers expired" with the project's own limit.
- Two jobs are needed, the sync and (from Step 5) the stored reading, each bounded and on its own queue.
- A seen release appears a minute or two after its first settled span (AC22 measures it), and a version seen
  only in rows that arrive more than the sync's hour late is missed until counted again.
- The period rules (90% for 10 minutes ends a period, 60 quiet minutes with others serving ends it, 20% and 5
  requests over 10 minutes is a return) are starting guesses, and can misread blue-green flips and long canaries.

## Rejected

- **A derived view over the rollup:** drifts as rows expire, cannot show a reported-only release, and gives no
  stable ids (D2).
- **One row with a moving start:** rewrites history on every rollback (D15).
- **A second table for first-seen times in ClickHouse:** solves drift but not ids or reported-only rows, and
  adds a store for what Postgres does simply.
- **The worker writing releases to Postgres directly:** a control-plane write on the hot path; a slow Postgres
  would hold back telemetry.

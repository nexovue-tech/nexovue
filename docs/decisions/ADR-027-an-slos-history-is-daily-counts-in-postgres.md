# ADR-027: A reliability target's history is daily counts in Postgres, not another rollup

## Status

Accepted 2026-11-02 with the F19 design ([`design.md`](../features/slo-tracking/design.md), *Proposed ADRs*,
the first), which the founder approved; filed at implementation start, 2026-11-07 (F19 plan Step 1, T1.2).

## Context

A monthly figure must exist on every install. A self-hosted organization sits on Free's plan row, because no
code assigns another plan at creation, so its rollups and check readings expire at 30 days
(`project_retention`, migration `0005`; `metricwriting.go`). `service_stats_1m` keeps at most 35 days on any
plan. F19's four kinds read three different sources (`service_stats_1m`, `endpoint_stats_1m`,
`metrics_gauge`).

## Decision

The scheduler stores **one row per target per local day** in Postgres, `slo_daily_counts` (Alembic `0052`):
good, all and failures; quiet, lost and excluded minutes; the day's leading, trailing and longest quiet
stretches, so a stretch can cross midnight from Postgres alone; and the definition version it was counted
under. A day is rewritten until 7 days have passed, then marked final (BR13). Rows last 400 days (Step 3).

## Why

It works alike for all four kinds; it survives rollup expiry; it makes the list independent of ClickHouse, so
the list answers while ClickHouse is down (AC40); and it adds no second write path into ClickHouse.

## Consequences

- A figure can only be recounted while its source still holds the minutes.
- A definition change leaves older days under the old definition, marked (Step 3).
- Rolling windows move by whole days in the project's zone, so a figure steps at midnight (D4).
- Stored counts outlive the plan's rollup retention, by the founder's decision 1 (13 months on every plan).
- The migration rolls forward only: these rows exist nowhere else once the rollups expire.

## Rejected

An hourly ClickHouse rollup (`service_stats_1h`): it could never hold a whole last month on a self-hosted
install, and it serves the service kinds only. A longer TTL on `service_stats_1m`: 395 days of minutes on a
table tuned for short reads, and no help for endpoints or checks.

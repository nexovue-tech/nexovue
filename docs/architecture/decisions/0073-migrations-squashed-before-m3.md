# 0073 — Migrations squashed to one file per object

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

By the end of M2 there were 65 ClickHouse migrations. **54 were written that day, and 38 rebuilt a
table created hours earlier** — `spans` alone three times, as `received_at` and then
`retention_days` each forced a partition-key rebuild.

A fresh database therefore created `spans`, rebuilt it, and rebuilt it again, all while empty. The
reversibility was the more expensive half: down-migrations copying 12M rows back to protect data no
environment held.

Nothing was behind them. One development database, no customers, no other deployment.

## Decision

Collapse the set to **one file per object** — twelve, matching the twelve tables and views that
exist — each creating the shape those 65 migrations arrived at.

The *why* stays in the ADRs, which do not get harder to read as they accumulate. A migration file
records a step; a record like this one records a decision, and only the second is worth keeping once
the step has been taken everywhere.

## How it was verified

Not by reading the result. The squashed set was applied to an **empty database** and `SHOW CREATE`
compared for every object against the live one, with whitespace and database name normalised.
**Eleven matched exactly.**

The twelfth, `span_ingest_offsets`, was deliberately held out of that check: a `KeeperMap` path is a
literal, so a copy in a scratch database is a second door onto the live guard rather than a copy of
it ([0068](0068-keepermap-is-excluded-from-restore.md)). It was compared as text instead, and the
live guard confirmed intact at twelve rows afterwards — reference counting held, as 0068 said it
would.

`up` and `down` were then proven from empty in a throwaway database, so the real 12M spans were
never at risk.

## Consequences

**Migration numbers in ADRs written before this date do not resolve.** Thirteen links broke, and
the affected records now point at the file that holds that shape today, with a note saying why. That
was found by re-running the link check the `write-adr` skill prescribes — *after* the squash, which
is the run that mattered and the one nearly skipped.

**The squash is repeatable and should be repeated**, at milestone boundaries and before the first
production deploy. It stops being available the moment a second environment exists: from then on
something is behind the migrations, and history is no longer free to rewrite.

**What is lost is the sequence, not the shape.** Nobody can now read how `spans` arrived at its
partition key from the migrations alone — [0067](0067-storage-partitions-on-our-clock-not-the-client-s.md)
and [0072](0072-retention-class-partitioning.md) carry that, which is the division of labour this
decision assumes.

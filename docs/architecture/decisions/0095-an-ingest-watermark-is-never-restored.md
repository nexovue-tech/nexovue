# 0095 — An ingest watermark is never restored, in either store

- **Status:** Accepted
- **Extends:** [0068](0068-keepermap-is-excluded-from-restore.md) — its rule was written for
  ClickHouse's `KeeperMap` tables and for the one hazard a rehearsal could reach at the time
  (restoring over a *live* guard). The same state exists in Postgres, the same defect follows from
  restoring it, and the wiped-broker case turns out to be the worse of the two. Everything 0068
  decided stands; this widens it and gives it a second reason.
- **Date:** 2026-09-04

## Context

P1-51 needed a restore that had actually been performed. Performing one surfaced a table 0068 did
not look at.

**Four tables hold the same kind of state**, and it is not ordinary data. Each records how far a
Kafka partition has been processed, as `topic:partition → offset`:

| Table | Store | Guards |
|---|---|---|
| `span_ingest_offsets` | ClickHouse `KeeperMap` | the counter bracket (ADR 0061) |
| `span_written_offsets` | ClickHouse `KeeperMap` | span rows (migration 000026) |
| `ingest_written_offsets` | ClickHouse `KeeperMap` | log and metric rows (migration 000029) |
| **`usage_ingest_offsets`** | **Postgres** | **the meter's counters (migration 0009)** |

The first three are `KeeperMap` and 0068 already refuses to restore them. The fourth sits in the
control plane, in the middle of `pg_dump`'s output, and comes back with the tenants — which is
exactly what made it worth writing this down: **there is no natural moment at which anyone would
notice it.**

**A watermark is a skip filter, not a position.** Where the consumer *reads from* is the Kafka
consumer group's committed offset. The watermark only decides whether a record the consumer has
already delivered gets written or counted. In `usage/counting.go`:

```go
func (c *counting) alreadyCounted(partition int32, offset int64) bool {
	counted, known := c.counted[partition]
	return known && offset <= counted
}
```

**And it only ever moves forward.** The upsert in `usage/postgres.go` ends
`WHERE settled.counted_through < excluded.counted_through`. Nothing in the pipeline can lower one.

Put those three facts together and the failure writes itself. Restore a watermark of 250 into a
system whose broker was rebuilt and now starts at offset 0, and offsets 0 through 250 are skipped —
not delayed, skipped — and no later flush will ever correct it. For `usage_ingest_offsets` that is
silent under-billing. For the span and row watermarks it is a permanent hole in stored telemetry.

**This is not a hypothetical.** In the rehearsal of 2026-09-04, the dump restored
`spans-raw:0 → 250` (and thirteen more rows like it) into a broker `just topics` had just recreated
with an end offset of **0** on every partition. Left in place, the meter would have skipped the
first 251 records of that partition for the life of the deployment.

**The reverse case — restoring a watermark that is too old — is the one 0068 already names**, and it
is the milder of the two: it rewinds a live guard and permits the double-counting ADR 0061 exists to
prevent. A duplicate is recoverable; the hole is not.

## Decision

**No ingest watermark is ever restored with its rows, in either store, in any scenario.** Back them
up — they carry real state and a cold-start rebuild is worth having, which is 0068's position and is
unchanged — and bring them back empty.

Empty is a legal, meaningful starting state: it reads as `NothingWritten` (`-1`), and both the Go
guard and the meter treat an unknown partition as one that must be written and counted, "because a
duplicate row is recoverable where a missing span is not". The pipeline rebuilds each watermark from
its next flush.

### The mechanism differs per store, because the tools do

**ClickHouse — excluded, then recreated structure-only.** Two statements:

```sql
RESTORE DATABASE nexovue
  EXCEPT TABLES nexovue.span_ingest_offsets, nexovue.span_written_offsets, nexovue.ingest_written_offsets
  FROM Disk('backups', 'restore.zip');

RESTORE TABLE nexovue.span_written_offsets
  FROM Disk('backups', 'restore.zip') SETTINGS structure_only = true;
```

The second is needed because `EXCEPT TABLES` leaves the table absent, and the pipeline needs it to
exist. `structure_only = true` **is not refused even where the guard is live**, because nothing is
inserted — which is a genuinely new fact beside 0068, whose refusal
("Cannot restore … because it already contains some data") is raised by the data half of the restore
and not by the DDL.

**Postgres — restored, then emptied, in the same script.** `pg_restore` has no equivalent of
`--exclude-table-data`, and a `-L` table-of-contents filter is exactly the kind of step that gets
skipped at 3am. So `restore-postgres.sh` restores the whole dump in one transaction, prints the
watermark it just restored, and truncates it. **Printing it is part of the decision**, not
decoration: it is the only place those numbers are visible to an operator, and a restore that
silently discards state teaches nobody anything.

### The rule is checked, not just documented

`just backup-verify` asserts, across all four tables at once, that **no watermark sits above the
broker offset it names**. That check does not care how the watermark got there, which is the point:
it catches a hand-restored table, an edited script, and a snapshot restored into the wrong broker,
none of which a comment in a script would catch.

## Alternatives considered

**Restore the watermark when the broker is provably the same one.** Rejected. There is no cheap,
reliable test for "the same Kafka" at the moment it matters — the cluster id is stable across a
volume wipe only if `KAFKA_CLUSTER_ID` is pinned, which it is here by default and need not be
elsewhere. And the case is empty: when the broker really is intact, the consumer group is already
committed past the restored watermark, so the watermark skips nothing that will be delivered and
restoring it changes no outcome. It buys nothing and costs a judgement call under pressure.

**Detect the stale watermark and warn, leaving the rows in place.** Rejected. The failure it guards
against is silent, permanent and financial. A warning printed during an incident, among everything
else printed during an incident, is not a control.

**Reset the Kafka consumer group to match the restored watermark**, so the two agree. Rejected. That
deliberately replays records into a pipeline whose duplicate protection has just been rewound —
inviting precisely the double-count of ADR 0061. Replaying a gap out of Kafka's buffer is a real
operation and it may one day be worth having; it is not part of an ordinary restore, and the runbook
says so rather than half-offering it.

**Exclude the watermark at dump time**, so the artifact cannot restore it. Rejected, for 0068's
reason: the values are worth keeping. The backup carries them and the manifest records them under
`watermark.*`; only the restore refuses them.

**One shared rule expressed as "never restore any offsets table".** Considered and not adopted as
code — the four tables are found by name in `deploy/backup/common.sh`, not by a pattern. A pattern
would silently start excluding a future table that happens to be named `…_offsets` and, worse, would
silently *not* exclude one that is not. The list is short, and it is checked by the broker
comparison above rather than trusted.

## Consequences

**What this buys.** The restore procedure has no step whose omission causes silent, permanent data
loss. The one that did is now inside the script that does the restore, and is asserted afterwards by
a check that is independent of it.

**What it costs.**

- **The meter under-counts the gap between the snapshot and the restore, and always will.** Those
  records were counted before the failure, the counters went back to the snapshot's values, and the
  Kafka records that produced them are not re-read. This is data loss in billing's direction —
  in the customer's favour — and it is a property of the recovery point, not of this decision.
- **The first flush after a restore writes every partition's watermark afresh.** Negligible, and it
  is one KeeperMap write and one Postgres upsert per partition.
- **Two mechanisms for one rule.** `EXCEPT TABLES` in ClickHouse and truncate-after in Postgres.
  They cannot be unified while `pg_restore` lacks per-table data exclusion, so the rule is stated
  once here and enforced twice, and the `backup-verify` check is what stops them drifting apart.

**What was verified**, and watched, on the dev stack on 2026-09-04 — two complete cycles, every
Docker volume deleted in between:

- **The stale watermark was real and was discarded.** `restore-postgres.sh` printed all 14 restored
  rows — `spans-raw:0 → 250` through `spans-raw:11 → 276`, `logs-raw:3 → 1105`,
  `metrics-raw:3 → 140` — then `discarded 14 watermark row(s)`. `just topics` had recreated every
  partition with an end offset of **0**.
- **The pipeline rebuilt them correctly and wrote nothing twice.** After the restore, three posts of
  a two-span trace landed on `spans-raw:9`; the meter counted `messages_metered=3 spans_metered=6
  bytes_metered=528 usage_rows_written=1`, and `usage_ingest_offsets` came back holding exactly
  `spans-raw:9 → 2` — a number derived from the new broker, not the old one.
- **The write guard held across an unplanned restart.** The worker rebuilt mid-test and replayed the
  same batch. The log records `resolving a replayed batch against the settled offsets … partition=9
  scenario=new count_from=0 store_from=3 batch_from=0 ambiguous=false` and
  `spans_already_stored=6`: the rebuilt watermark refused the replay. `spans` held 1,340 rows before
  and 1,340 after, and a full-table duplicate scan returned `spans_with_extra_copies=0,
  surplus_rows=0`.
- **New telemetry is written after a restore.** An errored trace (the sampler keeps errors in full;
  a non-errored probe was dropped by the sampler, which is why the first attempt proved nothing)
  landed within 30 seconds, taking `spans` to 1,342 with still zero duplicates.
- **0068's `KeeperMap` findings were re-confirmed on 26.3.20.7.** A `structure_only` restore of
  `span_ingest_offsets` under a second name succeeded against a **live** guard; the restored table
  read back the live 12 rows, and dropping it left those 12 intact.
- **`RESTORE DATABASE` into an existing empty database works**, so the procedure never has to
  `DROP DATABASE` — which matters here, because dropping the database would drop the `KeeperMap`
  tables and, where the guard were live, discard it.
- **The second cycle passed clean** on all 12 ClickHouse tables, all 17 Postgres tables, both schema
  revisions, and the watermark-versus-broker check.

**What was not verified.**

- **Nothing was run at production scale.** The whole snapshot was 847 KB and the restore took about
  40 seconds. Whether the exclusion costs anything at a store size where the restore is slow is
  unmeasured, and there is no reason to think it does.
- **Only the wiped-broker case was exercised end to end.** The "ClickHouse lost, Kafka intact"
  restore — where the restored watermark would be *below* the consumer group rather than above it —
  is argued from the code above, not run.
- **The Postgres truncate has no automated test.** It is a line in a shell script, proved by the two
  live runs and by `backup-verify` afterwards; nothing would catch its removal except that check.
- **No scheduling, retention, off-machine copy or encryption decision is made here**, and none is
  implied. Until those exist the recovery point is "whenever somebody last ran `just backup`".

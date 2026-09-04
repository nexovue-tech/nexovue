# Restore from backup

> **Type:** Delivery reference · **Background:** [ADR 0068](../../architecture/decisions/0068-keepermap-is-excluded-from-restore.md), [ADR 0095](../../architecture/decisions/0095-an-ingest-watermark-is-never-restored.md)
> **Blast radius:** replaces a whole store. The ClickHouse half refuses to run against a database that still holds tables; the Postgres half does not, and will overwrite a live control plane.

`just backup` writes one snapshot of both stores to `.backups/<timestamp>/` on the host — outside
every Docker volume, because `just down-hard` deletes volumes and a backup that dies with the thing
it protects is a file, not a backup.

This is the procedure for putting one back. **It has been run end to end twice**, against the dev
stack, wiping every volume in between; the numbers under *What it costs* are measured, not
estimated.

## When to run this

Any one of these, once you have decided that going back to the last snapshot loses less than
carrying on:

1. **A store is gone or unopenable.** A deleted volume, a corrupt data directory, a ClickHouse that
   will not start. ClickHouse has been OOM-killed twice in one day on this deployment, and the cap
   it dies against is [`config.d/memory.xml`](../../../deploy/clickhouse/config.d/memory.xml).
2. **A retention change dropped parts that should have been kept.** Retention is forward-only
   precisely because there is no undo (§9 of the phase 1 plan); this snapshot is the undo.
3. **A migration or a bulk statement destroyed data** — a `DROP`, a wrong `ALTER`, an
   `OPTIMIZE … DEDUPLICATE BY` with the wrong column list.
4. **You are rehearsing.** A restore nobody has run is a hope. Rehearsing is the point of this file.

## When NOT to run this

- **When only one store is broken.** Restore only that one. The two recipes are separate on purpose,
  the same way `migrate-down-pg` and `migrate-down-ch` are: a Postgres restore that nobody needed
  throws away every account change since the snapshot.
- **To recover data younger than the snapshot.** It is not there. Nothing here is continuous; see
  *What it costs*.
- **Onto a running pipeline.** The workers must be stopped first. A worker writing during a restore
  produces a store that is neither the backup nor what was there before, and `just backup-verify`
  will then be comparing against a moving target.
- **When the fault is still active.** Restoring into a ClickHouse that is still being OOM-killed
  buys you the same outage with older data. Raise the cap or reduce the load first.
- **On the Postgres half, when the control plane is healthy.** `pg_restore --clean` drops and
  recreates every table it restores. Unlike the ClickHouse half, nothing stops you doing that to a
  database that was fine.
- **Without reading the checksum output.** Both scripts check `SHA256SUMS` before touching anything
  and refuse on a mismatch. If they refuse, the snapshot is corrupt — find an older one.

## Before you start

**1. Pick the snapshot and read its manifest.** `just backups` lists them.

```
cat .backups/<timestamp>/manifest.txt
```

The manifest is the contract. It records the row count of every table, both schema revisions, the
image tags, and — importantly — the Kafka end offsets at the time. Compare `alembic_revision` and
`clickhouse_schema_version` against what the code you are deploying expects. **A snapshot restores
the schema it was taken with**, so a snapshot older than your last migration comes back on the old
schema, and you must run `just migrate` *after* the restore, not before.

**2. Know how much you are about to lose.** `taken_at` in the manifest is the recovery point. Every
byte ingested since is gone unless Kafka still has it — see *What it costs*.

**3. Stop everything that writes.**

```
docker compose -f deploy/compose/docker-compose.yaml stop receiver worker api web
```

**4. Do not run migrations first.** Both snapshots carry their own schema *and* the row that records
which migrations produced it — `alembic_version` in Postgres, `schema_migrations` in ClickHouse.
Running `just migrate-ch` first leaves tables in place, and the ClickHouse restore then refuses
because the database is not empty. This is the single most likely wrong move at 3am.

## The procedure

One store at a time. Watched.

### 1. Bring up infrastructure only

Not `just dev`. The services must not start before the data is back, or they will write into a
half-restored store and consume from topics that do not exist yet.

```
docker compose -f deploy/compose/docker-compose.yaml up -d --wait clickhouse postgres kafka redis
```

If the volumes were deleted, the images recreate the `nexovue` database in both stores, empty. That
is exactly what the restore wants.

### 2. Restore the telemetry

```
just restore-ch <timestamp>
```

It verifies the checksums, refuses if the database holds any table, copies the archive into the
container, and runs two statements: everything except the three `KeeperMap` watermark tables, then
those three **structure only** — recreated, empty, with no rows.

**That exclusion is the whole reason this procedure is more than one command.** A watermark records
which Kafka offsets have already been written through. Restored alongside a rebuilt broker, it sits
above offsets the new broker has not reached, and every record below it is skipped for ever, because
no flush ever moves a watermark backwards. See ADR 0068 and ADR 0095.

### 3. Restore the control plane

```
just restore-pg <timestamp>
```

One transaction, so a failure leaves the control plane as it was rather than half replaced. It
prints the meter's watermark as restored and then empties it, for the reason above.

### 4. Recreate the topics, if the broker was wiped

```
just topics
```

Auto-creation is off. Without this the receiver refuses every export with
`UNKNOWN_TOPIC_OR_PARTITION`, silently, at roughly 330 refusals a minute.

### 5. Check before starting the pipeline

```
just backup-verify <timestamp>
```

Do this **while the writers are still stopped**. It is the only moment the comparison is exact.

### 6. Start the services

```
docker compose -f deploy/compose/docker-compose.yaml up -d --wait receiver worker api web
```

## Verify it worked

`just backup-verify <timestamp>` is the check, and `PASS` is the pass. It compares, and exits
non-zero on any mismatch:

- **every table's row count**, in both stores, against the manifest;
- **both schema revisions** — `alembic_version` and ClickHouse's `schema_migrations`;
- **that no ingest watermark sits above the broker offset it names**, across all four watermark
  tables at once. This is the check that catches a restore which looks fine and quietly loses data.

Counts on the collapsing engines — `trace_summaries`, `endpoint_stats_1m`, `service_edges_1m` — are
read with `FINAL` on both sides. Without it the number moves whenever ClickHouse merges parts: on
the first rehearsal `trace_summaries` read 263 before the restore and 262 after, with nothing lost.
**Do not "fix" a mismatch there by re-running until it agrees.**

Then three things the manifest cannot check for you:

**A trace a customer named is queryable again.**

```sql
SELECT count(), uniq(service_name), min(timestamp)
FROM spans WHERE trace_id = 'THE_TRACE'
```

**The pipeline resumes without duplicating.** Send one errored trace — the sampler keeps errors in
full, so a non-errored probe is usually dropped and proves nothing — wait a minute, and check that
it landed and that nothing else doubled:

```sql
SELECT count() AS spans_with_extra_copies, sum(copies - 1) AS surplus_rows FROM (
  SELECT count() AS copies FROM spans
  GROUP BY tenant_id, project_id, trace_id, span_id, timestamp, received_at
) WHERE copies > 1
```

Both zero. If they are not, the cause is a restored watermark or a restore run over a live store,
and [collapse-duplicate-span-rows.md](collapse-duplicate-span-rows.md) is the cleanup.

**The watermarks rebuild from empty.** A minute after the first traffic, all four should hold rows
again, with offsets near the broker's current start rather than near the old ones in the manifest:

```
docker exec nexovue-clickhouse-1 clickhouse-client --user nexovue --password *** \
  --database nexovue --query 'SELECT * FROM span_written_offsets'
```

## When it half-worked

**ClickHouse restored, Postgres did not.** Safe to stop here and retry the Postgres half; the two
are independent. Do not start the workers in between — with a restored telemetry store and a stale
control plane, ingest is attributed against projects that may no longer match.

**Postgres restored, ClickHouse did not.** Also safe to stop, but the product is now serving
accounts whose telemetry is missing or older. Keep the workers stopped and finish the ClickHouse
half before anyone looks at a dashboard.

**`restore-ch` refuses: "database is not empty".** Something created tables — usually
`just migrate-ch` run too early, or the services started before step 2. The remedy the script prints
is to drop and recreate the database. **Read the warning it prints with it:** dropping the database
drops the `KeeperMap` tables, and where this stack's Keeper is live and holding watermarks, that
discards them. After a full wipe there is nothing to discard and it is free.

**`restore-pg` fails mid-way.** It runs in one transaction, so it rolled back. The control plane is
as it was. Read the error — the usual cause is a dump taken from a different major Postgres version
than the running image; the manifest records `postgres_image` for exactly this.

**RESTORE fails with "Permission denied" on a file that is plainly there.** `docker cp` writes as
root and the server runs as `clickhouse`. `restore-clickhouse.sh` chowns the staged archive for this
reason; if you are restoring by hand, do the same.

**`backup-verify` fails only on `usage_ingest_offsets` or a `KeeperMap` table.** It should not — they
are excluded from the row comparison by design. If it does, someone has edited the scripts.

**Counts are short by a little and nothing else is wrong.** Almost always the backup was taken with
ingest running, which the manifest header warns about. Restored ≤ manifest is explainable; restored
> manifest is not, and means something wrote after the restore.

**You need the data between the snapshot and the failure.** If Kafka's volume survived, the raw
records are still in the 7-day buffer — but the consumer group has committed past them, so nothing
replays them on its own. Resetting the group to replay is possible and is **not** covered here: it
interacts directly with the watermarks this procedure just emptied, and getting it wrong
double-counts every customer's usage. Scope it deliberately, in daylight.

## What it costs

**Measured on the dev stack, 2026-09-04**, twice, with every volume deleted in between. The data
was 1,342 spans / 3,612 logs / ~26,000 rows total, and the whole snapshot was **847 KB**
(779 KB ClickHouse archive, 47 KB Postgres dump):

| Step | Time |
|---|---|
| `just backup` | 6–7 s |
| `just down-hard` | 3–5 s |
| infrastructure up, from deleted volumes | 11 s |
| `just restore-ch` | 1 s |
| `just restore-pg` | < 1 s |
| `just topics` | 7 s |
| `just backup-verify` | ~10 s |
| **data back and checked** | **~40 s** |
| services up | 292 s |

**The 292 seconds is a dev-stack cost, not a restore cost.** `down-hard` also deletes `go-cache`,
`python-venv` and `web-node-modules`, so the Go and Node toolchains rebuild from nothing. A
deployment running built images does not pay it. **Read the restore itself as ~40 seconds at this
data size**, and understand that it scales with the archive: nothing here has been run against a
store large enough to be slow, so do not quote these numbers for a real one.

**The archive is written twice during a backup.** ClickHouse can only write to the disk
[`backups.xml`](../../../deploy/clickhouse/config.d/backups.xml) allows, which is inside its own data
volume, so `backup.sh` stages it there, copies it to the host and deletes the staged copy. **The
volume needs transient free space equal to the archive.** On a store near full, that is the thing
that will fail.

**`FINAL` on the collapsing tables is real work.** `backup-verify` reads `trace_summaries`,
`endpoint_stats_1m` and `service_edges_1m` with `FINAL`, which merges at read time. They are rollups
and small today. At real volume this check costs, and it is still the only correct measure.

### What is not covered

**The recovery point is the last snapshot, and nothing takes snapshots on a schedule.** Whether this
runs on a timer, what runs it, how long snapshots are kept, where they live off this machine, and
whether they are encrypted are all undecided. Until they are, the recovery point is "whenever
somebody last typed `just backup`", and that is not a recovery point.

**Not backed up at all:**

- **Kafka** — topics, buffered records, and consumer group offsets. `just topics` recreates the
  topics empty. Anything produced but not yet consumed is gone.
- **Redis/Valkey** — rate-limit counters and hot cache. Reconstructible by design; it is configured
  to evict rather than refuse.
- **SeaweedFS** — the S3 cold tier is P1-57 and not yet in use. **When it is, this file is wrong**
  and a ClickHouse archive alone will no longer be a complete restore.
- **ClickHouse's insert-deduplication log.** Server-side, not table data; a restore cannot carry it,
  which is the same conclusion ADR 0061 reached about restarts.

**Backed up and deliberately never restored:** the three `KeeperMap` watermark tables and Postgres's
`usage_ingest_offsets`. They are in the artifact so a cold-start rebuild has a number to start from,
and the manifest records their values under `watermark.*`.

**The backup is verifiable only for corruption, not for correctness.** `SHA256SUMS` proves the bytes
are the bytes that were written. Nothing proves the archive restores until it is restored. The only
real check is to run this procedure — which is why rehearsing is listed under *When to run this*.

---

*Written 2026-09-04, from two complete cycles against the dev stack. The first cycle found three
defects and they are fixed: `docker exec -i` swallowing a verification loop's input so only one
table per store was ever compared, `docker cp` staging the archive as root where the server could
not read it, and `count()` on the collapsing engines moving with the merge state. Correct this file
the next time it is run — especially the timings, against data that is not a dev stack's.*

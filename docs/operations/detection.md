# Detection

What to do when anomaly detection (F6) stops judging, or holds itself back because nexovue's own intake
failed. Written from a walk on the development stack on 2026-11-01 (F6 plan T7.2); *Walked* below says
which steps were run and what they showed.

## How detection runs

Once a minute, `sweep:detection` (on `scheduler-light`) works out the newest **settled** minute: the
newest minute every partition of `spans-raw` has been counted past, plus 3 minutes for late spans. It
then queues one **project job** per project that has had traffic in the last day or has a finding open.
Each job judges its project up to that minute and records a completed run in `detection_project_runs`.
The sweep records itself in `detection_sweep`: when it last ran, and the newest minute it handed out.

Two things stop a minute from settling, on purpose:

- **A partition the trace worker has not counted.** The worker writes how far it has counted into
  ClickHouse's `span_derived_progress`, per partition. A partition with records unread is settled only
  up to its last counted record. This is what customers see as *data delayed*.
- **A hold.** If the receiver recorded Kafka refusing exports in a minute (`intake_refusals`), or if 5
  organizations lost traffic while the receiver's accepted count fell (the *install-wide guard*), every
  project's minutes wait. A hold lasts at most 30 minutes; then the minutes are judged anyway.

Owners hear about detection only through their *detection stopped* email, after 15 minutes. **The sweep
sends that email**, so if the sweep itself stops, nobody but the operator is told. That is what
*detection stalled* is for.

## The commands used below

Run these on the host, in a shell that has them defined. On an install, `P` is `NEXOVUE_PROJECT` from
`/opt/nexovue/nexovue.env`; on the development stack it is `nexovue`. Each runs the store's own client
inside its container with the container's own user, so no password is typed or shown.

```bash
P=nexovue
pg()    { docker exec -i "$P-postgres-1" sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' psql "$@"; }
ch()    { docker exec -i "$P-clickhouse-1" sh -c 'clickhouse-client -u "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -d "$CLICKHOUSE_DB" "$@"' ch "$@"; }
kafka() { local tool="$1"; shift; docker exec "$P-kafka-1" "/opt/kafka/bin/$tool.sh" --bootstrap-server localhost:9092 "$@"; }
prom()  { docker exec "$P-prometheus-1" promtool query instant http://127.0.0.1:9090 "$1"; }
```

`prom` needs the monitor (`NEXOVUE_MONITORING=yes`, [monitoring](monitoring.md)). The scheduler's own
`/metrics` can be read without it, as *Reading them by hand* there shows.

## The readings

All are on `scheduler-beat`, read by its leader every 15 seconds; a beat that does not lead reports 0.
No label names a tenant or a project (C8): a project is named only in the scheduler's log and in
Postgres.

| Metric | Meaning | Healthy |
|---|---|---|
| `nexovue_scheduler_detection_settled_minute_age_seconds` | How old the newest minute handed to project jobs is; `+Inf` before any sweep settled one | 180 to 300 |
| `nexovue_scheduler_detection_projects_behind` | Projects detection should judge whose job has completed no run for 3 minutes: the console's *Detection is not running* | 0 |
| `nexovue_scheduler_detection_oldest_lag_seconds` | How long the project longest without a completed run has waited, healthy ones included | under 60 |
| `nexovue_scheduler_detection_events_recent{kind}` | `detection_events` rows of each kind, `intake_guard` and `hold_expired`, written in the last 15 minutes | 0 |

## NexovueDetectionStalled

**What it means.** The newest minute detection has settled is more than 10 minutes old, so nothing newer
is judged for any project. Either the sweep is not running, or it runs and no newer minute can settle.
Customers see *data delayed* or *Detection is not running*, and no finding can open.

**Confirm.**

```bash
prom 'max(nexovue_scheduler_detection_settled_minute_age_seconds)'
pg -c "SELECT swept_at, now() - swept_at AS since_sweep, settled_minute, now() - settled_minute AS age FROM detection_sweep"
```

- `since_sweep` under 2 minutes: **the sweep runs, and the pipeline does not settle.** Go to the next step.
- `since_sweep` older, or no row: **the sweep is not running.** Go to *The sweep is not running*.

**The sweep runs: find what holds the minute back.** The sweep says why in `scheduler-light`'s log:

```bash
docker logs --since 15m "$P-scheduler-light-1" 2>&1 | grep -E 'pipeline is behind|never counted|could not read which minute|CLICKHOUSE_DSN is not set|holding every project'
```

| Log line | Cause | Do |
|---|---|---|
| `the pipeline is behind; judging only what has settled … lagging=[…]` | Those partitions of `spans-raw` are not counted | *Which partition holds it back*, below |
| `a span partition holds records the worker has never counted … missing=[…]` | A partition has records and no progress row: no worker has ever counted it | Check the worker is running and owns every partition (`kafka kafka-consumer-groups --describe --group nexovue-workers`) |
| `could not read which minute is settled` | ClickHouse or Kafka did not answer the sweep | Fix the store; the traceback names which |
| `CLICKHOUSE_DSN is not set for the scheduler` | `scheduler-light` was deployed without the reader account | Set it and restart `scheduler-light` |
| `nexovue's intake failed; holding every project's minutes` | A hold, by design | *NexovueDetectionHeldByIntake*, below |

A `pipeline is behind` warning whose `settled_minute` is one minute before `current_minute` is ordinary:
the trace worker holds spans for its sampling window before counting them.

**Which partition holds it back.** Compare how far each partition is counted with what Kafka holds:

```bash
ch -q "SELECT partition_key, counted_through, counted_through_received_at FROM span_derived_progress ORDER BY toUInt32(splitByChar(':', partition_key)[-1]) FORMAT PrettyCompactMonoBlock"
kafka kafka-consumer-groups --describe --group nexovue-workers
```

A partition holds detection back when its `LAG` is above 0 and its `counted_through_received_at` is
older than the others. The consumer group's `CURRENT-OFFSET` is one past `counted_through`, because the
worker commits only what ClickHouse has taken. Then:

- **`has no active members`, or that partition has no `CONSUMER-ID`:** no worker is consuming it. Start
  or restart the worker; `NexovueServiceDown` or `NexovueServiceMissing` will usually be firing too.
- **A consumer owns it but `CURRENT-OFFSET` does not move:** the worker is stuck on it. Its log names the
  partition (`docker logs --since 15m "$P-worker-1" 2>&1 | grep -i 'spans-raw'`); a parked record is on
  `spans-dlq` ([dead letters](dead-letters.md)); a store refusing the rollup holds the partition for at
  most 15 minutes, then moves on.
- **`LAG` is large on every partition and falling:** the worker is catching up. Wait.

**The sweep is not running.** In order:

1. `NexovueNoBeatLeading` or `NexovueSchedulerLoopSilent` firing: fix beat first; no job is queued
   without a leader.
2. Beat queues the sweep every minute and the job expires after a minute, so a sweep nobody takes
   never waits long enough for `NexovueJobsWaiting`. Instead, look for the sweep sitting untaken:

   ```bash
   pg -c "SELECT task, now() - run_after AS waited FROM jobs WHERE queue = 'detection'"
   docker logs --since 5m "$P-scheduler-light-1" 2>&1 | grep -E 'worker started|detection project jobs queued'
   ```

   A `nexovue_scheduler.detection.sweep` row on every look, and no `detection project jobs queued` line,
   means `scheduler-light` is not taking the `detection` queue: it is down
   (`docker ps --filter name="$P-scheduler-light"`), every slot is busy, or it was started with
   `--queues` that leave `detection` out (its `worker started … queues=[…]` line says which).
3. Otherwise the sweep fails: `docker logs --since 15m "$P-scheduler-light-1" 2>&1 | grep -A20 'a job failed'`
   shows the traceback, under `task=nexovue_scheduler.detection.sweep`.

**Fixed when** `settled_minute_age_seconds` is back under 300 and the alert has resolved. Each project
then judges at most 2 minutes of catch-up per run. A project more than 10 minutes behind restarts at the
newest settled minute: minutes older than that are never judged, and nothing old is sent.

## NexovueProjectDetectionFailing

**What it means.** For the number of projects in the alert, no detection job has completed in over 3
minutes, though each had traffic in the last day or a finding open. Their console reads *Detection is
not running*. After 15 minutes the sweep emails each project's owners *detection stopped*, and *running
again* when a job completes.

If *detection stalled* is firing too, start there: a sweep that settles nothing queues no job, so every
project falls behind.

**Confirm, and find which projects.**

```bash
pg -c "SELECT org_id, project_id, last_completed_at, expected_since, stopped_reported_at
       FROM detection_project_runs
       WHERE expected_since IS NOT NULL
         AND GREATEST(last_completed_at, expected_since) < now() - interval '3 minutes'
       ORDER BY GREATEST(last_completed_at, expected_since)"
docker logs --since 15m "$P-scheduler-light-1" 2>&1 | grep -A20 'detection could not judge a project'
```

Each failed job logs `detection could not judge a project` with its `project_id` and `settled_minute`,
followed by the worker's `a job failed` traceback.

**What to do.**

- **Every project, with ClickHouse in the traceback:** ClickHouse is down or too slow for the job's 5-second
  queries. Fix ClickHouse; nothing else is needed.
- **One project, `a job passed its soft time limit` or `killed at its hard time limit`:** the project's
  job needs more than its 20 seconds. It continues from where it stopped each minute, so a project that
  only just overruns catches up by itself; one that always overruns needs its size looked at (the cost
  target is 2,000 services, NFR2).
- **`a job's process died` with `exit_code` -9:** the kernel killed a `scheduler-light` child, usually
  because the container reached its memory limit. A probe running in the same container can die the same
  way. Read the container's peak (`docker stats`) against its `mem_limit`.
- **One project, any other traceback:** a defect. Record the traceback and the project id, and raise it.
- **No traceback, and no `detection project judged` line for the project:** its job is not being run.
  Check `jobs_ready{queue="detection"}` as for a stopped sweep.

**Fixed when** the project's `last_completed_at` is within the last minute and the gauge is back to 0.
Owners told it stopped receive *running again* by themselves.

## NexovueDetectionHeldByIntake

**What it means.** Detection decided that nexovue's own intake failed, and wrote a `detection_events`
row. `kind` says which:

- **`intake_guard`:** at least 5 organizations lost traffic in the same minutes the receiver's accepted
  count fell to half or less. Every project's minutes wait, and no traffic finding opens for the drop.
- **`hold_expired`:** a hold, from refusals or from the guard, reached its 30-minute limit. Its minutes
  were judged anyway, with whatever arrived, so traffic findings may have opened for minutes nexovue lost.

**Confirm.**

```bash
pg -c "SELECT at, kind, detail FROM detection_events WHERE at > now() - interval '1 hour' ORDER BY at"
pg -c "SELECT minute, receiver_id, reason, count FROM intake_refusals WHERE minute > now() - interval '1 hour' ORDER BY minute"
prom 'sum(rate(nexovue_receiver_items_total{signal="spans", outcome="accepted"}[5m]))'
```

`detail` names the held span (`from`, `through`) and, for a guard, how many organizations voted.
Refusal minutes are written only for broker failures while the sender waited, at least 5 in a minute.

**What to do.** Find what failed on the way in: the receiver alerts below, the edge (Caddy, the host's
network) and the receiver's own `/readyz`. Detection resumes by itself: a hold ends once the pipeline has
delivered past its last minute. After a `hold_expired`, look for findings opened inside the held span:

```bash
pg -c "SELECT org_id, project_id, id, started_at, title FROM findings
       WHERE started_at BETWEEN '<from>' AND '<through>'::timestamptz + interval '15 minutes'
       ORDER BY started_at"
```

Those are candidates for a false alarm caused by nexovue; tell the affected owners.

**Fixed when** no new `detection_events` row appears and `NexovueDetectionStalled` is not firing.

## NexovueReceiverKafkaUnavailable

**What it means.** Kafka refused the receiver while a sender waited (`broker_unavailable`). Customers'
exports are refused and their SDKs retry. A sender cancelling its own export no longer counts here.
For detection, each minute with 5 or more such refusals becomes an `intake_refusals` row, which holds
every project's minutes.

**Confirm.**

```bash
docker exec "$P-prometheus-1" wget -qO- http://receiver:8081/readyz   # one replica; the image has no shell
kafka kafka-broker-api-versions >/dev/null && echo "kafka answers"
docker logs --since 15m "$P-kafka-1" 2>&1 | tail -50
```

**What to do.** Restore Kafka: a full disk (`df -h` on the host) and a broker that will not start are the
usual causes. The receiver needs no restart.

**Fixed when** `broker_unavailable` has not risen for 5 minutes, no new `intake_refusals` minute appears,
and the settled minute is moving again.

## NexovueReceiverDependencyUnavailable

**What it means.** Over half of export requests were refused as `unavailable` for 5 minutes: a
dependency other than Kafka is down, usually Postgres, which the receiver needs to check an API key.
These refusals write no `intake_refusals` row, so detection is held only if the install-wide guard
engages. On an install with fewer than 5 organizations it cannot, and a project's traffic finding
names "nexovue's intake" as a possible cause.

**Confirm.** The receiver's `/readyz` (above) names the failing check.

**What to do.** Restore that dependency. Then look for traffic findings opened during the outage, as
after a `hold_expired`.

**Fixed when** the refusal share is back under the threshold and the alert has resolved.

## Walked

On the development stack, 2026-11-01. The stores ran in Docker; the receiver, the trace worker, beat and
`scheduler-light` ran from source on the host, because the application images could not be built in that
environment, with a Prometheus loading `rules/nexovue.yml` and scraping them. A script sent 24 spans
every 2 seconds through the receiver.

Each step below began on a healthy stack: settled minute 260 to 350 seconds old, no project behind.

1. **A stopped trace worker** (00:22). The sweep kept running (`since_sweep` under a minute) and logged
   `the pipeline is behind … lagging=[0, …, 11] settled_minute=00:19`. `span_derived_progress` stood
   still at 00:22:05 on every partition; `kafka-consumer-groups` said `has no active members`, with lag
   rising from about 25 to 500 a partition. `NexovueDetectionStalled` fired at 00:30 ("11m 7s old"); no
   project was counted behind, because the jobs still ran for the old minute. The worker restarted at
   00:30, caught up, and the alert resolved at 00:32.
2. **The sweep not running, every process healthy** (00:33). `scheduler-light` was restarted with
   `--queues light,light.local`. Every Prometheus target stayed up and `NexovueJobsWaiting` stayed quiet:
   the queued sweep waited at most about 30 seconds before it expired. `NexovueDetectionStalled` fired at
   00:38, and `NexovueProjectDetectionFailing` with it. `since_sweep` read 6 minutes, and the log's
   `worker started … queues=['light', 'light.local']` named the cause. Restarted with its default
   queues, both alerts resolved within 30 seconds.
3. **One project's jobs failing** (00:40). `scheduler-light` ran with a wrong password for the detection
   writer. Each job logged `detection could not judge a project project_id=…`, then the traceback
   (`Authentication failed`). `NexovueProjectDetectionFailing` fired at 00:45; *stalled* stayed quiet.
   The query above named the one project. At 15 minutes the sweep queued the owners' `stopped`
   notification, and the first completed run queued `running_again`.
4. **A Kafka outage past the 30-minute limit**: 35 minutes of `broker_unavailable` refusals were written
   to `intake_refusals`, as the receiver writes them. The next sweep logged `a hold on every project's
   minutes reached its limit`, and wrote one `hold_expired` row. `NexovueDetectionHeldByIntake` fired,
   with `kind="hold_expired"`, and resolved 15 minutes after the row. No finding opened in the span.

Not walked: an `intake_guard` event, which needs 5 organizations losing traffic while the intake falls
(proved in `test_detection_failure_live.py` instead); a stuck single partition with others moving; the
two receiver alerts, which need Kafka or Postgres stopped under the shared dev stores; and every
command against a deployed install.

The walk found three defects, fixed with it: *held by intake* could never fire, because the sweep counted
its events in a worker's child process, whose metrics no scrape reads; a failing project job never
named its project; and a refactor that shadowed the per-service judging function, caught in step 3.

## Not yet established

- No walk on a deployed install: the `$P-…-1` container names and the `prom` helper are read from
  `deploy/nexovue` and `nexovue.yaml`, not run there.
- The thresholds (10 minutes, 3 minutes) are the design's first guesses, to be set from production.
- With 3 Kafka brokers (`nexovue.kafka3.yaml`), `kafka` runs on the first broker; any broker answers.

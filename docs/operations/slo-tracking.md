# Reliability targets (F19)

What to do when reliability targets stop being judged, or a message you expected about one did not
arrive. Written from a failure drill on a development machine on 2026-11-22 (F19 plan T6.4); *Walked*
below says which steps were run and what they showed. Nothing here has been run on a deployed server.

## How judging runs

Once a minute, the same sweep that hands out anomaly detection's minute hands out one **judging job** per
project that holds a live target, longest-waiting first, to the `slo` queue on `scheduler-light`
(two slots). A job reads the last 24 hours once for the project, works every target's state, saves
`slo_status` and judges each burn rule. Two quieter jobs run beside it: `slo_counts` rewrites the daily
counts every few minutes, and `slo_history` counts a new target's past days once.

A job not started within a minute is dropped, because the next minute's replaces it. So a busy
`scheduler-light` makes figures late and never wrong: a target past 15 minutes reads *Not up to date* on
screen, and no burn is judged on a stale minute.

A different container tells people. `scheduler-deliveries` finds a project whose targets have not been
judged for 15 minutes and sends one critical message ("Reliability targets in `production` are not being
judged since 02:14. A burn would not reach you.") to the channels its rules name, or the owners, and one
when judging returns. It needs only Postgres, so it can speak when `scheduler-light` cannot. If Postgres
or beat is down, nothing here can speak: that is what an outside monitor is for
([setup](../product/setup/reliability-targets.md#an-outside-heartbeat)).

## The commands used below

As in [detection](detection.md): `P` is `NEXOVUE_PROJECT` from `/opt/nexovue/nexovue.env` (`nexovue` on
the development stack), and `pg` runs `psql` inside the Postgres container with its own user.

```bash
P=nexovue
pg() { docker exec -i "$P-postgres-1" sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' psql "$@"; }
```

## The readings

On `scheduler-beat`, read by its leader every 15 seconds; a beat that does not lead reports 0. No label
names a tenant or a project.

| Metric | Meaning | Healthy |
|---|---|---|
| `nexovue_scheduler_slo_oldest_status_age_seconds` | How old the stalest live target's last judging is. A target never judged ages from its creation | under 300 |
| `nexovue_scheduler_slo_targets_late` | Live targets last judged over 15 minutes ago, the age the screens call *Not up to date* | 0 |
| `nexovue_scheduler_notifications_withheld_held` | Messages held back (for the same news, or a snooze) and not yet released | small, and falling when a problem ends |
| `nexovue_scheduler_jobs_ready{queue="slo"}` and `oldest_ready_job_seconds` | Judging jobs waiting for a slot | under 60 s |

## NexovueSloStatusStale

**What it means.** The stalest target was last judged over 15 minutes ago, for 10 minutes, **while
detection is settling minutes**. It is a ticket, not a page: the screens already say their figures are old.
When detection itself has stopped, `NexovueDetectionStalled` pages and this stays quiet, because one sweep
hands out both.

**Look.**

```bash
pg -c "SELECT queue, count(*) AS ready, round(extract(epoch FROM now() - min(run_after))) AS oldest_s
       FROM jobs WHERE queue IN ('slo','slo_counts','slo_history')
         AND run_after <= now() AND attempts < max_attempts GROUP BY queue"
pg -c "SELECT t.kind, count(*) AS targets, min(s.evaluated_minute) AS stalest
       FROM slo_targets t LEFT JOIN slo_status s ON s.slo_id = t.id
       WHERE t.deleted_at IS NULL GROUP BY t.kind"
docker logs --since 15m "$P-scheduler-light-1" 2>&1 | grep -i -E "slo|killed|oom" | tail -20
```

- **Jobs are ready and old, and `scheduler-light` is running:** it is busy or its slots are full. Detection,
  evidence and release reads share four seats with SLO work (the floor). Read what else is queued
  (`jobs_ready` by queue). Judging catches up when they drain. If it never does, the 768M ceiling or the
  slot count is too small for this install (see *Walked*).
- **No `slo` job is ready and statuses are old:** the sweep is not handing them out. Check
  `NexovueDetectionStalled` and beat's leadership (`nexovue_scheduler_beat_leading`).
- **`scheduler-light` is not running or keeps restarting:** probes stop with it. Start it
  (`docker compose up -d scheduler-light`), then read its log for the kill or the error. In the drill the container at its 768M ceiling peaked at 644 MiB with all SLO, detection and evidence work queued, and was not killed.
- **One project only:** its statuses age while others stay fresh. A failed read raises, and the
  worker records the failed job with its task; **no line names the project yet** (the design asked for
  one; not built). Read `slo_status.evaluated_minute` by project to find it.

**Fixed when** `nexovue_scheduler_slo_targets_late` is 0 and no target reads *Not up to date*. The people
told, if 15 minutes passed, get "judging again".

## A message I expected did not arrive

Burns are judged every minute, but a message goes only through the rule's channels, and a second message
about news a channel already heard is held back for up to an hour. Every held message is kept with its
reason, so look there before looking at delivery.

```bash
pg -c "SELECT n.kind, n.state, n.withheld_why, n.released_at, n.outcome_reason, n.created_at
       FROM notifications n
       WHERE n.withheld_why IS NOT NULL
       ORDER BY n.created_at DESC LIMIT 20"
```

- `withheld_why = same_news`: the channel was told about the same problem (a finding, a threshold rule or
  another burn) at the same or a higher severity in the last hour. `released_at` empty means it is
  still held; if the problem is still open when nothing holds it any more, one message goes out then.
- `withheld_why = service_snoozed`: the service is snoozed, so its burn messages wait.
- Nothing held and nothing delivered: the target has no rule naming a channel (the default rule
  *Reliability targets* names the owners), the rule is muted, or the channel is muted. The channel's delivery log
  in the console says which.
- A message about the exhaustion of the allowance and the notice of an exclusion are never held back.

## When judging stops for a project

The customer is told by the notice above. To see which projects the sweep thinks are blind, read the open
incidents of the fixed name:

```bash
pg -c "SELECT project_id, opened_at FROM incidents
       WHERE rule_name = 'Reliability targets not being judged' AND resolved_at IS NULL"
```

## Walked

See [`docs/features/slo-tracking/status.md`](../features/slo-tracking/status.md), *Step 6*, for the drill:
what was stopped, what each screen and message did, and what was not run.

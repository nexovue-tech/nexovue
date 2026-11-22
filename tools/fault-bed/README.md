# Fault bed (F6, plan T8.1)

The fault bed turns on the OpenTelemetry Demo's fault flags one at a time, and records what nexovue's
anomaly detection found. It then scores the run as the spec's AC36 table:

- whether each fault was caught, and how long that took;
- which service and signal caught it;
- which flags move no signal at all (the gap record, FR27);
- every false alarm in a week with no fault.

## What a run is

1. **Warm-up, 3 weeks.** It is made of 3 weeks of seeded history from `just detection-seed`,
   plus 1 week of the live demo.
2. **Reference run, 7 days.** No fault is turned on. Every finding that opens is a false alarm.
3. **Injections, about 4.4 days.** There are 14 faults, each injected 5 times. Each injection
   keeps its fault on for 30 minutes, then off for an hour. Every injection lands at the same weekday
   and time as a stretch of the reference run, exactly one week earlier. AC36 compares the two.

Each round runs the faults in a different order, so no fault always lands at the same time of day.

## Commands, on the Mac

```bash
# 1. The stack and the demo. Wait about 10 minutes, so the demo's services have reported.
just dev

# 2. The seeded half of the warm-up: 3 weeks, ending where the live data begins.
just detection-seed --weeks 3

# 3. Leave the demo running for 1 week (the live half of the warm-up).

# 4. A read API token for the `production` project: Console → Settings → API tokens → Create,
#    reads "production", expires in 30 days. Keep it in your shell, not in a file in the repo.
export NEXOVUE_TOKEN='nvt_live_…'

# 5. Checks the flags, the token and the project, in seconds.
just fault-bed check

# 6. The run itself: about 11.5 days. caffeinate keeps the Mac awake.
caffeinate -dimsu just fault-bed run --log tools/fault-bed/runs/run-1.jsonl

# 7. The AC36 table, as Markdown.
just fault-bed score --log tools/fault-bed/runs/run-1.jsonl --out tools/fault-bed/runs/run-1.md
```

If the run stops, start the same `run` command again. It resumes from its log, and turns off any
flag it left on. An injection whose start time passed while the run was stopped is recorded as
*skipped*, never run late, because a late start would give the wrong time to catch.

`run` takes `--faults paymentFailure,cartFailure` to inject only some faults, and `--rounds`,
`--slot-minutes`, `--inject-minutes` and `--seed`. The run log is a JSON Lines file. The `runs/`
directory is not committed. Copy the table into `status.md`.

## A reliability target on the bed (F19, AC49)

The bed also scores a reliability target's burns, as a demo result. Before step 6 above, in the
console create **payment availability, 99.9% over 28 days** for `payment` and tick the owners
channel, so a burn has somewhere to go. In `just dev`, alert email is written to the log: read it
with `docker logs -f nexovue-scheduler-deliveries-1 2>&1 | grep 'mail was rendered'`, or send
real mail with `just alert-email real` (`just alert-email log` puts it back). The run records every
burn the incident list holds (`GET /alerts/incidents`), and `score` adds a second table:

- a burn must fire in at least 4 of the 5 `paymentFailure` injections;
- a burn already open when an injection begins counts as caught only if it escalates inside it,
  and the table says how many were already open;
- where a finding also opened, whether the burn was linked to it, and how many emails the burn's
  own incident delivered or held back (the finding's email is not readable with a token);
- the no-fault reference week's burns, one by one, beside the demo's own failure rate for
  `payment`: a reported number, never a fixed zero.

Run `paymentFailure` in the full 14-fault order (the default) so about 21 hours separate its
injections and a burn can clear between them. Without a machine that stays on for the run, this
reads "not run". A short labelled demo (one fault, one round) proves the path, not the result.

## The flags

The flags are those of the demo release `tools/otel-demo/otel-demo.sh` pins, **3.0.0**. Its flag file is
[`src/flagd/demo.flagd.json`](https://github.com/open-telemetry/opentelemetry-demo/blob/3.0.0/src/flagd/demo.flagd.json).

| Fault | Flag = variant | Expected to move |
|---|---|---|
| `adFailure` | `adFailure` = `on` | ad · failing |
| `adHighCpu` | `adHighCpu` = `on` | ad · slower |
| `adManualGc` | `adManualGc` = `on` | ad · slower |
| `cartFailure` | `cartFailure` = `10%` | cart · failing |
| `emailMemoryLeak` | `emailMemoryLeak` = `100x` | email · slower |
| `failedReadinessProbe` | `failedReadinessProbe` = `on` | cart · traffic (likely a gap: compose has no readiness probes) |
| `imageSlowLoad` | `imageSlowLoad` = `5sec` | frontend-proxy · slower |
| `intlShippingSlowdown` | `intlShippingSlowdown` = `5sec` | shipping · slower |
| `kafkaQueueProblems` | `kafkaQueueProblems` = `on` | fraud-detection · slower |
| `paymentFailure` | `paymentFailure` = `10%` | payment · failing |
| `paymentUnreachable` | `paymentUnreachable` = `on` | checkout · failing |
| `productCatalogFailure` | `productCatalogFailure` = `on` | product-catalog · failing |
| `recommendationCacheFailure` | `recommendationCacheFailure` = `on` | recommendation · slower |
| `trafficAt40` | `loadGeneratorVUs` = 40% of its users | frontend-proxy · traffic |

**The expectations are unverified.** They come from each flag's description and the demo's code,
and they are used only to name a gap. What a flag actually moves is measured by the run.

**Traffic at 40% (D19).** The demo has no traffic fault. So the bed adds a `faultbed-40pct` variant
to `loadGeneratorVUs`, at 40% of its current value: 2 users where there were 5. Two things make the
real drop smaller than 60%:

- the load generator's browser scenario stays at its 1 user;
- k6 restarts on its next poll of the flag.

The run measures the drop, and only a drop to 45% or less counts.

**How a flag is set.** Upstream's `flagd-ui` is not run here (`otel-demo.sh` drops it). So the bed
rewrites the flag file itself, the way `flagd-ui` does: it writes a copy and renames it into place.
flagd watches the file and reloads it.

**Not yet seen working:** flagd's reload through Docker Desktop's bind mount on macOS. Watch the
first injection with `just otel-demo logs` before leaving a run alone.

## How it scores

The rules are in `fault_score.py`'s docstring. In short:

- **What a flag moves.** For each injection, the bed reads every service's requests, failures and
  p95 from `GET /services`. It takes the first full 5 minutes after the flag has had a minute to
  spread, and the same 5 minutes one week earlier.
- **The thresholds** are the normal-sensitivity minimums: 1.5× slower, +2 points failing, traffic
  at 50% or less.
- **Moved.** A service and signal past the minimum in a majority of a flag's injections is *moved*.
- **Gap.** A flag that moves nothing is a gap.
- **Counted.** An injection counts when it clears the minimum by a tenth (D19).
- **Caught.** A finding counts as a catch when it opens on a moved service while the fault is on.
  It is *in time* within 12 minutes at 200 requests a minute or more, and 20 minutes below (D16).
- **Pass.** At least 4 of every 5 counted injections caught in time, over at least 5.
- **When a finding opened.** The bed reads this from its id. `findings.id` is Postgres's
  `uuidv7()`, which stamps the time the row was written. The API's `started_at` is when the
  unusual minutes began, not when the finding opened.

## Not measured

- **D18's allowance.** On a service whose own failures burst often, a fault inside its usual bursts
  need not be caught. The bed cannot see which services are classed bursty. A *fail* on such a
  service needs reading against `GET /services/{name}/detection`.
- **The probable cause.** Scoring it is Stage 2's job.

## Chat under load (F4, AC80 and AC98)

`chat_load.py` (`just chat-load`) asks whether chat's heaviest questions hold detection and alerts
back. Each of four people keeps one question in flight, and every question makes eight 7-day reads
(`search_logs` for a phrase no log holds, so each read covers every row, then `list_traces`), from
the fake provider in `week-reads` mode. It records each question, every poll of the panel's two
routes and the memory of the processes or containers named. **AC80's guess, fixed before any
run:** every minute's detection judged inside the 60-second cycle, and each alert no more than 60
seconds later than in a chat-idle run, with the same findings and notifications.

```bash
# 1. The week-reads fake on the `ai` network, and an organization connected to it (Settings → AI
#    wording, OpenAI-compatible, http://172.30.250.10:8080/v1), with chat on, the daily limit at
#    1,000 and four members sharing one password.
FAKE_PROVIDER_MODE=week-reads docker compose --profile fake-provider up -d fake-provider

# 2. The chat-idle run is the AC36 run above. The chat run is a second one, with this beside it
#    for the whole run:
export NEXOVUE_LOAD_PASSWORD='…'
just chat-load run --project-id <the load project> --email a@… --email b@… --email c@… --email d@… \
  --minutes 16560 --container nexovue-scheduler-insight-1 --container nexovue-api-1 \
  --log tools/fault-bed/runs/chat-load.jsonl

# 3. Memory and the poll routes (AC98), and the alerts against the idle run (AC80).
just chat-load summary --log tools/fault-bed/runs/chat-load.jsonl
just chat-load compare --idle tools/fault-bed/runs/run-1.jsonl --chat tools/fault-bed/runs/run-2.jsonl
```

`compare` pairs the two runs' injections by fault and round, and fails any whose first finding or
first burn came more than 60 seconds later under chat, or whose findings differ on scope,
services, signals or severity, or whose notifications differ on kind, state, channel or reason.
An injection in only one run is not judged. A looper the API refuses (the daily limit, a rate
limit) stops and is reported, so refused load is never counted as load.

**Not yet measured by any tool here: AC80's detection half,** every minute judged inside the
60-second cycle on the fault bed. `just detection-scale watch` measures exactly that, but only for
AC29's generated tenants, not the demo's project (F4 status, open decision on AC80's detection
half).


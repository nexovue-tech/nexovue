# Status: ingest-cannot-be-knocked-over

## Lifecycle status

Implemented and verified on the development stack. Committed as `455410b`; not pushed.

## Current task

None.

## Completed

- Blocker 13 reproduced against the development receiver: eight keyless gzip exports raised it from
  262 MiB to 629 MiB of 768 MiB, every one refused `Unauthenticated` only after being read (`bug.md`).
- Blocker 12's refusal confirmed on development ClickHouse: 1,100-deep JSON refused `INCORRECT_DATA`,
  100-deep accepted, in a temporary table.
- **Step 1 (T1-T6) — the gRPC door refuses before it reads.** Red then green, observed:
  - `TestGRPCKeylessExportIsRefusedBeforeItIsRead` without the tap: `the server decompressed the body 1
    time(s) before refusing it`; `TestGRPCExportBeyondTheUnverifiedShareIsAskedToRetry`: `export
    succeeded; want a refusal`. With it: both pass.
  - `TestExportBeyondTheInFlightBudgetIsRefusedBeforeTheBodyIsRead` without the HTTP gate:
    `status = 413, want 429` (the body was read). With it: passes.
  - **Live, the bug's flood re-run against the rebuilt receiver:** 8 concurrent keyless 15 MiB exports
    × 3 rounds → memory 20.2 → 21.9 MiB (was 262 → 629 MiB); 20 concurrent × 3 → 20.1 → 22.6 MiB, every
    export `Unauthenticated`, `restarts=0 oom=false`.
  - Real exports pass: four concurrent exports with the development key → round 1 `OK:2
    ResourceExhausted:2` (the key was unseen since the restart, so the two-slot unverified share
    applied), round 2 `OK:4`. An exporter retries the `RESOURCE_EXHAUSTED` after the 1 s it is told.
- **T7, the live red run (11:00:23 UTC):** one failed span carrying a 1,100-level attribute key landed on
  `spans-raw` partition 9, offset 29165. The worker retried `insert spans` for two minutes, alternating
  `INCORRECT_DATA (117): Cannot parse JSON` with `packet: uvarint: read: EOF` (ClickHouse closes the
  connection after a refusal), then `11:02:32 ERR pipeline ended; the worker cannot continue … insert
  spans failed for 2m0s` and `11:02:37 INF worker starting … pipelines="trace-assembly, log-writing,
  metric-ingest, profile-ingest"` — every signal restarted, reading the same record again.
- **Step 2 (T8-T15) — one refused row no longer stops ingest.** Live green, on the rebuilt worker with no
  manual recovery:
  - `11:06:47.677 ERR the store refused a span insert; narrowing it to the refused rows … spans=1`,
    `11:06:47.696 WRN parked a row the store refused … dlq_topic=spans-dlq tenant_id=… project_id=…`,
    `11:06:47.950 INF recovered after a transient failure … insert trace summaries attempts=2`.
  - `nexovue-workers` on `spans-raw` partition 9 committed at 29166 — past the record — lag 0.
  - The parked record's headers: `nexovue-parked-payload: row-json`, `nexovue-source-offset: -1`, the
    refusal reason, the tenant and project.
  - A poisoned metric and a poisoned log sent the same way: `pipeline=log-writing … parked its records …
    messages=1`, `pipeline=metric-ingest … parked its records … messages=9`; `logs-dlq` 1, `metrics-dlq`
    9. The worker never stopped.
  - Unit tests, red then green: `storefailure` with `Judged` returning the error unmarked — `Judged
    (INCORRECT_DATA (117)) is not permanent: the loop would retry it until the worker exits`. New tests
    also pin `consume.ParkRefused`, the dead-letter payload header, `salvageRows` (one poisoned row in
    1,000 → 999 stored, 1 parked) and `stepOverRefusal`.
- **Step 3 (T16) — the attribute is stored instead of refused.** `otlpvalue.MaxDepth = 64`. Red with the
  cap disabled: `stored value nests 1100 levels; ClickHouse refuses this` and `… 1101 levels …`. Live: the
  same poisoned span re-sent was **stored** — span `poison`, status `ERROR`, attributes
  `{"nexovue":{"dropped_attributes":["a.a.a.a…"]}}` — and nothing was parked.
- **Step 4 (T17-T18) — the sampling buffer is bounded in bytes.** `WORKERS_SAMPLING_MAX_BYTES`, 256 MiB.
  Red without the byte condition: `buffer holds 11534336 bytes, past its 10485760-byte bound`; green with
  it.
- `/review-diff` run; findings below.

## Next action

None. Both open decisions below were taken by the founder on 2026-10-10: yes to each, as their own
records (`synthetics-suite-reads-a-clean-hour`, and the narrowing follow-up).

## Open decisions

1. **Narrow refusals in logs, metrics and profiles as spans now are?** A refused insert there parks the
   whole chunk; live, a poisoned metric took 8 innocent host-metrics messages to the dead-letter topic
   with it. They are kept, not lost, but no replay tool exists. Spans narrow to the one bad row. Doing the
   same for the other three is a follow-up change of its own size. Not blocking: this record's fix stands
   either way.
2. **`test_synthetics_live.py` needs its own record.** Its fixture accepts only a check with *no* failed
   run in two days, so it errors for up to 48 hours after any outage of the check's target. It errored in
   this change's `just check` because the OpenTelemetry demo stack no longer exists on the development
   machine — both checks target its storefront and have been `down` since 2026-10-09 18:34 (414 runs
   each). Unrelated to this change. Bringing the demo back does not clear it for two days.

## Blocked

Nothing.

## Risks

- **A stranger can occupy the unverified share.** Two slow keyless-but-well-formed gRPC exports can each
  hold an unverified slot for the full 60 s budget, renewing, so a key the receiver has never seen gets
  `RESOURCE_EXHAUSTED` (retried) until a slot frees. Known keys — any key seen since the receiver started
  — and the HTTP door are unaffected. A per-address share is the fix if it is ever seen.
- **After a receiver restart, each customer's first gRPC burst may be asked to retry once**, until its
  key is verified. No data is lost.
- **ClickHouse's refusal text quotes the refused row** into our log line and the dead-letter reason
  header. The row is already masked; the log pipeline has always done this.
- **A parked span is still counted in its trace summary** — the summary observed it before the insert.
- **A refused derived row is lost**, counted in the heartbeat as `derived_rows_refused`; derived rows
  are sums, and the source spans are mostly not stored.
- **The defaults are from the laptop**: 8 in-flight exports, 2 unverified, 256 MiB of sampling buffer,
  64 levels of nesting. Re-measured at the group 8 load test.
- **`grpc.InTapHandle` is experimental in grpc-go**; the gRPC tests fail the build if it changes.

## Validation

- `just check`: format and lint 0 issues; Go 36 packages `ok`, none failed; Python 4166 passed, 3
  skipped, **8 errors — all `test_synthetics_live.py`, open decision 2 above**. That stopped the recipe
  before the web tests and the contracts check, which were run separately: web 1350 passed, browser SDK
  120 passed, `buf lint` and `buf breaking` clean.
- The live runs above.
- Not run: a profile sent live (no OTLP/JSON profile fixture); the profiles path uses the same shared
  code as metrics and logs.

## Specification impact

None.

## Documentation impact

`docs/operations/production-readiness.md`: blockers 12 and 13 marked fixed on the development stack, with
this record; the sampling-buffer recommendation marked done.

## Last updated

2026-10-10

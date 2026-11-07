# Status: browser-door — the browser door stops counting in the rollups

## Lifecycle status

Implemented (2026-11-07), on `feature/phase-2`. `just check` passed (offline; `buf lint` could not reach its registry from the build machine, CI covers it). `just chaos` against a full dev stack is still owed.

## Current task

None in progress. All four plan steps are built in worktree `wt/browser-door`.

## Completed

- 2026-11-02 — workspace opened; `change.md` and `plan.md` written from the code in worktree
  `wt/browser-door` (cut from `feature/phase-2`); record added to `docs/changes/README.md`.
- Code reading done: how the door is stamped and who reads it, where the rollups are written, how the
  cap works, where `synthetic.*` readings come from — all with `file:line` in the plan's *Current system*.
- Answered from the code: **this change needs no `door` column.** `spans` and `logs` have none
  (`deploy/clickhouse/migrations/000001_spans.up.sql`, `000002_logs.up.sql`; a grep for `door` across the
  migrations matches only comments in `000030`/`000031`), and none is needed — every behavior here acts in
  the workers before rows are written, on the `Door` already stamped on each Kafka envelope
  (`envelope.proto:40`, `ingest.go:288`). The column on `spans`/`logs`, and `service_versions_1m`, are
  F28's (foundation section 8).

## Built, 2026-11-07

Every task in `plan.md`, with the founder's answers applied. What exists now, read from the code:

- **Step 1 — browser-door spans count in no rollup.** `traceassembly/stages.go`, `countsInRollups`: a `DOOR_RUM`
  span skips `counters.Observe`, so it reaches none of `metrics_sum`'s derived span counters,
  `endpoint_stats_1m`, `service_stats_1m` or `service_versions_1m`. Edges, the sampling buffer and storage still
  take it, so the trace view keeps it. F28's interim `countedVersion` branch and `spanRow.CountedVersion`
  (`traceassembly/flatten.go`), which F28 D55 says this change deletes, are deleted with their test; ADR-025
  point 5 says so. **F28 overlap, handled mechanically:** its version map is fed from `observeService`, behind
  the same `Observe` call, so the one filter covers all three tables as the plan said.
- **Step 2 — the cap keeps the busiest.** `derive/services.go`, `capServices` and `busiest`: requests in the
  drained tenant-minute descending, then service name, project and retention, applied at drain to the sorted
  rows, kept rows still in drain order (decision 2).
- **Step 3 — going over the cap is recorded.** `ServiceCap` is now one entry per tenant-minute (minute, tenant,
  services left out, the longest retention among the rows left out). ClickHouse migration
  **`000039_services_over_cap_1m`** (decision 1: `000035`'s engine and deduplication window), written by
  `derive/capwriter.go` with a token over the rows, from `insertDerived` right after `service_stats_1m`,
  retried within the loop's budget (`consume.RetryBudget`, now exported so the insert can use
  `RetryWithin` like F28's); a refusal is counted and holds no progress. The WARN line stays, totalled by
  `derive.CapReportOf`. In `erasure/tables.py`, `erasure/statements.py` (delete and count) and
  `test_erasure_live.py`. **ADR-026** records the decision, as the plan's *Questions* row 1 said it would.
- **Step 4 — `synthetic.*` comes only from the prober's door.** New package `internal/pipeline/proberdoor`
  (`Trusted`, `ClaimsMetric`, `ClaimsCheck`, `Refusals`). A claim is a metric named `synthetic.*`, or a resource
  whose attributes would be stored under `synthetic.check` — judged on the stored shape, because a key
  `synthetic` holding a map reaches the same JSON path as `synthetic.check.id` (`otlpvalue.AttributesMap`).
  `metricwriting/flatten.go` drops such points outside `DOOR_SYNTHETIC` and writes the rest (decision 4); its
  comment now states the exception and why. `logwriting/flatten.go` does the same for evidence records
  (decision 5). Each pipeline logs one WARN per batch with the count, tenant, project and door, and counts
  `synthetic_points_refused` / `synthetic_records_refused` in its heartbeat.
- **Live test** `apps/api/tests/test_browser_door_live.py` (T1.3, T3.3, T4.2): signs up, mints a site key,
  and through the real receiver, Kafka and worker proves the three behaviours below. Each absence is asserted
  only after a control sent beside it has landed.
- **Docs:** `architecture/current-system-reference.md`, `security-and-tenancy.md`, `database.md`;
  `engineering/performance.md` (the sort-key line now allows `(tenant_id, …)` for a row with no project,
  which `span_trace_lookup` already was); ADR-025 point 5; ADR-026 and its index line.

## Next action

Coordinator: review the diff, run `just check`, and commit. Then this record reads Verified, and F28's and
F19's Step 4 gates can open. `just chaos` once against a dev stack, as the plan's validation section says, is
still owed.

## Founder answers, 2026-11-07

The founder approved `plan.md` and answered open decisions 1 to 5 **as recommended**:

1. Going over the cap is recorded in **a small ClickHouse table** (migration at the next free number), one row
   per tenant-minute with the dropped count, deduplication-token pattern of `000035`, listed in
   `erasure/tables.py`. The WARN line stays as an operator pointer.
2. "The busiest" is **requests in the drained tenant-minute, descending, name ascending as tie-break**; the
   per-drain grain is accepted knowingly.
3. **No rebuild and no backfill** of existing rollup rows.
4. A forged `synthetic.*` reading is **dropped in the worker, the drop counted in a log line** (never silent),
   and the `metricwriting/flatten.go` comment updated in the same change to state the exception and why.
5. **Metrics and logs together:** the same door check guards `synthetic.check.*` evidence logs in
   `logwriting`. This widens the approved scope by the founder's own decision.

## Open decisions (answered 2026-11-07, kept for the record)

1. **What "recorded" means for going over the cap.** The approved scope says only "going over the cap is
   recorded". Today it is a WARN log line naming one tenant per drain, plus an in-process counter that dies
   with the worker (`traceassembly/pipeline.go:142-145,394,756-768`; `ServiceCap` aggregates to one tenant —
   `derive/services.go:131-137,223-236`). Options:
   - (a) **Keep the log and counter, promote the counter to a Prometheus metric.** Cheapest; visible to the
     operator; but no screen can tell a *tenant* their minute was capped, which F19's design asks for
     ("record which tenant-minutes went over the cap, so a screen can say so",
     `slo-tracking/design.md:452`).
   - (b) **A small ClickHouse table** (migration `000038`), one row per tenant-minute with the dropped
     count, written at drain with the deduplication-token pattern `000035` measured, listed in
     `erasure/tables.py`. Durable, per-minute, readable next to the rollups the capped data is missing from.
   - (c) **A Postgres table.** But the ingest path writes Postgres only through a short, named list of
     exceptions (AGENTS.md, *Repository invariants*), and widening that list is exactly what the invariant
     exists to resist.
   **Recommendation: (b),** because F19's screen reads ClickHouse alongside the rollups, the record is
   telemetry-shaped (per-minute, per-tenant, erasable), and (c) erodes a stated invariant for no gain.
   If (b), whether the WARN line stays as an operator pointer or goes (rule 11) is part of the same answer;
   recommend keeping it, since it fires at most once per drain.
2. **How the cap chooses "the busiest".** The scope says "keeps the busiest" without a measure. The cap runs
   at drain, per worker, on that worker's partitions' share of the minute (`services.go:166-199`), so any
   measure is per-drain, not global: a service can be kept by one worker and dropped by another in the same
   minute — exactly the grain today's cap already has. Options:
   - (a) **Requests in the drained tenant-minute, descending, name ascending as tie-break** — F19's design
     names this shape (`slo-tracking/design.md:452`, citing `services.go:172-176`); deterministic, so a
     replay drains the same block and deduplication still recognises it.
   - (b) Some cross-drain measure (e.g. a running per-service total). Global "busiest", but it makes the
     drain depend on history, which breaks the replay-determinism the deduplication token relies on
     (`services.go:166-171`).
   **Recommendation: (a).** The per-drain grain should be accepted knowingly: an attacker minting names can
   no longer evict a busy service, which is what the scope is for; that a 1-request real service can still
   lose to another 1-request service at the margin is today's behavior too.
3. **Whether existing rows need a rebuild.** Nexovue is pre-deployment: no customers, no live data
   (AGENTS.md rule 14), and legitimate browser spans were never counted anyway (`INTERNAL`/`CLIENT` kinds
   fail the `inbound` filter, `derive/endpoints.go:100-102`; `packages/browser-sdk/src/rum.ts:66-78`). Dev
   and demo rollup rows are disposable (`just fresh`). Options: rebuild the rollups; or leave them.
   **Recommendation: leave them — no rebuild, no backfill,** matching F3's "nothing is backfilled"
   (`ai-insights/design.md:473`). Non-blocking; recorded so it was decided rather than forgotten.
4. **What happens to a refused `synthetic.*` reading.** The scope says such readings "come only from the
   prober's door"; it does not say what the worker does with a forged one. The metric pipeline's own stated
   principle is to reject a whole message rather than drop the odd metric silently
   (`metricwriting/flatten.go:64-70`) — but that principle protects *customer data*, and a forged
   `synthetic.*` point is by definition not the prober's. Options:
   - (a) **Drop the offending points in the worker and count the drops in a log line.** The rest of the
     export is written. Cheap, and the sender learns nothing.
   - (b) **Park the whole message to the DLQ.** But the DLQ is for undecodable data (AGENTS.md, *Workers*),
     and an attacker could then fill it with well-formed junk.
   - (c) **Refuse at the receiver** with OTLP partial-success semantics. Strongest feedback, but it puts
     metric-name parsing on the receiver's hot path, which today decodes but does not walk metric names, and
     it teaches a probing attacker exactly which names are guarded.
   **Recommendation: (a),** with the drop counted (never silent), and the `flatten.go:64-70` comment updated
   in the same change to state the exception and why — otherwise the code contradicts its own comment
   (rule: propose the standard's update as part of the change that outgrew it).
5. **Whether forged synthetic evidence logs are in scope.** The check screens also read evidence from
   `logs` by `synthetic.check.*` attributes (`synthetics/queries.py:322`), and nothing stops a browser or
   secret key writing log records that imitate it — the same hole as the readings, one table over. The
   approved scope says "`synthetic.*` **readings**", which reads as metrics. Options: metrics only (the
   scope, literally); metrics and logs (the same guard in `logwriting`, closing the hole in one change).
   **Recommendation: metrics and logs together** — the fix is the same door check and leaving the logs half
   open invites the identical forgery — **but that widens the approved scope, so it is the founder's call,
   not this plan's.** A "metrics only" answer leaves the logs hole on the record here.

## Open decisions

None blocking. One observation for the founder, outside the approved scope and not acted on:

- **The usage meter still records browser-door service names.** `pipeline/usage/usage.go` takes every
  envelope's `service.name` into Postgres's `service_last_seen` whatever the door, and the *went silent* alert
  and service lists read it. So a site-key holder can still add service names there (not counts: the rollups
  are closed). Legitimate browser services need that row, so it is not a mechanical fix: a door column there,
  or filtering by kind, would be a scope decision.

## Blocked

Nothing. All five decisions answered 2026-11-07.

## Risks

- **Branch ordering.** This worktree is cut from `feature/phase-2`. The foundation orders F6's merge first
  (its findings and migrations 0041-0050 exist only on that branch); where this change's branch lands
  relative to F6 is the coordinator's to sequence. The change itself touches none of F6's files, but
  `service_stats_1m` (`000035`) must exist wherever it lands.
- **Per-drain cap grain** (open decision 2): "busiest" is per worker-drain, not global. Accepted knowingly
  or not at all.
- **F28 coupling:** the filter point covers `service_versions_1m` only because F28 feeds it from
  `observeService` (its design D55). The unit test and the live test now assert all three tables, so a move
  would fail them.
- **The browser door's derived span counters go too.** Skipping `Observe` (T1.1 as written) also leaves
  browser-door spans out of `metrics_sum`'s `nexovue.spans.*` series. Nothing in the API reads those for the
  browser; noted so it is not a surprise.
- **The over-cap count is summed across drains and workers**, so it can double-count a service or count one
  another drain kept (ADR-026). A reader treats any row as "this minute was capped".
- **The over-cap record's lifetime** follows the longest derived retention among the rows left out, capped at
  35 days like `000035`; a choice made in building, recorded in the migration.
- **Not measured under load.** The busiest-first sort runs only for a tenant-minute over the cap; the
  `ClaimsCheck` map is built only for a resource with a `synthetic…` key. Neither was timed.

## Validation

Run 2026-11-07 in `wt/browser-door`. Each behaviour's test was seen failing first; the decisive lines:

| Test | Seen failing (before the fix) | Then |
|---|---|---|
| `traceassembly/browserdoor_test.go` T1.1, T1.2 | `DOOR_RUM: service_stats_1m drained 1 rows … endpoint_stats_1m drained 1 rows … counter points drained 3 rows … service_versions_1m drained 1 rows`; the `DOOR_OTLP` half passed | pass |
| `derive/services_test.go` T2.1 | `checkout kept 0 requests, want all 50: minted names evicted the busiest service`; `aaa-01999 was kept` | pass |
| `derive/services_test.go` T3.1 | build failure: `unknown field Minute in struct literal of type ServiceCap` (no per tenant-minute entry existed) | pass |
| `test_erasure_inventory.py` | `no purge statement deletes from them … ['services_over_cap_1m']` | 9 passed |
| `traceassembly/overcap_test.go` T3.3 | build failure: `unknown field overCap in struct literal of type Pipeline` (nothing wrote the record) | pass |
| `metricwriting/flatten_test.go` T4.1 | `DOOR_RUM: wrote [synthetic.check.status synthetic.check.status page.load], want [page.load]; syntheticRefused = 0, want 2` (same for `DOOR_OTLP`); resource claim: `wrote 1 sum and 1 histogram rows, refused 0` | pass |
| `metricwriting/pipeline_test.go` drop log | build failure: `reportSyntheticRefused undefined` | pass |
| `logwriting/flatten_test.go` T4.3 | `DOOR_RUM: wrote 3 rows, want 1 … wrote a nexovue-prober row through DOOR_RUM … syntheticRefused = 0, want 2` (same for `DOOR_OTLP`) | pass |
| `logwriting/stages_test.go` drop log | build failure: `reportSyntheticRefused undefined` | pass |
| `test_browser_door_live.py`, worker built from `d4410dc` | `a browser-door SERVER span was counted in service_stats_1m … assert 3 == 0`; `the over-cap record did not land within 180s`; `a forged synthetic reading reached metrics_gauge … assert 2 == 0` — 3 failed | new worker: 3 passed in 79 s |

Guards that held before and after, so could not go red: `TestTheCapBreaksTiesTheSameWayEveryRun`,
`TestACappedDrainIsByteReproducible` (T2.2), `proberdoor_test.go`, `capwriter_test.go`.

- **Go, `apps/workers`:** `go test -count=1 ./...` — 28 packages ok, 525 top-level tests passed, 0 failed.
  `go vet -tags storeprobe ./...` clean; `golangci-lint run ./...` 0 issues. Store probes against ClickHouse
  26.3.20.7: `TestTheOverCapRecordReadsBackOnceByTenantAndMinute` (one drain inserted twice reads back once)
  and F28's `TestVersionRowsSumToTheServiceRowsInTheStore`, both passed.
- **Migration `000039`:** applied with the other 38 by `cmd/setup clickhouse`, reverted (`clickhouse-down 1`,
  table gone), reapplied (table back, `SHOW CREATE` as written).
- **Python:** `test_erasure_inventory.py` 9 passed; `test_tenant_chokepoint_structure.py` with
  `test_cross_service_contracts.py` 67 passed; `tools/repo-audit` passed; `ruff check`/`format --check` clean on
  the changed files; `uv run mypy .` — no issues in 744 files.
- **Live, end to end** on a local stack (ClickHouse, Postgres and Kafka as containers `bd-ch`, `bd-pg`,
  `bd-kafka` on network `bd-net`, non-default ports; receiver, worker and API from source; torn down after):
  a forged `SERVER` span through `/browser/v1/traces` landed in `spans` and in none of the three rollups,
  while the secret-key control counted 3 in each (so AC15's sum held for it); 2,001 minted `aaa-*` services
  plus a 5-request `zzz-busy-*` kept exactly 2,000 services with `zzz-busy-*` among them, and
  `services_over_cap_1m` held the tenant-minute; forged `synthetic.check.status` and evidence through both the
  browser door and a secret key reached neither `metrics_gauge` nor `logs`, while a reading published the
  prober's way (`publish.build_envelope`, `DOOR_SYNTHETIC`, straight to Kafka) landed. The worker logged the
  over-cap WARN and one drop WARN each for metrics and logs. With the receiver unreachable the suite raised
  `RuntimeError: the live browser-door suite cannot run …` rather than skipping.

**Not run:** `just check` (the coordinator runs it); `just chaos` (needs the dev stack; owed once, per the
plan); `test_erasure_live.py` (needs the full dev stack with SeaweedFS). The live suite ran on this local
stack, not `just dev`'s; the scheduler's own prober did not run (its envelope was built with its code).

## Specification impact

None. The scope is the founder's, verbatim (foundation section 9, item 7). Open decision 5 is the one place
an answer could widen it, and it goes to the founder for exactly that reason.
The founder answered decision 5 yes on 2026-11-07, so the scope now covers evidence logs as well as readings;
built that way.

## Documentation impact

Found while reading; recorded here rather than editing other features' documents (this change may not):

- **F19's `status.md` disagrees with itself about P2's owner:** `slo-tracking/status.md:324` says "P2 still
  has no owner", while `:268` and `:332` say F28 owns it (foundation item 22). The foundation settles it;
  the stale line at `:324` is F19's to fix when it next updates.
- **F28's documents call this "the F6 change"** (`deployment-release-tracking/design.md:250,784`) because it
  was expected to land under F6; the foundation then made it this standalone record. Same change, older
  name; no content conflict.
- **F28's plan expects to add its own tasks for this change "when this plan is next revised"**
  (`deployment-release-tracking/plan.md:279`): when F28 builds `service_versions_1m`, it must extend this
  record's live test to assert the third table (plan T1.3).
- `docs/changes/README.md` gained this record's line. Also noticed, not fixed here: that README lists nine
  records twice (from "Fresh rebuild check defects" through "Tests do not need the demo") — a defect in the
  list itself, left for its own small change.
- **Updated by the build (2026-11-07):** `architecture/current-system-reference.md` (§5.3–5.5 and the package
  table), `architecture/security-and-tenancy.md`, `architecture/database.md`, `engineering/performance.md`
  (sort-key line; the standard was already stale for `span_trace_lookup`), ADR-025 point 5, new ADR-026 and
  its index line, `product/phase-2-tracker.md`, one line each in F28's and F19's `status.md`. F28's plan T1.3
  extension is now done here; F28's plan still says it will add it (`deployment-release-tracking/plan.md:279`),
  which is F28's to update when it next revises.
- **Noticed, not fixed:** `logwriting/flatten.go` carries an orphaned `hexID` doc comment just above
  `countRecords` (pre-existing).

## Last updated

2026-11-02 — record created; state Proposed; next action: founder approves `plan.md`.
2026-11-07 — founder approved the plan and answered decisions 1 to 5 as recommended; state Approved.
2026-11-07 — all four steps built and validated (above); state Implemented; next action: coordinator review,
`just check`, commit.

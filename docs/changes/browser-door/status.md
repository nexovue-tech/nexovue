# Status: browser-door — the browser door stops counting in the rollups

## Lifecycle status

Proposed

## Current task

None in progress. The change record and plan are written; no code has been edited.

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

## Next action

Founder approves `plan.md`, and answers open decisions 1 and 4 below (they shape plan Steps 3 and 4;
Steps 1 and 2 are not blocked by them).

## Open decisions

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

## Blocked

- Plan Step 3 on open decision 1; plan Step 4 on open decision 4 (and T4.3 on decision 5). Steps 1 and 2
  only await plan approval.

## Risks

- **Branch ordering.** This worktree is cut from `feature/phase-2`. The foundation orders F6's merge first
  (its findings and migrations 0041-0050 exist only on that branch); where this change's branch lands
  relative to F6 is the coordinator's to sequence. The change itself touches none of F6's files, but
  `service_stats_1m` (`000035`) must exist wherever it lands.
- **Per-drain cap grain** (open decision 2): "busiest" is per worker-drain, not global. Accepted knowingly
  or not at all.
- **F28 coupling:** the filter point covers `service_versions_1m` only because F28 feeds it from
  `observeService` (its design D55). If F28's implementation moves that point, its both-tables test is what
  catches it; this record's live test must then be extended to three tables.
- Nothing here was run; every claim is from reading the code in this worktree.

## Validation

Nothing run yet — this record holds only document work. The plan's validation section names what will run:
per-module Go tests with `-count=1`, `just check`, the new live end-to-end test, and `just chaos` once.
Each behavior change's test must be seen failing before the fix lands, with the failure output recorded
here.

## Specification impact

None. The scope is the founder's, verbatim (foundation section 9, item 7). Open decision 5 is the one place
an answer could widen it, and it goes to the founder for exactly that reason.

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

## Last updated

2026-11-02 — record created; state Proposed; next action: founder approves `plan.md`.

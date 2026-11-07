# ADR-025: Version numbers are counted before sampling, in their own rollup

## Status

Accepted 2026-11-02 with the F28 design ([`design.md`](../features/deployment-release-tracking/design.md),
*Proposed ADRs*, the first), which the founder approved; filed at implementation start, 2026-11-07. Its cost
is measured by plan Step 1a's gate, and the founder's decision 24 (`phase-2-foundation.md`, *Founder
decisions*) says what happens if that gate fails.

## Context

A release is judged fairly only by comparing the old and new versions at the same moments. Two facts in
the code stood in the way:

- **Storage is sampled, and skewed.** The tail sampler keeps about one healthy trace in ten and every slow or
  failing one (`sample/policy.go`). A version split read from stored spans therefore reads failures roughly
  ten times too high, and an earlier design built that way measured wrong by an order of magnitude.
- **The exact rollups hold no version.** The worker has `service.version` on every span before sampling
  (`traceassembly/flatten.go`), and dropped it before the exact counts (`traceassembly/stages.go`,
  `countable`). `service_stats_1m`, which F6 judges, is per service and minute only.

## Decision

1. **The worker writes `service_versions_1m`** (ClickHouse migration `000038`) from exactly the spans that
   fill `service_stats_1m`, before sampling, with the same 64 latency buckets. It is fed from the same call
   (`derive/services.go`, `observeService`), so for one service and minute the version rows sum to the service
   row (F28 FR15, FR16).
2. **Detection's table is untouched.** No version column is added to `service_stats_1m`.
3. **Free-text versions are bounded by folding into `__other__`, never dropped:** 20 versions per service per
   drain by most requests, and at arrival 64 per partition, service and minute and 512 per tenant, partition
   and minute, first come; a version over 200 characters or failing the shared name check folds too
   (`derive/versions.go`). Every value is a starting guess.
4. **A failing write to it never holds detection or ends the worker.** It is inserted last; a refusal is
   counted and holds no progress; a transient failure has its own 30-second budget, then is stepped over and
   paused for 5 minutes (`traceassembly/pipeline.go`, `insertVersions`; F28 D13).
5. **A browser-door span counts under no version** until the browser-door change
   (`docs/changes/browser-door/`) keeps such spans out of both rollups (F28 D12, D55).

## Consequences

- An exact comparison by version and a rollout's share over time, with no deploy webhook, for any customer
  whose spans carry `service.version`.
- One more table written on every drain, and one more map in the worker's memory. Its rows, disk and memory
  are measured in plan Step 1a, on a development machine; the whole-server fit waits for Oracle.
- A failed write leaves a hole that is not repaired. The comparison labels it, by checking each minute's sum
  against `service_stats_1m` (FR28).
- Caps are per drain and first come at arrival, so one version's count can be short while totals stay exact.
  Any `__other__` request in a compared window makes the comparison say "Versions were combined" (FR17).

## Rejected

- **Comparison by time alone:** cheap, and wrong during a rolling deploy, when old and new serve the same
  minutes (design section 5). It stays as the labelled fallback.
- **Widening `service_stats_1m` with a version:** detection's reads would sum more rows, and a cap's folding
  could change what F6 judges. F6 was built and measured against its table.
- **Reading the version split from stored spans:** skewed by the sampler, as above. Fine only for questions
  of presence, such as which errors appear in a version.

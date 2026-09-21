# Status: one-worker-again

## Lifecycle status

**Released to `main`** as `c78e6f9`, "Profiles go back into the worker, carrying both memory budgets", after the founder read the diff on
2026-09-21. Not deployed — nexovue has no deployment target.

## Current task

None.

## Completed

- **`worker-profiles` deleted from all three compose targets**; `worker` runs `WORKER_PIPELINES:
  all`. Its air config (`deploy/air/worker-profiles.toml`) is gone with it.
- **The memory went with the pipelines.** Merging two processes merges two budgets, so the surviving
  worker carries both: **1280M** in deployment (768 + 512) and **1536M** in development, with
  `GOMEMLIMIT` at ~85% of each (1088MiB and 1300MiB). Without this the merged worker would have met
  the September incident sooner than the pre-split worker did.
- **The on-premise worker had no `GOMEMLIMIT` at all** — a limit with no ceiling under it, which is
  the incident waiting for a busy day. Added, at the same ratio.
- **`test_compose_signal_isolation.py` deleted** (it existed only to hold the split) and replaced by
  **`test_compose_worker_memory.py`**, which holds the invariant that now does the protecting: every
  Go service declaring a memory limit must declare a heap ceiling at about 85% of it, in every
  target. Seen red first — raising the dev ceiling to 1800MiB fails it at 123%.
- **Documents amended** rather than left to drift: the profiling spec's **NFR1 and AC15** (what is
  promised is now the door, not the process), its design and status, `docs/architecture/{system,
  deployment,current-system-reference}.md`, and `docs/operations/production-readiness.md`'s two
  lines about the split.

## Next action

None — this is in history.

## Open decisions

None. Splitting again is one environment variable and a memory limit, if the trade is ever
revisited.

## Blocked

Nothing.

## Risks

- **A profile flood that exhausts the worker's memory now stops spans, logs and metrics too**, for
  every tenant, until Kafka's backlog drains. This is the September incident, made possible again on
  purpose. What still stands between a flood and that outcome: the receiver's per-key
  samples-per-second budget and rate bucket, a 4 MiB bound on what the worker fetches at once, the
  larger budget above, and a restart that takes seconds.
- **A flood spread across many keys was never tested** — one key's flood is throttled at the door
  and the worker holds. That untested case now risks every signal rather than profiles alone, and is
  recorded in `docs/features/profiling/status.md` as such.

## Validation

- **The dev stack runs it.** One `worker` container, healthy, and its own log names all eight
  pipelines at boot — `trace-assembly`, `log-writing`, `metric-ingest`, `profile-ingest` and the
  four usage meters. No `worker-profiles` container remains.
- **Profiles still arrive through it end to end**: `profiles.spec.ts`, 3 passed — a profile sent
  with a new account's key becomes a flame graph, searchable and metered.
- `just check`: **4114 passed, 3 skipped**; format, lint, `mypy`, Go and the contract checks clean.
  - The one failure is the same environmental one recorded in
    `docs/changes/console-review-fixes/status.md`: `test_alert_telemetry_live.py::test_error_groups_
    come_back_with_the_errors_screens_identity` needs a span carrying an exception *event* in the
    last 24 hours, which only `just otel-demo up` produces here. Proved unrelated by running it on a
    clean worktree of `HEAD`, where it fails identically.
- `just e2e` was run green before this change; the worker is the only thing it touches, and the
  profiles spec above is the part of it that exercises the merged pipeline.

## Specification impact

`docs/features/profiling/spec.md` — **NFR1** and **AC15** amended. The promise is now that a flood is
refused or throttled at the receiver; it is no longer promised that a flood surviving the door leaves
the other signals running.

## Documentation impact

`docs/architecture/system.md`, `deployment.md`, `current-system-reference.md` (§3 topics, §5 worker),
`docs/operations/production-readiness.md`, `docs/features/profiling/{spec,design,status}.md`.

## Last updated

2026-09-20

# ADR-028: Reliability target judging runs on `scheduler-light`, under the shared floor

## Status

Accepted 2026-11-02 by the founder (F19 D66, superseding D39's `scheduler-slo`), with the F19 design
([`design.md`](../features/slo-tracking/design.md), *Proposed ADRs*, the fourth); filed at implementation start,
2026-11-07 (F19 plan Step 1, T1.5).

## Context

ADR-019 allows a role per container on one image. `scheduler-light` holds probes and alert sweeps. A cap per
queue alone does not protect them: the worker claims capped queues first and the uncapped lane last, with what
is left (`jobs/worker.py`, `_claim_into_idle_slots`). Detection (F6), evidence (F3) and releases (F28) already
ask for 6 of its 8 slots, and F19 asks for 4 more. The founder decided that F19 adds no container.

## Decision

1. Queues `slo` (cap 2), `slo_counts` (1) and `slo_history` (1) run on `scheduler-light`, in its default
   queues and `QUEUE_CAPS` (all guesses).
2. **The shared floor** (F3 A56) is built with them, since F19's Step 1 came first: a worker given
   `uncapped_floor` lets all its capped queues together hold at most `concurrency - uncapped_floor` slots,
   and each pass starts from the next capped queue so none starves. `scheduler-light` runs
   `--concurrency 8 --uncapped-floor 4` (`roles.py`, the development Compose file), so probes and alert sweeps
   keep 4 slots whatever the capped queues ask. Other roles default to a floor of 0.
3. `scheduler-light` keeps its 8 slots and its 768M ceiling, a guess until F19's AC50 measures it.

## Consequences

- No new container, and 384 MB less ceiling than D39's.
- Probes and alert sweeps never wait behind SLO work. SLO work waits behind detection, evidence and release
  reads, and is judged late, not never: jobs expire at 60 seconds and are handed out longest-waiting first.
- A read that crashes the container (an OOM kill) stops probes and SLO judging together.
- Idle slots while capped work waits.

## Rejected

A new role, `scheduler-slo` (D39), by the founder's decision. 12 or more slots on `scheduler-light`: 12
children at the 64 MiB child limit is the whole 768 MiB ceiling; kept as the fallback if AC50 fails. F3's own
proposed ADR on the floor (`ai-insights/design.md`, *Capped queues share one floor on a shared worker*) is F3's
to file with its design; this record covers the floor as F19 built it.

# Change: one-worker-again — profiles go back into the worker every other pipeline runs in

## Classification

Infrastructure (a deployment-topology reversal).

## Current behavior and problem

Every compose target runs two worker services from one image: `worker`
(`trace-assembly,log-writing,metric-ingest`) and `worker-profiles` (`profile-ingest`). They are the
same binary; `WORKER_PIPELINES` is the only difference.

The split was made on 2026-09-19 after a measured incident: a profile flood from one organization
held more decoded samples than the worker's 1 GiB cap, the kernel killed the process, and spans,
logs and metrics stopped with it **for every tenant** until the backlog drained. A process of its
own made "profiles cannot delay spans" true (F17, NFR1, AC15), and
`apps/api/tests/test_compose_signal_isolation.py` held it in place.

**Founder decision, 2026-09-20: run one worker, like every other service.** Two containers for one
binary is a shape nothing else in this stack has, and it is not worth its operational surface at
this size.

## Intended behavior

- **One `worker` service**, running every pipeline, in every compose target.
- **It carries the memory both used to have.** This is the part that must not be skipped: merging
  two processes means merging two budgets. The production cap becomes 1280M (768 + 512) with
  `GOMEMLIMIT` at ~85% of it, and development's becomes 1536M for the same reason plus the compiler
  the dev containers host. A merged worker on the old single budget would meet the September
  incident sooner than the pre-split worker did, not later.
- **What is given up, stated plainly:** a profile flood that exhausts the cap now stops spans, logs
  and metrics again, for every tenant, until Kafka's backlog drains. The bound that remains is the
  fetch limit — profile fetches are capped at 4 MiB — and the restart itself, which is fast. The
  risk is accepted knowingly, not overlooked.

## Affected specifications and architecture

- **F17's NFR1 / AC15** ("profiles cannot delay spans") is no longer met by process isolation. The
  specification is amended to say what is true, and `status.md` records the residual risk.
- `docs/architecture/{system,deployment,current-system-reference}.md` each describe two worker
  services.
- `docs/operations/production-readiness.md` cites the split as a health-check and blast-radius
  answer; both lines are rewritten.
- `apps/api/tests/test_compose_signal_isolation.py` exists only to hold the split and is deleted. It
  is replaced by a test of the invariant that now does the protecting: a worker's `GOMEMLIMIT` must
  be about 85% of its container's memory limit, in every target. That rule was only ever stated in
  comments, and it is what stands between a busy worker and the kernel.

## Risks and rollback

- **The risk is the incident itself, and it is the point of the change.** Recorded in `status.md`
  rather than mitigated away.
- **Rollback is `git revert`** — no schema, no store, no data. `WORKER_PIPELINES` is read at
  startup, so splitting again is a compose edit and a restart.

## Plan and validation

One step, since it is a deletion: the three compose files, the air config, the tests, then the
documents. Validation in [status.md](status.md).

## Status

In development.

# ADR-011: The API dispatches run-now tasks to the scheduler's broker

## Status

Accepted 2026-09-17, with the F18 design. Executed in F18: `POST /check-runs` dispatches, `GET /check-runs/{id}` waits up to 25 s for the result; the task's arguments are held against the worker's signature in `test_cross_service_contracts.py`. **Amended 2026-10-10** by ADR-020 — see *Amendment*.

## Context

F18's Run now (spec FR30, AC24) runs a check once, saved or unsaved, and shows the result to the person who
asked — an interactive action, expected within the check's time limit plus a stated overhead (NFR2).

Checks run in the scheduler. **The API has no way to ask the scheduler for anything today**: it has no Celery
or broker client, and the scheduler learns about checks only by re-reading Postgres on beat's refresh timer.
The API does already connect to Redis, for sessions and rate limits.

## Decision

1. The API dispatches a run-now task **by name with Celery's `send_task`**, onto the queue for the chosen
   location — without importing any scheduler code.
2. The task runs exactly as a scheduled run does, but is marked `run_now`: it **publishes nothing and writes no
   state**.
3. It stores its result in Redis under a request id, with a short expiry.
4. The console polls `GET /checks/runs/{request_id}`.
5. **Run now has its own rate quota**: each call makes outbound requests from our infrastructure to an address
   the caller chose.

## Rejected

- **A Postgres row that beat picks up.** Beat re-reads on a timer, so the wait is its refresh interval — too
  slow for an interactive button.
- **An HTTP endpoint on the scheduler.** A second inbound surface on a service that has none, needing its own
  authentication, for something the broker already carries.
- **Running the check inside the API.** Would put outbound probing of customer-chosen addresses in the control
  plane, beside the database credentials and signing keys — the process that should make no such requests.

## Consequences

- **The API gains a runtime dependency on the broker.** If Redis is down, Run now fails — which it would
  anyway, since scheduled checks stop too.
- The API and the scheduler now agree on task names and argument shapes. That is a contract, and belongs in
  the cross-service contract tests (`apps/api/tests/test_cross_service_contracts.py`).

## Amendment — 2026-10-10: Run now dispatches through the `jobs` table

**What changed.** Redis and Celery are gone (ADR-020, `docs/changes/redis-goes/`). The API no longer has a
broker client: it inserts a Run now job into the Postgres `jobs` table — through the contract in
`nexovue_platform/jobs.py`, still without importing scheduler code — with an expiry of 60 seconds, and sends
`NOTIFY` on `nexovue_jobs` so an idle worker picks it up at once. The worker writes its result to `job_results`
(kept 300 seconds), and the API reads it filtered by organization, request id and kind. Send test works the same
way.

**What does not change.** Decisions 2, 4 and 5: the run publishes nothing and writes no state, the console
polls, and Run now keeps its own quota.

**Consequence.** The API's runtime dependency on the broker is gone; Run now depends on Postgres, which the API
needs anyway. There is no "broker not configured" refusal any more; a database failure is a `503` naming the
dependency.

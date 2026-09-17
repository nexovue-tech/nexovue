# ADR-011: The API dispatches run-now tasks to the scheduler's broker

## Status

Accepted 2026-09-17, with the F18 design. Executed in F18: `POST /check-runs` dispatches, `GET /check-runs/{id}` waits up to 25 s for the result; the task's arguments are held against the worker's signature in `test_cross_service_contracts.py`.

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

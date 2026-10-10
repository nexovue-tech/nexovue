# Status: redis-goes

## Lifecycle status

Approved. In development.

## Current task

Step 1 — login state in Postgres.

## Completed

- Research and founder decisions recorded in `change.md`.
- Design approved, 2026-10-10 (`d121fc7`).
- Plan written from a code inventory (`plan.md`).

## Next action

On plan approval: step 1, task T1 — migration `0037` with the eight tables.

## Open decisions

Two non-blocking assumptions in `plan.md` Questions (nullable `org_id` on `jobs`; the queue contract in
py-platform as SQL text).

## Blocked

None.

## Risks

See `plan.md` New risks.

## Validation

None run yet.

## Specification impact

None.

## Documentation impact

ADR-011 and ADR-020 to amend; architecture pages naming Redis or Celery — task T31.

## Last updated

2026-10-10

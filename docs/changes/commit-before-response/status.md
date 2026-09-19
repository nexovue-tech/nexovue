# Status: commit before response

## Lifecycle status

Verified

## Current task

None — awaiting the founder's review of the diff.

## Completed

- `DbSession` is `Depends(get_db_session, scope="function")` (`apps/api/src/nexovue_api/deps.py`): the
  session commits, or rolls back, after the route function and before the response is sent.
- Audit of what uses the session past the route function:
  - `DELETE /organizations` (`organizations/router.py`) hands its background erasure
    `resources.session_factory`, not the request's session — unaffected, and now its erasure
    obligation is committed before the task that relies on it starts.
  - The log-tail WebSocket (`logs/tail.py`) takes the session only through `CurrentIdentity`; for a
    WebSocket the "function" is the whole connection, as the request was before — no change.
  - No `StreamingResponse` route reads the session.
- FastAPI's scope rule — a request-scoped dependency may not depend on a function-scoped one — holds:
  `get_db_session` is the only yield dependency in `apps/api`, and the app starts.

## Next action

Founder: read the diff, then say **push**.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The log-tail WebSocket holds the request's database session, through `CurrentIdentity`, for the
  life of the connection — not introduced here, and not measured. Its docstring says it avoids a
  database dependency to keep from holding a pooled connection. Worth a reading of the pool under
  several open tails before it is relied on.

## Validation

- `test_db_session_commit_order.py`: failed before the fix (`['handler', 'response', 'commit']`),
  passes after.
- Live, after the API reloaded: `repro_commit.py` 60 rounds → `missing right after 201: 0/60`
  (before: 1/40). No `repro-` channels left behind.
- `pnpm --filter @nexovue/e2e test alerts.spec.ts logs`: 5 passed in the parallel run, including the
  test that failed and the live tail.
- API suite: 3,163 passed, 3 skipped. One earlier `-x` run failed once on
  `test_metrics_live.py::test_a_name_arriving_in_two_shapes_is_not_called_readable` (ClickHouse, no
  Postgres session involved); it passed alone and in the full run.
- `just check`: first run failed 8 `test_metrics_live.py` tests on `MEMORY_LIMIT_EXCEEDED` — development
  ClickHouse at 4.0 of 4.0 GiB resident with ~1 GiB allocated, the allocator-retained creep
  `deploy/clickhouse/config.d/memory.xml` documents; no Postgres session is involved. After restarting
  ClickHouse: exit 0 — 3,555 Python passed (3 skipped), web 1,264, browser SDK 120.

## Specification impact

None.

## Documentation impact

`docs/changes/signup-session-before-commit/` described the per-route fix; this change removes the
cause it worked around. That record is history and is left as written. Also corrected while here:
`docs/features/synthetic-checks/status.md` and `docs/product/feature-catalog.md` still said F18 was
uncommitted — it was committed as `75b5424` on 2026-09-18.

## Last updated

2026-09-19

# Status: clickhouse-protects-itself

## Lifecycle status

Implemented and verified on the development stack. Committed; not pushed.

## Current task

None.

## Completed

- **Step 1 — every read bounded in memory.** `READ_SETTINGS` gains `max_memory_usage` 1 GiB and
  `max_bytes_before_external_group_by` 512 MiB. `test_read_settings_live.py`: red `assert 0 ==
  1073741824` (the server ran a read with no memory cap), green after.
- **Step 2 — readers can only read.** `deploy/clickhouse/users.d/reader.xml`: `nexovue_reader`,
  `readonly = 2`, `GRANT SELECT ON nexovue.*`, memory and time capped with minimum and maximum
  constraints. `test_clickhouse_reader_live.py`, 11 cases: INSERT, ALTER DELETE, CREATE, the `url()`
  function, DROP and TRUNCATE (on a throwaway table) refused; `max_memory_usage` and `max_execution_time`
  refused at 0 and above their caps. Red with the minimums removed: the two `= 0` cases accepted.
  The API reads through it (`CLICKHOUSE_DSN`) and erases through `CLICKHOUSE_WRITER_DSN`
  (`organizations/dependencies.py`, pinned by `test_erasure_uses_the_writer.py`, red then green);
  `scheduler-light` reads through it. All three compose files updated; both deployment files demand
  `CLICKHOUSE_READER_PASSWORD`, and render with every required variable supplied.
  Live, against the recreated stack: the API reports ready as `nexovue_reader`; the tenant-isolation
  suite (31 with deletion, over HTTP to the running API) and the scheduler's alert suite (4) pass.
- **Step 3 — the live tail.** Opens only on a project the caller's organization owns, checked with a
  short-lived session; at most 5 per person and 20 per organization in a process. Red then green for
  both. Live over a real WebSocket: a made-up project → `failed: That is not a project.`; the own
  project → `ready`; a sixth from one person → `failed: You already have 5 live tails open. Close one to
  open another.`
- **Step 4 — the LLM-calls read capped at 500 groups and says so.** Red: 501 returned uncut. The screen
  notes when the list is cut (`LLMCallsView.test.ts`, red with the notice hidden).
- **Step 5 — hosted scheduler memory.** `scheduler-light` 768M, `scheduler-deliveries` 384M in
  `docker-compose.app.yaml`, as dev measured.

## Next action

Group 2b (`redis-goes`) design.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Limits are laptop sizing**: 1 GiB a read, spill at 512 MiB, 5 tails a person, 20 an organization.
  Re-measured at the group 8 load test.
- **`readonly = 2` lets a reader change settings**; the profile's constraints are what bound them.
- **The writer DSN is one more secret** for every deployment.
- **The organization-wide ceiling for API tokens is not built** — moved to 2b with the rate-limit
  redesign.

## Validation

`just check`: exit 0 — lint 0 issues, `mypy` no issues in 585 files, Go 36 packages `ok`, `gateseed`
12.6 s, Python 4198 passed and 3 skipped, web 1352, browser SDK 120. Plus the live checks above.

## Specification impact

None.

## Documentation impact

`production-readiness.md`: blockers 14 and 23 marked fixed on the development stack; the read-only
user, live-tail and LLM-calls recommendations marked done.

## Last updated

2026-10-10

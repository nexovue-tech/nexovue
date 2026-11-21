# Status: test workers hold memory

## Lifecycle status

**Implemented** — the three causes in [bug.md](bug.md) fixed and measured on the development machine;
not yet seen on a GitHub runner.

## Current task

None.

## Completed

- `_signal_cases` shares a case's minutes across its judgements (`test_detection_method.py`).
- The chokepoint and scheduler Postgres tenancy suites read parsed source through module-scoped
  fixtures; the Postgres tenancy suite parametrizes by file, name and line and parses at test time.
- `tests/helpers/fastapi_caches.py`, registered in `addopts`, empties FastAPI's dependency caches
  after each test; `apps/api/tests/test_fastapi_caches.py` proves both FastAPI's behaviour and the
  release.
- `docs/engineering/testing.md` states the collection rule.

## Next action

Founder: read the diff; then push, and watch CI's `check` finish with `-n 6`.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Not measured on GitHub's runner**, which left no log in the stalled runs. The development numbers
  below say six workers now need about 4 GB at their peak rather than about 13–14 GB; that the runner
  froze from memory is inferred from the missing log and the unfired timeouts.
- `fastapi_caches` reads FastAPI's private module. If FastAPI renames it the plugin does nothing,
  quietly; `test_fastapi_keeps_an_override_s_closure_after_its_app_is_gone` is what would notice a
  change in the behaviour, not in the name.
- **Found while verifying, not fixed here:** `test_detection_memory_live.py`'s gather test writes 2,500
  `spans` and 2,000 `service_edges_1m` rows for its tenant and never deletes them (its `project`
  fixture registers only the detection tables). That tenant becomes the busiest project in `spans`,
  and `test_traces_live.py`'s `busiest` fixture then finds no failed hour, so all 20 of its tests
  error — on any stack after one full run, and within one run whenever the same worker runs the
  memory suite first. Fixing it means choosing between deleting those rows in the test and making
  `busiest` independent of other tenants; left for the founder.
- `test_fix_source_pack.py` failed once in each full run, on a different test each time (`ac82`
  before the change, `ac84` after) and passed alone: an intermittent failure unrelated to memory.

## Validation

Measured 2026-11-21 with a temporary plugin (removed) recording each worker's `VmRSS` before the first
and after the last test of each file; `pytest -n 2 --dist loadfile` against the dev stack.

| | Before (a1ea4ba) | After |
|---|---|---|
| Collection alone, one process | 1,831 MB | 359 MB |
| Collection time | 28 s | 9 s |
| A worker before its first test | 1,846 / 1,850 MB | 367 / 367 MB |
| A worker's peak | 2,404 / 2,258 MB | 714 / 639 MB |
| Live objects after eight API suites, one process | 1.65 million | 0.79 million |

Each curve flattens in its last half (after: 643→701 MB and 609→639 MB over the last five deciles of
files); what remains is first-time imports and allocator high-water, not retention.

- Full suite after: 16,049 passed, 6 skipped, 2 xfailed. Failed or errored: `test_connect_guard.py`
  (needs internet DNS; also before), `test_traces_live.py` ×20 (the leftover tenant above; 21 passed
  once its rows were deleted), `test_tenant_isolation_live.py` ×2 (the gate seed had aged past its
  window; passed after `just gate-seed`), `test_fix_source_pack.py` ×1 (intermittent, above).
- The changed suites: 4,281 + 2,851 + 14 passed (the same counts as before), 21 passed in the
  chokepoint suite, 2 passed in `test_fastapi_caches.py`, and its second test seen failing with the
  clearing line removed.
- `uv run mypy .`: no issues in 1,214 files. `uv run ruff check .`: passed.
  `uv run ruff format --check .`: 1,615 files formatted.
- Not run: `just check` as a whole (Go, web, contracts), and CI.

## Specification impact

None.

## Documentation impact

`docs/engineering/testing.md` gains the collection rule; `pyproject.toml`'s `addopts` comment names the
new plugin.

## Last updated

2026-11-21

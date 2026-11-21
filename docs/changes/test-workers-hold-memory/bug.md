# Bug: test workers hold memory — each pytest worker grew to 2 GB and froze CI's runner

## Reported behavior

GitHub CI's `check` job (`just check`; `test-py` is `pytest -n 6 --dist loadfile` beside a full dev
stack) stalled on the last four runs (96cbd81, caf2737, a1ea4ba). No log survived, and neither
`test-py`'s `timeout 40m` nor the step's `timeout-minutes: 45` fired — what a runner frozen by
running out of memory looks like. 301f450 passed. On a development machine each xdist worker reached
1.9–2.1 GB, and earlier runs' kernel logs show the OOM killer stopping Python processes at about
2.0 GB.

## Reproduction

A temporary pytest plugin (not committed) recorded each worker's `VmRSS` before the first and after
the last test of every file, and a second one recorded RSS while each module was collected. Measured
2026-11-21 at a1ea4ba, `pytest -n 2 --dist loadfile` against the dev stack:

| | gw0 | gw1 |
|---|---|---|
| Before the first test | 1,846 MB | 1,850 MB |
| Peak | 2,404 MB | 2,258 MB |

**Most of a worker's memory was there before any test ran.** Collection alone, in one process: 89 MB
to 1,831 MB. xdist has every worker collect every file, and pytest keeps each collected item — with
its parameters — and every imported test module for the whole run, so this is paid once per worker:
six workers held about 11 GB of GitHub's 16 GB before the first test. At 301f450 the same
measurement was 1,812 MB; the stall commits added ~1,260 tests and new live API suites, whose growth
during the run (below) is what tipped a runner already at the edge. That last step is inferred: the
GitHub runner left no log to measure.

Then **the run itself grew each worker by 400–550 MB**, concentrated in the live API suites
(`test_releases_live` +87 MB, `test_dashboards_live` +82, `test_slos_live` +56 …). A per-file count
of live objects after a full `gc.collect()`, over eight of those suites run in one process, grew from
0.47 to 1.65 million — retention, not allocator high-water.

## Root cause

Three test-infrastructure causes. Product code does not leak: it builds one app with a fixed set of
dependencies.

1. **`test_detection_method.py`'s signal cases: 1,234 MB per worker.** `_signal_cases` rebuilt a
   case's 190-minute map for each of its 300 judgements: 3,600 parameters × 190 = 684,000 `Minute`
   objects, each with its own 64-element bucket tuple and endpoint dict, held by every worker all run.
2. **Three structural suites kept parsed source at module level: 279 MB per worker together.**
   `test_tenant_chokepoint_structure.py` (`API_MODULES`, `SCHEDULER_MODULES`: 141 MB),
   `test_scheduler_postgres_tenancy_structure.py` (`SCHEDULER_MODULES`: 70 MB), and
   `test_postgres_tenancy_structure.py`, whose 2,851 parameters were the API's function nodes and so
   held every API module's tree (76 MB).
3. **FastAPI kept every test's dependency overrides, and through them its session and engine.**
   `fastapi.dependencies.models` (0.162.0, unchanged on upstream master at 0.165.0) classifies each
   dependency callable through three module-level `lru_cache(maxsize=4096)`s keyed by the callable
   itself. The API's live suites build an app per request and override dependencies with closures
   over the test's session. Traced with `objgraph.find_backref_chain`: module
   `fastapi.dependencies.models` → `_lru_cache_wrapper` → `_CallIdentity` → `_client.<locals>.db_override`
   → cell → `AsyncSession` → `AsyncConnection` → `AsyncEngine`, whose compiled-statement cache is
   the bulk (`ResultColumnsEntry`, `BindParameter`, `_ColumnsPlusNames` led every file's growth).
   Ten of a 13-test file's engines were still alive at the end of it.

Collection-time imports of the product code (~350 MB) are the floor every worker needs and are not
a defect.

## Specification impact

None.

## Regression test

`apps/api/tests/test_fastapi_caches.py`. It serves one request with an override that closes over an
object and asserts, with a weak reference, that FastAPI keeps the object alive, and that
`clear_dependency_caches()` lets it go. With the clearing line removed the second test fails
(`assert <_Held object> is None`); the first fails, deliberately, if a FastAPI upgrade stops keeping
the callables, so the plugin can be deleted then.

Causes 1 and 2 change only how a test reaches its data, not what it asserts: the same 4,281, 2,851,
21 and 14 tests are collected and pass.

## Fix plan and validation

1. `_signal_cases` builds each case's minutes once and shares them across its judgements, as
   `_parity_cases` already did.
2. The chokepoint and scheduler-tenancy suites read source through module-scoped fixtures, built
   only by the worker running the file and released when it ends. The Postgres tenancy suite
   parametrizes by a function's file, name and line and parses at test time, one file at a time.
3. `tests/helpers/fastapi_caches.py`, registered with `-p fastapi_caches`, empties every `lru_cache`
   in `fastapi.dependencies.models` after each test.
4. `docs/engineering/testing.md` states the collection rule.

Before and after numbers are in [status.md](status.md).

## Status

Implemented.

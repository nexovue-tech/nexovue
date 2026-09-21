# Plan: faster-gate

> **Status:** Approved by the founder, 2026-09-21 — "okay do it".

Three levers, each measured before and after on the founder's machine with a loaded dev stack.

## Step 1 — a teardown that deletes stops waiting to watch it happen

- [x] **T1** — `apps/api/tests/test_purge_live.py` · the `two_tenants` fixture registers its tenants
  with `clickhouse_cleanup.delete_after_run` instead of running a full purge per tenant per test and
  waiting on every mutation · **57s → 25s**
- [x] **T2** — `apps/api/tests/test_deletion_live.py` · the same for `two_tenants_with_telemetry` ·
  **34s → 23s**

The tests that *prove* a purge works still call it in their own body and still wait for it. What
stopped waiting is the cleanup, which nothing reads.

## Step 2 — the Python suite runs in parallel

- [x] **T3** — `pyproject.toml` · `pytest-xdist`
- [x] **T4** — `justfile` · `uv run pytest -n 6 --dist loadfile`, with the measurements in the
  recipe · **248s → 61s**, stable across three runs

Checked before the flag went in, not assumed: the migration suites each create a scratch database
with a UUID name, the leader suite keys its locks by UUID, and every live fixture provisions its own
tenant. `--dist loadfile` keeps a file's tests on one worker, so a fixture is never shared across
processes. Six is the measured floor — 71s at four, 61s at six, 63s at eight; past six the one
development stack is the limit.

## Step 3 — the e2e suite stops queueing behind one allowance

- [x] **T5** — `deploy/compose/docker-compose.yaml` · `TRUSTED_PROXY_COUNT: 1`, which is true of the
  development topology: the web container is the one proxy in front of the API
- [x] **T6** — `tools/e2e/tests/support/worker-client.ts` (new) · an address per worker, from
  Playwright's own `TEST_PARALLEL_INDEX`
- [x] **T7** — `tools/e2e/tests/support/accounts.ts` · both auth doors send it, so no spec has to
  know
- [x] **T8** — `tools/e2e/tests/support/auth-quota.ts` · one ledger per worker, since one worker is
  now one client
- [x] **T9** — `tools/e2e/playwright.config.ts` · six workers · **186s → 87s**

The limit is not widened: a worker still gets ten auth requests a minute and is still throttled if it
spends them faster. What changed is that four workers are counted as four callers, which is what they
are.

## Step 4 — close out

- [x] **T10** — `just check` and `just e2e` read for counts; `docs/engineering/` left alone (nothing
  it says about the gate changed); this record's `status.md` carries the before and after.

## Outcome

| | Before | After |
|---|---|---|
| `just check` | 284s | **95s** |
| `just e2e` | 186s | **87s** |
| **Both** | **470s** | **182s** |

Nothing was skipped, marked flaky, or weakened to get there: the same 4117 Python tests and 22 e2e
specs run, against the same real stores, with the same rate limits in force.

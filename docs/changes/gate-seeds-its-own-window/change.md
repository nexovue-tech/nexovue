# Change: gate-seeds-its-own-window — the gate stops going red with the passage of time

## Classification

Infrastructure — the pre-push gate's own reliability.

## Current behavior and problem

Five suites check the read paths against **real telemetry in ClickHouse** rather than fixtures.
That is deliberate and worth keeping: it is how we know the query behind the metrics screen
survives real histogram shapes, and it caught a `HAVING sum(sum(...))` that every offline test
passed. Each asks whether there is data inside a rolling window and **fails** when there is not,
because a skip is indistinguishable from a pass.

The data only ever came from generators run by hand: `just load`, and the OpenTelemetry demo's
twenty-one containers and ~2 GB for anything else. So the gate went red on a clean tree after two
quiet days.

**Measured 2026-09-21.** Three `test_metrics_live` tests turned red **six minutes** after a green
run on the same commit. The newest `k6.http_req_duration` point was `2026-09-19 06:38:56`; the run
was at `2026-09-21 06:44`. Nothing changed but the clock. Two `test_tenant_isolation_live` tests and
one `test_alert_telemetry_live` test were red for the same reason.

The cost is not the red X. It is that a red gate no longer distinguishes a defect from stale data,
so every run needs interpreting — and a gate that has to be interpreted is a gate people learn to
ignore. This session lost time to it twice.

## Intended behavior

**`just check` seeds the window it is about to assert on**, through the ingest path a customer uses,
and waits for what it sent to become readable before pytest starts.

`tools/gateseed` sends the three things the suites cannot find for themselves:

- **80 traces**, each its own trace id. More than one page (`MAX_PAGE` is 50 in the isolation
  suite), because a forged cursor cannot be tested when the first page exhausted the result.
- **One span carrying an exception *event***, which the worker promotes to `exception_type`. An
  exception recorded as a span *attribute* does not satisfy the error-group reads, which is why only
  the demo could produce this before.
- **A cumulative histogram**, several series and several rising exports each, so the read that must
  not sum cumulative points has a shape that can actually exhibit the defect.

It is not a load generator. `just load` still exists for measuring throughput.

**The seeded traces are deliberately slow (2.5s).** Tail sampling keeps every error, every trace
past `WORKERS_SAMPLING_SLOW_THRESHOLD` (2s as shipped), and one healthy trace in ten — so the first
attempt's 80 fast healthy traces became **11 rows**. Sending 800 instead would rest on a ratio
holding; sending slow ones is deterministic. Slow rather than *failing*, because 80 seeded errors
every gate run would put a fault nobody has on the Errors screen.

**`test_metrics_live` no longer names the demo's metric.** Four tests asked for
`k6.http_req_duration` by name. The subject is *a real histogram*, not that one, so a fixture
discovers the histogram with the most points — and requires `notEmpty(explicit_bounds)`, because a
real store holds bucketless histograms that these tests cannot fold. Both the seeder and the demo
satisfy it. The `len(bounds) == 15` assertion went with it: that was a fact about k6's ladder
wearing the clothes of a fact about the read.

## Affected specifications and architecture

None. No specification describes the gate. `docs/engineering/workflow.md` names `just check` as the
gate; what it runs is unchanged apart from the new prerequisite.

## Risks and rollback

- **The gate now writes to the development stack on every run.** 80 spans and 18 metric points,
  under their own service name (`gateseed`), so no real service's latency or error rate moves.
  They age out on the same retention as everything else.
- **The gate takes ~12.4s longer** (measured over three runs: 12.37s, 12.37s, 12.35s). Nearly all of
  it is the ingest path's own latency — Kafka, the worker's assembly window, the ClickHouse insert —
  not this tool.
- **`test-py` now depends on the stack being up.** It already did: these suites fail rather than
  skip without it. `NEXOVUE_SKIP_LIVE_TESTS=1` makes the seeder a no-op, as it already did for them.
- Rollback is removing the `gate-seed` prerequisite from `test-py`. The suites then behave exactly
  as they did, including going red with time.

## Plan and validation

1. `tools/gateseed/` — a new module with no external dependencies. The receiver reads OTLP/JSON on
   its server doors, so this is plain `net/http` and `encoding/json`: no SDK, no protobuf toolchain,
   small enough to read in one sitting.
2. `justfile` — `gate-seed` recipe; `test-py: gate-seed`; the module added to `go_modules` so it is
   vetted and tested with the rest.
3. `go.work` — the module added.
4. `apps/api/tests/test_metrics_live.py` — the `histogram_metric` fixture, five call sites.

## Status

See `status.md`.

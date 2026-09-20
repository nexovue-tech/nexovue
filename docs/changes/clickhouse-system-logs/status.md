# Status: ClickHouse's own logs fill the memory the product's queries need

## Lifecycle status

**Implemented and verified**, 2026-09-20. Awaiting the founder's review.

## Current task

None.

## Completed

- Measured the store rather than reasoning about it: 6.7 GiB of the 7.3 GiB on disk was ClickHouse's own
  system logs, against ~600 MiB of customer telemetry, inside a 4 GiB server cap.
- `deploy/clickhouse/config.d/system-logs.xml` — four logs nothing reads are off; the three an operator
  reads keep three days. Mounted for every target, because unbounded system logs would fill a production
  disk too.
- `deploy/clickhouse/config.d/dev-parts.xml` — `old_parts_lifetime` 30 s, **development only**, found
  necessary by measuring the first fix (see the record's *second cause*).
- `apps/api/tests/test_clickhouse_system_logs_live.py` — 7 tests, seen red then green.
- The development store repaired once by hand: the four tables dropped, the three truncated and given
  their TTL. A fresh store needs none of this.

## Next action

Founder: read the diff and say **push**.

## Open decisions

- **A purge rewrites every part of every telemetry table** (a tenant is not a partition, ADR-014). That is
  what makes the gate's purges expensive, and lowering `old_parts_lifetime` only shortens how long the
  cost is held. It is ADR-014's own unresolved consequence, it costs a customer nothing today, and it will
  cost a production node when tenants are deleted at volume. Worth its own record when that matters.

## Blocked

Nothing.

## Risks

- **The development store was repaired by hand, and the repair is not scripted.** Another machine that
  already has these tables keeps them until someone drops them; the test says so in its failure message,
  which is how the next person will find out. A fresh `just fresh` needs nothing.
- **Three days of `query_log` may be short during a long investigation.** It is a number in one file, and
  the reason to raise it would be an actual investigation rather than a guess.
- **`metric_log` remains on.** It is the one that replaced `asynchronous_metric_log`'s readings and is
  cheap by comparison, but it is still a row per second for ever within its TTL.

## Validation

- `test_clickhouse_system_logs_live.py`: **7 failed** against the store as it was (four disabled tables
  present, three kept tables with no TTL), **7 passed** after the configuration and the repair.
- Store, before → after the repair: `system` database 6.7 GiB → **51 MiB**; resident memory at idle
  3.04 GiB → **1.64 GiB** of the 4 GiB cap.
- **Two consecutive `just check` runs, no restart between them** — the thing that could not be done before:

  | | resident after | `code: 241` failures | result |
  |---|---|---|---|
  | before this change, run 1 | 3.41 GiB | 0 | passed |
  | before this change, run 2 | 4.11 GiB | 89 | **failed** |
  | after this change, run 1 | 2.85 GiB | 0 | passed |
  | after this change, run 2 | 2.75 GiB | 0 | passed |

  Superseded parts after two runs: 16,253 before, ~1,500 after. Memory now *falls* across runs rather
  than climbing. Python 4,057+ passed, web 1,315, Go 34 packages, in both runs.

## Specification impact

None. Store configuration, not product behaviour.

## Documentation impact

`deploy/clickhouse/config.d/memory.xml` still tells a reader to restart the container when the live
suites fail. That advice remains true for the allocator-retention half and is now rarely needed; the new
file names this record. Neither was rewritten, because both are still accurate about what they describe.

## Last updated

2026-09-20

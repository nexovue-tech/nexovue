# ADR-023: The scheduler writes detection readings to ClickHouse, through an account that can insert into that one table only

## Status

Accepted 2026-10-31, with the F6 design ([`design.md`](../features/anomaly-detection/design.md),
revision 2).

## Context

F6 judges each settled minute of each service. Three requirements call for keeping each judged minute:

- **BR5 and NFR4: a judgement stands, and can be reproduced.** The rollups change after a minute is judged,
  because a span up to 7 days late reopens its minute (`derive/counters.go`, `clocktrust.PastSkew`).
- **AC40: a finding stays explainable.** Its readings are what its page draws.
- **Volume.** At one row per service per minute for 35 days, a thousand services is about 50 million rows.
  That belongs in ClickHouse, not Postgres.

Until now the scheduler has only read ClickHouse, as `nexovue_reader`. That account is shared with the API,
and allows `SELECT` only (`deploy/clickhouse/users.d/reader.xml`). The tenant chokepoint and its structure
test know only about reads (`test_tenant_chokepoint_structure.py`).

## Decision

1. **The project job writes `detection_readings`**, one row per service per judged minute. The row holds the
   window's values, the baseline used, and each signal's verdict.
2. **It writes as a new account, `nexovue_detection_writer`,** which has `GRANT INSERT ON
   nexovue.detection_readings` and nothing else, and its own DSN. The reader account is never widened,
   because widening it would let the internet-facing API write into every table.
3. **The writer lives in the tenant chokepoint, built for one project** by `for_project`. Its row type has
   no tenant or project field; the writer fills both in from the project being judged. The structure test
   is widened to `INSERT INTO` and `.insert(`, and allows them only in that module.
4. **The table is a `ReplacingMergeTree` with a version column, read with `argMax`.** A minute rewritten
   after a crash is counted once. Postgres alone decides what is sent.
5. **Retention is the lesser of the plan's derived retention and 35 days.** The table is in the erasure
   inventory.

## Consequences

- A finding can be reproduced from the readings it was judged on, while they are kept. After that, from the
  values it stored itself.
- The install gains a third ClickHouse account to provision, rotate and test. Two live tests guard it:
  - written as project A and read as project B, the row is never found;
  - the API's account is refused an insert.
- **Rejected:**
  - Postgres for readings: the wrong engine for 50 million rows on a 16 GB install.
  - Reading the rollups again at explanation time: they change after judging.
  - Widening `nexovue_reader`: it is the API's account too.

# ADR-045: The API reads ClickHouse through its own bounded account

## Status

Accepted 2026-11-02 by the founder with the F20 design ([`design.md`](../features/dashboards/design.md),
*Proposed ADRs*, the third; spec NFR14, D45); filed at implementation, 2026-11-11 (F20 plan Step 5, T5.6b).

## Context

The API and the scheduler both read as `nexovue_reader` (`deploy/clickhouse/users.d/reader.xml`), and
nothing on the server limited what that account used in total. Each read may hold 1 GiB
(`nexovue_platform.tenant_statements`), and the server fails every query, the workers' inserts and the
scheduler's alert and detection sweeps included, once its own memory cap is reached
(`deploy/clickhouse/config.d/memory.xml`). The API's caps on dashboard scans
(`dashboardpanels/caps.py`) protect only its own connections: they hold neither across two API processes nor
for any other screen. A load test was the only safeguard for alerts, and a test cannot hold on customer data.

## Decision

The API reads through its own account, `nexovue_api_reader` (`deploy/clickhouse/users.d/api-reader.xml`):
the reader's profile, plus two server-side bounds on everything the account runs at once,
`max_memory_usage_for_user` (2.4 GiB) and `max_concurrent_queries_for_user` (12), each held `const` so no
query can lift it. The scheduler stays on `nexovue_reader`, outside both bounds. A read past the concurrency
bound is refused with `TOO_MANY_SIMULTANEOUS_QUERIES` (202), which a dashboard panel reads as `refused`; one
past the memory bound fails with `MEMORY_LIMIT_EXCEEDED` (241), read as `failed`.

Both setting names were read on the pinned ClickHouse 26.3 image's `system.settings`, and the refusal's
code was seen through `clickhouse_connect` there (`test_clickhouse_reader_live.py`). Both values are guesses
until F20's measurement (plan T5.7) sets them.

## Consequences

- At their worst, API reads cannot take the alert sweeps, detection or the inserts down with them.
- One more secret, `CLICKHOUSE_API_READER_PASSWORD`, created by `nexovue install` and added by `nexovue
  upgrade` to an install from before it (`deploy/nexovue`, `ACCOUNT_PASSWORDS`).
- **Every API screen shares the bound, not only dashboards.** Under load a screen's read can be refused or
  fail at the limit where it would not have. That is the point, and T5.7 looks for it.

## Rejected

API caps alone (they hold neither across processes nor for other screens); a load test before release as the
only guard (it cannot hold on customer data); a lower `priority` for the API's reads (it needs the
scheduler's reads to carry one too, and what pausing does to held memory is untested; D45).

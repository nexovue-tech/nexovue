# 18. Data layer design

- **Status:** Accepted — attribute storage superseded by [0034](0034-clickhouse-attributes-as-native-json.md)
- **Date:** 2026-08

## Context

Storage design was at risk of being driven by the shape of incoming data and by the number of
ingestion doors, which would put per-source branching into the schema and into every query.

## Decision

Four principles (detail in `../system-architecture.md` §5c):

1. **Everything becomes one of three shapes** — spans, logs, metric points — at normalization.
   Storage never learns that doors exist. Two exceptions: deploy events go to Postgres (low volume,
   joined against cases and findings), and session-replay blobs go to object storage indexed by
   `session_id`.
2. **Hybrid schema** — typed promoted columns for the fields every query touches, plus attributes
   in a flexible column so customers can send anything without a schema change. A JSON *string*
   blob is rejected outright: it destroys columnar reads. At this point the flexible column was
   specified as `Map(LowCardinality(String), String)`. Promotion rule: an attribute that becomes hot
   in queries gets promoted to a real column.
3. **Design backwards from the screens** — the UI's and insight engine's questions decide what is
   promoted, what gets a skipping index, and what gets a materialized view. Per-minute
   `endpoint_stats_1m` exists so dashboards read tiny tables and never scan raw spans.
4. **Three filtering gates** — receiver rejects garbage; tail sampling keeps 100% of error and slow
   traces and 1–10% of healthy ones, with metrics derived before sampling; TTL keeps raw 30 days,
   metrics 90 days, aggregates 13 months.

`tenant_id` is first in every `ORDER BY`.

## Consequences

One schema serves every door, and adding a door adds a normalizer rather than a table. Dashboards
are cheap because they read aggregates. The cost is that the promoted-column set is a standing
maintenance job — it has to be revisited as query patterns move.

The `Map` choice in principle 2 was reversed by
[0034](0034-clickhouse-attributes-as-native-json.md): attributes now use ClickHouse's native `JSON`
type. Everything else here stands.

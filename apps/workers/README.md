# workers

The processing side of the data plane. Consumes the normalized envelope from Kafka, turns it
into rows, and writes them to ClickHouse.

**One binary, many pipelines.** `WORKER_PIPELINES` selects which of `trace-assembly`,
`log-writing`, `metric-derivation` and `service-edges` a process runs (ADR 0050). An unknown
name fails at boot rather than silently not running. In production the same image runs as many
times as needed with different selections — splitting a deployment is configuration, not a
rewrite.

## Pipelines

| Pipeline | State | What it does |
|---|---|---|
| `trace-assembly` | **built (M1)** | `spans-raw` → `spans`, write-through with no buffer (ADR 0054) |
| `log-writing` | M3 | `logs-raw` → `logs` |
| `metric-derivation` | M2 | exact counters from all traces, before sampling |
| `service-edges` | M3 | caller→callee edges derived from cross-service spans |

## Running

Part of the dev stack: `just dev`. Configuration is read once at boot and validated, so a
missing or malformed variable stops the process with that variable named rather than failing
later somewhere confusing.

Writes use `ch-go` (not `clickhouse-go`), which is what ClickHouse recommends for insert-heavy
paths (ADR 0027).

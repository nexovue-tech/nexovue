# workers — the processing pipeline (Go)

**One binary.** `WORKER_PIPELINES` selects which pipelines a process runs — `trace-assembly`,
`log-writing`, `metric-derivation`, `service-edges` (ADR 0050). An unknown name fails at boot rather
than silently not running. The names are load-bearing: splitting a deployment is a config change,
not a rewrite.

Pipeline order carries two invariants:
- **`derive` runs before `sample`** — metrics are computed from all traces, so counts stay exact
- **`mask` runs before `sink`** — unmasked data is never written

**`trace-assembly` does not buffer yet.** In M1 it consumes, decodes, flattens and inserts, with no
wait window; ClickHouse joins spans at read time. The in-memory buffer exists to let tail sampling
see a whole trace and arrives with it in P1-21 (ADR 0054, superseding ADR 0009's window in part).

Offsets commit only after the insert returns — at-least-once, which is safe because re-inserting an
identical span row changes nothing.

Writes use `ch-go` (not `clickhouse-go`), which is what ClickHouse recommends for insert-heavy paths.

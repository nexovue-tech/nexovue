# workers — the processing pipeline (Go)

Four binaries over one shared `internal/`: `trace-assembler`, `log-writer`, `metrics-deriver`, `edge-aggregator`.

Pipeline order carries two invariants:
- **`derive` runs before `sample`** — metrics are computed from all traces, so counts stay exact
- **`mask` runs before `sink`** — unmasked data is never written

Writes use `ch-go` (not `clickhouse-go`), which is what ClickHouse recommends for insert-heavy paths.

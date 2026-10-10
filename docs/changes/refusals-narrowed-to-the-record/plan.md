# Plan: refusals narrowed to the record

> **Status:** Complete

## Preconditions

`ingest-cannot-be-knocked-over` committed (`455410b`). Founder: "yes" to this follow-up, 2026-10-10.

## Current system

Read from the code on 2026-10-10.

- `traceassembly/refusal.go` — `salvageRows(ctx, rows, refusal, write, park)`: halves a refused set
  until each refusal is one row; tested by `TestOneRefusedSpanCostsOnlyItself` (999 of 1,000 stored).
  Generic in all but its type.
- `consume/park.go` — `ParkRefused(ctx, parker, covered, cause)`: parks every record of a chunk.
- `logwriting/pipeline.go` `Absorb`/`writeChunk` — rows of every record are appended straight into one
  `logBlock`; `inBlock` keeps only the records. Vitals readings are promoted, and inserted, before the
  log insert (`promoteVitals`, `:455`).
- `metricwriting/metricwriting.go` `Absorb`/`writeChunk`/`insertBlocks` — five blocks, inserted in a
  fixed order, one `Retry` each; a refusal at table *k* leaves tables before *k* written for the whole
  chunk. Per-message rows arrive as `rows{gauge, sum, histogram, expHistogram, summary}`
  (`metricwriting/rows.go:123`).
- `profilewriting/profilewriting.go` `Absorb`/`writeChunk` — one block, one table.

## Change required

[`change.md`](change.md).

## Steps and tasks

### Step 1 — one narrowing routine, used by spans

*Judgeable:* the span refusal tests pass unchanged against the shared routine.

- [x] **T1** — `consume/narrow.go` (new) · `Narrow[T](ctx, items, refusal, write, park)`, moved from
  `traceassembly.salvageRows` and made generic · proved by `consume/narrow_test.go`, the span tests
  moved with it (one poisoned item in 1,000 → 999 written, 1 parked; an outage mid-narrowing still
  returned)
- [x] **T2** — `traceassembly/refusal.go` · `salvage` calls `consume.Narrow`; `salvageRows` deleted ·
  proved by `traceassembly` tests passing

### Step 2 — logs, metrics and profiles park only the refused record

*Judgeable:* send one poisoned metric while the host-metrics agent is sending: `metrics-dlq` grows by
**1**, not 9, and the agent's points from the same chunk are in `metrics_gauge`. The same for one
poisoned log.

- [x] **T3** — `logwriting/pipeline.go` · keep each record's rows beside it in the chunk; on a refusal,
  narrow over the records — re-inserting a subset rebuilds a block from its records' rows — and park only
  the records refused alone; `consume.ParkRefused` is deleted once nothing calls it · proved by a new
  `logwriting/narrow_test.go` against a fake insert (one poisoned record among many: the rest written,
  one parked)
- [x] **T4** — `metricwriting/metricwriting.go` · narrow **one table at a time**: insert table *k* for the
  active records; on a refusal narrow over them on table *k* alone, park the records refused, and drop
  them from the tables after *k*. Tables already written are never rewritten, so narrowing adds no
  duplicate rows; a parked record keeps the rows its earlier tables took (as today — stated in the code)
  · proved by `metricwriting/narrow_test.go`: a record refused on the histogram table is parked, its
  gauge rows stay written once, and the other records' histogram and summary rows are written
- [x] **T5** — `profilewriting/profilewriting.go` · narrow over records, one table · proved by
  `profilewriting/narrow_test.go`
- [x] **T6** — **live:** the poisoned metric and log above, with the development host-metrics agent
  running; read `metrics-dlq`/`logs-dlq` growth and the agent's rows in the same minute

## New risks

- **Narrowing repeats inserts.** About 2·log₂(n) for one refused record among n — only on a refusal.
- **Logs: vitals are promoted for the whole chunk before the log insert**, so a parked log record's
  web-vitals readings are already stored. Unchanged from today; stated in the code.
- **ClickHouse closes the connection after each refusal** (`packet: uvarint: read: EOF` on the next
  insert); every narrowing step that follows a refusal pays one ordinary retry. Observed in the parent
  change; accepted.

## Questions

None.

## Gates

- [x] Specification approval — not applicable
- [x] Design approval — the design is this plan
- [x] **Plan approval — before the first edited file** (founder, 2026-10-10)
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Documentation and status update

## Validation plan

Each new test seen failing first — against the chunk-parking code for T3-T5. `just check` once at the
end, counts read. T6 live.

## Rollout and rollback

Pre-deployment. Revert the commit.

## Outcome

Built as planned. What changed from the plan, and why:

- **`consume.ParkRefused` and the `Parker` interface were both deleted**, not only `ParkRefused`:
  nothing else used `Parker`.
- **The narrowing helpers report exact counts.** A first draft of the logs branch estimated the rows
  of parked records for the `logs_written` counter; `narrowLogs` and `narrowProfiles` now return
  records parked and the rows they held, so the heartbeat stays exact.
- **The logs refusal branch counts the web-vitals readings as written** — they are inserted for the
  whole chunk before the log insert, and the first draft missed them.
- **An empty profile insert is skipped** (`profilewriting.insert`): narrowing can hand over a record
  that carried no samples.
- **T6 needed the nesting cap lifted.** `ingest-cannot-be-knocked-over` capped attribute nesting at 64
  levels, so the 1,100-level trigger is now stored, not refused — the first live burst stored all 21
  metrics and 21 logs and parked nothing. `otlpvalue.MaxDepth` was raised on the development worker for
  the run and restored immediately after; the restored file is identical to the commit.

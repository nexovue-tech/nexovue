# Status: refusals-narrowed-to-the-record

## Lifecycle status

Implemented and verified on the development stack. Committed; not pushed.

## Current task

None.

## Completed

- **T1-T2:** `consume.Narrow[T]` — the span pipeline's halving, made generic; spans call it, and
  `traceassembly.salvageRows` is gone. Tests moved with it, plus a two-refusal case.
- **T3-T5:** logs and profiles narrow over records; metrics narrow one table at a time
  (`metricwriting/narrow.go`, `writeTables`), so a table already written is never rewritten.
  `consume.ParkRefused` and `consume.Parker` deleted.
- **Red then green**, with `consume.Narrow` made to park the whole chunk as before:
  - logs: `parked [0 1 2 3 4 5 6 7 8 9] (10 records, 30 rows); want only offset 4, with its 3 rows`
  - profiles: `parked [0 1 2 3 4 5] (6 records, 12 rows), written 0; want nil, [0], 1, 2, 10`
  - metrics: `parked [0 1 2 3 4 5 6 7]; want only offset 5`
  - With narrowing: all pass; the metrics test also proves the gauge rows written before the refused
    histogram table are written exactly once.
- **T6, live** — 20 healthy and 1 poisoned metric, and the same for logs, sent in one burst, with the
  nesting cap lifted for the run (see `plan.md` *Outcome*):
  - `pipeline=log-writing … parked only the records it refused … messages_parked=1
    messages_in_chunk=23`; `logs-dlq` 1 → 2.
  - `pipeline=metric-ingest … parked only the records it refused … messages_parked=1
    messages_in_chunk=23`; `metrics-dlq` 9 → 10 — where one poisoned metric parked 9 that morning.
  - Stored from the same burst: 20 healthy metrics, 20 healthy logs.
  - Cap restored; `otlpvalue.go` identical to the commit.

## Next action

None.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- Narrowing costs about 2·log₂(n) inserts for one refused record among n, plus one ordinary retry per
  step because ClickHouse closes the connection after a refusal — only on a refusal.
- A parked metric record keeps the rows its earlier tables took; a parked log record's web-vitals
  readings are stored. Both as before narrowing, stated in the code.
- No live trigger for a refusal exists any more with the nesting cap in place: the next one will be a
  different kind of bad value, and this is the path it takes.

## Validation

- Unit tests above, red then green.
- `just check`: exit 0 — lint 0 issues, `mypy` no issues in 582 files, Go 36 packages `ok`, Python
  4174 passed and 3 skipped, web 1350, browser SDK 120. (One earlier run failed `mypy` on a stale
  cache written by a one-file `mypy` run of this session; a fresh cache showed no issues, and the stale
  one was moved aside, not deleted.)
- T6 live, above.

## Specification impact

None.

## Documentation impact

None beyond this record and the changes index.

## Last updated

2026-10-10

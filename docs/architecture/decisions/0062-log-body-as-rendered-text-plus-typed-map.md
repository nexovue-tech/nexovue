# 0062 — Log body as rendered text beside a typed map

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

`logs.body` was `String`. OTLP's `LogRecord.body` is an **AnyValue**: it may be a string, a number,
a boolean, a byte string, an array, or a key-value map. Structured loggers — the common case in the
services nexovue will watch — emit the map form. Flattening that to a string throws away the field
structure the product most wants to filter on, and it is not recoverable later: the receiver never
stored it.

M3 writes this table next month and it is empty today, which is the only moment the shape is free
([0049](0049-project-id-in-the-spans-sort-key.md), [0057](0057-project-id-in-every-telemetry-sort-key.md)).

Two constraints were measured rather than assumed, both against the version we run (26.3.20.7):

- **ClickHouse's `JSON` type only accepts objects.** Inserting `42`, `true`, `"hello"` or `[1,2,3]`
  at the top level fails with `JSON object should start with '{'`. A single `body JSON` column
  therefore cannot hold an AnyValue.
- **`Dynamic` holds every AnyValue shape**, and `dynamicType()` recovers the original type — but
  `ch-go` v0.74.0, the driver every writer in this repo uses, has no `Dynamic` or `Variant` column
  at all. The pipeline could not write such a column.

The reference implementations do not help: the OpenTelemetry ClickHouse exporter and ClickStack both
type `Body` as plain `String` and accept the loss.

## Decision

Two columns.

- `body String` — the rendered body, always populated, and the only thing the text index searches.
  For a non-string AnyValue it holds that value's JSON text.
- `body_structured JSON` — the AnyValue when it was a map, so structured logs keep their fields as
  typed subcolumns; `{}` otherwise.

Separately, and for the same "free while empty" reason: `severity_number Int32` beside the existing
free-text `severity`, because SDKs write WARN, WARNING, W and warn for one level and "warn and above"
is not expressible against the text; and `attrs` split into `resource_attrs` and `log_attrs`, which
is what `spans` already does and what OTLP already separates.

## Consequences

Full-text search stays on a plain `String` column. That matters more than it looks: a text index
defined over an expression only applies when the `WHERE` clause repeats that expression verbatim, so
indexing `toJSONString(body)` would have pushed that spelling into every query the product writes.

A map body is stored twice — once as text, once typed. It is the minority of records and ZSTD makes
the duplicate cheap, and it buys the ability to search and to filter on fields without a second read
path.

Array, number and boolean bodies keep their JSON text in `body` and lose their type. This is the
accepted loss. Recovering it needs `Dynamic`, which needs driver support that does not exist; if
`ch-go` gains it, `body_structured` can widen without touching `body`.

The writer M3 builds now owes three things it would not otherwise: mapping SeverityText to
SeverityNumber, splitting resource from record attributes, and deciding per record whether the body
is a map. None of them is recoverable after the fact, which is why the shape lands before the writer.

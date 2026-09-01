# 0088 — The first exception event on a span fills the promoted columns

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

Migration `000013` added `spans.exception_type` and `spans.exception_message`
in anticipation of error grouping, and **nothing ever wrote them**. Measured on
the dev store before this change: 0 of 12,499,337 span rows had a non-empty
`exception_type`, while 2,900 rows carried an `exception` event inside their
`events` JSON. Only the log pipeline filled its half of the pair — 401 rows of
391,561.

That gap became visible the day the service detail screen shipped. Its Errors
panel reads error groups, error groups read `exception_type`, and the screen
therefore said `ERRORS 715 · 17.0%` above `No exception was recorded in this
service in the window.` Both statements were true. Together they read as broken,
and they would read that way for every service whose exceptions arrive as span
events — which is the OpenTelemetry default.

OpenTelemetry records an exception as a span event named `exception` carrying
`exception.type`, `exception.message`, `exception.stacktrace` and
`exception.escaped`. Type, message and stacktrace are stable and none was ever
renamed; `exception.escaped` is now **deprecated**, and the span-event
convention as a whole is deprecated in favour of the log-based one
([exceptions-spans](https://opentelemetry.io/docs/specs/semconv/exceptions/exceptions-spans/)).
Verified against real producers rather than the spec alone: across the
OpenTelemetry Demo's Node, Python, Go and C# services in our own store, every
exception event uses exactly those keys.

**A span can carry more than one.** Measured: of 2,900 spans holding an
exception event, 226 held two. In all 226 the two events had the *same*
`exception.type` — a client retrying inside one span and recording the same
throw twice. The columns are singular, so one must be chosen.

## Decision

The **first** `exception` event that recorded a type or a message fills the two
columns. Events named anything else are ignored, and an event carrying neither
key is skipped rather than consuming the columns a later one would have filled.

The extraction is shared with the log pipeline: the attribute names and the
read live in `otlpvalue`, which is the package that exists to stop each pipeline
growing its own copy of OTLP handling. The log writer's private copies of
`exception.type` and `exception.message` were deleted in the same change.

Neither `exception.stacktrace` nor `exception.escaped` is promoted. No schema
change was needed — `000013` already declared both columns.

## Alternatives considered

**The last exception, or the deepest one.** Rejected: the last event on a span
is usually the same exception re-recorded as the stack unwinds, so it names the
symptom rather than the throw. It also disagrees with the read side — the API
falls back to the *first* exception event when it digs the matching stacktrace
out of `events`, so promoting the last would show one exception's type beside
another's stacktrace whenever a message was masked or truncated.

**Prefer the one with `exception.escaped=true`.** Rejected on two counts. The
attribute is deprecated by the current conventions, and it is not reliably
present or reliably typed — only 687 of 2,900 spans carry it at all, and the
Python SDK sends the string `"False"` rather than a boolean. Building the choice
on it would make the column depend on an attribute most producers omit.

**A third column for the stacktrace.** Rejected: it is large, never filtered on,
and already stored. The API reads it out of `events` for the handful of rows a
detail view renders, which is the same place `000013` said it would stay.

**An array column holding every exception.** Rejected as out of scope here — it
changes the sort key question and the grouping query, and the measured loss it
would recover is zero rows on the data we have.

## Consequences

A service whose exceptions arrive the OpenTelemetry way now reaches the Errors
screen. Verified live: with the demo's `payments-errors` fault injected, the Go
SDK's `span.RecordError` produced rows with `exception_type =
*errors.errorString`, and the shipped error-grouping statement returned a group
with `sample_source: span` — a row shape that could not previously exist.

**What is lost.** A span that throws two genuinely different exceptions reports
only the first as a group. Nothing is discarded — every event stays whole in
`events` — but the second throw does not get an error group of its own. On the
data measured this costs nothing (all 226 multi-exception spans held one
distinct type), and it becomes wrong only for a span that catches one exception
and throws a different one without ending.

**Existing rows are not backfilled.** The 2,900 spans already stored with an
exception event keep empty columns and stay invisible to the Errors screen until
they age out under retention. No backfill was run. A mutation could fill them —
`ALTER TABLE spans UPDATE` reading `events` — but it would have to handle two
JSON shapes, because events written before the attribute-nesting change store a
flat `"exception.type"` key (504 rows, up to 2026-08-28) while later ones store
`{"exception":{"type":…}}` (2,396 rows, from 2026-08-30). Against dev demo
traffic that is a rewrite of the table's parts to recover data nobody will read.

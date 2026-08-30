# 0078 — Queue edges are derived from span links, not from the parent link

- **Status:** Accepted
- **Date:** 2026-08-30

## Context

`service_edges_1m` has carried an `edge_kind` column since
[000014](../../../deploy/clickhouse/migrations/000014_service_edges_1m.up.sql), with two values:
`call` for CLIENT→SERVER and `queue` for PRODUCER→CONSUMER. The dashed-line rendering in
`DependencyEdge.tsx` and the "DASHED, QUEUED" legend on the service map
([0031](0031-service-map-rendering-with-react-flow.md)) both exist for it.

**Not one `queue` row had ever been written, and none could be.**

Measured against the OpenTelemetry Demo 3.0.0: `checkout` emitted 20 PRODUCER spans (`orders
publish`), `accounting` 26 CONSUMER (`orders receive`), `fraud-detection` 4. Yet
`service_edges_1m` held zero rows with `edge_kind='queue'`, and of the traces holding a producer,
`producer_only_traces = 10`, `traces_with_both = 0`.

The cause is in `serviceedges/pairing.go`: it paired a PRODUCER with a CONSUMER **underneath it** —
matching a callee's `parent_span_id` against a caller's `span_id`, within one trace. Modern
messaging instrumentation does not produce that shape. The consumer starts a **new trace** and
*links* back to the producer, because a span has exactly one parent and a batch consume has many
producers.

That is not our reading of the conventions; it is what they say. The
[messaging span conventions](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/)
make links the default correlation between producer and consumer, and
[OTEP 0220](https://github.com/open-telemetry/oteps/blob/main/text/trace/0220-messaging-semantic-conventions-span-structure.md)
gives the reasons: a span can only have one parent, batch consumption has many producers, and
consumption often happens inside an unrelated ambient context.

**The evidence was in `links`, and `links` was not stored at all** — not on `spans`, not anywhere.
So the data had to exist before the pairing could change.

Confirmed on real demo telemetry once the column existed: `accounting`'s `orders receive` span in
trace `15950a71…` carries `links → (c32718077f0b8737…, ba34205b91e6a31b)`, which is exactly
`checkout`'s `orders publish` span — in a **different trace**. The link is the only connection
between them.

### This defect is inherited, not local

The OpenTelemetry Collector's
[servicegraph connector](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/connector/servicegraphconnector/connector.go)
— which our package's comment named as its model — keys its in-memory store `store.NewKey(traceID,
span.SpanID())` for a client span and `store.NewKey(traceID, span.ParentSpanID())` for a server
span. **It never consults span links.** Its README claims producer/consumer support on span kind
alone. [Issue #29148](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/29148)
asked for broader detection and was closed without it.

So a `queue` edge is not something we failed to copy from upstream. Upstream cannot draw one either
against instrumentation that follows the conventions.

## Decision

### 1. Links are stored as a `Nested` column, in our own id shape

[000025](../../../deploy/clickhouse/migrations/000025_spans_links.up.sql):

```sql
ALTER TABLE spans ADD COLUMN IF NOT EXISTS links Nested(
    trace_id FixedString(32), span_id FixedString(16), attributes String
) CODEC(ZSTD(1))
```

The shape follows the OpenTelemetry
[ClickHouse exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/clickhouseexporter/internal/sqltemplates/traces_table.sql),
which declares `Links Nested(TraceId String, SpanId String, TraceState String, Attributes
Map(LowCardinality(String), String)) CODEC(ZSTD(1))`. With `flatten_nested = 1` (the server default,
checked) it becomes three parallel arrays: `links.trace_id`, `links.span_id`, `links.attributes`.

Two deliberate deviations from the exporter:

- **Hex `FixedString` at our own widths**, not `String`. A link then compares against
  `spans.trace_id` and `spans.span_id` with no conversion on either side. That is not theoretical —
  the producer/consumer ingest gap reported below was measured with exactly that join,
  `arrayJoin(arrayZip(links.trace_id, links.span_id))` against `spans`.
- **No per-link `trace_state`.** We do not store a span's *own* tracestate, because nothing queries
  it; storing a link's would be less defensible still.

**`links.attributes` is a plain `String` holding JSON, deliberately** — not the native `JSON` type
that `span_attrs` and `resource_attrs` use ([0034](0034-clickhouse-attributes-as-native-json.md)).
A JSON column gives every attribute path its own subcolumn, and that is precisely the shape that
took every pipeline down for an hour: the demo's PHP service sent `context` (an object holding
`total`) and `context.total` beside it, both naming one ClickHouse path, and the insert was refused
outright with `INCORRECT_DATA` — permanent. Link attributes are read with the link and never
filtered on, so a subcolumn per key buys no query and reopens that failure mode for nothing.

### 2. A queue edge pairs on links; a call edge does not

A callee span names its caller by its **parent** always, and — **for a queue hop only** — by each of
its **links**. The union is deduplicated, so a consumer that is both the producer's child and linked
to it names one cause, not two.

Links are read for `queue` and not for `call`. On a CLIENT/SERVER pair the parent link is the
convention and a link means something else — a retried request naming the attempt it repeats, say —
and pairing on it would count that call a second time.

The pairing buffer is otherwise unchanged: same key `(tenant, project, trace, caller span id)`, same
window, same bound. Tenancy stays in the key, and matters more now than before: a queue hop crosses
traces, and W3C trace context is designed to propagate between organisations, so two customers can
export the same trace id.

### 3. One call per (producer span, consumer service)

An edge's `call_count` means **messages delivered**, so it can be compared with the publish rate.
A batch consume linking five producers is five deliveries, split across the edges those producers
belong to. Two mechanisms make that hold, and neither is optional:

**A queue caller stays parked after it pairs.** One published message reaches every consumer group.
In the demo, `accounting` and `fraud-detection` are separate groups on the `orders` topic and each
receives every message. Under the old remove-on-pair rule the first consumer consumed the producer
and the second dependency disappeared. A call caller is still removed on pairing: one request is
answered once.

**A queue caller remembers which callee services it has already reached.** The conventions have a
consumer emit a *Receive* span and a *Process* span, each linked to the same message. Counted
naively that is two deliveries for one message. Measured live: `probe2-accounting` sent two CONSUMER
spans linking one producer and the edge recorded **1 call**, not 2.

A service that runs two consumer groups of its own would be counted once rather than twice. That is
the accepted cost, and it is the cheaper error: over-counting deliveries would make a queue look
busier than the publisher it reads from, which is the one comparison this number exists for.

### 4. The latency on a queue edge is the producer's publish duration

`latency_quantiles` keeps **one definition across both edge kinds: the caller's own span duration.**
On a `call` that is the request the caller waited on, network included. On a `queue` it is how long
the publish took.

**It is not the delivery lag, and the API and the map now say so** — the edge label reads `publish
p95`, not `p95`.

The number people actually want from a queue is `consumer.start − producer.end`: how long the
message sat. That measurement crosses two machines' clocks, and this system explicitly refuses to
trust a client clock — the `clocktrust` package exists for that reason, and
[0067](0067-storage-partitions-on-our-clock-not-the-client-s.md) partitions on our clock rather than
the sender's. It needs its own column and its own skew decision, and it is **not in this change**.

Putting it in the existing column instead would repeat the mistake
[000023](../../../deploy/clickhouse/migrations/000023_spans_rpc_columns.up.sql) was written to
avoid: two different quantities in one column make every filter over it silently wrong for a subset
of rows.

### 5. An absent id is not an empty slice

Found while doing the above, and fixed here because the same buffer depends on it.

`pairing.go` guarded with `len(span.ParentSpanID) == 0` — *"a root span was called by nobody we can
see"*. **That check never fired in production.** Ids reach `serviceedges` hex-encoded and padded for
the `FixedString` columns, so an absent parent is sixteen NUL bytes, and an all-zeroes id is sixteen
ASCII zeroes. Both have non-zero length. Every root span was therefore parked in the pairing buffer,
holding a slot for the full window, waiting for a parent that cannot exist.

`usableID` now rejects an id whose every byte is `0x00` or `'0'`. It gates the buffer and
`participates()`, so a root span is ignored — no slot, no settlement minute — as it always should
have been.

## Alternatives considered

**One JSON String column for links, like `events`.** `events` is a String precisely because events
are read whole with their span and never filtered on. Links are the opposite: the whole point is to
find *the span on the other end*, which means comparing a link against `spans.trace_id` and
`spans.span_id`. A JSON blob makes that a parse per row. Rejected.

**Deriving queue edges in ClickHouse by joining consumers to producers.** Cross-partition and
cross-worker for free, which would fix both limits named below. Rejected because `spans` is written
**after** tail sampling ([0054](0054-spans-written-through-until-tail-sampling.md)), which keeps
roughly one healthy trace in ten ([0010](0010-tail-sampling-defaults.md)) — a graph joined from it
would report a tenth of the real call rate. That is the same reason
[000024](../../../deploy/clickhouse/migrations/000024_endpoint_stats_1m_from_derive.up.sql) moved
`endpoint_stats_1m` off a materialized view over `spans`.

**Dropping a consumer whose producer is not already in the buffer.** Would have avoided parking
callees at all. Rejected: export batching and Kafka partitioning make arrival order unreliable in
both directions, so the graph would have been flaky rather than merely incomplete.

**A separate `queue_lag_ms` quantile column in this change.** Rejected as premature — see decision 4.
It cannot be added honestly without deciding what to do when the two clocks disagree, and that
decision has not been made.

**Widening `PairWindow` so queue halves have longer to meet.** Rejected here, deliberately, rather
than guessed at — see the first limit below.

## Consequences

Queue edges exist. Verified end to end through receiver → Kafka → worker → ClickHouse, with a real
OTLP payload: links read back from `spans` (`links.trace_id: ['aa000…ff01']`, `links.attributes:
['{"messaging":{"batch":{"message_index":0}}}']`), two `queue` rows written from one message and
three consumer spans across two services, `1` call each, `p50 = 7 ms` — the publish duration. The
`/api/v1/service-map` response carries them, and the map renders them dashed with a `publish p95`
label. Tests: 84 passing across `serviceedges` and `traceassembly`, 12 of them new; each new test was
negative-controlled by breaking the mechanism it guards and watching it fail.

**A busy publisher now holds more of the pairing buffer.** A queue caller stays parked for the whole
window instead of being freed on its first pairing, so the buffer holds roughly the publish rate
times the window rather than the unpaired remainder. Bounded by `MaxPending` (500,000 parked spans by
default), and a batch consumer parks one entry per link it names, which the same bound covers.

**Storage cost is negligible, and measured only for the empty case.** On 12.47M `spans` rows the
three link columns are **455 bytes compressed in total** — an empty array costs essentially nothing
under ZSTD. Populated cost is about 50 bytes per link uncompressed, on the small fraction of spans
that are messaging spans. **This was not measured under sustained messaging load**, because the
demo's `checkout` was OOM-killed 62 times at its upstream 20 MiB limit during the run and published
almost nothing.

There is no cardinality cost: `links.attributes` is a String, so a link's keys never become
subcolumns.

### Three limits, written down so they are not rediscovered

**`PairWindow` may be too short for a queue hop, and this is unresolved.** The window is the sampling
window, ten seconds ([0058](0058-tail-sampling-window-is-ten-seconds.md)), chosen when both halves of
an edge were in one trace on one partition and therefore in memory together. A queue hop's halves are
not: the consumer's new trace keys to a different partition, and the two services' exporters flush
independently. Of the one real producer/consumer pair measurable in this run, **the producer reached
our receiver 13,145 ms after the consumer** — past the window, so no edge formed. **One sample, from
a service that was crash-looping, is not a distribution**, and it should not be read as one. But the
mechanism is real and the sample is consistent with it. Widening `PairWindow` is not the fix: it is
the offset-commit milestone for *every* span, so it spends the §4 latency budget on a problem only
queue hops have. A longer queue-only wait needs its own buffer with its own bound, and that has not
been designed.

**Replication breaks queue edges.** The producer's trace and the consumer's trace key to different
Kafka partitions, so they pair only while one worker process holds both. With the trace-assembly
pipeline replicated ([0050](0050-one-worker-process-pipelines-by-config.md) makes that a config
choice), the fraction of queue edges straddling two workers is silently lost. Call edges never have
this problem — both spans are in one trace, on one partition, by construction. Making this correct
under replication needs durable producer identity (KeeperMap, Valkey, or a store), not a bigger
in-memory buffer.

**A consumer that neither links nor carries the producer's context makes its edge underivable by
anyone** — not by us, not by the upstream connector, not by any tool, because the evidence is not in
the telemetry. **The absence of an edge for such a service is not a bug in this pipeline**, and
should not be read as one.

How often that happens here is **not established.** Of the three `accounting` `orders receive` spans
stored after the column existed, **two carried a link and one did not**; the demo's `checkout` was
being OOM-killed throughout the run, so a message published by a restarting producer plausibly went
out without a `traceparent` header — but that is a hypothesis, not a measurement, and no run has been
made with a healthy `checkout` to test it.

> An earlier draft of this record asserted that `accounting` carries neither a link nor the
> producer's context, and that `checkout → accounting` was therefore underivable. **That was wrong.**
> It rested on a single `accounting` span whose `links` array was empty — a row received at
> `13:46:09`, thirty-eight seconds *before* the writer that populates the column started at
> `13:46:47`. Its empty `links` meant "we were not storing links yet", not "the span carried none".
> The contradicting evidence: `accounting`'s span in trace `15950a71…` links to
> `(c32718077f0b8737…, ba34205b91e6a31b)`, which is `checkout`'s `orders publish` — quoted in the
> Context above, and read from the same table.

### Left in the dev database

The `probe-*` and `probe2-*` services on the dev service map are the live evidence above — six rows.
They stay: clearing them means a mutation on an `AggregatingMergeTree` rollup, which is a larger
footprint than the data.

# Wireframes — Investigation core (batch 2 of 3)

> **Status:** Awaiting approval. Five screens across three archetypes — Event stream, Entity detail,
> Graph. Every field below was read from the API's own response schemas.

## Correction, 2026-09-13: there was no systemic gap

This batch originally reported that `ServiceEdge` carries no latency and called it one pattern with
`ServiceHealth`. **Both readings were wrong.** `ServiceEdge` carries `p50_ms`, `p95_ms` and `p99_ms`,
with a comment explaining that on a queue edge the duration is the publish and not the delivery lag.

The genuine stored-but-unshown items in this batch stand, and were read from the schemas correctly:

| Stored | Not on the schema |
|---|---|
| `trace_summaries.span_services[]`, `span_endpoints[]`, `worst_severity` | `TraceSummary` |
| `spans.exception_type`, `exception_message`, `events`, `links`, `deployment_version` | `SpanDetail` |

`SpanDetail` does expose `rpc_system`, `rpc_method`, `rpc_status`, and `ServiceEdge` exposes
`callee_kind` — four AC4 dimensions already served and simply not drawn.

---|---|
| `trace_summaries.span_services[]`, `span_endpoints[]`, `worst_severity` | `TraceSummary` |
| `spans.exception_type`, `exception_message`, `events`, `links`, `deployment_version` | `SpanDetail` |
| `service_edges_1m.latency_quantiles` | `ServiceEdge` |

`SpanDetail` does expose `rpc_system`, `rpc_method`, `rpc_status`, and `ServiceEdge` exposes
`callee_kind` — so four of the twelve AC4 dimensions are already served and simply not drawn.

---

## 6 · Logs — `/logs` *(Event stream)*

**Answers:** what was the system saying when this happened?
**Does not show:** metrics, or logs from outside the selected window.

**Reads:** `GET /logs/search` → `LogSearchResponse` (`LogLine` = `LogRecord` + `row_key`,
`next_cursor`) with `q` (case-insensitive substring), `service`, `min_severity`, `cursor`
· facets → `ServiceFacet`, `SeverityFacet` · `WS /logs/tail` with the identical filter set.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Logs   [ q: timeout______ ]  [ All services ▾ ] [ ≥ WARN ▾ ]  ◉ Live     │
├──────────────────────────────────────────────────────────────────────────┤
│ SERVICES          SEVERITY          ← facets, as counts not a dropdown   │
│ checkout-api 2.1k ERROR   412                                            │
│ cart-service  840 WARN  1,204                                            │
├───────────┬──────────────┬───────────────────────────────────────────────┤
│ 15:04:12.3│ checkout-api │ ERROR  Connection to upstream timed out af…  ↗│
│ 15:04:12.1│ payment-gw   │ WARN   Retry 2/3 for charge_id=ch_8f…         │
│ 15:04:11.9│ checkout-api │ INFO   POST /checkout 200 in 84ms             │
└───────────┴──────────────┴───────────────────────────────────────────────┘
```

**Facets are counts, not a dropdown.** `ServiceFacet.records` and `SeverityFacet.records` are already
served; showing them as numbers tells the reader where the volume is before they filter, which a
`<select>` cannot.

**`↗` appears only when `trace_id` is non-empty** — it is the jump into the trace, and it is the
single most valuable link on the screen. A log without one shows no affordance rather than a dead
control.

**Live is a toggle, not a mode.** The same filters drive the tail socket and the search read, so
turning Live on never changes what matches.

**States.** *Empty (filtered)* names the term and offers to widen the window, because
`positionCaseInsensitive` over a bounded window is what makes half a word findable at all.
*Partial* — `next_cursor` present means more exist; the screen says so rather than implying the page
is the result.

---

## 7 · Traces — `/traces` *(Event stream)*

**Answers:** which requests were slow or failed?
**Does not show:** spans — that is one click deeper.

**Reads:** `GET /traces` → `TraceListResponse` of `TraceSummary` (`root_service`, `root_name`,
`duration_ms`, `span_count`, `error_count`, `status_code`), `next_cursor`
· `GET /traces/facets` → `ServiceFacet`, `OperationFacet`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Traces    [ checkout-api ▾ ]  [ Errors only ]  [ > 500ms ]   [ 1h ▾ ]    │
├──┬──────────────────────────────┬──────────┬────────┬────────────────────┤
│ ●│ checkout-api  POST /checkout │ 4,210 ms │ 18 sp  │ 15:04:12  ▓▓▓▓▓▓░░ │
│  │ checkout-api  POST /checkout │   340 ms │ 16 sp  │ 15:04:09  ▓░░░░░░░ │
│  │ catalog-api   GET /items     │    64 ms │  4 sp  │ 15:04:08  ▓░░░░░░░ │
└──┴──────────────────────────────┴──────────┴────────┴────────────────────┘
```

The bar is **duration relative to the slowest trace in the window** — the one place a shared scale
across a list is the whole point, and the same rule as the sparkline.

**Selection arrives pre-applied (IA1).** Reaching this screen from `checkout-api` lands with the
service filter set, rather than asking the reader to re-choose what they just chose.

**States.** *Empty (never)* is the onboarding moment — this is where a new customer currently lands
after setup (Flow B), and it must confirm rather than dump. *Partial* — a trace whose spans were
sampled away still lists; `span_count` from the summary is authoritative.

**Backend:** expose `span_services[]` and `span_endpoints[]` so "which traces touched payments" is a
filter rather than a scan, and `worst_severity` so a trace with an ERROR log but no failed span is
visibly different from a clean one.

---

## 8 · Trace detail — `/traces/$traceId` *(Entity detail)*

**Answers:** where did this request spend its time, and where did it break?

**Reads:** `GET /traces/{id}` → `TraceDetailResponse.spans[]` of `SpanDetail`
· `GET /logs?trace_id=…` → `LogsForTraceResponse` (`TraceLink.exists` tells you whether the trace is
in the lookup window at all).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ← Traces    POST /checkout · 4,210 ms · 18 spans · 3 errors              │
├──────────────────────────────────────────────────────────────────────────┤
│ checkout-api  POST /checkout          ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  4,210 ms     │
│  ├ cart-service   GET /cart            ▓▓░░░░░░░░░░░░░░░░░░     84 ms     │
│  ├ payment-gw     POST /charge          ░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  3,890 ms  ●  │
│  │  └ postgres    SELECT                 ░░▓▓▓▓▓▓▓▓▓▓▓▓░░░  3,120 ms     │
│  └ notification   publish                         ░░░░░▓░░     12 ms     │
├──────────────────────────────────────────────────────────────────────────┤
│ SPAN  payment-gw POST /charge                                            │
│ TimeoutError  Connection to upstream timed out after 3000ms              │
│ http.method POST · http.status 504 · rpc.system grpc · deploy v2026.9.11 │
│ ── 3 correlated logs ──────────────────────────────────────────────────  │
└──────────────────────────────────────────────────────────────────────────┘
```

**The waterfall is the screen; the detail pane is subordinate.** Selecting a span fills the pane
without navigating, so the shape of the request stays on screen while its parts are read.

**`exception_type` and `exception_message` lead the detail pane**, above attributes. They are stored
as promoted columns precisely because they are what the reader came for, and `SpanDetail` does not
expose them today.

**`deployment_version` answers Flow A step 8** — "did a deploy cause this" — for one span, without
needing the deploys screen that does not exist.

**`events` and `links`** are stored. Events become a timeline inside the selected span; links become
"triggered by" navigation, which is how a queue consumer connects to its producer (ADR-0078 derives
queue edges from links, so the map and this pane would finally agree).

**States.** *Partial* is normal — a trace whose spans were tail-sampled shows the summary and says so
rather than rendering an incomplete waterfall as if it were whole. *Error* — `TraceLink.exists:
false` does not mean failure. Its own docstring: logs for the trace are held but the trace is not,
because its spans were sampled away or expired first. Those are two different sentences to a reader
and neither is "failed to load"; the link is simply not offered.

---

## 9 · Service detail — `/services/$name` *(Entity detail)*

**Answers:** what is this service's shape over time, and which endpoint is worst?

**Reads:** `GET /services/{name}` → `ServiceHealthResponse` · `GET /services/{name}/endpoints` →
`EndpointHealth[]` (`span_kind`, `endpoint`, `requests_per_minute`, `error_rate`)
· `GET /services/trend` → `ServiceTrend.points[]`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ← Services   checkout-api                                   [ 1h ▾ ]     │
│ 1.2k/min · 4.1% errors · p95 340 ms                                      │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │           rate ────────────────╱▔▔╲───   errors ▁▁▁▂▅▇█             │ │
│ │    ┊ deploy v2026.9.11                                              │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────┬─────────┬───────────┬────────┬────────────┤
│ ENDPOINT                     │    RATE │    ERRORS │    P95 │        1h  │
│ POST /checkout          http │  840/min│  5.8% err │ 410 ms │ ▁▂▃▅▇      │
│ GET  /checkout/{id}     http │  360/min│  0.2% err │  88 ms │ ▁▁▁▁▁      │
└──────────────────────────────┴─────────┴───────────┴────────┴────────────┘
```

**One real chart, then the ranked-entity row.** This is the only screen in batch 2 where a full chart
earns its place — the question is genuinely "what shape over time," not "which is worst."

**The deploy marker (IA3) lives here.** `deploy_events` is served and displayed nowhere; a vertical
rule on this chart is the cheapest honest answer to Flow A step 8, and it is neutral, never a series
colour.

**States.** *Partial* — endpoints with too few buckets keep the row, lose the sparkline.

---

## 10 · Service map — `/service-map` *(Graph)*

**Answers:** what calls what, and which hop is failing?

**Reads:** `GET /servicemap` → `ServiceMapResponse.edges[]` of `ServiceEdge` (`caller`, `callee`,
`edge_kind`, `callee_kind`, `calls_per_minute`, `error_rate`).

```
        ┌──────────────┐   1.2k/min          ┌──────────────┐
        │ checkout-api │ ═══════════════════▶│ payment-gw ● │
        └──────┬───────┘   4.1% err          └──────┬───────┘
               │ 3.1k/min                            │ 1.2k/min
               ▼                                     ▼
        ┌──────────────┐                      ┌─────────────────┐
        │ cart-service │                      │ postgres    db  │  ← callee_kind
        └──────────────┘                      └─────────────────┘
```

**Edge weight is call volume; edge colour is error rate.** Two encodings, two questions, neither
substituting for the other.

**`callee_kind` labels the node type** — `db`, `queue`, `http` — which is what stops an inferred peer
from being drawn as if it were an instrumented service of the customer's own.

**`edge_kind` distinguishes a synchronous call from a queue edge** derived from span links
(ADR-0078). Drawing those identically would claim a producer waited for its consumer.

**States.** *Empty* — one service and no edges is normal for a first deployment and must read as
"nothing calls anything yet," not as a failure. *Partial* — a node named only by an IP is collapsed
for display (ADR-0082) and says so.

**Backend:** `ServiceEdge` needs the latency quantiles the rollup already stores, so a slow hop is
distinguishable from a failing one.

---

## Filters and deltas — added by OD8/OD9 (accepted 2026-09-13)

Traces and logs are the two screens that already filter well — seven and five respectively — and
they are the reference the Event-stream contract was written from, not screens needing work.

**The service map has none, and needs two**: a volume floor and a failing-only toggle. An unfiltered
map of fifty services is a hairball, and `calls_per_minute` and `error_rate` are already on every
edge.

**Entity detail screens take no filters of their own** — they inherit the selection that brought the
reader there. Adding filters to a detail screen would let it disagree with the list it came from.

## Open questions for this batch

1. **Expose `latency_quantiles` once, across `ServiceHealth`, `EndpointHealth` and `ServiceEdge`?**
   Recommended — it is one pattern and three screens are diminished without it.
2. **Are `events` and `links` in scope for trace detail?** Both stored, both unshown, both real work.
   Links in particular would make the map and the waterfall agree about queues.
3. **Where does the deploy list live** once markers exist? Service detail carries the markers; the
   list itself has no obvious home short of a screen we agreed not to add.

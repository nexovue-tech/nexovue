# Wireframes — Ranked entities (batch 1 of 3)

> **Status:** Awaiting approval. Five screens, one archetype. Measured against
> [prototype-ranked-entities.html](prototype-ranked-entities.html), which renders the row from the
> real tokens.
>
> Every field named below was read from the API's own response schemas, not assumed.

## What this batch found: the archetype needs backend work

**None of the five list endpoints returns a time series.** The ranked-entity row requires one, so
every screen in this batch needs a widened read. This was named as a risk in `design.md`; it is now
confirmed, with the exact shape of the gap per screen.

**`ServiceHealth` carries no latency at all** — `request_count`, `error_count`,
`requests_per_minute`, `error_rate`, and nothing else. `endpoint_stats_1m` stores
`latency_quantiles(0.5, 0.95, 0.99)`, so p95 exists in the rollup and is not exposed.

The prototype shows a p95 column. **That column is currently unservable.** Either the API widens, or
the column goes. Recommendation: widen — the data is already computed, pre-sampling, and latency is
half of why anyone opens a service list.

---

## 1 · Services — `/services`

**Answers:** which of my services is worst right now?
**Does not show:** per-endpoint detail (one click deeper), infrastructure, or anything not derived
from spans.

**Reads:** `GET /services` → `ServiceListResponse.services[]` of `ServiceHealth`
· `GET /services/trend` → `ServiceTrendResponse` (`bucket_seconds` is server-chosen, `points[]` of
`requests_per_minute` + `error_rate`).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Services                              [ Last 1h ▾ ]   [ Filter… ]        │
├──┬────────────────────────┬─────────┬───────────┬────────┬───────────────┤
│  │ SERVICE                │    RATE │    ERRORS │    P95 │            1h │
├──┼────────────────────────┼─────────┼───────────┼────────┼───────────────┤
│ ●│ checkout-api      http │ 1.2k/min│  4.1% err │ 340 ms │ ▁▂▃▅▇▅▃▂      │  ← critical
│ ▪│ payment-gateway   http │ 1.2k/min│  1.8% err │ 512 ms │ ▁▁▂▁▂▁▂▂      │  ← warning
│  │ cart-service      http │ 3.1k/min│  0.4% err │  88 ms │ ▁▁▁▁▁▁▁▁      │  ← healthy: no mark
│  │ catalog-api       http │ 2.6k/min│  0.1% err │  64 ms │ ▁▁▁▁▁▁▁▁      │
│  │ notification-…   queue │   96/min│ 50.0% err │ 220 ms │ ▁▁▁▁▁▁▁▁      │  ← 50% of 96 ranks low
└──┴────────────────────────┴─────────┴───────────┴────────┴───────────────┘
```

**Row → `/services/{name}`.** Selection travels (IA1): the service is carried into traces, logs and
errors from there.

**States.** *Empty (never)* → "Point an SDK at your endpoint." *Empty (filtered)* → names the filter
and the count it excluded. *Partial* → a service younger than the minimum bucket count renders
without a sparkline, not hidden.

**Backend:** `ServiceHealth` gains p95 from `latency_quantiles`; the list and its series arrive in
one response, not N.

---

## 2 · Errors — `/errors`

**Answers:** which exception is hurting most, and is it new?
**Does not show:** individual stack traces (one click deeper), or errors without an exception.

**Reads:** `GET /errorgroups` → `ErrorGroupSummary`: `exception_type`, `message_template`,
`occurrences`, `first_seen`, `last_seen`, `services[]`, `grouped_by`, `sample_trace_id`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Errors                                [ Last 24h ▾ ]  [ All services ▾ ] │
├──┬───────────────────────────────────┬──────────┬──────────┬─────────────┤
│  │ EXCEPTION                         │    COUNT │     SEEN │         24h │
├──┼───────────────────────────────────┼──────────┼──────────┼─────────────┤
│ ●│ TimeoutError                  NEW │    1,204 │  2m ago  │ ▁▁▁▂▅▇█     │  ← first_seen in window
│  │ payment-gateway · checkout-api    │          │          │             │
│  │ Connection to upstream timed out… │          │          │             │
├──┼───────────────────────────────────┼──────────┼──────────┼─────────────┤
│ ▪│ ValidationError                   │      318 │ 14m ago  │ ▃▃▂▃▃▂▃     │
│  │ cart-service                      │          │          │             │
└──┴───────────────────────────────────┴──────────┴──────────┴─────────────┘
```

Three lines per row, not one — the exception type is the subject, the services are the context, and
the message is the evidence. `message_template` is shown, never `latest_message`, so the row names
the group rather than one occurrence.

**NEW** is derived: `first_seen` falls inside the selected window. It is the answer to "did this
start with the deploy" and costs nothing to compute.

**States.** *Empty (never)* → "No exceptions recorded — that is the good outcome," not an error
shape. *Partial* → a group seen once has no trend and says "first seen 2m ago" in the column instead.

**Backend:** an occurrences-per-bucket series per group. `ErrorGroupSummary` has no series today.

---

## 3 · Synthetics — `/synthetics`

**Answers:** is the site up from outside, and is it getting slower?
**Does not show:** check *definitions* — those are settings, reachable from here but not this screen.

**Reads:** `GET /synthetics/checks` → `CheckResultSummary`: `uptime_percent`, `up`,
`latest_status_code`, `mean_duration_ms`, `max_duration_ms`, `latest_run_at`, `tls.days_remaining`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Synthetic checks                                       [ Last 24h ▾ ]    │
├──┬───────────────────────────┬─────────┬──────────┬─────────┬────────────┤
│  │ CHECK                     │  UPTIME │     MEAN │     TLS │        24h │
├──┼───────────────────────────┼─────────┼──────────┼─────────┼────────────┤
│ ●│ checkout flow     browser │  97.2 % │  4,210ms │       — │ ▇▇▇▁▇▇▇    │  ← the gap IS the story
│  │ www.example.com     http  │ 100.0 % │    182ms │  61 days│ ▃▃▃▃▃▃▃    │
│  │ api.example.com     http  │ 100.0 % │     94ms │   9 days│ ▂▂▂▂▂▂▂    │  ← ▪ warn on TLS
└──┴───────────────────────────┴─────────┴──────────┴─────────┴────────────┘
```

**The sparkline here is duration, and a failed run is a gap in the line, not a zero.** A down check
that rendered as 0 ms would read as "instant," the opposite of true. This is the em-dash rule applied
to a chart.

**TLS expiry earns a column** because it is the one thing on this screen that fails silently and on a
schedule. `days_remaining` is already served; nothing displays it. Under 14 days is a warning even
when uptime is 100%.

**States.** *Empty* → "No checks yet" with a create action; this is the fastest zero-instrumentation
value in the product and the screen a new customer is least likely to find (Flow B). *Partial* →
`up: null` means not yet run; em dash, not "down."

**Backend:** an uptime/duration series per check. `PhaseBreakdownResponse` exists but is per-check
detail, not a list column.

---

## 4 · Infrastructure — `/infrastructure` *(Database merges in, IA2)*

**Answers:** is any machine or database in trouble?
**Does not show:** anything a customer's own collector is not sending.

**Reads:** `GET /infrastructure/hosts` → `NodeSummary` (`cpu_utilization`, `memory_utilization`,
`filesystem_utilization`, `stale`, `signals[]`, all nullable) · `GET /dbstats/statements` →
`StatementCost` (`query_hash`, `latest_mean_ms`, `worst_mean_ms`, `calls_in_window`) ·
`GET /dbstats/connections` → `ConnectionStateCount`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Infrastructure        [ Hosts ]  [ Databases ]         [ Last 1h ▾ ]     │
├──┬───────────────────────────┬────────┬─────────┬────────┬──────────────┤
│  │ NODE                      │    CPU │  MEMORY │   DISK │           1h │
├──┼───────────────────────────┼────────┼─────────┼────────┼──────────────┤
│ ●│ ip-10-0-3-14    host      │  94.2% │   71.0% │  44.1% │ ▃▄▅▆▇█       │
│  │ worker-2        container │  31.0% │   58.2% │      — │ ▃▃▃▃▃▃       │  ← — is "not reported"
│ ▪│ ip-10-0-3-22    host      │      — │       — │      — │              │  ← stale: last seen 14m
└──┴───────────────────────────┴────────┴─────────┴────────┴──────────────┘
```

Two views, one destination. **`stale` is a first-class state, not a styling detail** — a node that
stopped reporting is the most important row on the screen and the one a naive design shows as
healthy. It renders em dashes with a warning mark and "last seen 14m ago", never zeros.

The **Databases** view is the same archetype over `StatementCost`, ranked by
`calls_in_window × latest_mean_ms` — total time spent, not slowest single query. `query_hash` is the
subject; **no statement text exists to show**, by design (ADR-0101), and the screen says so rather
than leaving a blank column.

Reachable **scoped from a trace's database span** (Flow A, step 7), which is how it is mostly reached.

**States.** *Partial* is the normal case here, not an edge: every metric on `NodeSummary` is
nullable. *Truncated* — `NodeListResponse.truncated` must be surfaced; a silently cut list is a lie.

**Backend:** a utilization series per node; a cost series per statement.

---

## 5 · The shared row — one component, five screens

| Screen | Subject | Primary measure | Ranked by | Trend |
|---|---|---|---|---|
| Services | service name | error rate | absolute error count | requests/min |
| Errors | exception type | occurrences | occurrences | occurrences |
| Synthetics | check name | uptime | failures, then duration | duration (gaps = down) |
| Infra · Hosts | node | worst utilization | worst utilization, stale first | utilization |
| Infra · Databases | query hash | total time | calls × mean | total time |

The column *meanings* differ; the column *positions* and the row's grammar do not. That is what makes
five screens one design.

## Open questions for this batch

1. **Widen `ServiceHealth` with p95?** Recommended yes — the prototype assumes it and the rollup
   already has it. Otherwise the column is removed from the archetype for every screen.
2. **Ranking `notification-worker`** — 50% of 96 requests currently ranks below 4.1% of 1,204. Correct
   for a Dev triaging impact; possibly wrong for a small customer where 96 requests is the whole
   business.
3. **Is `stale` a warning or a critical?** A node that stopped reporting may be scaled down on
   purpose or may be on fire, and nexovue cannot tell the difference.

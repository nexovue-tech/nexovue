# Decision log, 0001–0112

> **Type:** Decision index · **Status:** Compiled 2026-09-12 from the 112 decision records that
> existed before the current `ADR-NNN` series.
> **Companion docs:** [README.md](README.md) (the current ADR series) ·
> [`../architecture/current-system-reference.md`](../architecture/current-system-reference.md)

## What this is

Between 2026-08 and 2026-09-10, 112 decision records were written under
`docs/architecture/decisions/` — 10,206 lines. They were deleted in commit `e0f11a0`. This is the
index of what they decided, so a question can be answered without reading 112 files, and so a settled
decision is not re-litigated by someone who never knew it was settled.

**This index is not a replacement for them.** It records *what* was decided. The deleted records hold
*why* — the alternatives rejected and the measurements that decided it — and that is the part no
amount of reading the code can recover. Where this index and a full record disagree, the record wins.

## Reading the full text of any decision

Every record is intact in git history:

```bash
git show e0f11a0^:docs/architecture/decisions/0075-block-deduplication-replaces-the-read-before-retry.md
git show e0f11a0^ --name-only -- docs/architecture/decisions/    # list all 112 filenames
```

Cross-references inside a record use the original four-digit numbers, which is why this index keeps
them rather than renumbering into the `ADR-NNN` series. Renumbering would break the link graph, and
the graph is a large part of what makes a chain of decisions readable.

## Where the 112 stand

| Verdict | Count | Meaning |
|---|---|---|
| stands | 92 | Still the decision, as far as the record shows |
| amended | 12 | Still stands, with a specific claim corrected by a later record |
| superseded | 5 | Replaced outright |
| claim withdrawn | 1 | 0061 — the decision holds, its title's causal claim does not |
| deferred | 1 | 0053 — revisit in production |
| proposed | 1 | 0047 — flagged, never decided |

A verdict here is what the *record* says about itself, not a re-verification against today's code.
Code remains current-state truth.

## The chains — decisions that changed

These are the highest-value entries, because they are the ones someone will otherwise re-propose.

| Chain | What happened |
|---|---|
| 0002 → **0020** → **0026** | Kafka chosen, replaced by Redpanda, reversed back to Apache Kafka 4 in KRaft mode the same day. **Kafka stands.** |
| 0035 → **0037** | MinIO for local dev, superseded by SeaweedFS. |
| 0038 → **0072** | Per-tenant retention by conditional TTL, superseded by retention-class partitioning. |
| 0051 → **0052** | Combined control-plane container, reverted before it shipped. |
| 0018 → **0034** | ClickHouse attribute storage moved from `Map` to the native `JSON` type. |
| 0008 → **0050** | "Small single-purpose services" dropped for one worker process with pipelines selected by config. The language split survives. |
| 0033 → **0041** | Colour contrast method amended — verified against APCA *and* WCAG 2.1 AA, not APCA alone. |
| 0060 → **0061** → **0075** | Counter idempotency. See below — this chain is not merely history. |
| 0111 → **0112** | A month's bill reverted from `rate x committed` back to what was actually monitored, after the payment processor changed. |
| 0032 | Base UI + shadcn/ui as the primitive layer — **decided, never installed**, withdrawn rather than deleted so the decision is not silently re-made. |

## One chain contradicts a current document

`0061` recorded a reproducible **+512 span overcount** across a ClickHouse restart and attributed it
to the deduplication log being lost. `0075` then measured that premise false — the log survives both
a graceful restart and a SIGKILL — and showed the overcount reproducing **identically on both sides**
of the fix meant to address it, across three chaos runs. `0061`'s own status carries the withdrawal.

The overcount is therefore real, bounded by KeeperMap, attributed to a different cause, and **open**.

[`../architecture/current-system-reference.md`](../architecture/current-system-reference.md) states
that the durable watermarks let workers "survive a restart without losing or double-counting data."
That overclaims. Correcting it needs its own change record; it is named here so the contradiction is
not lost a second time.

## Records that should be restored in full

Twelve records carry measurements, falsified premises, or reproduction evidence — the material that
cannot be re-derived from code and that this index can only gesture at:

| # | Lines | Why |
|---|---|---|
| 0096 | 514 | Egress proxy judging addresses at connect time — SSRF reasoning and testing |
| 0094 | 404 | Revoked partition's partial counter bucket |
| 0089 | 432 | `text/plain` on the browser door — the `sendBeacon` constraint |
| 0078 | 257 | Queue edges from span links, not the parent link |
| 0090 | 221 | Rebalance drops buffered spans |
| 0080 | 209 | Naming an unanswered call's callee |
| 0081 | 206 | Leaked-key recovery and `last_used_at` throttling |
| 0061 | 138 | The overcount, and its withdrawn causal claim |
| 0075 | 120 | The falsified premise and three chaos runs |
| 0104 | 86 | Limiter fallback bound |
| 0056 | 72 | Pricing unit measurement |
| 0065 | 71 | Per-column compression codecs chosen by measurement |

Restoring these is roughly 2,500 lines and is a separate change. The other 100 are adequately served
by this index plus git.

## The full index


### Ingestion and the doors

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0001 | Ingestion edge is our own custom receiver | 2026-08 | **amended** | receiver language superseded in part by 0021 |
| 0016 | Ingestion protection lives in the receiver | 2026-08 | stands |  |
| 0055 | Receiver data-plane drivers, and why key lookups cache in process | 2026-08-27 | stands |  |
| 0081 | A leaked key has a way out, and `last_used_at` is throttled by the credential cache | 2026-08-31 | stands |  |
| 0086 | A key's kind is the table it lives in, and the public one is not hashed | 2026-08-31 | stands |  |
| 0087 | The Origin is what ties a public key to its owner, and it is abuse control, not authentication | 2026-08-31 | **amended** | Corrected in part by 0097 — the paragraph "What a scraped key costs its owner, in the worst case" was true when writte… |
| 0089 | The browser door reads `text/plain` as OTLP/JSON, because it is the only body `sendBeacon` can deliver | 2026-09-01 | stands |  |
| 0097 | The month's allowance is split per door, and the door is the kind of key that opened it | 2026-09-04 | stands |  |
| 0103 | A URL leaves the page trimmed to its path unless the page's owner asked otherwise | 2026-09-07 | stands |  |
| 0104 | A limiter that cannot count leaves the door open and bounds it in process | 2026-09-07 | stands |  |
| 0107 | Over the allowance we stop and say so, and the customer may raise the ceiling | 2026-09-07 | stands |  |

### Buffer, envelope, and delivery

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0002 | Kafka as the buffer between ingestion and processing | 2026-08 | **superseded** | Superseded by 0020 — which was itself reversed by 0026, so Kafka stands |
| 0020 | Redpanda replaces Kafka as the buffer | 2026-08-25 | **superseded** | Superseded by 0026 — reversed the same day |
| 0024 | The internal normalized envelope is a frozen contract | 2026-08-25 | stands |  |
| 0026 | Buffer = Apache Kafka 4.x in KRaft mode | 2026-08-25 | stands |  |
| 0030 | Internal envelope = a thin protobuf wrapper around OTLP | 2026-08-25 | **amended** | the "forward the same bytes" description corrected for traces by 0045 |
| 0045 | Envelope contract frozen | 2026-08-26 | stands |  |
| 0085 | A batch is bounded in rows and bytes, not only in messages | 2026-08-31 | stands |  |
| 0090 | A partition that leaves takes its buffered spans with it | 2026-09-01 | stands |  |
| 0095 | An ingest watermark is never restored, in either store | 2026-09-04 | stands |  |

### Workers and processing

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0009 | Trace assembly = in-worker memory plus Kafka partitioning by trace_id | 2026-08 | **amended** | superseded in part by 0054: the ~30s wait window only. Partitioni… |
| 0010 | Tail sampling defaults | 2026-08 | stands |  |
| 0050 | One worker process, pipelines selected by config | 2026-08-26 | stands |  |
| 0054 | Spans are written through; the assembly buffer arrives with tail sampling | 2026-08-27 | stands |  |
| 0058 | The tail-sampling window is ten seconds, and the lag budget stands | 2026-08-27 | **amended** | one claim in Consequences corrected by 0079 |
| 0059 | Masking is on by default only for what can never be needed | 2026-08-27 | stands |  |
| 0064 | `span_kind` as a dimension of `endpoint_stats_1m`, not just a filter | 2026-08-28 > **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named | stands |  |
| 0078 | Queue edges are derived from span links, not from the parent link | 2026-08-30 | stands |  |
| 0079 | Span rows are idempotent across a restart, on a watermark with no bracket | 2026-08-30 | **amended** | the residual bound in its Consequences is corrected in part by 0090: under a rebalance it was the whole last sampling … |
| 0088 | The first exception event on a span fills the promoted columns | 2026-08-31 | stands |  |
| 0091 | Log and metric rows are idempotent across a restart, on one watermark shared by both signals | 2026-09-01 | stands |  |
| 0092 | A host metric whose resource named no machine is grouped by the process that reported it, and is never called a host | 2026-09-01 | stands |  |
| 0094 | A revoked partition's partial counter bucket is dropped | 2026-09-04 | stands |  |
| 0105 | Sampling and masking are the customer's settings, and only the off-switch is guarded | 2026-09-07 | stands |  |

### ClickHouse and the telemetry schema

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0003 | ClickHouse for telemetry, Postgres for the control plane | 2026-08 | stands |  |
| 0011 | Promote error fields to real columns | 2026-08 | stands |  |
| 0012 | Multi-tenancy = shared tables with tenant_id first in ORDER BY | 2026-08 | stands |  |
| 0018 | Data layer design | 2026-08 | stands |  |
| 0034 | ClickHouse attributes use the native `JSON` type, not `Map` | 2026-08-25 | stands |  |
| 0038 | Per-tenant retention via conditional TTL | 2026-08-26 | **superseded** | Superseded by 0072 |
| 0049 | project_id in the spans sort key | 2026-08-26 | stands |  |
| 0057 | `project_id` in every telemetry sort key, not just spans | 2026-08-27 | stands |  |
| 0060 | Counter idempotency rests on ClickHouse's own insert deduplication | 2026-08-27 | **amended** | superseded in part by 0061: it does not survive a ClickHouse resta… |
| 0061 | Insert deduplication does not survive a ClickHouse restart | 2026-08-27 > **Correction, 2026-08-29 (0075).** The claim in this record's title — and in the sentence below beginning *"the deduplication log that would have recognised it did not survive the restart"* — is **withdrawn**. It was inferred from the chaos observation, not measured. Measured since, on the pinned `clickhouse:26.3.20.7`: an identical block re-inserted after a graceful restart, and again after a `SIGKILL`, was dropped both times — the sum stayed at 100 rather than doubling to 200, with `uptime()` confirming 13s and 2s respectively. The deduplication log survives both. | **claim withdrawn** | 2026-08-27 — remedy implemented and proven. **Title claim withdrawn 2026-08-29**, see below; the decision and its reme… |
| 0062 | Log body as rendered text beside a typed map | 2026-08-28 | stands |  |
| 0063 | Daily partitions on the trace-id lookup table | 2026-08-28 > **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named | stands |  |
| 0065 | Compression codecs chosen per column, by measurement | 2026-08-28 | stands |  |
| 0066 | Five metric tables, for OTLP fidelity | 2026-08-28 | stands |  |
| 0067 | Storage partitions and expires on our clock, not the client's | 2026-08-28 > **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named | stands |  |
| 0068 | KeeperMap tables are excluded from a restore | 2026-08-28 | stands |  |
| 0069 | Severity before time in the log sort key | 2026-08-28 > **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named | stands |  |
| 0072 | Retention is stamped on the row and partitioned by | 2026-08-28 | stands |  |
| 0073 | Migrations squashed to one file per object | 2026-08-28 | stands |  |
| 0074 | Insert deduplication belongs on a materialized view's target, not only on its source | 2026-08-29 | stands |  |
| 0075 | The read-before-retry is removed; it lost late data and its premise was false | 2026-08-29 | stands |  |
| 0084 | Newest first is a sort-key read of a per-trace row written after the sampler | 2026-08-31 | stands |  |

### Control plane, tenancy, and integrations

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0004 | pgvector for RAG, inside Postgres | 2026-08 | stands |  |
| 0005 | Redis as the utility layer | 2026-08 | stands |  |
| 0042 | Tenancy model — organization is the tenant, project is the environment | 2026-08-26 | stands |  |
| 0043 | Identity — one user belongs to one organization | 2026-08-26 | stands |  |
| 0044 | Control-plane schema | 2026-08-26 | stands |  |
| 0070 | The audit log records actors it does not own | 2026-08-28 | stands |  |
| 0076 | Door 4b holds no customer credential it can avoid holding | 2026-08-29 | stands |  |
| 0100 | A database we cannot reach is polled by the customer's own collector | 2026-09-06 | stands |  |
| 0101 | Spans carry query text and the database poller does not | 2026-09-06 | stands |  |
| 0102 | Email and payments arrive behind a port, and the port is written first | 2026-09-07 | stands |  |

### Frontend and design

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0023 | Frontend = Vite + React + TanStack Router/Query, uPlot for time series | 2026-08-25 | stands |  |
| 0028 | Frontend router = TanStack Router | 2026-08-25 | stands |  |
| 0031 | Service-map rendering = React Flow (xyflow) | 2026-08-25 | stands |  |
| 0032 | UI stack = Tailwind v4 + Base UI, scaffolded with shadcn/ui | 2026-08-25 | stands |  |
| 0033 | Colour system — OKLCH, dark-first, and health is the absence of colour | 2026-08-25 | **amended** | contrast method amended by 0041 |
| 0041 | Colour verified against APCA *and* WCAG 2.1 AA | 2026-08-26 | stands |  |

### Billing and pricing

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0056 | Pricing unit is the service, not the gigabyte | 2026-08-27 | **amended** | the *Schema follows* paragraph is discharged by 0077, which also settles where a price lives. The unit, the allowance … |
| 0071 | Plan tiers, and retention as a product statement | 2026-08-28 | stands |  |
| 0077 | The billing unit is a table, and the price is a dated row beside it | 2026-08-30 | **amended** | **superseded in part** by 0083, which settles the three questions section 4 refused to invent answers to and therefore… |
| 0083 | The period is the calendar month, the quantity is the union, and the allowance is bytes | 2026-08-31 | stands |  |
| 0093 | The free tier ends on bytes, uniformly, and says so in three places | 2026-09-04 | **amended** | Superseded in part by 0097 — the allowance below is one pooled number refused at every door, which made a scraped publ… |
| 0109 | Cashfree stays, and the processor declares the contact details it needs | 2026-09-08 | stands |  |
| 0110 | The webhook names the subscription, and we answer whose it is | 2026-09-09 | stands |  |
| 0111 | The bill is the commitment, and the processor multiplies it | 2026-09-10 | stands |  |
| 0112 | The bill returns to what was monitored, and "service" has to mean one | 2026-09-10 | stands |  |

### Scheduler, probes, and pollers

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0007 | Doors 2 and 4b (probes and pollers) run on Celery | 2026-08 | stands |  |
| 0036 | We write our own Celery Beat scheduler | 2026-08-25 | stands |  |
| 0096 | A browser flow's only way onto the network is a proxy that judges the address | 2026-09-04 | stands |  |
| 0098 | A probe reaches a private address only when two settings both say development | 2026-09-06 | stands |  |
| 0099 | The demo storefront gets our SDK from the proxy in front of it | 2026-09-06 | stands |  |
| 0106 | Network telemetry is accepted and never collected, and SIEM is out of scope | 2026-09-07 | stands |  |
| 0108 | A web vital is grouped by the customer's route template, and falls back to the path | 2026-09-08 | stands |  |

### Infrastructure and deployment

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0006 | Object storage as the cold tier | 2026-08 | stands |  |
| 0015 | Deployment = all services as containers, on-premise docker-compose for now | 2026-08 | stands |  |
| 0035 | Object storage — MinIO pinned for local dev, AWS S3 in deployment | 2026-08-25 | **superseded** | Superseded by 0037 |
| 0037 | Object storage = SeaweedFS (local dev) + AWS S3 (deployment) | 2026-08-26 | stands |  |
| 0046 | Recorded — dev machine is an Apple M4 Pro, 24 GB | 2026-08-26 | stands |  |
| 0047 | Flagged for Phase 2 — Ollama will not run usefully on the dev machine | 2026-08-26 | **proposed** | Proposed (flagged, not decided) |
| 0048 | Valkey instead of Redis | 2026-08-26 | stands |  |
| 0051 | Combine the control plane and Celery into one container | 2026-08-26 > **Reconstructed record.** This decision was made and reversed within a single working | **superseded** | Superseded by 0052 — reverted before it shipped |
| 0052 | The full production container inventory | 2026-08-26 | stands |  |
| 0053 | nexovue monitors nexovue, with an out-of-band escape hatch | 2026-08-26 | **deferred** | Deferred 2026-08-26 — revisit once the platform is in production |

### Language, stack, scope, and sequencing

| # | Decision | Date | Verdict | What changed |
|---|---|---|---|---|
| 0008 | Production language split — Go on the data plane, Python on the control plane | 2026-08 | stands |  |
| 0013 | Anomaly detection v1 is statistical, not ML | 2026-08 | stands |  |
| 0014 | LLM strategy = provider-flexible behind our own interface | 2026-08 | stands |  |
| 0017 | Full-feature audit — core architecture unchanged, nine additions adopted | 2026-08 | stands |  |
| 0019 | Phase 1 sequencing = walking skeleton, then widen | 2026-08-25 | stands |  |
| 0021 | Go on the data plane from day one | 2026-08-25 | stands |  |
| 0022 | Monorepo, polyglot | 2026-08-25 | stands |  |
| 0025 | Phase 1 signal, category and door coverage | 2026-08-25 | stands |  |
| 0027 | ClickHouse write path uses `ch-go`, not `clickhouse-go/v2` | 2026-08-25 | stands |  |
| 0029 | Confirmed by research: franz-go, uv, uPlot, Biome, Celery | 2026-08-25 | stands |  |
| 0039 | Confirmed by research: Go workers and our own tail sampling both stand | 2026-08-26 | stands |  |
| 0040 | Risk accepted — OTel browser instrumentation is experimental | 2026-08-26 | stands |  |
| 0080 | An unanswered call's callee is named by what the caller asserted, and never by an IP or a transport | 2026-08-30 | **amended** | Its open question — should a bare IP be allowed to name a node? — is answered by 0082: the address is still stored, an… |
| 0082 | A peer named by a bare IP is stored as it was asserted and collapsed into one node for display | 2026-08-31 | stands |  |

---

## How this index is kept honest

It is a snapshot of what 112 records said about themselves on 2026-09-12. It is not re-verified
against the code and does not claim to be. When a decision here is acted on, read the full record
first — and if the code disagrees with it, the code is right and the record is history.

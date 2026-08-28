# 0071 — Plan tiers, and retention as a product statement

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

[0038](0038-per-tenant-retention-via-conditional-ttl.md) built the mechanism for per-tenant
retention — conditional TTL regenerated from plan tiers in Postgres — and left the tiers themselves
undecided. `organizations.plan` exists, defaults to `'free'`, and carries a comment saying as much.
Nothing reads it. Every tenant currently gets the same fixed retention: 30 days for spans and logs,
90 for metrics, 13 months for the endpoint rollup.

[0056](0056-per-service-pricing.md) fixed the billing unit as the service, with volume overage kept
live and a ceiling on the service-count component. It records that a `plans` table is still
required.

## What the market does

| | free | paid |
|---|---|---|
| **Datadog** | 1 day (metrics) | 15 days traces and logs, 15 months metrics |
| **Grafana Cloud** | 14 days | 30 days logs, 13 months metrics |
| **Honeycomb** | 60 days | 60 days — the same for everyone, deliberately |
| **Better Stack** | 3 days | 30 days |

Two patterns hold across all of them. **Raw signals live 15–30 days; aggregated metrics live 13–15
months.** The split is not arbitrary — raw traces are large and are read while an incident is warm,
aggregates are tiny and are read for trends. Honeycomb is the interesting outlier: it refuses to
make retention a pricing lever at all, and says so as a marketing position.

## Decision

**Four tiers, and retention split by what the data is for rather than by signal type.**

| tier | raw signals — spans, logs | derived — metrics, rollups, findings |
|---|---|---|
| `free` | **7 days** | **30 days** |
| `pro` | **30 days** | **13 months** |
| `scale` | **90 days** | **13 months** |
| `enterprise` | negotiated | negotiated |

One number per tier for everything raw. Not a number per signal per tier, which would be twelve
values to explain and would contradict the predictability that 0056 makes the whole pricing thesis.

### Why raw is short and derived is long

This follows from what the product is. nexovue's value is the finding and the plain-language
explanation, not the archive — the raw span is the *input*, and nobody browses a ninety-day-old
span. What people do want a year later is *"was this endpoint always this slow"*, and that question
is answered by `endpoint_stats_1m`, which is already 13 months and is tiny.

So the product statement is: **we keep your answers for a year, not just your raw data for a
fortnight.** That is cheaper for us than Datadog's shape and more useful than it, which is a rare
combination and worth saying out loud in pricing copy.

### Why free gets 7 days

Long enough to debug last week, short enough that nobody runs a business on it. Between Better
Stack's 3 and Grafana's 14. Free tenants keep 30 days of derived data so the product's actual value
— the insight layer — is visible during a trial rather than gated behind payment.

### Why `pro` is 30 and not 15

Datadog's 15 days is the number customers complain about, and 30 is the number Grafana and Better
Stack both settled on. For **traces** the extra fortnight is nearly free: spans measure **17.9
bytes** on disk (0065) and marginal compute is **$0.0017 per million** (0056).

For **logs** it is not free, and the next section does that arithmetic rather than assuming the
trace economics carry over. They do not — sampling discards most traces and nothing discards logs.
30 days still holds after that check; 90 does not, without the cold tier.

## What it costs — the arithmetic, and what is missing from it

Measured on the live table today: a span is **17.9 bytes** on disk after codecs, compressing 10.6×.
A `metrics_sum` row is **5.3 bytes**, and `endpoint_stats_1m` is small enough to ignore — the
13-month derived retention is close to free, which is what makes the second column of the table
above affordable.

Storage, per GB-month: **EBS gp3 $0.08**, **S3 Standard $0.023**, **S3 Infrequent $0.0125**. Local
disk is 3.5× S3 and 6.4× S3-IA.

A mid-size tenant — 20 services, ~500 req/s, 8 spans per request, tail sampling keeping ~15%:

| | per day | 30 days | 90 days |
|---|---|---|---|
| spans (measured rate) | 0.93 GB | 28 GB | 84 GB |
| logs (**estimated**, see below) | ~6 GB | ~180 GB | ~540 GB |
| **on gp3** | | **$16.60/mo** | **$50/mo** |
| **on S3 Standard** | | $4.80/mo | **$14.40/mo** |

Against per-service revenue for twenty services, 30 days on local disk is a few percent of the
bill. **Ninety days on local disk is not** — it is where a tier stops paying for itself, which is a
second reason `scale` waits for the cold tier rather than only a capacity one.

**The number above that is not measured is the one that dominates.** `logs` holds zero rows, because
P1-28 has not shipped. The log line above assumes four lines per request at ~35 bytes compressed,
and on that assumption **logs are roughly 87% of stored bytes** — because tail sampling discards
most traces and nothing discards logs. Every per-GB price in this market exists because of logs, not
traces.

So this decision's cost model rests on an estimate for its largest term. **It must be re-checked
against real numbers once P1-28 ships**, and that is a task, not a note.

### First measurement, 2026-08-28

The log pipeline shipped and the demo app now emits logs, so the estimate has a
first reading against it:

| | rows | bytes/row on disk | compression |
|---|---|---|---|
| `spans` | 12,042,879 | **17.9** | 10.8× |
| `logs` | 65 | **85.2** | 3.5× |

**This does not settle it, and must not be quoted as if it does.** Sixty-five
rows give ZSTD almost nothing to work with — the ratio is 3.5× against spans'
10.8× on twelve million — so the per-row figure is an upper bound that will fall,
probably a long way, as volume arrives. The estimate above assumed ~35 bytes.

What the reading does establish is direction and rough scale: a log row is
several times a span row, which is what the model assumed. The task on P1-28
stays open until there is a sample large enough to divide by.

## The cost control is the volume allowance, not the retention day count

If measured log volume comes in far above the estimate, the lever to pull is **not** shortening
retention. [0056](0056-per-service-pricing.md) already priced a volume allowance per service with
overage above it. That meter targets the tenant actually generating the cost; cutting retention
degrades the product for every tenant to discipline a few.

Retention is therefore a **product feature** with a cost consequence, and volume is the **meter**.
Keeping those two jobs separate is what stops retention drifting into a per-GB bill by the back
door, which 0056 rejected on purpose.

## Consequences

**`scale` cannot be sold until the cold tier ships.** [0006](0006-object-storage-cold-tier.md) sets
the hot window at 15–30 days with S3 behind it, and that tiering is decided but **not implemented** —
no storage policy is configured and ClickHouse never writes to SeaweedFS. Ninety days today would
mean ninety days on local disk. `free` and `pro` are both inside the hot window and need nothing new.

**The mechanism is still unbuilt.** Nothing reads `organizations.plan`, there is no `plans` table,
and no job regenerates the TTL expression. Until that exists every tenant gets today's fixed
retention, which is `pro`-shaped. That is the safe direction — nobody is under-served — but it means
free tenants currently receive more than this decision grants them.

**Retention is a downgrade hazard.** Moving a tenant from `scale` to `free` shortens their TTL, and
the next merge drops the parts. Data is gone with no undo. Whatever applies a plan change must warn
before narrowing retention, and a downgrade should be a scheduled action rather than an immediate
one.

**Enterprise is deliberately not a number.** Long retention on request is a negotiation, and the
mechanism supports any value per tenant, so nothing needs deciding here in advance.

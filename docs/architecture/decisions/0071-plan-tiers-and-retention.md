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

Datadog's 15 days is the number customers complain about. Matching Grafana's 30 costs us almost
nothing: measured marginal cost is **$0.0017 per million spans** (0056), and spans compress to
**16.4 bytes** each (0065). Ninety days of a busy tenant is single-digit gigabytes. Retention is not
where this product's cost lives, so it is a poor place to squeeze.

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

# 0056 — Pricing unit is the service, not the gigabyte

- **Status:** Accepted
- **Date:** 2026-08-27

## Context

`vision-and-scope.md` §5 carried a direction — *per-site/per-service, predictable, not per-GB* —
and deferred the decision. M1 made it decidable, because it produced the first real cost numbers:
the receiver sustains 260,267 spans/sec per core, ClickHouse stores a span in 128 bytes once the
trace-id lookup table is counted, and tail sampling keeps roughly 15%.

That puts our marginal cost at **$0.0017 per million spans**. Dash0 lists **$0.60** for the same
million. The concern that framed this question — that per-service decouples price from cost and
threatens margin — turned out to be false at these numbers: infrastructure is a rounding error
against any defensible price, and the conclusion survives the pipeline costing 100× more CPU than
measured.

A market scan found **no vendor pricing per service**. The field is per-host, per-agent, per-GB
ingested, per-CPU-core, or per-signal. Absence of precedent is the main risk accepted below.

## Decision

**Price per service monitored**, with a volume allowance per service and a ceiling on the
service-count component of the bill.

Four promises the structure exists to make, each answering a specific recorded market complaint:
the bill has a ceiling · never per seat · never per host · over the allowance we **block and say so
rather than bill**. That last one follows the market's own answer to quota exhaustion.

The ceiling is not generosity. Flat per-service would charge a 120-service customer roughly $3,000
against Datadog's $700 sticker, penalising precisely the microservice architecture this product is
best at. The cap applies to the **service-count component only**; volume overage above the
allowance stays live, so growth still pays.

**Rejected: per-GB ingested**, which is what most of the field does. It fails us for two reasons
beyond the generic complaint. First, the bill is unpredictable in a way neither side controls: the
same traffic bills across a 10× range depending only on how richly a customer's SDK is configured —
71 bytes for a bare span, measured on our own ingestion topic, against roughly 1,200 for one with
custom attributes and events. A library upgrade that adds attributes moves the invoice while nothing
about the business changed. Second, and specific to us, **per-GB taxes instrumentation**: every
attribute a customer adds raises their bill, and Phase 2's entire differentiation — detection,
findings, the assistant — is derived from that data. Datadog can carry that trade because they sell
dashboards; a product selling conclusions cannot pay customers to send less.

## Consequences

Pricing becomes a position rather than a line item, and one competitors cannot copy without
abandoning their own model. Finance gets a predictable number; engineering is never punished for
instrumenting more.

**Costs accepted.** Per-GB earns more at the top end — roughly 2.4× on a 120-service customer — and
choosing this forfeits that. The unit is unprecedented, and no evidence was found either way about
whether others tried and failed. And per-service does not track value cleanly: twenty small
functions cost more than three large monoliths while delivering less. The cap blunts the extreme;
the objection remains real and will be raised by customers.

**Falsifiable test.** If buyers ask *"what is that in gigabytes?"*, the unit has failed and we
switch. If they ask *"what counts as a service?"*, they have accepted it and are scoping. Pricing is
reversible while customers are few, which is what makes this bet cheap.

**Not yet modelled: Phase 2 inference.** Insight generation runs a model per finding, and that cost
scales with incidents and services rather than spans — the one place this unit could genuinely
break. No tier bundling findings may be published until it is measured.

**Schema follows.** `usage_daily` counts spans, logs, metric points and bytes — the shape per-GB
would have wanted. The billing unit now needs *distinct `service.name` per project per day*, with
the byte counters kept as the guardrail meter rather than the bill. A `plans` table is still
required; [0038](0038-per-tenant-retention-via-conditional-ttl.md) already reads tiers from Postgres
that do not exist.

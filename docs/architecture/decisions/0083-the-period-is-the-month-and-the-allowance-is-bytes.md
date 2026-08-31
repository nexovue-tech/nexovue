# 0083 — The period is the calendar month, the quantity is the union, and the allowance is bytes

- **Status:** Accepted
- **Supersedes in part:** [0077](0077-the-billing-unit-is-a-table-and-the-price-is-a-dated-row.md) — its
  section 4, *No cost is computed*, and only that. The three schema objects, the refusal to price
  from ClickHouse, and the settled-flag design all stand.
- **Amends:** [0056](0056-per-service-pricing.md) — it required a volume allowance per service and
  never gave it a unit. This gives it one.
- **Date:** 2026-08-31

## Context

[0077](0077-the-billing-unit-is-a-table-and-the-price-is-a-dated-row.md) put a defensible quantity
and a defensible rate side by side and refused to multiply them, naming three things that would
have to be invented first: the billing period, which quantity that period bills, and the unit of
0056's volume allowance. All three are now decided, so the refusal has nothing left to stand on and
the screen can show what a month costs.

## Decision

### 1. The billing period is the calendar month

**Rejected: the anniversary of signup.** Every customer would be mid-period at a different moment,
every proration question becomes bespoke, and no two usage screens are comparable — ours or a
customer's against a colleague's. It is also what the field does: Datadog "calculates fees based on
product usage during each calendar month"
([Datadog pricing](https://docs.datadoghq.com/account_management/billing/pricing/), read
2026-08-31).

The period is the calendar month containing the last day the caller asked for, so it is the current
month on a screen looking at today, and August when someone goes back to look at August.

### 2. The billable quantity is the union of distinct services over that period

*"You ran twelve services in August, you pay for twelve."* A service running every day of the month
is one service that month. `queries.services_in_range` already computed exactly this — a union, not
a sum of the days — and it is now what the cost is computed from.

**Rejected: peak-day and high-water-mark billing.** Datadog bills infrastructure hosts on "the
maximum count (high-water mark) of the lower 99 percent" of hourly readings, dropping the top 1% "to
reduce the impact of spikes in usage on your bill"
([Datadog pricing](https://docs.datadoghq.com/account_management/billing/pricing/)). Even with that
softening, the standing complaint is that "a few hours of autoscaling spikes can raise the whole
month's bill" ([OpsLyft](https://www.opslyft.com/blog/datadog-pricing)). We would be adopting the
mechanism *and* its known failure, to earn more from the customers who scale — which is the opposite
of the predictability 0056 sells. Predictable beats optimal for a first product.

### 3. The volume allowance is ingested bytes, per billed service, per month — pooled

Bytes because it is the only unit that spans traces, logs and metrics, and because it is what
actually costs storage and transfer. **Counting spans would penalise a well-instrumented service for
being well instrumented**, which is precisely the tax on instrumentation 0056 rejected per-GB
pricing to avoid. Bytes are already metered end to end in `usage_daily.bytes`, so this needed no new
counter and no change to the metering worker.

**Pooled across the organization**, not enforced service by service: each billed service brings its
allowance and the month's total ingestion is measured against the sum. That is what the field does —
Datadog's per-host span allowance is "summed over all the APM hosts", so a quiet host lends its
unused allowance to a busy one.

`plan_prices.included_bytes_per_service` (Alembic `0013`) holds it. **NULL is "no allowance
recorded", which is a different statement from an allowance of zero** — a zero would report every
byte ingested as overage. The table still ships with no rows.

### 4. The cost shown is the service-count component, and it says so

`billed_services × price_per_service_minor_units`, where `billed_services` is the month's union held
down by `max_billed_services`. Four refusals survive from 0077 and are tested:

- **No price recorded, no cost.** Never a zero — an unpriced tier is not a free one.
- **A month still being metered is labelled**, not shown as a quiet small number. Same frontier, same
  rule as the per-day flag.
- **Money divides by the currency's own ISO 4217 exponent.** The yen's minor unit is the yen.
- **The cost is the organization's**, even when the day table is narrowed to one project. A narrowed
  cost is a number nobody is ever charged.

### 5. `price_per_service_cents` is renamed `price_per_service_minor_units`

0077 asked the next migration touching this table to do it; `0013` is that migration. The table has
never held a row in any database, so the rename moved no data.

### 6. Cardinality is warned about, and never capped

The billing unit is distinct service names per project per day, so a customer whose `service.name`
carries a pod id inflates their own bill by that factor — the gap 0077 recorded as "a real gap and
it is billing-visible". The causes are the well-documented ones: per-pod and per-container labels,
uuids, request and session identifiers
([Hydrolix](https://hydrolix.io/glossary/cardinality-explosion/)).

**A cap is the one response we will not make.** Capping means dropping a customer's telemetry to
hold their bill down, and they would then be debugging an outage with data we threw away. For an
observability product that is the worst available failure. So the screen shows the change ("twelve
services, up from three in the seven days before") and, where it can, the cause.

**The cause is detected conservatively, because a false accusation is worse than silence.** A single
odd name is never reported. What is reported is a *family*: three or more distinct names sharing a
stem, each ending in a separator followed by one of four narrow shapes — a canonical uuid, a run of
at least eight hex characters, a Kubernetes pod suffix, or a bare ordinal. `checkout-7` alone is a
service called `checkout-7`; `checkout-0`, `-1` and `-2` together are a StatefulSet. The Kubernetes
shape is matched against that generator's own alphabet, `bcdfghjklmnpqrstvwxz2456789` — vowels
omitted upstream "to reduce the chances of 'bad words' being formed"
([k8s.io/apimachinery/pkg/util/rand](https://github.com/kubernetes/apimachinery/blob/master/pkg/util/rand/rand.go)),
which is what makes it safe: an English word cannot be spelled in it.

The advice given is the specification's own. `service.name` "MUST be the same for all instances of
horizontally scaled services", and what tells instances apart is `service.instance.id`
([OpenTelemetry service semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/service/)).
So the warning is actionable rather than a scold.

## The contradiction inside 0056, which this record does not resolve

[0056](0056-per-service-pricing.md) says two different things about what happens when a customer
goes over the volume allowance, and building the cost figure is what surfaced it. Both lines, as
written:

> over the allowance we **block and say so rather than bill**

and, two paragraphs later:

> The cap applies to the **service-count component only**; volume overage above the allowance stays
> live, so growth still pays.

**These are two different products.** One promises a customer that their bill cannot run away and
accepts that their telemetry stops; the other promises their telemetry never stops and accepts that
their bill can. They imply different contracts, different support load, and different behaviour in
the exact moment a customer is having an incident.

This ADR **does not choose**, because choosing would be inventing a customer promise nobody made.
The consequence is carried in the product: **the cost figure covers the service-count component
only**, and the screen says so in words — *"This covers the services only. What volume above the
included allowance costs is not decided, so nothing for it is included here."* When the
contradiction is settled, the figure can grow an overage term; until then it is a partial number
that names what it omits, which is the only honest shape available.

## Alternatives considered

**Pricing the month on the rate in force on the month's last day.** Rejected. On the 15th it would
apply a repricing that starts on the 20th to a month that has not reached it. The rate used is the
one in force on the day being read.

**Enforcing the allowance per individual service.** Rejected, and not on principle — it has a
blocking prerequisite. Bytes are metered per message, one message can carry several services, and
attributing bytes to individual services means choosing how to split one payload's bytes between the
resources inside it: by span count, by serialized size, evenly. **That rule changes bills**, so it is
a decision to be taken deliberately, not an implementation detail to be picked while writing the
query. Pooling needs no such rule and has the precedent above. Revisit this only if per-service
enforcement is actually wanted; it is the one part of this that would require changing the metering
worker.

**Capping distinct service names per project.** Rejected — see 6. A cap is the failure mode this
product exists to prevent.

**Computing the cost in the browser.** Rejected. The screen would then be multiplying whichever
quantity it happened to be showing, and the quantity on screen is the *browsed range*, not the
month. The API does the multiplication and the browser reads it; the offline and live suites both
carry a test whose failure signature is exactly that mistake.

## Consequences

**What this buys.** The usage screen answers the question a customer came with. A month in progress
is labelled rather than quietly small. A customer inflating their own bill with pod ids is told, in
words, with examples, and with the attribute to move them into — before the invoice, not after it.

**What this costs.**

- **The endpoint issues four more reads per page load** — the month's services, the month's bytes,
  the two-window service counts, and the names of projects big enough to be a cause. **Not
  measured.** The dev database holds tens of rows, so nothing here has been timed against a table
  that could be slow, and this should be measured before a customer with real volume reaches it.
- **Name examination is capped at 500 names per project**, over the projects carrying eight or more
  services. A very large organization therefore does real work on a page load, and the cap means a
  family beyond the first few hundred names alphabetically can be missed. Also not measured.
- **The warning thresholds are chosen, not derived.** Eight services before a project is examined, a
  doubling inside seven days, three members before a family is named. They move no money — nothing is
  capped and no bill changes — but they will be wrong for somebody, and the only way to tune them is
  real customers.
- **A project with no previous week is never called a jump.** "Ten services, up from none" is a
  customer onboarding. Pod ids in that first week are still caught, by the names rather than by the
  change.
- **The cost is not a forecast.** It is what has accrued so far this month. `route-manifest.ts` and
  P1-48 both promised "projected cost" and have been reworded, because a projection would need an
  assumption about the rest of the month that nobody has made.

**Still open, and each one is about money.**

- **The price itself.** `plan_prices` remains empty. Nothing shows a cost until a row is inserted,
  and that row is a person's decision.
- **The allowance amount.** `included_bytes_per_service` is NULL everywhere, so no allowance is shown
  and no overage can be computed.
- **What happens above the allowance** — the contradiction above.
- **A repricing that starts mid-month.** Current behaviour, so whoever decides is not guessing at it:
  the month prices at **the rate in force on the day being read**, so a rate starting on the 20th
  does not price a month sitting on the 15th, and the same month reads differently before and after
  the change. Whether a month spanning two rates should be split, priced at the old rate, or priced
  at the new one is undecided.

**What was verified.** 92 usage tests — 71 offline and 21 against real Postgres — all run and
watched. Six negative controls were run and watched to fail. **The one that matters most: removing
the Kubernetes alphabet check made the detector accuse `order-queue-cache`, `order-queue-store` and
`order-queue-index` — three honestly named services — of being one pod family.** That is the
false-positive risk made concrete, and it is why every constraint in the detector is there. The
others: removing `distinct` from the windowed service count made real Postgres report **7 services
for one service reporting on seven days**; pricing the month from the browsed range instead of the
month failed four tests; returning zero for an unpriced tier failed one; replacing the per-project
name cap with a plain `LIMIT` starved the second project of every name; and returning `$0.00` for an
unpriced month failed three web tests. Migration `0013` was proved up → down → up against the dev
Postgres, single-store.

**What was not verified.** No price row exists in any persistent database — the live tests insert one
inside a transaction they roll back, so a rate has now been read from real Postgres for the first
time, but nothing has ever been billed to anyone. No query here has been run against a table large
enough to time. No HTTP round trip was made against the running API; the live tests exercise the
service layer and its Pydantic models, not the router. The cardinality detector has never seen a
real customer's service names — every name it has been shown was written by us.

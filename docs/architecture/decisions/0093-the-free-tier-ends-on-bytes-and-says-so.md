# 0093 — The free tier ends on bytes, uniformly, and says so in three places

- **Status:** Accepted. Superseded in part by
  [0097](0097-the-allowance-is-split-per-door-and-the-door-is-the-kind-of-key.md) — the allowance
  below is one pooled number refused at every door, which made a scraped public site key able to
  end a customer's whole month. It is still one number pooled across the organization; a plan row
  may now divide it between the browser door and the server door, and each door is judged against
  its own share. Everything else here stands.
- **Settles in part:** [0056](0056-per-service-pricing.md) — its contradiction between "we block and
  say so rather than bill" and "volume overage above the allowance stays live, so growth still
  pays", which [0083](0083-the-period-is-the-month-and-the-allowance-is-bytes.md) recorded and
  deliberately did not choose. This chooses the first, **for the free tier only**, and leaves the
  second open for every tier that pays.
- **Amends:** [0083](0083-the-period-is-the-month-and-the-allowance-is-bytes.md) — its
  `included_bytes` figure now follows the plan's ceiling rather than the running service count. The
  period, the pooling, the quantity and everything in §6 stand.
- **Date:** 2026-09-04

## Context

**The free tier is currently free and unlimited.** `plan_prices.max_billed_services = 3` caps what
is *billed*, not what is *allowed*, and the free row's price is zero, so an organization running
eighteen services and sending a terabyte a month is charged $0.00 for ever. Nothing anywhere refuses
it. That is the whole of the problem.

**An earlier attempt was to block the fourth service, and it was abandoned before it was built.** The
billing unit is a service, so a service cap makes a whole service *disappear* rather than thinning
the data: `checkout` does not get sparser, it goes dark, and a dark service is indistinguishable
from one that is merely quiet — the single worst confusion to introduce into an observability
product. Simulated against the live store, first-seen ordering would have carried `accounting`, `ad`
and `cart` and refused `checkout` and `frontend`, which nobody would choose, and the product has
nowhere for a customer to choose differently. Sentry, PostHog and Grafana all cap on **volume** for
the same reason: losing the 1,000,001st event still leaves a million spread across everything.

## Decision

### 1. The boundary is bytes over the calendar month, org-wide

The period and the quantity are 0083's: the calendar month, pooled across the organization, measured
from `usage_daily.bytes` — the counter the meter already writes and the usage screen already shows.
Not per day, which would limit nothing; not per service, which needs a rule for splitting one
payload's bytes between the resources inside it, and that rule changes bills (0083 rejected it for
exactly that reason and it is still unwritten).

### 2. The allowance is the plan's ceiling, not the running service count

`included_bytes_per_service × max_billed_services` — 2 GiB × 3 = **6 GiB** on the free row migration
`0015` seeded.

**This changes 0083.** Its `included_bytes` multiplied by `billed_services`, the month's union held
to the ceiling, so a free organization running one service was shown a 2 GiB allowance and one
running three was shown 6 GiB. That was defensible while the number was only *displayed*. It is not
defensible now that it is *enforced*: a customer refused at one service's worth would be told, in
effect, that starting a second service is the remedy — and on the 1st of each month the stated
allowance would climb 2 → 4 → 6 GiB as their services reported in, which is not a limit anybody can
plan against. The allowance is now the plan's own number, flat, and the same on the 1st as on the
30th. Uncapped tiers are unchanged: with no ceiling there is nothing to be flat about, so each
service run still brings its own.

### 3. `max_billed_services IS NULL` means uncapped, and nothing that pays is ever refused

> **Superseded in part on 2026-09-09 by [0107](0107-over-the-allowance-we-stop-and-say-so-and-the-customer-may-raise-the-ceiling.md), built as G6a.**
> A paid tier is now sized on the count the customer committed to when they authorised their
> mandate, and volume past it is refused. What survives unchanged is the reason this section gave:
> **we do not cap a paying customer to hold our own costs down.** Enforcing a ceiling the customer
> set for themselves, can see, and can raise is keeping their instruction rather than imposing
> ours — and ADR 0107 chose it over automatic overage because an unbounded invoice is the thing
> this product is sold against. A paid tier with **no active subscription** still enforces nothing,
> which is the rule below, unamended.

`pro` and `scale` record no ceiling. A row with either `max_billed_services` or
`included_bytes_per_service` NULL enforces nothing, whatever the volume. **Refusing a paying
customer would be a far larger failure than the hole this closes**, so that rule is asserted at the
column where it is decided rather than left implicit — see the negative control below, which caught
that it was not.

An allowance recorded as **zero** is honoured as zero: a plan that includes nothing refuses on the
first byte. NULL and 0 are different statements, as `0013` already said.

### 4. The whole request is refused, and nothing is re-encoded

No `partial_success`, no per-resource filtering, no rewriting of the payload. The gate only *reads*
the request the decoder already produced, so [0045](0045-envelope-contract-frozen.md)
is untouched: the receiver still forwards the bytes it was given.

### 5. `RESOURCE_EXHAUSTED` with no `RetryInfo`

The spec makes `RESOURCE_EXHAUSTED` non-retryable **unless** the server attaches `RetryInfo`, which
is why the rate limiter attaches one and this does not. A retryable refusal would have a compliant
client resend the same batch until the 1st; the OpenTelemetry Collector drops a queued batch after
five minutes of retries, so the data is lost either way and the only difference is three weeks of
the customer's egress spent discovering it.

### 6. The gate sits between `origin` and `ratelimit`

`auth → origin → allowance → ratelimit → decode → normalize → produce`.

After `auth` because it needs the organization. Above `ratelimit` because it answers from an
in-process cache where the limiter is a Valkey round trip, so the customer this gate exists for —
one sending hard and being refused for every request — costs nothing to turn away. Same argument the
origin gate already makes one line above it.

The cache is keyed by organization, TTL `RECEIVER_ALLOWANCE_CACHE_TTL` (default 30s, ceiling 5m),
built on the bounded expiring map extracted from `internal/auth` into `internal/cache` so both use
one answer to "how stale may our view of the control plane be". The TTL is its own setting rather
than the key cache's: that one is sized for how long a revoked key may keep working, and moving it
for a security reason must not silently change what a customer is billed.

**A read that fails admits the request**, exactly as the limiter's does, counted as
`allowance_unavailable`. Losing the protection costs money; refusing costs the customer their data.

### 7. Three places say so, because a refusal nobody can read is the defect

- **The receiver's message** names the volume and the day it returns:
  `"the included monthly ingest volume for this plan is spent; ingestion resumes on 2026-10-01"`.
- **The refusal log and the summary** count it as `allowance_spent`, not `throttled`. Both verdicts
  are `RESOURCE_EXHAUSTED`, so `reasonFor` alone could not tell them apart and a `refusal` now
  carries its own tally name. A window folding them together would report a customer whose free
  month ended as one sending too fast — different conversation, different fix.
- **The usage screen** says it before and after. Before: *"1.9 MB of 600.0 kB included. Ingestion
  stops once this is spent and resumes Oct 1, 2026."* After: *"— spent. Further ingestion is refused
  until Oct 1, 2026"*, plus a warning line naming the loss rather than softening it: *"Telemetry
  sent in the meantime is not stored and cannot be recovered."* The API sends
  `period.allowance_enforced`; the browser does not work out which tiers are capped, because that is
  policy and 0083 already refused to compute policy in the browser.

### 8. The service-count copy is fixed at the same time

*"The 18 counted are held to the plan's ceiling"* read as a discount. A service cap is not a
discount — the eighteenth service is monitored and unbilled — so the sentence now says it in the
customer's direction: **"You are running 18 services; Free bills 3."** And where the allowance is
enforced there is no overage to bill, so the cost figure's footnote changes from *"what volume above
the included allowance costs is not decided"* to *"volume above the included allowance is refused
rather than billed, so there is nothing to add for it."*

## Reconciling with 0083 §6, which says a cap is the one response we will not make

0083 §6 is about **cardinality**, on a **paying** customer, applied **silently**:

> A cap is the one response we will not make. Capping means dropping a customer's telemetry to hold
> their bill down, and they would then be debugging an outage with data we threw away.

Every clause of that is a different thing from this, and the difference is not a technicality:

- **It is cost control versus a commercial boundary.** §6 refuses to *degrade a customer we are
  charging* in order to make their invoice smaller — an action taken on their behalf that they did
  not ask for and cannot see. A free plan running out is the end of what was given away. Nobody's
  bill is being held down; there is no bill.
- **It is selective versus uniform.** A cardinality cap drops some services and keeps others, so
  what is missing is unpredictable and looks like an outage. This refuses everything at once, so
  what is missing is a clean, dated edge.
- **It is silent versus loud.** §6's alternative was to *warn*, and the cardinality notice is that
  warning. This warns too — in the message, in the log, and on the screen before the line is
  crossed — and then stops.
- **It never touches a paying tier.** `max_billed_services IS NULL` on pro and scale, and §3 above
  is the code and the test that keep it that way.

So 0083 §6 stands, unedited and in force. Nothing here caps cardinality and nothing here caps a
customer who pays.

**What is not reconciled, and must not be dressed up.** A free customer over the line loses the
telemetry sent while refused, and they may lose it during an incident. That is the cost, it is real,
and the only defences are that they were told beforehand, they are told during, and the remedy is
theirs rather than ours.

## Alternatives considered

**Capping the fourth service.** Rejected — see Context. The billable unit is the wrong unit to
enforce in, because it removes a whole service rather than thinning the data, and the absence is
indistinguishable from silence.

**Sampling or throttling the free tier down instead of stopping it.** Rejected. A free tier that
half-works is harder to explain than one that ends, the customer cannot tell degraded from broken,
and it would put the very failure §6 names — data we threw away — inside a product that is meant to
be trusted. An ending is legible; a permanent degradation is not.

**Counting bytes in the receiver as well, to tighten the cap.** Rejected. The meter is the one
counter, and it is what the customer's screen shows. A second, per-process counter would disagree
with the first and there would be no answer to which is right. The overshoot it would have bought is
small next to the meter's own lag.

**Enforcing per project or per service.** Rejected, as 0083 rejected it, and for its reason: it
needs a rule for apportioning one payload's bytes between the resources inside it, and that rule
changes bills.

**Refusing part of a request — `partial_success` with per-resource filtering.** Rejected. It means
re-encoding on the hot path, which is what 0045 exists to avoid, in exchange for a partial month
that is harder to explain than a stopped one.

## Consequences

**What this buys.** The free tier has an end. It is one number, printable in marketing — 6 GB a
month — flat, org-wide, the same on the 1st as on the 30th, and the same number the screen states
and the door applies.

**What it costs.**

- **The cap is a ceiling with slack, not an exact byte count.** The meter reads Kafka at its own
  pace and the gate caches for up to 30s, so an organization overshoots by whatever it sends in
  (meter lag + TTL). The meter's lag dominates and is not measured here.
- **One policy, written twice.** `allowance.usageFrom` in Go and
  `nexovue_api.usage.billing.included_bytes` in Python compute the same figure from the same row,
  and nothing but a pair of tests using identical numbers keeps them in step. Unavoidable across two
  languages; the risk is that a change lands in one and the customer is then shown a limit the door
  does not apply.
- **One Postgres read per organization per TTL**, on a table (`usage_daily`) whose primary key
  starts with `org_id`, so it is a range scan over projects × days for the month. Never timed
  against a table large enough to be slow — the same caveat 0083 already recorded about the usage
  endpoint.
- **A refused customer has nowhere to go.** There is no upgrade flow in the product, so the screen
  names the limit and the reset date and stops there. Whoever builds billing has to close that.
- **`plan_prices` is now load-bearing at ingest.** Until today it only affected a number on a
  screen; a wrong row now stops a customer's telemetry. The 5-minute ceiling on the cache TTL is
  what bounds how long a corrected row takes to take effect.

**What was verified**, and watched:

- **Live, against the dev stack with the OpenTelemetry Demo producing.** With the seeded 6 GiB
  allowance and 1.39 MB metered: `accepted_requests=236 refused_requests=0`. A `plan_prices` row
  lowering free to 200,000 bytes per service (600 kB pooled) was inserted at 07:26:02Z; the first
  refusal was logged at **07:26:11Z** — `status=429 reason=allowance_spent path=/v1/logs` — and the
  window at 07:26:49Z read `refused_allowance_spent=331`, `refused_throttled=0`.
- **A paying organization stayed uncapped, live.** `acme` was moved to `pro` with a row recording
  1,000 bytes per service and **no ceiling**, against ~2 MB already metered — thousands of times
  over. The next full window: `accepted_requests=383 refused_requests=0
  refused_allowance_spent=0`.
- **Restored.** Both temporary rows deleted, the plan set back to `free`; the following windows read
  `accepted_requests=427` and `373`, `refused_requests=0`. The demo was taken down.
- **The screen, rendered in a browser**, showed all three sentences at once: *"You are running 18
  services; Free bills 3."*, *"1.9 MB of 600.0 kB included — spent. Further ingestion is refused
  until Oct 1, 2026."*, and the warning line about telemetry that cannot be recovered.
- **Nine negative controls were run and watched to fail.** The one that matters most **did not fail
  the first time**: making a NULL ceiling mean "one billed service" left every test green, because
  the paid-tier guarantee was only ever asserted through test doubles that set `Enforced`
  themselves. The reader's mapping was extracted into `usageFrom` and tested at the column where the
  decision is made; the same break then failed. The other eight: attaching `RetryInfo` (2 tests),
  dropping the distinct tally name (1), refusing instead of failing open (1), comparing `>=` instead
  of `>` (1), reverting the allowance to the running service count (3 Python), enforcing on an
  uncapped tier (2 Python), dropping the reset date from the screen (2 web), and restoring the
  discount wording (1 web).

**What was not verified.**

- **The SQL in `allowance/postgres.go` has no test of its own.** It was exercised end to end by the
  live run above — the gate fired on real rows and stopped when they changed — but nothing asserts
  the statement's shape, and the receiver module has no live-Postgres suite to put one in.
- **Nothing was timed.** Neither the query nor the added hot-path cost was measured against a table
  or a request rate large enough to be slow.
- **No migration was needed and none was written**, so `0016` remains unused. The read uses the
  `usage_daily` primary key's leading `org_id` and needs no new index at dev volumes; whether it
  needs one at real volumes is part of what was not timed.
- **The month boundary has never been crossed in a running system.** The rollover is proved by unit
  tests over a fake clock, including February in a leap year and December rolling the year over.

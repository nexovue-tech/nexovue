# 0107 — Over the allowance we stop and say so, and the customer may raise the ceiling

- **Status:** Accepted
- **Date:** 2026-09-07
- **Resolves:** the contradiction inside
  [0056](0056-per-service-pricing.md), flagged by migration 0015
  and never settled.

## Context

ADR 0056 makes four promises the pricing structure exists to keep, and the fourth is:

> the bill has a ceiling · never per seat · never per host · over the allowance we **block and say
> so rather than bill**

Six lines later, in the paragraph explaining what the service-count ceiling covers:

> The cap applies to the **service-count component only**; volume overage above the allowance stays
> live, so growth still pays.

**Those are two different products.** One tells a customer their data stopped; the other tells them
their invoice grew. A customer cannot be sold both, and neither sentence is marked as the loser.

Migration `0015` caught it while seeding the price rows and refused to guess:

> ADR 0056 says both that we block above the allowance and that overage stays live and bills — two
> different products — and nothing here resolves that. `max_billed_services` caps the free tier at
> three; what happens when a paid tier exceeds its included volume is still undecided.

**It has been safe to leave open only because nothing enforces a paid tier.** `plan_prices` has
`max_billed_services` NULL on `pro` and `scale`, and `allowance/postgres.go` reads NULL as *not
enforced* — deliberately, so that *"no paying customer is ever refused for volume"*. The
contradiction is invisible while every paid plan is unlimited, and becomes load-bearing the moment
one is not.

**It also blocks a screen.** P1-74 is the plan-change screen, and a plan-change screen has to
contain a sentence saying what happens when you exceed your plan. There is no way to write that
sentence without this decision. C2 has been waiting on it.

## Decision

**Ingestion stops at the allowance, the customer is told before and when it happens, and the
customer — not us — may raise their own ceiling.**

Three parts, and the third is what makes the first survivable:

1. **The default is to stop.** A plan's included volume is a ceiling, not a threshold. Reaching it
   refuses further ingest for that month, with the refusal the door already has —
   `RESOURCE_EXHAUSTED` with no `RetryInfo`, counted under its own reason, carrying the date the
   month turns.
2. **Nobody is surprised into it.** Stopping silently is worse than billing silently: telemetry that
   ends is indistinguishable from a service that went quiet, which is the one failure an
   observability product must never cause. Warnings before the ceiling and a notice at it are part
   of this decision, not a follow-on nicety.
3. **The customer can raise their own ceiling, in advance, to a number they choose.** Not automatic
   overage — an amount the customer sets and can see, above which we still stop. A customer who
   would rather pay than go blind can say so; a customer who never touches it can never be billed
   more than their plan.

**ADR 0056's fourth promise wins, and its "overage stays live" clause is withdrawn.** The two cannot
both stand, and the promise is the one the product is positioned on.

## Alternatives considered

**Bill for overage automatically, as most of the field does.** Rejected because it dismantles the
thing this product is sold on. 0056's own market scan records the complaint it was built against —
unpredictable invoices — and lists a bill with a ceiling as the first of four answers. A plan whose
worst case is an unbounded invoice is the competitor's product with our logo, and the customer
discovers it a month after the fact.

**Bill for overage with a cap we choose.** Better, and still ours to get wrong. Any number we pick
is either so low it stops a growing customer anyway or so high it is not a ceiling. The customer
knows what an unacceptable invoice looks like for them and we do not.

**Stop, with no way to raise the ceiling at all.** The purest reading of 0056, and it fails a real
customer: an incident is exactly when telemetry volume spikes and exactly when losing telemetry
costs most. A product that goes blind during the customer's worst hour, because the customer's worst
hour is expensive for us, has inverted its own purpose.

**Soft-stop — keep ingesting but stop *charging*, and drop data quietly.** Rejected outright. It
makes the store an unreliable record with no signal that it is unreliable, which is worse than both
other options and cannot be reasoned about afterwards.

## Consequences

**A customer can still go blind, and it is now a chosen behaviour rather than an unanswered
question.** The mitigation is warnings and a ceiling they control, not a promise it cannot happen.

**Three things now have to exist that did not**, and each is a row rather than a footnote: the
warning path before the ceiling (which needs the transactional mail G2 just built), the
customer-set ceiling as a stored value and a screen control, and the enforcement path for a paid
tier — which is **not** the small change the plan implies. `allowance/postgres.go`'s `usageFrom`
returns unenforced when `max_billed_services` is NULL, and the browser cap lives *inside* the
enforced branch, so a paid plan currently takes an early return that no cap can be reached from.
Enforcing a paid tier means an allowance that does not depend on a service-count ceiling.

**The number itself is still blocked, and correctly.** What a paid tier's included volume should be,
and what the browser door's cap inside it should be, need usage data that does not exist because
there is no usage. This decision fixes the *behaviour*, not the figures.

**Free-tier behaviour does not change.** It already stops at the allowance and says so.

**Migration 0015's note is answered** and should stop describing this as undecided.

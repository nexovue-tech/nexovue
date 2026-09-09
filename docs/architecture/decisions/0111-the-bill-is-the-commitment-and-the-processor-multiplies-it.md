# 0111 — The bill is the commitment, and the processor multiplies it

- **Status:** Accepted
- **Date:** 2026-09-10
- **Amends:** [0056](0056-per-service-pricing.md), which priced per service monitored.
- **Extends:** [0107](0107-over-the-allowance-we-stop-and-say-so-and-the-customer-may-raise-the-ceiling.md),
  whose ceiling this makes into the charge itself.

## Context

ADR 0056 bills per service *monitored*: count what reported during the month, multiply by the rate,
hold it down to the commitment. The bill therefore moves month to month.

Raj chose one processor for every customer rather than a domestic gateway and a merchant of record.
That made a property of the chosen processor load-bearing, and it was measured on 2026-09-10 rather
than assumed:

- Its **Subscriptions** API answers with a `short_url` — a hosted page. `Checkout.url` is satisfied
  by a documented field, with no browser SDK on our origin and no undocumented form post.
- That subscription charges `plan.amount x quantity`, **fixed**. Not a variable amount.
- Its variable-amount product is a different one: tokenise, then charge as needed. Charging "as
  needed" means a monthly billing run that does not exist, and the authorisation step there does not
  hand back a link.
- Its own add-ons feature, which could have carried the variable part, is deprecated.

So a variable bill costs a hosted page, a billing run, and a third-party script. A fixed one costs
neither.

## Decision

**A month is the commitment: `rate x services_committed`, charged whether or not that many services
ran.**

`quantity` is the count the customer chose. The processor multiplies, and renders both halves on its
own page — *"399.00 x 5 units"* — so the customer sees the same arithmetic we do.

`Commitment` replaces `Money` on `begin_checkout`, carrying the count and the per-service rate
separately. **A multiplied total cannot be divided back out by an adapter that does not know the
divisor**, and the two processors want opposite halves: one takes a ceiling for the whole mandate,
the other a per-unit plan and a quantity.

## Alternatives considered

**Keep the variable bill and use the processor's tokenised recurring product.** Rejected on cost:
a monthly charging job, its retries, its failure states, and its reconciliation — before a first
customer — plus a browser SDK for the authorisation step, which ADR 0102's `Checkout.url` exists to
avoid.

**Keep the variable bill and switch back to two processors.** Rejected because Raj had just decided
the opposite, for a reason this ADR does not overturn: one processor is one adapter, one dashboard,
one reconciliation, one approval.

**Charge the commitment but refund unused services.** Rejected as the same variable bill with worse
paperwork, and it puts a refund path on the happy case.

## Consequences

**A customer who commits to five and runs three pays for five.** Named rather than softened. The
mitigation is that they chose the number, can see it, and can change it — which ADR 0107 already
required.

**The invoice stops being a prediction.** ADR 0056's market scan opened on the complaint this
product is positioned against: unpredictable bills. "Your bill has a ceiling" becomes "your bill is
this number", which is the stronger version of the same promise and the easier one to put on a
pricing page.

**`usage.billing.service_cost_minor_units` no longer describes what is charged.** It still computes
what the usage screen shows about services counted, which is a different and still useful figure —
but it is no longer the bill, and anything that says so has to stop.

**The allowance is unchanged.** ADR 0107's ceiling still sizes the month's volume and still stops
ingestion when spent; `services_committed` remains the number both are computed from. What changed
is that the same number is now also the invoice.

**Raising a commitment mid-term needs a decision this ADR does not take.** The processor can update
`quantity` on a card-authorised subscription but not on one authorised by UPI or e-mandate, so an
Indian customer paying by UPI has to cancel and re-sign. That is a product question about what the
"change plan" control does, and it is open.

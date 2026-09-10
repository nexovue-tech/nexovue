# 0112 — The bill returns to what was monitored, and "service" has to mean one

- **Status:** Accepted
- **Date:** 2026-09-10
- **Reverses:** [0111](0111-the-bill-is-the-commitment-and-the-processor-multiplies-it.md), whose
  premise no longer holds.
- **Restores:** [0056](0056-per-service-pricing.md)'s billable unit as a *measurement* rather than a
  purchase quantity.

## Context

ADR 0111 made a month's bill `rate x services_committed`, charged whether or not that many services
ran. It did so because the processor chosen at the time — Razorpay — charges a fixed
`plan.amount x quantity` and its variable-amount product needs a monthly charging run that did not
exist.

**Raj then chose Cashfree**, whose `ON_DEMAND` mandate is explicitly variable: *"The customer
authorises a maximum mandate amount once, at setup. You can then charge any amount up to that
maximum for each billing cycle."* The constraint 0111 was built around is gone.

**And 0111 left a word lying.** Raj found it by reading the screen as a customer would:

> *"if user buy more service but not use it what will happen"*

He is right, and it is worse than unused commitment being charged. Because the ingest gate refuses
on **bytes and never on a service count** — deliberately, since a capped service goes dark and a
dark service is indistinguishable from a quiet one — the committed number never limited services at
all:

| Commits to | Runs | Pays | Volume |
|---|---|---|---|
| 5 | 3 | 5 x rate | 50 GB |
| 5 | **50** | 5 x rate | 50 GB |

So "5 services committed" meant *"I bought 50 GB"*. A customer reading it literally would expect to
choose which services count, or to pay more for a sixth. Neither is true. A customer reasoning
about it would find they can commit to one and run forty.

ADR 0056 priced per service *monitored* — a measurement. 0111 quietly turned the same word into a
purchase unit and nobody noticed the meaning had changed.

## Decision

**The bill is what was monitored, held down by the commitment: `min(services_monitored,
services_committed) x rate`.** The commitment returns to being a ceiling — ADR 0107's number, which
the customer sets, sees and raises — rather than the charge itself.

`usage.billing.billed_services` already computes exactly this and never stopped; 0111 routed around
it. This restores the caller.

**Until the charge can be raised, the screens say what is true today.** Commitment-based billing is
what the code does right now, and copy implying otherwise is the defect Raj found. The wording
changes in this commit; the arithmetic changes when the billing run lands.

## What this costs, and it is not small

A variable charge cannot be raised on the last day of the month. RBI requires a pre-debit
notification, and Cashfree enforces the timing:

> *"A **25-hour window** is required between a successful pre-debit notification and the execution
> of the charge."*
>
> *"The debit amount **cannot change** after the PDN is initiated. Banks enforce amount-match logic
> and will decline transactions if the executed amount differs from what was notified."*

Scheduling is bounded too: UPI T+1 to T+14, cards T+2 to T+14, nothing at T+15 or beyond.

**So the month closes early.** Services are counted to a cut-off around day 29, notified, and
charged on the period boundary. A service first seen after the cut-off is billed in the following
month. That is the regulation's shape, not ours, and the pricing page has to say so.

## Alternatives considered

**Keep 0111 and rename the unit — sell volume outright.** *"₹399 per 10 GB a month."* Honest, ships
immediately, no billing run. Rejected as the product decision rather than the engineering one: ADR
0056's market position is per-service pricing, and a customer running three services should not pay
for five. Kept as the interim copy, because it describes today's behaviour truthfully.

**Bill the commitment but allow changing it at any time.** Same lie in slower motion: the number
still does not limit services, and a customer who forgets to lower it pays for capacity no
mechanism ever gave them.

**Cap the service count so the word means what it says.** Rejected for the reason it was rejected
the first time: the billable unit is a service, so a cap makes one go dark, and a dark service is
indistinguishable from a quiet one. That is the failure an observability product must never cause.

## Consequences

**A monthly billing run has to exist**, and it is now the largest remaining piece of Phase 1:
count, notify, wait out the 25 hours, charge, and retry — Cashfree allows three retries per cycle.

**It is the same job as reconciliation.** Cashfree does not replay webhooks (*"no replay
functionality currently exists"*), so a missed delivery is otherwise lost forever. The job that
talks to Cashfree monthly is the job that can also correct a subscription whose event never
arrived, and handle the `card_expired` and paused states this product currently drops.

**The allowance is untouched.** ADR 0107's ceiling still sizes the month's volume from
`services_committed` and still stops ingestion when it is spent. What changes is only that the same
number stops being the invoice.

**Until the run lands, `services_committed` is still the bill.** Stated here so the gap between
this decision and its implementation is a known state rather than a discovered one.

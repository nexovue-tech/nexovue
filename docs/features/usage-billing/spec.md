# Feature: Usage, plan allowances, and billing

## Status

**Proposed.** Related catalogue feature: **F25**. Written 2026-09-21 against the code, not against
the catalogue — the previous document at this path was a 21-line *"verified current-state baseline"*
with no user stories, no business rules and no acceptance criteria, and its absence had already cost
two defects (`docs/changes/commitment-is-the-bill-ceiling/`, and the two-figure Cost row the console
review found).

**Awaiting the founder's approval.** No implementation follows from this document until then; what
it describes is, except where marked **GAP**, what the code already does.

## Problem

A customer sending telemetry to nexovue cannot answer three questions without asking us:

1. **"How much am I using, and how close am I to stopping?"** Telemetry that stops because an
   allowance ran out is indistinguishable from a service that went quiet — and an observability
   product that silently stops observing has failed at the one thing it sells.
2. **"What will this cost me?"** Pricing is per service monitored. Until a customer can see the
   count we will bill, the rate behind it and the month's running figure, the first invoice is a
   surprise.
3. **"What was I charged, and why?"** A charge with no record behind it is a support ticket.

For nexovue the same gap is worse in the other direction: without a metered record there is nothing
to bill from, nothing to reconcile a processor's charge against, and no way to answer a customer who
disputes one.

## Goal

A customer can see, without asking anyone: what they have sent this month, what it is measured
against, what happens when they reach it, what the month will cost, and what has been charged
before. Every figure on the screen is the same figure the ingest door and the payment processor use.

## Target users

From `docs/product/vision.md` §1.

- **P1 — the full-stack developer at a small startup.** The buyer as often as the user here. Reads
  the usage screen to decide whether to raise the commitment before a launch, and the billing screen
  when finance asks what the charge was.
- **P2 — the indie hacker / solo founder.** Lives on the free tier. The allowance and what happens
  at it are the *only* parts of this feature they ever see, and they must be legible without
  reading a pricing page.

**Not served: P3, the non-technical owner.** Money and quotas are the account holder's, not the
viewer's; a viewer has no reason to read a bill and no permission to change one. **GAP**: nothing in
the current screens states this, and a viewer's experience of them has not been checked.

## Scope

- Daily metering per organization and project: spans, logs, metrics, profiles, bytes, browser bytes,
  and the count of distinct services.
- The usage screen: the day table, the range, the per-project breakdown.
- The month's allowance, what sizes it, and whether it is enforced.
- The billing screen: plan, rate, services monitored, cost so far, subscription state, plan change,
  cancellation, charge history and its CSV.
- Checkout through a domestic payment processor, and the signed webhook that is the only thing
  which may change subscription state.
- The ingest door's refusal when an allowance is exhausted.

## Out of scope

- **Tax invoicing.** The processor issues the tax invoice; our history is a record of charges
  exclusive of tax, and says so (`HISTORY_IS_NOT_AN_INVOICE`).
- **Holding card or bank details.** They live with the processor and are changed there
  (`PAYMENT_METHOD_IS_THE_PROVIDERS`).
- **Selling volume overage.** Decided against: *"over the allowance we block and say so rather than
  bill"* is a standing pricing promise (vision §5).
- **Per-seat or per-host pricing**, both explicitly refused by the same promises.
- **Self-hosted billing.** A self-hosted install has no processor and no plan to buy; what the
  screens do there is **GAP** and is named in `docs/features/data-lifecycle-controls/`.

## User requirements

- **US25.1** — As a Dev (P1), I can see how much I have sent this month against what my plan
  includes, so that I know whether I am about to be cut off.
- **US25.2** — As a Dev (P1), I can see *which project* is spending the allowance, so that I can act
  on the one that matters instead of guessing.
- **US25.3** — As an Indie (P2), I am told what happens when I reach the allowance *before* I reach
  it, and on what day it resets, so that a stop is never a mystery.
- **US25.4** — As a Dev (P1), I can see how many services will be billed and at what rate, so that
  the month's cost is something I can predict rather than discover.
- **US25.5** — As an owner, I can move onto a paid plan by choosing a plan and the number of
  services I commit to, so that I control my own ceiling.
- **US25.6** — As an owner, I can see what has been charged, when, on what plan and for how many
  services, and take it away as a file, so that I can reconcile it against my statement.
- **US25.7** — As an owner, I can end the arrangement and be told plainly what I lose and when, so
  that cancelling is not a leap.
- **US25.8** — As an owner who abandoned a checkout, I am told nothing was charged, so that a
  half-finished flow does not read as a silent failure.

## Business rules

- **BR25.1 — The customer's committed count is the ceiling on the service-count component of the
  bill.** They choose it, they can see it, they raise it themselves. It is the `quantity` the
  processor charges. *This is the rule `commitment-is-the-bill-ceiling` restored.*
- **BR25.2 — One number, two meanings.** The committed count sizes both the bill and the month's
  volume allowance (`included_bytes_per_service × committed`). A plan with its own
  `max_billed_services` — the free tier — uses that instead, for both.
- **BR25.3 — The allowance is the ceiling's, never the running service count's.** An allowance that
  moved with the count would refuse one export and admit the next as services came and went, and
  would tell a customer refused at 2 GiB that starting a second service is the remedy.
- **BR25.4 — The allowance is pooled across the organization**, not enforced per service. A quiet
  service lends its unused allowance to a busy one.
- **BR25.5 — Past the allowance we refuse; we never bill overage.** The refusal carries no retry
  directive, because retrying cannot succeed until the month turns.
- **BR25.6 — The same rule is computed in two languages and must agree.**
  `usage.billing.allowance_service_count` (Python) and `allowance.allowanceServiceCount` (Go) size
  the allowance identically. Divergence refuses a customer at a limit the screen never showed them.
- **BR25.7 — A control-plane read failure fails open.** If the allowance cannot be read, telemetry
  is accepted. Losing a customer's data to protect our margin is the wrong trade.
- **BR25.8 — Only a signed, delivery-idempotent webhook changes subscription or plan state.** A
  redirect back from the processor proves nothing; a customer who closes the tab is in the same
  state as one who did not.
- **BR25.9 — A repricing is a new `plan_prices` row with a later `effective_from`, never an edit.**
  A past charge must always be recomputable from the row it was billed on.
- **BR25.10 — An absent figure is words, never zero.** An unpriced tier is "priced by agreement";
  an unreadable charge amount is an em dash. `$0.00` is a different statement and it is false.
- **BR25.11 — A month still being metered is never presented as a final bill.**
- **BR25.12 — A `pending` subscription grants nothing.** It is equally the state of somebody who
  authorised a mandate and somebody who walked away, and the two are indistinguishable until the
  webhook arrives.
- **BR25.13 — An organization holds at most one active subscription.** A second mandate would charge
  twice, so a plan change is cancel-then-choose.
- **BR25.14 — Currency follows the payment instrument**, chosen by the customer, never inferred from
  a network address.

## Functional requirements

- **FR25.1** — The worker meters per day and project: spans, logs, metrics, profiles, bytes, browser
  bytes, and distinct service names.
- **FR25.2** — `GET /usage` returns those counters by day for a range, scoped to the caller's
  organization, with a per-project breakdown.
- **FR25.3** — `GET /billing` returns the plan, its price row, the subscription, and the month's
  usage: services monitored, services billed, bytes, the included allowance, the browser sub-cap,
  and whether the allowance is enforced.
- **FR25.3a** — A plan's `min_committed_services`, when set, is the fewest services it can be bought for.
  Checkout refuses fewer, and the billing screen says so. Scale's is 10 (founder decision, 2026-10-31).
- **FR25.4** — Services **billed** is `min(services monitored, ceiling)` where the ceiling is the
  plan's `max_billed_services` if set, else the committed count, else nothing.
- **FR25.5** — The month's cost is services billed × the rate in the effective price row, or `null`
  where no rate is recorded.
- **FR25.6** — The browser door's spend is capped at `browser_share_percent` of the allowance, taken
  out of the whole only as it is spent. A public site key is readable by anyone who views source, so
  a scraped key must not be able to silence the customer's servers.
- **FR25.7** — `POST /billing/checkout` creates the processor's subscription with the committed
  count as `quantity` and returns a handoff for our own page. It refuses a commitment below one and
  a rate of zero.
- **FR25.8** — `POST /billing/webhook` accepts only a signed event, is idempotent on redelivery, and
  is the sole writer of subscription and plan state.
- **FR25.9** — `DELETE` on the subscription asks the processor to end it; the plan returns to free
  at the end of the period the processor is billing.
- **FR25.10** — `GET /billing/history` lists charges newest first, each with day, outcome, plan,
  services committed, currency and amount; `GET /billing/history.csv` returns the same columns.
- **FR25.11** — The receiver refuses ingest when the allowance is exhausted, when the organization
  is suspended, and when it is deleted. A deleted organization's keys must not authenticate.
- **FR25.12** — A complimentary grant (F31/FR27) takes precedence over the paid plan while it is
  active, for both the rate and the count that sizes the allowance, and `organizations.plan` is
  never rewritten.

## Non-functional requirements

- **NFR25.1** — The allowance read sits in the ingest hot path; it must not add a synchronous
  round-trip per request.
- **NFR25.2** — Every figure the billing screen shows is produced by the same function the usage
  screen calls for that figure. Two code paths to one number will disagree within a month.

## Error cases

- **EC25.1** — Allowance exhausted → ingest refused, no retry directive, and the screen says
  ingestion stops and names the day it resumes. *Covered.*
- **EC25.2** — The control plane cannot be read → ingest accepted (BR25.7). *Covered.*
- **EC25.3** — No price row for the plan → cost is "priced by agreement", not zero. *Covered.*
- **EC25.4** — Checkout abandoned → the row stays `pending`, nothing is charged, and the screen says
  so before re-showing the form. *Covered.*
- **EC25.5** — Webhook redelivered → applied once. *Covered.*
- **EC25.6** — A charge whose price row can no longer be read → amount is an em dash. *Covered.*
- **EC25.7** — An active subscription already exists → checkout refused, and the screen explains the
  cancel-then-choose order while showing the plans for comparison. *Covered.*
- **EC25.8** — Monitoring more services than committed → billed at the commitment; the screen
  reports both figures and states that the commitment is not a limit on how many services may run.
  *Covered as of `commitment-is-the-bill-ceiling`.*
- **EC25.9** — **GAP: nothing tells a customer they are above their commitment.** Both numbers are
  on the screen; nothing prompts them to raise it, and the volume they share is sized for fewer
  services than they run.
- **EC25.10** — **GAP: no processor configured.** The screen says upgrading is unavailable. What a
  self-hosted install should say instead is undecided.
- **EC25.11** — **GAP: a charge the processor made that we hold no row for.** There is no
  reconciliation path and no alert; it would be found by a customer asking.

## Acceptance criteria

- **AC25.1** — The usage screen **must** show, for the selected range, each day's spans, logs,
  metrics and bytes, and a per-project breakdown.
- **AC25.2** — The billing screen **must** show the plan, the rate per service, services monitored,
  the month's cost, and the allowance with the proportion spent.
- **AC25.3** — Where the allowance is enforced, the screen **must** state that ingestion stops and
  **must** name the day it resets, before the limit is reached.
- **AC25.4** — Where it is not enforced, the screen **must not** threaten a stop.
- **AC25.5** — Services billed **must** equal `min(monitored, ceiling)` per BR25.1 and FR25.4, and
  the figure shown **must** equal the `quantity` the processor is charging.
- **AC25.6** — An unpriced tier **must** read "priced by agreement" and **must not** render a zero.
- **AC25.7** — A month not yet settled **must** be labelled as still being metered.
- **AC25.8** — A `pending` subscription **must** state that nothing has been charged and **must
  not** claim a bank is confirming anything.
- **AC25.9** — Cancelling **must** state what is lost and when; discarding a `pending` checkout
  **must not** threaten the loss of a tier the customer is already on.
- **AC25.10** — The charge history **must** state that it is not a tax invoice and that the invoice
  comes from the processor.
- **AC25.11** — The screen **must** state that payment details are held by the processor and never
  by nexovue.
- **AC25.12** — Ingest **must** be refused once the allowance is exhausted, and **must** be accepted
  when the allowance cannot be read.
- **AC25.13** — A redelivered webhook **must** change state exactly once.
- **AC25.14** — Checkout **must** be refused while an active subscription exists, and the screen
  **must** explain the order in which a plan change happens.
- **AC25.15** — The Python and Go allowance rules **must** produce the same count for the same row;
  a test **must** hold this rather than a comment. **Closed 2026-09-21**: the cases live in
  `packages/contracts/fixtures/allowance-service-count.json` and are read independently by
  `packages/contracts/tests/test_allowance_rule.py` and
  `apps/receiver/internal/allowance/allowance_rule_test.go`, following the envelope round-trip's
  pattern. Seen red by making the Go side ignore the commitment: *"Go sized the allowance on nothing
  (enforcing no limit), the shared cases say 26."*

## Dependencies and constraints

- `plan_prices`, `billing_subscriptions`, `plan_grants`, `usage_daily` in Postgres.
- The receiver's allowance read (`apps/receiver/internal/allowance/`) and the gate chain in
  `otlp/gates.go`.
- ADR-008 (the payment processor's role) and F31/FR27 (complimentary grants).
- The processor is Razorpay; `PATCH /v1/subscriptions/:id` can change `quantity`, only in the
  `authenticated` or `active` states, with a minimum prorated difference of 50 subunits. Recorded
  because any future change to a live commitment starts there.

## Open decisions

- **Paid-tier *volume* overage.** Vision §5 still records the pricing decision as stating both that
  we block and that overage bills. BR25.5 takes the first; the second is not implemented anywhere.
  Closing the wording in §5 is the remaining step.
- **What a self-hosted install shows** on both screens (EC25.10).
- **Whether to prompt a customer above their commitment** (EC25.9), and whether that prompt should
  offer to raise it.
- **Reconciliation** between the processor's charges and our rows (EC25.11).

## Code evidence

`apps/workers/internal/pipeline/usage/`, `apps/receiver/internal/allowance/` and `otlp/gates.go`,
`apps/api/src/nexovue_api/usage/{router,service,billing,queries}.py` and `billing/`, and
`apps/web/src/features/{usage,billing}/` are authoritative. Where this document and they disagree,
they are right and this is a defect.

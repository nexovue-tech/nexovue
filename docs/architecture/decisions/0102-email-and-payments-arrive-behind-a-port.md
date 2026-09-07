# 0102 — Email and payments arrive behind a port, and the port is written first

- **Status:** Accepted
- **Date:** 2026-09-07
- **Extends:** [0037](0037-seaweedfs-for-local-dev-object-storage.md)'s generic-API rule to two
  more vendors,
  and applies [0076](0076-door-4b-holds-no-customer-credential-it-can-avoid-holding.md)'s posture
  to money.

## Context

P1-69 and P1-73 are the two hard dependencies M8 is built on, and the plan calls each *"a vendor
decision before it is a code decision"*. Both were about to be decided, and neither should be yet.

**Email is genuinely low-stakes and easy to get wrong anyway.** The whole volume is verification,
password reset and invoices — perhaps thirty messages a month at ten customers, against free tiers
of 3,000 (Resend) and 9,000 (Brevo). But each is a message whose non-arrival is a *lost account*
rather than a lower open rate, so if deliverability ever disappoints the provider must be
replaceable in an afternoon.

**Payments is not low-stakes, and the constraint is not technical.** nexovue is built by a solo
developer in India with no registered company and no GST registration, and that shapes the answer:

- A **domestic gateway** — Cashfree, Razorpay — onboards an individual on PAN and Aadhaar with no
  GSTIN required, holds RBI PA-CB Export authorisation, and issues the FIRC that proves money
  arrived as export income. But it leaves **us** the legal seller, and a non-EU seller owes EU VAT
  from the first euro with no registration threshold.
- A **merchant of record** — Paddle, Lemon Squeezy — becomes the seller and carries that VAT
  entirely. Most require a registered entity, which does not exist yet, and none is good at India.

**So the likely end state is two processors, chosen per customer**, and the decision cannot be
taken today because it depends on facts that do not exist: whether a company gets registered,
whether EU customers appear, and what a chartered accountant says about GST on export of services.

## Decision

**Both are Protocols, written before either vendor, and nothing above them knows which
implementation ran.**

`notifications/sender.py` declares `EmailSender`; `billing/processor.py` declares
`PaymentProcessor`. Neither imports an SDK, an HTTP client, or anything else — a port declares an
interface and talks to nothing.

**The interface speaks this product's vocabulary and never a provider's**, which is
[ADR 0037](0037-seaweedfs-for-local-dev-object-storage.md)'s rule restated —
*"the generic-S3-API-only rule stands"*. `store.py` carries
it for the S3 API — *"nothing here may use a feature outside that API, because the two have to be
the same store from this side"* — and the same sentence is true of a mail sender. A
`message_stream`, a `cf_order_token`, a Paddle price id: the first vendor noun to reach a port is
what makes the second implementation a rewrite, and nothing fails at the moment it is added.

**Three shapes carry that rule and are worth naming.**

`Message` is a closed enum — `VERIFY_EMAIL`, `RESET_PASSWORD`, `INVOICE` — so the message *name* is
the contract and the body is the implementation's problem, wherever the provider keeps templates.

`plan_slug` is ours (`pro`, `scale`, matching `plan_prices`) and never a processor's price id.
Mapping one to the other is the implementation's only permitted piece of vendor knowledge.

**`BillingEvent` is the load-bearing one, because the webhook is the hard half.** Every processor
pushes events in its own shape, and the temptation is to hand that JSON straight to the code that
changes a plan — at which point the plan-change logic knows Paddle's field names and the
abstraction was decoration. An implementation verifies the signature, translates into one of four
members, and nothing downstream sees the original. `event_from_webhook` takes the **raw body**, not
a parsed object, because a signature covers the bytes that arrived — the same reason door 4a reads
the raw stream.

**The rule is enforced, not written down.** `tests/test_provider_ports.py` parses both modules and
fails if a vendor name appears outside the docstring, if either imports a provider SDK or an HTTP
client, or if either stops being a `Protocol`. Negative-controlled: adding `paddle_price_id` to
`Checkout` fails it; removing it passes.

## Alternatives considered

**Pick the providers now and write clients against them.** Rejected. Every fact the payment choice
depends on is absent — company registration, EU customers, a CA's reading of GST on export of
services. Writing a client now means writing it twice, and the second time under time pressure with
customers attached.

**One `PaymentProcessor` and swap it later.** Rejected as the wrong shape rather than wrong. India
plus international is a *likely* end state, not a fallback: a merchant of record is poor at UPI and
a domestic gateway does not carry EU VAT. Two implementations of one interface, selected per
customer, is the ordinary outcome and only cheap if it was never assumed away.

**Abstract base classes rather than `Protocol`s.** Rejected. Structural typing means an
implementation does not inherit from us, which is what lets a test double exist with no import and
lets an implementation live wherever it belongs.

**Let each caller send its own mail.** Rejected for the reason `auth/cookies.py` gives about cookie
attributes: *"a mismatch between them is the kind of bug that only shows up as"* a user silently
not receiving something. One port, one place to add a retry or a bounce path.

## Consequences

**The vendor choices stop being urgent**, which is the point. Start on a free tier — Resend's 3,000
a month is two orders of magnitude above the need — and on Cashfree for India, and add a merchant
of record if and when EU customers make VAT real. None of those is a commitment.

**Nothing is implemented.** These are interfaces with no implementation, and the modules will sit
unused until P1-69 and P1-73. That is deliberate: they exist so the code written *then* has a shape
to fit, and so the decision it embodies is recorded while the reasoning is fresh.

**A new vendor must be added to `VENDOR_NAMES` in the same change**, or the guard silently stops
covering it. The list is what is under consideration on this date, not an exhaustive registry.

**What we store stays minimal**, following ADR 0076: the processor's customer reference and nothing
avoidable. `Checkout` returns a **URL** rather than a form precisely so card details never reach
this application.

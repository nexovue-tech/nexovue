# 0110 — The webhook names the subscription, and we answer whose it is

- **Status:** Accepted
- **Date:** 2026-09-09
- **Extends:** [0102](0102-email-and-payments-arrive-behind-a-port.md), which put payments behind a
  port.
- **Unblocks:** [0109](0109-cashfree-stays-and-the-processor-declares-the-contact-it-needs.md), which
  chose Cashfree and left `begin_checkout` refusing.

## Context

`BillingEvent` carried an `org_id` and a `plan_slug`, and the Cashfree adapter read both out of
`subscription_tags` — custom key/value pairs we ask the processor to store against the subscription
and echo back on every webhook.

That arrangement had two costs, and only the second was understood when it was written.

**It made the checkout call unbuildable.** Cashfree's tagged create call answers with a session id
for a browser SDK; the call that answers with a redirectable link does not carry tags. `Checkout`
promises a URL. So no single call satisfied both the port and the webhook path, and
`begin_checkout` raised `CheckoutHandoffUndecidedError` rather than pick.

**It put our tenant identifier in a third party's storage, and then trusted it back.** The
organization a payment belonged to was whatever the processor returned in a field we had asked it to
hold. The signature proves the *processor* sent the payload; it does not prove the tag was still the
value we set, and nothing on our side could tell a changed tag from an unchanged one. A wrong
`org_id` there moves the wrong tenant's plan.

## Decision

**A webhook is required to name one thing — the processor's own subscription reference — and the
organization is resolved from our own row.**

1. `BillingEvent` carries `kind`, `subscription_ref` and `delivery_id`. No `org_id`, no `plan_slug`.
   The reference is opaque: nothing parses it or branches on its shape.
2. `begin_plan_change` writes a `billing_subscriptions` row **at checkout**, status `pending`,
   holding the org id, the plan and the reference the processor just issued. It is written while an
   authenticated member is on the call, which is what makes the org id ours rather than claimed.
3. `subscription_by_processor_ref` looks that row up unscoped, and it is what establishes the tenant.
4. A signed event naming a reference with no row is **logged at ERROR and acknowledged**, never
   attributed. Acknowledged so the processor stops redelivering; loud because a signed event we
   cannot place means our record and theirs have diverged.

This is the same shape as `find_live_key_by_prefix` on the ingest door, for the same reason: the
credential names a row, the row names the tenant, and the caller never states the tenant. It is
recorded as an exemption in `test_postgres_tenancy_structure.py` with that reason.

## Alternatives considered

**Keep the tags and change `Checkout` to carry a session id instead of a URL.** Rejected: it rewrites
ADR 0102's port around one vendor's SDK, which is the coupling the port exists to prevent, and the
second cost — our identifier in their storage — would survive untouched.

**Keep the tags and verify them against a local row.** Rejected as strictly worse than dropping
them: if a local row must exist to check the tag against, the tag is doing no work, and the checkout
call is still unbuildable.

**Sign our own value into the tag.** Rejected. It works, and it is a key-management scheme, a
rotation story and a verification path added so that a field we do not need can be trusted. The row
we already have is cheaper and cannot be tampered with.

## Consequences

**A pending row exists for a checkout the customer abandoned.** Accepted deliberately: it is a
record of an attempt, and the alternative is an event for an arrangement we have no record of
starting, which cannot be attributed at all. Nothing charges, and nothing moves a plan, off a
`pending` row.

**`_apply` no longer creates subscriptions.** Creating one on the spot was only ever possible while
the event carried an organization id; with that gone, an unknown reference is refused by the caller
rather than invented downstream.

**The reference turns out to be ours to mint, which makes this stronger than it was written.** One
Cashfree sandbox create call on 2026-09-09 — made, not read — echoed back the `subscription_id` we
sent it. Their own `cf_subscription_id` is a separate field we do not use. So the identifier tying a
webhook to a tenant is chosen by us and merely stored by them, which is the opposite of the
arrangement this ADR replaced.

**`begin_checkout` still refuses, and the reason is now a port question.** The same call showed the
response carries a `subscription_session_id` for Cashfree's browser SDK and no redirectable link;
the one endpoint that does answer with a URL requires `payment_method` — card, UPI, enach — so
getting a URL means choosing the payment instrument in our own application, which is what ADR 0076's
posture forbids. `Checkout` promises a URL. Either the port stops promising one and describes a
handoff the front end completes, or paid subscriptions are taken elsewhere. That is ADR 0102's
wording and is not settled here.

**A processor that issues no reference before the redirect cannot satisfy this port.** Stated so it
is a known boundary rather than a surprise at the second adapter.

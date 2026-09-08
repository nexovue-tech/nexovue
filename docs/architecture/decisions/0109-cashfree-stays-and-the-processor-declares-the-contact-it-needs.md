# 0109 — Cashfree stays, and the processor declares the contact details it needs

- **Status:** Accepted
- **Date:** 2026-09-08
- **Extends:** [0102](0102-email-and-payments-arrive-behind-a-port.md), which put payments behind a
  port and chose a domestic gateway without choosing between Cashfree and Razorpay.

## Context

`begin_checkout` could not be implemented. The port promises
`(org_id, plan_slug, billing_email) -> Checkout` and Cashfree needs a `customer_phone` that none of
those carries — required on the Create Order call and on the subscription flow both, checked in
their own documentation rather than remembered:

> `customer_details` required fields: `customer_id`, `customer_phone`
> — [Create Order](https://www.cashfree.com/docs/api-reference/payments/latest/orders/create)

> The hosted checkout page itself does not collect customer information — it only handles payment
> method selection and authorization.
> — [Subscriptions hosted checkout](https://www.cashfree.com/docs/payments/subscription/hosted-checkout)

**Raj's objection was the right one and it is a product objection, not a technical one.** Signup
collects an organisation name, an email, a password and optionally a name — there is no phone column
anywhere in `models.py`. Adding a phone field to *signup* would tax every free-tier account for a
regulation that only touches paying ones, and it reads to a customer as *"they are going to text me
offers"*. Resistance to a phone box is about what people assume it is for, not about the typing.

**Two things were checked before deciding, because the first framing was too broad.**

*"India requires this" is wrong.* RBI does require a pre-debit notification at least 24 hours before
each recurring charge, by SMS or email ([Razorpay: RBI e-mandate](https://razorpay.com/blog/rbi-e-mandate-regulations/)),
so a provider must hold a number. But **Razorpay satisfies that by collecting it on its own page** —
its checkout asks for contact when unprefilled, and its Create Subscription call needs only
`plan_id` and `total_count`, with `customer_id` *"populated automatically once the customer completes
the authorisation transaction"*
([Create Subscription](https://razorpay.com/docs/api/payments/subscriptions/create-subscription)).
Stripe is the same shape ([phone numbers](https://docs.stripe.com/payments/checkout/phone-numbers)).
**Cashfree is the outlier, not the market.**

*The constraints 0102 actually turned on are met by both.* Razorpay onboards a sole proprietor on
PAN and Aadhaar with no GSTIN, and holds the RBI **PA-CB** cross-border licence with automatic
per-transaction eFIRC ([KYC guide](https://razorpay.com/blog/payment-gateway-kyc-onboarding-india/),
[PA-CB licence](https://www.business-standard.com/amp/companies/news/razorpay-rbi-pa-cb-licence-approval-cross-border-payments-india-fintech-125120200556_1.html)).
So the choice was genuinely open.

**Raj chose Cashfree after hearing all of that.** The sandbox account exists, the adapter is built
and tested, and the webhook signature — Base64 HMAC over `timestamp + body`, not the hex shape
GitHub uses — is already researched and negative-controlled. That is real, verified work against a
provider he has a relationship with, weighed against a provider we would be meeting for the first
time.

## Decision

**Cashfree stays.** The consequence is that we collect a phone number, and where we collect it is
the part that matters.

**It is collected at checkout, never at signup.** Signup is unchanged. The billing screen asks,
immediately before handing the customer to Cashfree — which from the customer's side is still
"during payment", the moment a phone next to card details is unremarkable.

**The screen says why, and the reason is true**: the bank requires a mobile number to send the
notice before each renewal. That answers the marketing-SMS assumption before it forms, which is what
the objection was actually about.

**We do not store it.** Cashfree holds it to send those notices, which is the regulation's purpose.
A copy of our own is personal data with a retention obligation, bought for the convenience of not
retyping on a second subscription that most customers will never create. [0076](0076-door-4b-holds-no-customer-credential-it-can-avoid-holding.md)'s
posture — hold nothing we can avoid holding — applies unchanged to a phone number.

**The processor declares what it needs; the port does not name a field.**

```python
def required_contact_fields(self) -> frozenset[ContactField]: ...
```

Cashfree returns name, email and phone. A Razorpay or Paddle adapter returns less. The screen
renders exactly what the configured processor asks for, so a switch removes a field instead of
leaving a dead one — and **a missing field becomes a control that is not drawn, rather than a 5xx
from a payment provider at the worst possible moment.**

## Alternatives considered

**Put `phone` on the port as a required field.** The obvious move and the one first recommended
here. Rejected: it writes one vendor's requirement, itself downstream of one country's regulation,
into a contract 0102 built to outlive vendors. The port names no vendor and should name no
jurisdiction.

**Collect the phone at signup.** Rejected above — every free account taxed for a paid-tier
regulation, at the exact moment the plan says onboarding is most fragile.

**Switch to Razorpay.** Genuinely competitive: it meets both of 0102's constraints and removes this
problem entirely, because its own page collects the contact. Rejected *for now* by Raj, and the word
is load-bearing — the case is recorded here precisely so it stays cheap to revisit. What a switch
would cost is one adapter file and fresh signature research; what survives is the port, the webhook
route, the tables, the composition root and their tests. **That is what 0102 bought, and this ADR is
the first time it has been priced.**

**Store the phone after the first payment.** Rejected: see above.

## Consequences

**Payments cannot complete until the billing screen exists.** C3 was previously "after C1"; it is
now *part of* C1's path to working. That ordering is new and it is the price of this choice.

**A field exists that a Razorpay switch would delete.** Small, and visible — `required_contact_fields`
is where it lives, so removing it is removing one entry.

**`begin_checkout` still needs two things this ADR does not supply**: a price, resolved from
`plan_prices` before the port is called, and a return URL, which is a deployment fact and belongs
beside `APP_BASE_URL` in settings. **The return URL is the same missing fact blocking G7's SDK
distribution** — one deployment target answers both.

**Nobody has run a real Cashfree call.** The adapter is driven entirely through a mock transport,
deliberately. The timestamp freshness window is still unset because the unit is unverifiable without
one sandbox delivery, and this decision does not change that.

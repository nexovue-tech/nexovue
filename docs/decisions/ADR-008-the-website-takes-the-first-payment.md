# ADR-008: The website takes the first payment, and cloud customers are onboarded by hand

## Status

Accepted 2026-09-16 — explicit founder decision. Not yet executed: the website takes no payment, the
platform admin console (F31) does not exist, and hosted signup is still open to anyone.

## Context

nexovue is self-hosted and hosted ([ADR-004](ADR-004-agpl-with-a-permissive-browser-sdk.md)). There
are no customers, no marketing has started, and nobody is waiting. Running hosted infrastructure costs
money every month from the day it starts.

The founder's position: **a real payment is the signal that justifies investing in infrastructure.**
Until someone pays, no production environment should exist to pay for. That makes the order of events
fixed: a cloud customer pays *before* anything is provisioned for them, then the server path is chosen
([ADR-006](ADR-006-terraform-with-two-delivery-paths.md)), then they are onboarded.

The marketing site is `nexovue.com`: a Next.js application on Firebase, with the domain on Cloudflare.
It is due for a redesign around the product. That redesign is outside the feature catalogue.

## Decision

**Plans.** Self-hosting is free and available. Cloud plans are paid and can be bought. **There is no
free cloud tier** — for exactly the reason above: provisioning follows payment.

**The website takes the first payment.** Razorpay, through Firebase Cloud Functions.

1. The customer chooses a cloud plan on the website and pays.
2. **Payment is confirmed only by Razorpay's signed webhook**, verified server-side — never by the
   browser's return redirect, which a customer can fabricate.
3. On a verified payment: an email to the founder's personal address, a receipt to the customer, and
   a record in Firestore.
4. The customer is told plainly what happens next and when — their workspace is set up by hand and
   they are emailed when it is ready.

**Onboarding is manual, through a platform admin console.**

5. The founder chooses the server path, provisions, and opens the admin console.
6. The admin console creates the organization and its owner, in the chosen region
   ([ADR-009](ADR-009-regions-are-cells.md)).
7. **The owner receives a one-time link by email to set their own password.** The founder never sets,
   sees or transmits a customer's password.
8. **The website payment is attached to the new organization**, and from that moment the product's
   own billing — Razorpay checkout, signed webhooks and subscription state in Postgres, all already
   built — owns renewals.

## Rejected

- **Take payment only inside the product.** The product's billing needs a running hosted instance for
  the customer, and under this decision none exists until after payment. The first payment cannot
  happen there.
- **A free cloud tier.** It spends on infrastructure before anyone has committed.
- **The admin sets the customer's password.** The founder would know a customer's credential, and so
  would anything that logged the request. The code already anticipates the alternative: `auth/
  credentials.py` has a state for *"a user with no password set (invited, never activated)"*.

An objection was raised and withdrawn during the decision: that a second Razorpay integration in
Firebase would create two sources of truth for who has paid. Step 8 answers it — there is one owner of
subscription state at any moment, the website before onboarding and the product after.

## Consequences

- **F31 moves from "Later" into the first version.** The catalogue parked it because *"with no
  customers it is a console with one row"*; manual onboarding makes it the first thing a paying
  customer depends on.
- **Hosted signup must be closed.** Public self-service signup (F32) contradicts manual onboarding and
  would let anyone create an organization at our cost — blocker 3 in
  `docs/operations/production-readiness.md`. The `free` plan row is not offered on hosted deployments.
- **Firebase must be on the Blaze plan.** Cloud Functions cannot call anything outside Google — neither
  Razorpay nor Resend — on the free Spark plan. Blaze keeps the free quotas, so this is a billing
  account, not necessarily a cost.
- **Taking money for a service not yet provisioned needs terms, a privacy policy and a refund policy**
  that covers failing to provision. Razorpay's own activation checklist for website pages was looked
  for and not found stated; it must be checked in the Razorpay dashboard.
- Email for both the website and the product goes through Resend.

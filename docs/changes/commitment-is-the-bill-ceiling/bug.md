# Bug: the billing screen and the bank disagreed about the bill

## Classification

Bug — billing correctness.

## Symptom

The billing screen's **Cost** row and the amount the payment processor actually charges are two
different numbers whenever a customer monitors more services than they committed to.

On the pro plan at ₹399 per service, a customer monitoring **28** services on a commitment of
**26**:

| | number | where it comes from |
|---|---|---|
| Razorpay charges | 26 × ₹399 = **₹10,374** | the mandate's `quantity`, fixed at checkout (`razorpay.py:207`) |
| The console showed | 28 × ₹399 = **₹11,172** | `billed_services()` counted every service |

Under-charging rather than over-charging, so it cost nexovue rather than the customer. The screen
was wrong either way, and a customer reconciling their card statement against it would find ₹798 a
month unaccounted for.

## Reproduction

`apps/api/tests/test_usage.py::test_the_commitment_is_the_ceiling_on_a_plan_that_caps_nothing` —
an uncapped price, a commitment of 2, five services monitored. **Observed before the fix:**

```
>       assert response.period.billed_services == 2, "what they committed to is what they pay for"
E       AssertionError: what they committed to is what they pay for
E       assert 5 == 2
```

## Root cause

`apps/api/src/nexovue_api/usage/billing.py` held **one rule written twice, and only one of them
applied it.**

`allowance_service_count()` states it outright: *"A plan that caps the services it bills sets the
count itself. A plan with no cap — every paying tier — is sized on what the customer committed to
when they authorised the mandate, which is their own ceiling: they chose it, they can see it, and
they raise it themselves."*

`billed_services()` used `price.max_billed_services` alone and **was never given
`services_committed`**. That column is NULL on pro and scale — every paying tier — so on exactly
the plans where the commitment is the only ceiling there was no ceiling at all.

The intent is not in doubt. `StartCheckoutRequest.services_committed` says *"the number of services
a customer commits to is at once the most they can be charged in a month and the volume they may
send"*, and `docs/product/vision.md` lists **"the bill has a ceiling"** as one of the four pricing
promises. The commitment is that ceiling; the bill simply did not honour it.

## Customer impact

nexovue is pre-deployment, so no customer has been shown a wrong bill. After the first one: every
customer monitoring above their commitment reads a Cost that is too high, and reconciliation
against their card statement fails. It is a billing-correctness defect, which is the category a
customer forgives least, and the direction of the error does not make it safe — it would have been
found by a customer asking why the screen and the statement disagree.

## Fix

`billed_services()` takes `services_committed` and derives its ceiling from
`allowance_service_count()` — the same function, so the two cannot drift again. Precedence is
unchanged: a plan's own cap still wins, which keeps the free tier at three however much somebody
committed to.

`service_cost_minor_units()` passes it through. Both call sites — `usage/service.py` and
`billing/service.py` — already had `services_committed` in scope.

## What this does not decide

**Paid-tier *volume* overage is still open**, and this change does not touch it. Two different
questions were being answered by one number:

- **Service count** above the commitment — decided, and now enforced: the commitment is the
  ceiling.
- **Bytes** above the allowance on a paid tier — `docs/product/vision.md` §5 still records this as
  undecided, and `allowance_is_enforced` is where that decision will land.

Billing the service-count overage instead was considered and **rejected**: it is a different product
from the one the vision promises, and changing "the bill has a ceiling" is a pricing decision, not a
patch. It remains available to the founder — it would need the promise rewritten first.

## Regression coverage

- `test_the_commitment_is_the_ceiling_on_a_plan_that_caps_nothing` — seen red, output above.
- `test_the_plans_own_ceiling_still_wins_over_a_larger_commitment` — precedence, so the fix cannot
  be "fixed" later by dropping the plan cap.
- `test_an_uncapped_price_bills_every_service` — unchanged and still passing: with no commitment
  there is still no ceiling, which is not a ceiling of zero.

## Status

See `status.md`.

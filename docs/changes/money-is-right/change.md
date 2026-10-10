# Change: money-is-right — nexovue never takes money it should not, or gives a plan nobody pays for

## Classification

Bug (billing correctness). Readiness group 3, blockers 20 and 21 with four recommendations
(`docs/operations/production-readiness.md`).

## Current behavior and problem

Read from the code (paths under `apps/api/src/nexovue_api/`):

1. **Closing an organization keeps charging its card (blocker 20).** `organizations/service.request_deletion`
   (:95-115) marks, scrubs and audits; `erase` deletes `billing_subscriptions` rows
   (`organizations/queries.py:46-50`). Neither calls the processor. Razorpay keeps debiting the
   mandate (up to `total_count=12` cycles, `billing/razorpay.py`), and each later webhook names a
   subscription we no longer hold, so it is logged and ignored (`billing/service.py:144-156`) —
   money taken with no record left to refund from.
2. **Checking out twice debits twice (blocker 21).** `begin_plan_change` (`billing/service.py:801-810`)
   refuses only when the *newest* row is `active`; any number of `pending` rows can exist, two
   authorised tabs make two live mandates, and `end_subscription` (:874) cancels only the newest.
3. **A late "activated" re-grants a cancelled plan.** `_apply` (:197-203) sets `active` whatever the
   row's status, so `activated` arriving after `cancelled` puts a customer who left back on a paid tier.
4. **A webhook without an event id breaks deduplication.** `razorpay.py:370` reads
   `x-razorpay-event-id` as `""` when absent. The first id-less event is recorded under `""`; every
   later one for that organization is dropped as a duplicate — a cancellation included — and one for
   another organization violates the global unique `(processor, delivery_id)` at commit: a 500 the
   processor retries forever.
5. **Nothing stops test payments in production — and the setting meant to is a no-op.**
   `PAYMENTS_DOMESTIC_ENV` (`settings.py:290-298`) selects a base URL, and both of Razorpay's are
   `https://api.razorpay.com/v1` (:150-153). What decides test or live is the key: `rzp_test_…` or
   `rzp_live_…`. The hosted file defaults the no-op setting to `sandbox`
   (`docker-compose.app.yaml:78`) and nothing reads the key prefix.
6. **A permanently failed payment keeps the paid tier for ever.** `subscription.halted` maps to
   `PAYMENT_FAILED` beside `pending` (`razorpay.py:120-121`), and `_apply` returns early for it.

## Intended behavior

1. Deleting an organization first cancels every live arrangement at the processor, **immediately**
   (the organization is going, so there is no cycle to wait for). If the processor cannot be reached,
   or the arrangement was taken through a processor this deployment is not configured with, the
   deletion is refused with a sentence and nothing is scrubbed — a retry is safe, a debit is not.
2. One live checkout per organization. Starting a checkout first cancels any `pending` (and lapsed)
   arrangement at the processor, under a lock on the organization row so two tabs serialize. Ending a
   subscription ends every live one, not only the newest.
3. A `cancelled` arrangement stays cancelled: `activated` on it is recorded and changes nothing.
4. A webhook with no event id is refused (400) and nothing is recorded; the column refuses `''`.
5. `PAYMENTS_DOMESTIC_ENV` is deleted. The mode comes from the key, and the API refuses to start with
   a test key when `ENV=production`, or a live key when it is not.
6. **Founder decision (b), 7 days:** `subscription.halted` keeps the paid tier for a 7-day grace and
   emails the owners the date. A successful charge in that time clears the grace. When it runs out
   the organization drops to free and the arrangement is marked `lapsed` — not `cancelled`, so a
   customer who pays late (Razorpay resumes a halted subscription when the card is fixed) gets the
   plan back on that charge.

## Affected specifications and architecture

- `docs/architecture/current-system-reference.md` billing section; `docs/product/setup/` if it names
  `PAYMENTS_DOMESTIC_ENV`; root `.env.example`; `docker-compose.app.yaml`.
- No spec changes beyond the founder decision recorded here and in the readiness document.

## Risks and rollback

- The halted→grace path is the only new state; its sweep runs in the scheduler.
- Deleting an organization now depends on the processor answering. Refusing is deliberate.
- Rollback: `git revert`; the migration's downgrade drops the added columns and enum value use.

## Plan and validation

`plan.md`.

## Status

`status.md`.

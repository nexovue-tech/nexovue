# Status: money-is-right

## Lifecycle status

Implemented and verified on the development stack. Not committed.

## Current task

None.

## Completed

- Code read; `change.md` and `plan.md` written. Founder decision: `halted` → 7-day grace (b).
- **Step 1 — webhooks cannot be fooled.** A delivery with no event id is refused (red: "DID NOT
  RAISE"), and `0038` adds `CHECK (delivery_id <> '')`. An activation after a cancellation changes
  nothing (red: the plan moved back to `pro`).
- **Step 2 — one live arrangement, and closing one stops the money.** `cancel(…, immediately=True)`;
  `live_subscriptions`; a checkout locks the organization and supersedes earlier pending or lapsed
  arrangements at the processor; ending a subscription ends every live one; closing an organization
  cancels every live arrangement immediately before scrubbing and is refused when it cannot. Red then
  green: closing stops every charge (red: nothing cancelled); closing refused when the processor is
  down or unconfigured; a checkout supersedes an earlier one, and starts nothing if it cannot;
  ending ends every live arrangement; **two real concurrent checkouts leave one pending** (red without
  the lock: `['pending', 'pending']`).
- **Step 3 — a failed payment gets 7 days, then free.** `SUBSCRIPTION_HALTED` kind; `grace_until`
  set once on halt; owners emailed the date (`Message.PAYMENT_FAILED`), mail failure never refusing
  the delivery; a payment clears the grace and revives a lapsed arrangement; the scheduler's hourly
  `billing.end_lapsed_grace` lapses it and drops the plan in one statement. Red then green for the
  grace, the email, the clearing and the revival; the sweep's live test leaves paying and in-grace
  organizations alone.
- **Step 4 — test keys cannot take money in production.** `PAYMENTS_DOMESTIC_ENV` deleted (it chose
  between two identical hosts); the key prefix decides; production refuses a key that is not live, and
  any other environment refuses a live one. Red then green. The dev API (a test key) still starts.
- **Live on the dev API, with signed webhooks:** no event id → 422; `halted` → 200, plan `pro`, grace
  set, the owner's email rendered in the console log; grace moved into the past and the sweep run →
  `free`/`lapsed`; `charged` → `pro`/`active`; `cancelled` then `activated` → stays
  `free`/`cancelled`.

## Next action

Founder reviews the diff; on approval, commit. Then readiness group 4 (deletion finishes).

## Open decisions

None. Both `plan.md` assumptions accepted with the plan (2026-10-10).

## Blocked

None.

## Risks

- Closing an organization now depends on the processor answering; a slow or down processor delays or
  refuses it. Deliberate.
- A customer in grace who never pays gets at most 7 days of unpaid service.
- The `0038` downgrade rebuilds the enum (Postgres cannot drop a value), moving `lapsed` rows to
  `cancelled` first.

## Validation

`just check` exit 0 — Python 4239 passed, 3 skipped (the existing deliberate ones); web 1352; browser
SDK 120; ruff, mypy (602 files) and the Go linters clean. The first run failed five tests, all fixed:
a renamed test helper that could not be iterated, a missing sample for the new message, and two
production-mode tests that inherited the developer's `.env` test key — which production now refuses,
as it should; they now clear the payment variables.

## Specification impact

None.

## Documentation impact

Updated: the readiness document (blockers 20 and 21 and the four recommendations fixed, group 3
built), the current-system reference's billing row, root `.env.example`, the hosted compose file.

## Last updated

2026-10-10

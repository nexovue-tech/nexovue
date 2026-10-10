# Plan: money-is-right (readiness group 3)

> **Status:** Approved 2026-10-10
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` written; founder decision for `halted`: (b), **7 days** grace (2026-10-10).

## Current system

In `change.md` §Current behavior, with `file:line`. Three facts the readiness review did not have:

1. `PAYMENTS_DOMESTIC_ENV` changes nothing for Razorpay — both values resolve to the same API root
   (`settings.py:150-153`); the key prefix is what decides test or live.
2. The status vocabulary is a Postgres **ENUM** (`billing_subscription_status`, migration 0020:61-63),
   so a new status is `ALTER TYPE … ADD VALUE`, not a CHECK edit.
3. Entitlement is `organizations.plan` plus the newest **`active`** subscription row (receiver
   `allowance/postgres.go:57-63`, API `usage/queries.active_services_committed`). Keeping a row
   `active` during grace means neither reader changes.

## Change required

`change.md` §Intended behavior.

## Impact

| Area | What changes |
|---|---|
| Files | `billing/{service,razorpay,processor,queries}.py`, `organizations/service.py`, `settings.py`, `data/postgres/models.py`, py-platform email templates, a scheduler sweep, compose + `.env.example` |
| Services | API, scheduler |
| Schema / migrations | `0038`: enum value `lapsed`; `billing_subscriptions.grace_until`; `CHECK (delivery_id <> '')` |
| UI | None required. The billing screen shows `lapsed` as ended (it already treats non-active as free); grace shows as active |
| Tests | `test_billing.py`, `test_razorpay.py` extended; new `test_billing_live.py` for the deletion and checkout paths; scheduler sweep test |

## Reuse

- `CancellationOutcome` and the adapter's idempotent cancel (`razorpay.py:279-328`).
- `lock_organization` (`organizations/queries.py:125`) — the same row lock serializes checkouts.
- `nexovue_platform.email` port and templates; `members` queries for the owners' addresses.
- The scheduler's beat sweep entries and job registry (group 2b).
- `StubProcessor` in `test_billing.py`.

## Gaps

- A way to cancel *now*: `PaymentProcessor.cancel(…, immediately: bool)`.
- A `SUBSCRIPTION_HALTED` event kind, distinct from a single failed charge.
- The grace sweep, and a payment-failed email.

## Steps and tasks

### Step 1 — webhooks cannot be fooled

*Judgeable:* the tests below, red then green.

- [ ] **T1** — `billing/razorpay.py` · a missing `x-razorpay-event-id` raises `BillingError` (400) ·
  `test_razorpay.py` (new case).
- [ ] **T2** — migration `0038` + `models.py` · `CHECK (delivery_id <> '')` on `billing_webhook_deliveries`.
- [ ] **T3** — `billing/service._apply` · `activated` on a `cancelled` row changes nothing (logged) ·
  `test_billing.py`.

### Step 2 — one live arrangement, and closing one stops the money

*Judgeable:* two checkouts leave one pending arrangement at the (stub) processor; deleting an
organization with an active one cancels it first, and is refused when the processor is down.

- [ ] **T4** — `billing/processor.py`, `razorpay.py` · `cancel(subscription_ref, immediately=False)`;
  immediately sends `cancel_at_cycle_end=0` · `test_razorpay.py`.
- [ ] **T5** — `billing/queries.py` · `live_subscriptions(org_id)` (pending, active, lapsed).
- [ ] **T6** — `billing/service.begin_plan_change` · lock the organization row; cancel every pending
  or lapsed arrangement (immediately) and mark it cancelled before starting a new one; still refuse
  over an active one · `test_billing.py` + live test (two concurrent checkouts → one pending).
- [ ] **T7** — `billing/service.end_subscription` · ends every live arrangement, not only the newest.
- [ ] **T8** — `organizations/service.request_deletion` + router wiring of the processor · cancels
  every live arrangement immediately before scrubbing; refuses with 503 (processor unreachable or not
  configured) or 409 (another processor) and scrubs nothing · `test_billing_live.py` / deletion tests,
  red then green.

### Step 3 — a failed payment gets 7 days, then free

*Judgeable:* a signed `subscription.halted` keeps the plan, sets `grace_until` 7 days out and the
owners' email appears in the console log; a `charged` clears it; with `grace_until` moved into the
past, the sweep drops the organization to free and marks the row `lapsed`; a later `charged`
restores the plan.

- [ ] **T9** — `billing/processor.py`, `razorpay.py` · `subscription.halted` → `SUBSCRIPTION_HALTED`
  (`subscription.pending` stays a single failed charge) · `test_razorpay.py` translation table.
- [ ] **T10** — migration `0038` + `models.py` · enum value `lapsed`; `grace_until timestamptz NULL`;
  `GRACE_PERIOD_DAYS = 7` in `billing/service.py`.
- [ ] **T11** — `billing/service._apply` · halted on active → `grace_until = now + 7d` (once, not
  extended by a repeat); charged/activated → clear `grace_until`, and on `lapsed` → `active` with the
  plan restored · `test_billing.py`.
- [ ] **T12** — py-platform `email` · `Message.PAYMENT_FAILED` template (organization, date, billing
  link); `billing/service.record_from_webhook` sends it to the owners, idempotent on the delivery id,
  a mail failure logged and never failing the webhook · `test_billing.py`.
- [ ] **T13** — `nexovue_scheduler/billing.py` (new task) + beat entry hourly · rows `active` with
  `grace_until < now()` → `lapsed`, organization plan → free · `apps/scheduler/tests/test_billing_grace_live.py`.

### Step 4 — test keys cannot take money in production

*Judgeable:* the API refuses to start with `ENV=production` and an `rzp_test_` key, and with a live
key anywhere else.

- [ ] **T14** — `settings.py` · `PAYMENTS_DOMESTIC_ENV` and `PaymentEnvironment` deleted; the mode is
  read from the key prefix; a mismatch with `ENV` stops boot naming the variable · `test_settings.py`
  / `test_billing.py` settings cases.
- [ ] **T15** — `docker-compose.app.yaml`, root `.env.example`, docs naming the variable · removed.

### Step 5 — documents

- [ ] **T16** — readiness document (blockers 20, 21, the four recommendations, group 3),
  `current-system-reference.md` billing section, `status.md`.

## New risks

- `ALTER TYPE … ADD VALUE` cannot be undone by a downgrade without rebuilding the type; the downgrade
  rebuilds it (no `lapsed` rows may exist — they are moved to `cancelled` first).
- Deletion now waits on the processor; a slow processor is a slow (or refused) deletion.
- A customer in grace who never pays costs at most 7 days of paid service.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | A live key outside production is refused too (so a developer's machine cannot take real money). Assumed yes. | No | `change.md` |
| 2 | One email, at the halt (with the date). No second email when the grace ends. Assumed enough for now. | No | `change.md` |

## Gates

- [x] Specification approval — not applicable
- [x] Design approval — the founder chose (b), 7 days
- [x] **Plan approval — before the first edited file** — 2026-10-10
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Documentation and status update

## Validation plan

Every regression test seen red first. `just check` with counts read. Live: drive signed webhooks at the
dev API for halted → grace → sweep → lapsed → charged, and a deletion with the stub processor refusing.

## Rollback

Pre-deployment; `git revert` plus the migration downgrade.

## Outcome

Filled in when the work completes.

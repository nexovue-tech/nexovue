# Status: commitment-is-the-bill-ceiling

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- `billed_services()` and `service_cost_minor_units()` take `services_committed` and take their
  ceiling from `allowance_service_count()`, so the bill and the allowance cannot describe different
  ceilings again.
- Both call sites updated; `billing/service.py` reads the commitment before it prices the month
  rather than after.
- Two regression tests, one of them seen red first.

## Next action

Founder: review the diff and say **push**.

This was reached by a different route than the one approved. The founder chose **billing the
overage** on 2026-09-21, on a recommendation made before `docs/product/vision.md` §5 and
`allowance_service_count`'s own docstring had been read. Both say the commitment is the ceiling and
that "the bill has a ceiling" is a standing pricing promise — so billing above it is a different
product, not a fix, and the screen was simply wrong. **Say so if the intent was to change the
promise**, and the overage version can be specified properly.

## Open decisions

- **Paid-tier volume overage** — untouched here and still open in `docs/product/vision.md` §5.
  Distinct from the service count, which this settles; the two were being conflated by one number.
- **Whether to sell overage at all.** Available, and it would mean rewriting one of the four
  pricing promises. A pricing decision, not an implementation one.

## Blocked

Nothing.

## Risks

- **A customer above their commitment now sends more than they pay for, with nothing asking them to
  raise it.** The screen reports both figures — "Services monitored" against the plan's committed
  count — but nothing prompts. That is the same behaviour as before the fix; what changed is only
  that the Cost row no longer overstates. Worth a nudge on the billing screen, which is a change of
  its own.
- **The ceiling is only applied where these two functions are called.** Nothing structural stops a
  third caller computing a cost without the commitment. Both existing ones are covered; a test that
  parses the source, like `test_tenant_chokepoint_structure`, would hold it properly.

## Validation

- `just check`, exit 0: **4121 Python passed, 2 skipped**; **1335 web** (123 files); **120** browser
  SDK; Go, format, lint, `mypy` (572 files) and the contract checks clean.
- **Seen red before green** — `assert 5 == 2`, "what they committed to is what they pay for".
- **Razorpay's behaviour was verified rather than assumed**: `PATCH /v1/subscriptions/:id` can
  update `quantity`, with `schedule_change_at` of `now` or `cycle_end`, a minimum prorated
  difference of 50 subunits, and only in the `authenticated` or `active` states. That was checked
  while costing the overage option, and is recorded because it is what any future overage work
  starts from.

## Specification impact

`docs/features/usage-billing/spec.md` is a 21-line current-state baseline with no acceptance
criteria, so there was nothing to amend. The authoritative statement of the rule is
`allowance_service_count`'s docstring and `docs/product/vision.md` §5, and both already said it.

## Documentation impact

`docs/product/vision.md` §5 — the "still open" paragraph now separates the two overage questions it
was running together.

## Last updated

2026-09-21

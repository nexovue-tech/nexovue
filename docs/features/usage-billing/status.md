# Status: usage-billing (F25)

## Lifecycle status

**Specification proposed, awaiting the founder's approval.** No design, no plan, no code follows
until then. The feature itself is built and released; what is new is the document that says what
correct means for it.

## Current task

None.

## Completed

- `spec.md` rewritten from a 21-line current-state baseline into a specification: problem, target
  users including the one it does not serve, scope and out-of-scope, 8 user requirements, 14
  business rules, 12 functional requirements, 11 error cases and 15 acceptance criteria.
- Written **against the code**, not the catalogue. Where a rule is asserted, the function that
  implements it is named.
- **The gaps are marked GAP rather than smoothed over** — six of them, including the one that
  matters most below.

## Next action

Founder: read `spec.md` and approve, or say what is wrong. Then **F23 Onboarding** is the next
specification, per the order in `docs/features/README.md`.

## Open decisions

- **Paid-tier volume overage** — BR25.5 says we refuse and never bill. Vision §5's wording still
  records the original pricing decision as saying both things; closing that wording is the
  remaining step.
- **What a self-hosted install shows** on the usage and billing screens (EC25.10).
- **Whether to prompt a customer above their commitment** to raise it (EC25.9).
- **Reconciliation** between the processor's charges and our rows (EC25.11).

## Blocked

Nothing.

## Risks

- ~~**AC25.15 has no test behind it.**~~ **Closed the same day.** The allowance rule is written in
  Python and in Go, both comments asserted the other must agree, and nothing checked. Seven shared
  cases now do, read independently by both languages. This was the most valuable thing the
  specification turned up — a gap in the product, found by writing down what correct means.
- **A specification written after the code risks describing the code rather than the requirement.**
  Guarded by marking every divergence GAP instead of writing the code's behaviour into a rule; six
  are marked. It is still the weaker order, and the remaining ten should not be written this way if
  the feature is still moving.

## Validation

Mostly a document, but one acceptance criterion was buildable and cheap, so it was built:
`just check` exit 0 with the cross-language cases passing in both languages, and **seen red** by
making the Go rule ignore the commitment.

Otherwise: The claims in it were each checked against the
function named beside them; the Razorpay constraint was verified from their API documentation
rather than recalled.

## Specification impact

This *is* the specification. `docs/product/feature-catalog.md` F25 moves from `🔲 TO SPECIFY` once
the founder approves.

## Documentation impact

`docs/features/README.md` — the backlog and its order.

## Last updated

2026-09-21

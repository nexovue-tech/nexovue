# Usage, plan allowances, and billing

## Status

**Verified current-state baseline.** Related catalog feature: F25. Payments/reconciliation remain
incomplete product scope and must not be inferred as production-ready.

## Implemented behavior

- The worker meters spans, logs, metrics, bytes, browser bytes, and distinct services by day/project.
- Usage and billing screens show current plan, subscription/mandate state, allowance, and usage.
- The receiver applies organization allowance at ingestion. Exhausted allowance is refused without a
retry directive; control-plane allowance read failures fail open to avoid telemetry loss.
- Checkout creates a payment-provider handoff; only a signed, delivery-idempotent webhook changes
subscription/plan state.

## Code evidence

`apps/workers/internal/pipeline/usage/`, `apps/receiver/internal/allowance/` and `otlp/gates.go`,
`apps/api/src/nexovue_api/usage/router.py` and `billing/`, plus web `features/usage/` and `billing/`
are authoritative.

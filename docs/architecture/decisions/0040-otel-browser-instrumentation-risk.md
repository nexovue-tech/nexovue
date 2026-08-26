# 40. Risk accepted — OTel browser instrumentation is experimental

- **Status:** Accepted (risk)
- **Date:** 2026-08-26

## Context

Door 3 (RUM, P1-39) rests on OpenTelemetry's browser instrumentation. The core JS SDK is stable,
but browser instrumentation is explicitly "experimental and mostly unspecified" and lives in a
separate repository.

## Decision

Accept the risk. Pin hard and budget for churn.

Related semantic-convention findings, decided here:

- HTTP semantic conventions stabilized with 17 attribute renames. Promoted columns use the **stable
  names**, and normalization must accept both old and new — customers run varying SDK versions.
- `gen_ai.*` attributes stay **unpromoted**; they were renamed three times in one 2026 release
  cycle.

## Consequences

Door 3 carries a maintenance cost the other doors do not, and RUM upgrades are a scheduled activity
rather than an incidental one. Accepting both attribute spellings adds a normalization branch that
cannot be removed until customer SDKs converge — but promoting an unstable attribute would put
churn into the schema instead, which is far worse.

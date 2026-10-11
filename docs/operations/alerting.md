# Alerting

## Current status

No alert routing or on-call configuration exists for nexovue itself. Every service reports what an
alert would be built on — health endpoints and `/metrics` (ADR-021) — and `monitoring.md` names the
first thresholds worth alerting on. Nothing reads them yet: the monitor and where it pages are chosen
with the hosting target (readiness group 8).

The customer-facing alerting product (F21) is built, and is not a substitute: it runs inside nexovue,
and the alert that nexovue is down has to come from outside it (decision 0053, rule 1).

Still to define before production: who is paged and how, the escalation path, and alerts for backup
failures, security-sensitive events and capacity, which no metric covers yet.

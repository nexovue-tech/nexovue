# Alerting

## Current status

No alert routing or on-call configuration exists for nexovue itself. Every service reports what an
alert would be built on — health endpoints and `/metrics` (ADR-021). The rules are written and
tested (`deploy/monitoring/rules/nexovue.yml`, `promtool test rules` in `just lint-deploy`), and
`monitoring.md` names their thresholds, but Alertmanager routes to nowhere: where it pages is chosen
with the hosting target (readiness group 8).

Each alert's procedure lives beside its area: [monitoring](monitoring.md) for the pipeline and the
scheduler, [detection](detection.md) for detection, and [ai](ai.md) for AI wording and suggested
fixes (`NexovueSuggestionRefusedAgain`, the tamper signal; `NexovueSuggestionSweepStalled`, rung 3's
sweep).

The customer-facing alerting product (F21) is built, and is not a substitute: it runs inside nexovue,
and the alert that nexovue is down has to come from outside it (decision 0053, rule 1).

Still to define before production: who is paged and how, the escalation path, and alerts for backup
failures, security-sensitive events and capacity, which no metric covers yet.

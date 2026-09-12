# Feature workspaces

Two kinds of document live here, and they must not be confused.

**Proposed and in-flight features** state what a capability *must* do. Their acceptance criteria are
requirements, and they move through the lifecycle in `docs/engineering/workflow.md`.

- [Console redesign](console-redesign/spec.md) — **Proposed.** All 20 routes redesigned in place so
  each screen's form follows its data; insights and dashboards both first-class.

## Existing feature baselines

The rest are **current-state baselines**, derived from code and tests. They record what is already
implemented; they do not authorize future behavior.

When a capability changes, create or extend its `design.md`, `plan.md`, and `status.md` under the
same directory, then update this baseline when implementation truth changes.

- [Account access](account-access/spec.md)
- [Project credentials and onboarding](project-credentials-onboarding/spec.md)
- [Telemetry intake and protection](telemetry-intake-protection/spec.md)
- [Telemetry investigation](telemetry-investigation/spec.md)
- [Infrastructure, database, and RUM](infrastructure-database-rum/spec.md)
- [Synthetic checks](synthetic-checks/spec.md)
- [Integrations and deploy events](integrations-deploy-events/spec.md)
- [Usage and billing](usage-billing/spec.md)
- [Data lifecycle controls](data-lifecycle-controls/spec.md)

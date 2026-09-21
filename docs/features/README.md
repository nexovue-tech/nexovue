# Feature workspaces

Two kinds of document live here, and they must not be confused.

**Proposed and in-flight features** state what a capability *must* do. Their acceptance criteria are
requirements, and they move through the lifecycle in `docs/engineering/workflow.md`.

- [Console redesign](console-redesign/spec.md) — **Proposed.** All 20 routes redesigned in place so
  each screen's form follows its data; insights and dashboards both first-class.
- [Usage and billing](usage-billing/spec.md) — **Proposed 2026-09-21**, F25. The first baseline
  rewritten into a real specification, with fourteen business rules, fifteen acceptance criteria and
  the gaps named rather than smoothed over.

## The specification backlog

**Eleven features are built and have no acceptance criteria.** They are marked `🔲 TO SPECIFY` in
`docs/product/feature-catalog.md`, and the mark is honest: nobody has written down what correct
means for them, so nobody can say whether they are.

This is not a formality. The usage-billing baseline was 21 lines, and in its absence the billing
screen charged for every service monitored while the bank charged the committed count
(`docs/changes/commitment-is-the-bill-ceiling/`) — a defect that a single acceptance criterion would
have caught before a customer did.

Written, in the order recommended 2026-09-21. The order is by **what it costs to be wrong**, not by
what is easiest:

| | Feature | Why here |
|---|---|---|
| 1 | **F25 Usage and billing** | ✅ Done. Money, and the gap already cost two defects |
| 2 | **F23 Onboarding** | The first ten minutes decide whether there is a customer at all |
| 3 | **F12 Logs** and **F11 Errors** | The two screens somebody opens during an incident |
| 4 | **F8 Service overview** and **F10 Trace waterfall** | The investigation path the product sells |
| 5 | **F15 RUM** | Its SDK is about to be published; the contract should exist first |
| 6 | **F24 Integration settings** | Partly built, so the spec also settles what "done" is |
| 7 | **F13 Infrastructure**, **F14 Database**, **F9 Service map** | Built, stable, and read least |

**One at a time, each through the specification gate**, because a specification written in bulk to
clear a backlog is the thing the template warns about: a heading answered reads as considered when
it was not.

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

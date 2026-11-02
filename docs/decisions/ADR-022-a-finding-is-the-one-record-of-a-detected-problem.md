# ADR-022: A finding is the one record of a detected problem, and the outbox addresses findings or incidents

## Status

Accepted 2026-10-31, with the F6 design ([`design.md`](../features/anomaly-detection/design.md),
revision 2).

## Context

The founder decided that one detected problem is one record (F6 spec, D1). The finding is that record: F1
shows it, alert rules route its notifications, and Stage 3 grows it into a case.

F21's records do not fit it:

- **An incident belongs to a rule.** `incidents.rule_id` is not null, and the open index is
  `(rule_id, subject_key)` (`apps/scheduler/src/nexovue_scheduler/alerts/incidents.py`). A finding exists
  with no rule, and with several.
- **The outbox is bound to incidents throughout**, not only in its table:
  - `notifications.incident_id` is not null (`0028_alerts.py`);
  - delivery's claim joins `incidents` (`outbox.py`, `_TAKEN`);
  - the in-order rule matches on `incident_id` (`_IN_ORDER`);
  - `Notice` requires an incident's fields;
  - the PagerDuty `dedup_key` and the message link come from `incident_id` (`delivery.py`, `messages.py`);
  - the webhook schema emits `incident.*` events only (`payloads.py`).

## Decision

1. **Findings are their own table.** Two partial unique indexes allow one open finding per service and one
   per project. Two are needed because Postgres treats nulls as distinct.
2. **The outbox has two kinds of subject, findings and incidents.**
   - `notifications` gains `finding_id`, with a check that exactly one subject is set, and a unique key for
     each kind.
   - Delivery reads both kinds through one `Subject` shape.
   - `Notice` carries `subject_kind` and `subject_id`, and the in-order rule matches on both.
   - PagerDuty's `dedup_key` is `finding:<id>` or `incident:<id>`.
   - The webhook schema is version 2, with `incident.*` and `finding.*` events and `data.subject`.
   - ADR-013's rules hold for both kinds:
     - the row is written in the deciding transaction;
     - deliveries are claimed under a lease;
     - each subject's messages go in the order they were decided;
     - a closing message goes only to the channels told at opening.
3. **Rules of kind `anomaly_finding` only route a finding's notifications.** They create no record of their
   own. The finding's severity is the detector's, which amends F21's BR10 for this kind.
4. **A threshold or *went silent* incident on the same service is linked, not merged,** through
   `finding_incidents`.

## Consequences

- F21's incident invariants and its tests stand. What changes is the outbox's subject handling, the
  message renderer, the PagerDuty key and the webhook schema version.
- nexovue is pre-deployment, so webhook version 1 is replaced rather than kept beside version 2.
- Stage 3 grows a case from a finding. Whether a check or threshold incident also becomes a case is Stage
  3's decision, and `finding_incidents` is where that starts. `phase-2-plan.md`'s "grows the existing
  incident" is amended to "grows the finding".
- **Rejected: widening `incidents` to hold findings.** It would drop `rule_id`'s not-null and change the open
  index that enforces F21's BR1. That would rewrite F21's tested invariants to gain nothing over a second
  subject.

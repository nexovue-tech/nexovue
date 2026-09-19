# Bug: an alert's link dies with the check it was about

## Reported behavior

The founder followed the link in a real *[resolved]* alert email and landed on "No such check — It may
have been deleted, or it belongs to another organization." (2026-09-19).

## Reproduction

1. Real alert email on (`just alert-email real`); the `acme` project's default rule emails the owners.
2. Create an API check against `http://host.docker.internal:18000/api/v1/resolve-demo-missing`
   (404 → down). At 20:34 IST the incident opened and `opened|owners|delivered` was written.
3. Point the check at `/healthz`. At 20:35 IST the incident resolved: `resolved|owners|delivered`;
   Resend answered `200 OK` to both.
4. Delete the check (`DELETE /checks/{id}` → 204).
5. Open either email's link, `/synthetics/01a0ba31-3a6c-77d9-9222-40e6f4e3b5cc`: "No such check".

The same link travels in every channel — Slack, Discord, Teams, PagerDuty's `links`, the webhook's
`data.link` — so each dead-ends the same way.

## Root cause

`apps/scheduler/src/nexovue_scheduler/alerts/messages.py` (`_said`) builds every message's one link
from the incident's *subject* — the check, the service, the error list — because FR16 says the link
goes "to the evidence". The subject can be deleted; the incident cannot. Nothing in the console showed
one incident on its own (`GET /alerts/incidents/{id}` existed in the API with no screen), so there
was nowhere durable to link to. A message outlives what it describes by design — an email is read a
week later, a PagerDuty incident is reviewed at the post-mortem — and its link must too.

## Specification impact

FR16 amended (founder, 2026-09-19): the link goes to the **incident**, and the incident page links
onward to the evidence while the evidence exists. A message about several incidents (FR12, FR13,
FR15) still links to the Alerts screen.

## Regression test

- `apps/scheduler/tests/test_alert_messages.py` — every single-incident message links to
  `/alerts/incidents/{incident_id}`, whatever the condition.
- `apps/api/tests/test_alerts_live.py` — an incident whose check was deleted reports `check_deleted`.
- `apps/web/src/features/alerts/IncidentScreen.test.ts` — the page says what happened, who was told,
  links to the check while it exists and says it was deleted once it is not.
- `tools/e2e/tests/alerts.spec.ts` — the incident page opens from the Alerts screen for a real incident.

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.

# Status: alert links to incident

## Lifecycle status

Verified

## Current task

None — awaiting the founder's review of the diff.

## Completed

- Every single-incident message links to `/alerts/incidents/{incident_id}`
  (`apps/scheduler/src/nexovue_scheduler/alerts/messages.py`); the per-condition evidence links it built
  are deleted. A message about several incidents still links to `/alerts`.
- `IncidentSummary.check_deleted` and `IncidentNotification.id` (`apps/api/src/nexovue_api/alerts/`).
- The incident page, `apps/web/src/features/alerts/IncidentScreen.tsx`, at `/alerts/incidents/$incidentId`:
  what broke, open or resolved and for how long, why, the rule, the evidence one link on (switching to the
  incident's project if another is selected) or that the check was deleted, and who was told.
- Reached from each row of the Alerts screen (*Details*) and from a check's *Open incident* strip.
- F21 spec FR16 amended and D9 added; `docs/product/setup/alerts/{channels,webhooks}.md` updated.

## Next action

Founder: read the diff, then say **push**. Then decide the finding under *Open decisions*.

## Open decisions

- ~~**Every API write commits after its response is sent.**~~ **Fixed** in `dcfc9b2`, "Every API
  write commits before its response is sent": `DbSession` carries
  `Depends(get_db_session, scope="function")`, which FastAPI ends *before* the response goes out.
  This entry outlived the fix and was reported as pending on 2026-09-21 by reading it rather than
  the code — `deps.py:212` is the answer, and a document is not.

## Blocked

Nothing.

## Risks

- The webhook payload's `data.link` now points at the incident, not the evidence. Kept at schema
  version 1: the field still means "where to look in nexovue", and nexovue is pre-deployment, so no
  receiver reads it yet.
- The incident page refreshes every 15 seconds even once resolved; cheap (one indexed row), not needed.

## Validation

- Regression tests seen red before the fix: `test_alert_messages.py` (3 link assertions) and
  `test_alert_delivery_live.py` (`assert None` on the incident-link match) failed against the old
  composer; pass after. `IncidentScreen.test.ts` "still reads once its check is deleted" failed with
  the `check_deleted` branch disabled (*expected … to contain 'The check this was about has since been
  deleted'*) and passes with it.
- Found in self-review: the Alerts screen's row still linked a deleted check's name to "No such check".
  Fixed; `IncidentScreen.test.ts` "does not link the Alerts screen's row to a check that is gone" failed
  with the guard removed and passes with it.
- `test_alerts_live.py::test_an_incident_outlives_its_check_and_says_the_check_is_gone`: passes
  (28 in the file).
- `tools/e2e/tests/alerts.spec.ts`: the incident test passes end to end — a real check goes down, the
  row's *Details* opens `/alerts/incidents/<id>`, which after the check's deletion reads *Resolved
  after …*, *has since been deleted* and *Opened → Organization owners*. The other test in the file
  failed once in the parallel run (see *Open decisions*) and passed twice alone.
- `just check`: exit 0 — 3,554 Python passed (3 skipped), web 1,263 passed, browser SDK 120 passed.
- Live, before the fix: two real Resend emails whose link led to "No such check" once the check was
  deleted (*Reproduction* in `bug.md`).

## Specification impact

F21 `spec.md`: FR16 amended; D9 added.

## Documentation impact

`docs/product/setup/alerts/channels.md` (*What a message says*) and `webhooks.md` (the example
`link`) updated. `docs/features/alerts/status.md` unchanged — this record holds the change.

## Last updated

2026-09-19

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

- **Every API write commits after its response is sent.** Found while running this change's E2E:
  `alerts.spec.ts` "a new organization…" failed once under the parallel run — the channel list kept
  showing only the owners after *Connect Webhook*. The API log shows `POST /channels 201` at
  15:16:02.644 and the list's refetch `GET /channels 200` at 15:16:02.655, 11 ms later, with no further
  read. `get_db_session` (`apps/api/src/nexovue_api/deps.py:54`) commits in a yield dependency's exit, and
  FastAPI's default dependency scope (`"request"`) runs that exit *after* the response is sent
  (FastAPI docs, *Advanced Dependencies*; `fastapi` 0.141.1 here). So a client that re-reads at once can
  read before the commit. The signup instance of this was fixed alone
  (`docs/changes/signup-session-before-commit/`); the cause is platform-wide. The likely fix is
  `Depends(get_db_session, scope="function")`, which commits before the response — but it needs an audit
  of every route that reads the session after its function returns (streaming responses, the log-tail
  WebSocket). Out of this change's scope; its own change record, on the founder's word.

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

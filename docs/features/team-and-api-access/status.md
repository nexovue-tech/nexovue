# Status: Team, API and admin access (F26 + F31 + F36)

## Lifecycle status

**In development** — Stage 1 (Team) implemented and verified 2026-09-19, awaiting the founder's review.
Stages 3 (admin console) and 2 (read API and MCP) are planned and not started.

## Current task

None — Stage 1 is waiting for the founder's review of the diff.

## Completed

- Specification (D1–D13 decided; D6 and D7 changed by the founder to include the MCP server and the audit
  log screen).
- Design, ADR-016, ADR-017.
- Plan written from four code readings; nine design-vs-code corrections recorded (plan, C1–C9) and folded
  into the spec and design (Step 0).
- **Stage 1 — Team** (plan Steps 1–4, T1–T24): one permission table enforced on every route; invite,
  accept, list, resend, withdraw, change role, remove, transfer; the four roles reflected on every screen;
  the audit log screen. See `plan.md` → *Outcome* for what changed from the plan and why.

## Next action

Founder: review the Stage 1 diff and say **push**. Then approve starting Stage 3 (admin console, plan
Steps 5–8).

## Open decisions

- **The e2e suite is over its rate budget on the pushed code already** (2 synthetics failures from a
  clean start). Adding `members.spec.ts` makes it worse; lowering to 3 workers did not clear it. The
  config's own rule is "adding screens should make this suite slower, not flakier", which no worker count
  now satisfies. Needs its own change record — for example, the suite reaching the API as distinct
  callers rather than one proxied address. Not changed here.

- Q2 — production routing of the admin host for the hosted cloud (non-blocking).
- Q3 — whether self-hosted installs run `admin-api` (assumed no; non-blocking).

## Blocked

Nothing.

## Risks

See `plan.md` → *New risks*. Found while building Stage 1:

- **No render test of the billing screen by role.** Its checkout and cancel controls are gated
  (`useCan("commit_spend")`) and the API refuses a non-owner regardless; the test was not written because
  its overview fixture is large. Every other screen with a control has an owner/viewer pair.
- **Members invited in development get no real email** unless `just alert-email real` style wiring is added
  for the API: invite mail goes through the API's own sender, which stays on the console provider in
  development. The e2e reads the link from the API log.

## Validation

Stage 1, 2026-09-19:

- API: `test_policy.py` (54 — every action × role), `test_authorization_coverage.py` (every mutating
  route pinned to its policy; seen red with Send test widened to members), `test_members_live.py` (17 —
  AC1, AC2, AC5–AC10, AC12), `test_audit_read_live.py` (5 — AC36, pages complete, filters, removed names,
  other tenants absent), `test_people_migration_live.py` (5 — up, down, up; constraints),
  `test_auth_routes.py` (`/auth/me` permissions), `test_notifications_console.py` (invite mail renders).
- Scheduler: `test_alert_email_recipients_live.py` (3 — AC11; seen red with the removed filter taken out).
- Web: `shared/identity.test.ts`, `MembersScreen.test.ts`, `AcceptInviteScreen.test.ts`,
  `AuditScreen.test.ts`, and owner/viewer pairs for synthetics, alerts, channels, ingest keys and
  projects (AC4).
- E2E: `tools/e2e/tests/members.spec.ts` — owner invites; the invite mail is read from the API log; the
  teammate accepts and signs in; as a Member sees *New check*; made a Viewer, loses it on reload; removed,
  is signed out. Passed.
- `just check`: Go 34 packages, Python 3,715 passed (3 skipped), web 1,291 + browser SDK 120, contracts
  clean. Two runs each tripped one live suite for environmental reasons, both fixed by the environment
  and then passing: the tenant-isolation suite needs recent seed traffic (`just load`, then 9 passed), and
  development ClickHouse reached its documented 4 GiB guard after repeated runs (restarted, then 18
  passed).
- `just e2e`, A/B from a clean start (ClickHouse and Redis restarted, one idle minute before each run):

  | Code | Suite | Failed / passed |
  |---|---|---|
  | pushed (`dcfc9b2`) | all | 2 / 19 — both synthetics |
  | Stage 1 | all but `members.spec.ts` | 2 / 19 — the same two |
  | Stage 1 | all, 4 workers | 5 / 17 |
  | Stage 1 | all, 3 workers | 3 / 19 — all synthetics; members passes |

  Every failure is a 429 ("Too many requests") rendered on screen, mostly the telemetry tier (60 reads a
  minute per address, and the whole suite is one address behind the web proxy). **Stage 1's code adds no
  failures**; its new spec adds load to a suite already at its budget.

## Specification impact

Pending Step 0: FR17, FR18, FR19 amended to match the code (C2, C3, C5).

## Documentation impact

Catalogue F26/F31/F36 rows and the architecture reference change at Step 12.

## Last updated

2026-09-19

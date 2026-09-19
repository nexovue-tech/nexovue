# Status: Team, API and admin access (F26 + F31 + F36)

## Lifecycle status

**In development** — Stage 1 (Team) built and pushed 2026-09-19 (`a9ae18d`). Stage 3 (admin console)
built and verified 2026-09-20, awaiting the founder's review. Stage 2 (read API and MCP) is planned and not
started.

## Current task

None — Stage 3 is waiting for the founder's review of the diff.

## Completed

- Specification (D1–D13 decided; D6 and D7 changed by the founder to include the MCP server and the audit
  log screen).
- Design, ADR-016, ADR-017.
- Plan written from four code readings; nine design-vs-code corrections recorded (plan, C1–C9) and folded
  into the spec and design (Step 0).
- **Stage 1 — Team** (plan Steps 1–4, T1–T24): one permission table enforced on every route; invite,
  accept, list, resend, withdraw, change role, remove, transfer; the four roles reflected on every screen;
  the audit log screen. See `plan.md` → *Outcome* for what changed from the plan and why.
- **Stage 3 — Admin console** (plan Steps 5–8, T25–T43): staff accounts with a second factor from the
  terminal and their own app, cookie and port; organization search and page; onboarding a paying customer
  (organization, region, owner link, website payment); hosted signup closed by a switch; suspend and resume
  ingest; a complimentary plan until a date, read by the API and the receiver; the owner's support access and
  a read-only, audited *view as*.

## Next action

Founder: review the Stage 3 diff (and the rate-limit change beside it, `docs/changes/rate-limit-per-person/`)
and say **push**. Then approve starting Stage 2 (read API and MCP, plan Steps 9–12).

## Open decisions

- ~~**The e2e suite is over its rate budget.**~~ Resolved 2026-09-20 at its cause — signed-in reads were
  counted per network address, so a team in one office shared one person's budget
  (`docs/changes/rate-limit-per-person/`). `just e2e` from a clean start: 22 passed.

- ~~Q2, Q3~~ — recorded in ADR-016 as built: the admin API has its own port and is never routed from the
  customer host (its hostname is the deployment's to add); self-hosted installs do not run it.

## Blocked

Nothing.

## Risks

See `plan.md` → *New risks*. Found while building Stages 1 and 3:

- **No render test of the billing screen by role.** Its checkout and cancel controls are gated
  (`useCan("commit_spend")`) and the API refuses a non-owner regardless; the test was not written because
  its overview fixture is large. Every other screen with a control has an owner/viewer pair.
- **No e2e of *view as*.** `support.spec.ts` was not written: it needs a staff account with a live TOTP
  inside the e2e run. The path was checked live instead (Validation, Stage 3), and every rule it rests on is
  a live API test.
- **The admin console is not reachable in the production replica.** Its web page is built into the customer
  image, but no proxy sends `/admin/v1` there — deliberately, since the admin host is the deployment's
  (ADR-016). Until then staff use the development stack or the admin API directly.
- **Replacing support access ends a view in progress.** A new grant withdraws the one in force (one grant
  at a time), and a view is tied to its grant. The screen's *Replace access* comment says so.
- **Development ClickHouse is at its memory cap under the full gate.** Three runs of `just check` in a
  row failed on `MEMORY_LIMIT_EXCEEDED`, all from superseded parts the purge suites leave behind
  (`deploy/clickhouse/config.d/memory.xml`). Not caused by this stage; worth a change record before the
  suite grows again.
- **Members invited in development get no real email** unless `just alert-email real` style wiring is added
  for the API: invite mail goes through the API's own sender, which stays on the console provider in
  development. The e2e reads the link from the API log.

## Validation

Stage 3, 2026-09-20:

- API: `test_admin_migration_live.py` (5 — up, down, up; constraints), `test_staff_live.py` (9 — AC26: no
  account without a matching code; one refusal sentence; a code signs in once; disabled; secrets sealed
  apart), `test_admin_app.py` (6 — AC27: neither app honours the other's session or serves its routes; every
  admin route but sign-in refuses without staff), `test_admin_live.py` (7), `test_onboarding_live.py` (6 —
  AC23–AC25), `test_signup_switch.py` (2 — AC31), `test_admin_actions_live.py` (7 — AC28, grants),
  `test_view_as_live.py` (12 — AC29, AC30; the read-only rule, the narrowed permissions and the audit row
  surviving a failed read each seen red first), `test_deletion_live.py` (the four new tables classified).
- Receiver: `allowance/storeprobe_test.go` against the development Postgres — red on the old SQL, green now.
- Web: `admin.test.ts`, `AdminApp.test.ts` (sign-in, search, onboarding, *View as* only for people who have
  joined), `SignupScreen.test.ts`, `SupportAccessScreen.test.ts`, `SupportBanner.test.ts`.
- Live, development stack:
  - Staff (Step 5): a probe account signed in with a real TOTP; the same code replayed was refused; search
    found `acme`; the staff cookie was refused by the customer API. The probe account was deleted.
  - Onboarding (Step 6): a probe staff session onboarded *Test Co* over HTTP (201); the welcome mail was in
    the admin API's log; its link previewed as *Test Co, owner, invited by nexovue*; accepting signed the
    owner in; the link then refused (422). The probe account was deleted; *Test Co* remains in the
    development database.
  - *View as* (Step 8): a 72-hour grant on `acme` and a ticket; the ticket redirected once (303) and then
    refused (401); `/auth/me` carried `support_view` and only `read_audit`; an invite was refused 403
    *Support view is read-only*; the browser showed the banner (*Support view, read-only. Raj at nexovue is
    viewing as Dev Owner. 30 min left.*), a Members screen with no invite form or role controls, and no
    Support access entry; *End now* returned to sign-in; the audit log held `access_granted`, `view_started`
    (with the reason), one `view_request` per request, and `view_ended`. The grant on `acme` runs out on its
    own.
- `just check`, 2026-09-20: Go 34 packages; Python 3,879 passed (2 skipped) — the two tenant-isolation
  tests then passed after `just load` (9 passed), as in Stage 1; web 1,310 + browser SDK 120; `mypy`,
  lint, format and contracts clean. Development ClickHouse reached its 4 GiB guard during three earlier runs
  (every failure `code 241`, in suites this stage does not touch); each was cleared by restarting it once its
  mutations had drained. **The gate now runs at the guard's edge** — the purge suites alone take resident
  memory to the cap on some runs.
- `just e2e` from a clean start (ClickHouse and Redis restarted): **22 passed**.

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

2026-09-20

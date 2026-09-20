# Plan: super-admin-in-the-console

> **Status:** Built 2026-09-20. Every task done; see *Outcome*.
>
> One document: the order of the work and its tasks. The founder approves it before any file is edited.

## Preconditions

- The founder's decision of 2026-09-20, recorded in [change.md](change.md): one console, one sign-in, a
  super-admin role seeded from the environment, with step-up re-authentication at the admin boundary.
- Nexovue is pre-deployment. No staff account exists outside development, so nothing has to be migrated —
  the development one is recreated by the seed.

## Current system

Read from the code, 2026-09-20.

- **Staff accounts.** `data/postgres/models.py:1400` `StaffAccount` — email, name, password hash, sealed
  TOTP secret, `disabled_at`. Created only by `nexovue_admin_api/staff/__main__.py`, which enrols the
  factor before the row exists. `staff/service.py:41` `create_account` refuses without a matching code.
- **Staff sessions.** `staff/sessions.py` — Redis `staff-session:`, cookie `nexovue_staff`, 8 hours,
  host-only, `samesite=strict` (`nexovue_admin_api/router.py:58-66`).
- **The admin app.** `nexovue_admin_api/main.py` `create_admin_app`, prefix `/admin/v1`, OpenAPI off,
  mounted as its own compose service on port 18001 (`deploy/compose/docker-compose.yaml:373`) and 8001 in
  the production replica (`docker-compose.app.yaml:286`).
- **Twelve routes** (`nexovue_admin_api/router.py`): sign-in, sign-out, me, organization search,
  onboard, owner-link, suspend, resume, grant plan, end grant, view-as, one organization.
- **The services underneath are plain functions** — `nexovue_admin_api/service.py` (onboard, suspend,
  resume, grant_plan, end_grant, search, page) and `queries.py` (search_organizations, members_of,
  projects_of, last_ingest_days, pending_owner, payment_is_recorded). They take a session and ids; none
  of them knows about staff sessions or HTTP. **This is what makes the move cheap.**
- **Cross-tenant reads are named exemptions**: `tests/test_postgres_tenancy_structure.py:75`
  `search_organizations`, `:78` `payment_is_recorded`.
- **Roles are a Postgres enum.** `models.py:69` `USER_ROLES = ("owner", "admin", "member", "viewer")`,
  `UserRole = Enum(*USER_ROLES, name="user_role")` — so adding a value is `ALTER TYPE`, not a CHECK edit.
- **The permission table** is `auth/permissions.py`: `Action` and `ALLOWED`, roles within an organization.
  Platform powers are not tenant powers and do not belong in that table.
- **Sessions** are `auth/sessions.py:93` `Session(user_id, org_id, created_at, last_seen_at, client,
  impersonation)`, encoded to JSON in Redis. There is no re-authentication marker today.
- **Second factor already exists for users**: `auth/second_factor.py` `verify` (`:200`), enrolment and
  recovery codes, and `auth/credentials.py` `verify_password`. Step-up needs no new cryptography.
- **The admin console** is `apps/web/src/admin/` — 658 lines of `AdminApp.tsx` plus `api.ts`, built as a
  second Vite entry (`vite.config.ts:71-77`) and shipped inside the customer web image.
- **Support view** (`support/`, `auth/dependencies.py:_hold_support_view`) is owner-granted, read-only,
  30 minutes, audited per request. **Unchanged by this plan.**

### Where the code and the documents will disagree after this

ADR-016 §3 ("its own host, app and cookie") stops being true the moment Step 2 lands. The ADR is
superseded rather than edited — T15.

## Change required

[change.md](change.md). The two shaping decisions — a user in an internal organization, and step-up
re-authentication with a 30-minute window — were made by the founder on 2026-09-20.

## Impact

| Area | What changes |
|---|---|
| Files | `nexovue_admin_api/` → `nexovue_api/admin/` (services and queries unchanged); `staff/` deleted; `auth/sessions.py`, `auth/dependencies.py`, `auth/policy.py`, `auth/router.py`, `main.py`, `seed.py`, `settings.py` |
| Services | `admin-api` compose service deleted from both targets; one API serves everything |
| Schema / migrations | `0035`: `ALTER TYPE user_role ADD VALUE 'super_admin'`; drop `staff_accounts`; `website_payments.recorded_by` and `plan_grants.granted_by` re-point to `users` |
| UI | `src/admin/` → `src/features/admin/` inside the console shell; `admin.html` and the second Vite entry deleted; a step-up screen; nav entry for the super admin only |
| Tests | 5 admin/staff suites rewritten to the new shape; `test_packaging.py` back to one package; authorization coverage gains the admin routes; compose-dependencies loses a service; `test_admin_app.py` (two apps cannot see each other) is deleted and replaced by a step-up suite |

## Reuse

- **Every admin service and query function** moves unchanged. This plan must not re-write onboarding,
  suspension or plan grants.
- **Password and TOTP verification** (`auth/credentials.py`, `auth/second_factor.py`) are the step-up
  check; no new verification code.
- **The console's own shell** — `AppShell`, `Sidebar`, `SettingsLayout`, `Button`, `TextField`,
  `SelectField`, `InlineFailure`, `EmptyState` — replaces the admin console's hand-rolled layout.
- **The audit vocabulary** already has every `organization.*` action; only the actor changes from a staff
  label to a user id.
- **Support view** is untouched.

## Gaps

- No re-authentication marker exists on a session, and nothing in the API asks "how recently did this
  person prove who they are?".
- No route in the customer API is gated on a platform role; `auth/policy.py` has tenant policies only.
- The seed creates a development owner; it creates no super admin, and `settings.py` has no
  `SUPER_ADMIN_EMAIL`.

## Steps and tasks

### Step 1 — A super admin exists, signs in, and is refused everything else

*Judgeable:* set `SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD`, restart, sign in at `/login` with them,
and `GET /api/v1/auth/me` answers with `role: "super_admin"`. Every existing screen still works for every
existing role.

- [x] **T1** — `apps/api/migrations/versions/0035_super_admin.py` · `ALTER TYPE user_role ADD VALUE
  'super_admin'` (outside a transaction, as Postgres requires), and a reserved organization row for the
  platform's own users · proved by `tests/test_super_admin_migration_live.py` (the value exists; an
  existing user keeps its role; the reserved organization is present)
- [x] **T2** — `settings.py` · `super_admin_email`, `super_admin_password` (both optional; absent means no
  super admin is seeded) · proved by `tests/test_settings.py`
- [x] **T3** — `nexovue_api/superadmin/seed.py` (new) · create-or-update the super admin at startup from
  those settings, into the reserved organization, idempotently, never logging the password · proved by
  `tests/test_super_admin_seed_live.py` (created once; a second boot changes nothing; a changed password
  in the environment updates the hash; absent settings create nothing)
- [x] **T4** — `auth/permissions.py` · `super_admin` is not a tenant role: it appears in no `ALLOWED` row,
  and `permissions_of` returns nothing for it · proved by `tests/test_policy.py` (every action × role,
  including the new one)

### Step 2 — The admin API lives in the customer API, behind the role and a fresh re-authentication

*Judgeable:* signed in as the super admin, `GET /api/v1/admin/organizations?q=acme` answers 403 until
`POST /api/v1/auth/admin-mode` is called with the password; then it answers, and thirty-one minutes later
it answers 403 again. Signed in as an owner, it answers 403 always.

- [x] **T5** — `auth/sessions.py` · `Session.admin_until`, encoded and decoded with the rest; a helper
  that says whether a session is in admin mode now · proved by `tests/test_sessions.py`
- [x] **T6** — `auth/router.py` · `POST /auth/admin-mode` — verifies password or TOTP, stamps
  `admin_until = now + 30 min` on the session, audited; `DELETE` ends it early · proved by
  `tests/test_admin_mode_live.py` (wrong password refused and not stamped; a member with the right
  password refused; the marker expires; sign-out clears it)
- [x] **T7** — `auth/policy.py` · `MayAdministerThePlatform` — refuses a role that is not `super_admin`,
  and refuses a super admin whose `admin_until` has lapsed, each with its own sentence · proved by
  `tests/test_admin_mode_live.py`
- [x] **T8** — `nexovue_admin_api/` → `nexovue_api/admin/` · router rewritten to `/admin` under the
  versioned prefix with `MayAdministerThePlatform`; sign-in, sign-out and `me` deleted (the console's own
  serve them); service, queries and schemas move unchanged · proved by the existing
  `tests/test_admin_live.py`, `test_admin_actions_live.py`, `test_onboarding_live.py`, re-pointed
- [x] **T9** — `tests/test_authorization_coverage.py` · every admin route named in `POLICY_OF` with the new
  policy; the staff-gate rule deleted with the staff session · proved by that suite
- [x] **T10** — `tests/test_postgres_tenancy_structure.py` · the two cross-tenant admin queries keep their
  named exemptions, now inside `nexovue_api` · proved by that suite
- [x] **T11** — delete `nexovue_admin_api/staff/`, the `staff_accounts` table (migration `0035`), the
  `admin-api` service in both compose files, the `just staff` recipe, and `test_admin_app.py`; re-point
  `website_payments.recorded_by` and `plan_grants.granted_by` at `users` · proved by
  `tests/test_compose_dependencies.py`, `tests/test_packaging.py` and the migration suite

### Step 3 — The admin area is part of the console, in the production layout

*Judgeable:* sign in as the super admin; the sidebar shows **Platform**; opening it asks to confirm it is
you; after that, onboarding, search, suspend, resume and plan grants all work inside the normal console
chrome. Sign in as an owner: no Platform entry, and `/admin` refuses by address.

- [x] **T12** — `apps/web/src/features/admin/` (from `src/admin/`) · the screens rebuilt on `AppShell`
  and the shared components; `api.ts` points at `/api/v1/admin` · proved by `AdminScreens.test.ts`
  (an owner sees no entry and is refused by address; a super admin sees every control)
- [x] **T13** — `apps/web/src/features/admin/ConfirmItIsYou.tsx` · the step-up screen, shown when the API
  answers "admin mode required", returning the person to what they were doing · proved by its test
- [x] **T14** — `app/routes/route-manifest.ts`, `app/router.tsx`, `shared/identity.ts` · `/admin` routes,
  a nav section visible only to `super_admin`; delete `admin.html`, the second Vite entry, the
  `/admin/v1` dev proxy and `src/admin/` · proved by `route-manifest.test.ts` and `admin.test.ts` deleted
  with the app it judged

### Step 4 — Close out

- [x] **T15** — `docs/decisions/ADR-018-the-platform-console-is-part-of-the-console.md` · supersedes
  ADR-016, states what the origin boundary bought, why it is being given up, what replaces it, and what
  must be true to revisit it (a second person with platform powers) · proved by review
- [x] **T16** — `docs/features/team-and-api-access/{spec,design,status}.md`, `docs/architecture/current-system-reference.md`,
  `docs/product/feature-catalog.md` · amended to describe one app, two principals · proved by review
- [x] **T17** — `just check`, `just e2e` from a clean start, then `/review-diff` · proved by their output

## New risks

- **Deleting `staff_accounts` deletes the only credential that required a second factor.** The super
  admin's 2FA is optional today, because a `users` row's is. Step-up re-authentication accepts a password,
  so a super admin who never enrols TOTP has password-only platform access. Recorded rather than solved:
  the fix is to require 2FA for the role, and it belongs with the second person, not the first.
- **`ALTER TYPE … ADD VALUE` cannot be undone.** The downgrade leaves the enum value in place and only
  drops what it can; a rollback therefore leaves a value nothing uses, which is harmless and must be said
  out loud in the migration.
- **`/admin` on the customer origin is reachable by any XSS in the console.** Step-up is the mitigation
  and it is not a complete one: an attacker who lands script *while* admin mode is live inherits it for
  the remainder of the window. A Content-Security-Policy for the console would be the next control and is
  not in this plan.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | Should the super admin's 2FA be mandatory rather than optional? | No — assumed optional for one user, recorded as a risk | ADR-018 |
| Q2 | Does the reserved organization appear anywhere a customer can see it (counts, billing, the audit log)? Assumed no: it is excluded from every tenant listing | No | ADR-018 |

## Gates

- [x] Specification approval — not applicable, no product behaviour changes for customers
- [x] Design approval — carried by this plan and ADR-018
- [x] **Plan approval — before the first edited file** — founder, 2026-09-20
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Documentation and status update

## Validation plan

- Each step's live suite as it lands, read for counts.
- `just check` and `just e2e` from a clean start at the end, plus a hand check of the judgeable path in
  each step.
- Specifically re-run: `test_authorization_coverage.py` (the admin routes are now judged by the same rule
  as every other mutating route), `test_postgres_tenancy_structure.py`, `test_compose_dependencies.py`.

## Rollout and rollback

Pre-deployment; rollback is `git revert` plus `alembic downgrade`. The development stack needs
`SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD` in `docker-compose.yaml` and a reseed; no other machine
has a staff account.

## Outcome

Built 2026-09-20, in the planned order. What the code does that the plan did not say:

- **ADR-018 is named `ADR-018-the-operator-is-a-user-in-the-console.md`** (T15's placeholder title said
  *the-platform-console-is-part-of-the-console*, which described the move rather than the decision).
- **The admin package is `nexovue_api/admin/`, and `nexovue_admin_api/` is gone entirely** — including the
  `module-name` list in `pyproject.toml` that the earlier split had needed. `tests/test_packaging.py`
  stays: it proves the wheel ships the package, which is the failure that list existed to prevent.
- **T5 found a live defect while being written.** `SessionStore._touch` rebuilt the session by hand and
  dropped `impersonation`, so a read-only support view became a full session 60 seconds in. Fixed with
  `dataclasses.replace`, and `tests/test_session_store_live.py` was seen red first. It is not a defect
  this change introduced; adding a field to `Session` is what exposed it.
- **Q2 is answered in code, not only in the ADR.** `admin/queries.py` excludes the platform organization
  from the operator's search — it is not a customer, and it matches the word an operator is most likely
  to type. Proved by `test_admin_live.py::test_the_search_never_returns_nexovues_own_organization`, seen
  red first.
- **`ConfirmItIsYou` marks neither field `required`.** Either proof opens the area, so a required empty
  field would have had the browser hold the submit back and the button do nothing. The screen test
  submits through `requestSubmit`, which is what caught it.
- **Deleted with the change**, beyond the plan's list: `auth/schemas.py`'s `StaffSignIn`/`StaffSummary`,
  `STAFF_TOTP_PURPOSE`, the `staff_accounts` entry in `organizations/tables.py`, the
  `NEXOVUE_ADMIN_API_ORIGIN` setting, the `/admin/v1` dev proxy, and the `admin.html` and `admin/api.ts`
  allowances in `landmarks.test.ts` and `status-interpretation.test.ts` — both of which were already
  failing against files that no longer existed.

Found and fixed in the self-review (`/review-diff`), each with the test that holds it:

- **A refused step-up was never written down.** `adminmode.enter` recorded the refusal and then raised,
  and the 403 rolled the row back with the request — so somebody guessing at the platform gate left no
  trace. Committed before the refusal now, as a denied sign-in already was (`auth/service.py`), and
  `test_a_refused_attempt_is_written_down_before_the_refusal` was seen red first.
- **The platform's two reads were structurally ungated.** `test_authorization_coverage.py` judges
  mutating routes; `search_organizations` and `organization` change nothing but cross every tenant, so
  they are now in `SECRET_READS` with their policy. Losing a gate on either is now a failing test.
- **A support view replaces the operator's own session.** One origin, one cookie since ADR-018, so
  redeeming the ticket signs the operator out of themselves until the view ends. Not hidden: the screen
  says it, and `support/tickets.py` records why the alternative — a second cookie — would rebuild what
  ADR-018 removed. **The founder's call if it should be built.**
- **Docstrings the merge made false** were corrected rather than left: `middleware/stack.py` (two apps),
  `support/tickets.py` (two origins), `organizations/valve.py`, `AuditLogEntry`, and a dangling
  reference in `superadmin/seed.py` to the deleted `staff/__main__.py`.
- **The reserved organization is created by the seed, not by migration `0035`** as T1 said: it needs the
  same free-slug handling the seed has, and a migration that writes a row the seed then adopts is two
  places that can disagree.
- **Two Redis payloads still keyed an operator's label `"staff"`** while the field had been renamed;
  both now say `"operator"`.

**Validation.** `just check` exit 0 in one run: 4105 Python passed, 3 skipped; 1309 web; 120 browser SDK;
Go and the contract checks clean. `just e2e`: 22 passed. The live path was walked by hand against the dev
stack — sign in as the seeded operator, `/admin/organizations` refused 403 with the lapsed-re-auth
sentence, a wrong password refused and not stamped, `POST /auth/admin-mode` opened it, the search
answered, and `DELETE /auth/admin-mode` closed it again immediately.

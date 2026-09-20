# Status: super-admin-in-the-console

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None — the work is finished and reviewed against `docs/quality/code-review.md`.

## Completed

- The shape was decided with evidence rather than preference: nine documented breaches of staff tooling,
  eight of which reached a tool that was already separate. Summarised in `change.md`.
- Two shaping decisions taken by the founder: a super admin as a `users` row in a reserved organization,
  and step-up re-authentication with a thirty-minute window.
- **Step 1 — the role and the account.** Migration `0035` (the `super_admin` enum value, `staff_accounts`
  dropped, `website_payments.recorded_by` and `plan_grants.granted_by` re-pointed at `users`);
  `superadmin/seed.py`, seeding from `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` at boot; the role holds
  no tenant permission.
- **Step 2 — the API.** `superadmin/adminmode.py` and `Session.admin_until`; `POST` and `DELETE
  /auth/admin-mode`; the admin routes moved to `/api/v1/admin/…` behind `MayAdministerThePlatform`; the
  staff package, app, service, cookie and CLI deleted.
- **Step 3 — the console.** `features/admin/` inside the shell: *Confirm it is you*, the platform search,
  an organization's page, and onboarding; a sidebar section only the operator sees; `admin.html`, the
  second Vite entry, the dev proxy and `src/admin/` deleted.
- **Step 4 — close-out.** ADR-018 written and ADR-016 marked superseded; the F31 spec (BR13, FR21, NFR4),
  design §6, and status amended; the architecture reference's principals section and the catalogue's F31
  row rewritten; this record and `plan.md`'s *Outcome* filled in.

## Next action

Founder: review the diff and say **push**.

## Open decisions

- **Q1 — mandatory 2FA for the super-admin role.** Still open, and still assumed optional while there is
  one such user, who is the founder. It becomes the right default the day a second person holds the role;
  recorded in ADR-018 as the first thing to fix once the platform holds customer data.
- ~~**Q2 — the reserved organization's visibility.**~~ Answered: it is excluded from the operator's
  organization search (`admin/queries.py`), and it is invisible everywhere else by construction — it has
  no project, so no usage row, no allowance and no bill, and no customer-facing listing crosses tenants.

## Blocked

Nothing.

## Risks

- **An XSS in the console reaches platform powers while admin mode is live.** Step-up narrows the window
  to thirty minutes; it does not close it. A Content-Security-Policy for the console is the next control
  and is not in this change.
- **Password-only platform access** is possible until the super admin enrols TOTP, because a user's second
  factor is optional. `staff_accounts` could not exist without one; a `users` row can.
- **`ALTER TYPE … ADD VALUE` cannot be undone.** The downgrade drops what it can and leaves the enum value
  behind; the migration says so in its own docstring.
- **A support view replaces the operator's own session in that browser.** One origin and one cookie
  since ADR-018: redeeming the view-as ticket overwrites `nexovue_session`, and ending the view (a
  logout) signs the operator out entirely — they sign in and step up again. The screen says so rather
  than surprising them. The alternative is a second cookie name for impersonation sessions, which
  rebuilds part of what ADR-018 removed and needs a rule for which cookie wins. **Not fixed: it is the
  founder's call whether it is worth that.** Nothing about it is unsafe — the support session is still
  read-only, time-boxed and audited.
- **Two features were named in the same breath as this change and are not in it**: a support-ticket
  surface, and platform settings with feature flags. Each needs its own specification, and neither should
  be smuggled into a refactor.

## Validation

- `just check`, exit 0 in one run after the review's fixes: **4108 Python passed, 3 skipped**; **1309 web**; **120 browser SDK**;
  Go, format, lint, `mypy` (571 files) and the contract checks clean.
  - Two tenant-isolation tests failed on a first run because the dev stack's default trace window was
    empty — the suite's own message says to send traffic. `just load`, then green. Not a defect.
- `just e2e`: **22 passed**.
- New suites, all live: `test_super_admin_seed_live.py` (5), `test_admin_mode_live.py` (10),
  `test_super_admin_migration_live.py` (6), `test_session_store_live.py` (2),
  `apps/web/src/features/admin/PlatformScreens.test.ts` (4).
- **Seen red before green**: the session-store impersonation test (against the unfixed `_touch`), the
  platform-organization search exclusion (against the unfiltered query), and the refused-step-up audit
  row (against the version that let the 403 roll it back).
- **By hand, against the dev stack**: the operator seeded at boot from compose; sign-in on the ordinary
  page; `/api/v1/admin/organizations` refused 403 with the lapsed-re-auth sentence; a wrong password
  refused with its own sentence and no stamp; `POST /auth/admin-mode` → 200 with an expiry; the search
  answered; `DELETE /auth/admin-mode` → 204 and the surface closed again.

## Review

`/review-diff` against `docs/quality/code-review.md`, on the whole working tree. Findings and their
fixes are listed in `plan.md` → *Outcome*; the one left unfixed is the support-view session above, and
it is recorded as a decision rather than dropped. No unintended file: every path in `git status` is
explained by this record.

## Specification impact

`docs/features/team-and-api-access/spec.md` amended: **BR13** (the operator holds nothing inside an
organization), **FR21** (ordinary sign-in plus a step-up, seeded from the environment) and **NFR4** (the
role checked before any credential, and the stamp on the session record). No customer-visible behaviour
changed.

## Documentation impact

- `docs/decisions/ADR-018-the-operator-is-a-user-in-the-console.md` written; **ADR-016 marked superseded**
  with a pointer, and the decisions README updated.
- `docs/features/team-and-api-access/design.md` §6 and its screen, route, migration and operations lines
  amended; `status.md`'s Stage 3 entry and its Q2/Q3 answers rewritten.
- `docs/architecture/current-system-reference.md`: "three principals" is now two, `staff_accounts` is out
  of the table list, and the ingest-valve paragraph names the console's platform area.
- `docs/product/feature-catalog.md`: F31's row and its schema-hook note.
- `deploy/compose/docker-compose.yaml` and `docker-compose.app.yaml` carry `SUPER_ADMIN_EMAIL` /
  `SUPER_ADMIN_PASSWORD`; the dev values are constants, as every other dev credential there is.

## Last updated

2026-09-20

# Plan: Team, API and admin access (F26 + F31 + F36)

> **Status:** Approved for Stage 1 (founder, 2026-09-19) — In progress
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order; tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited.**

## Preconditions

- Specification approved 2026-09-19 ([spec.md](spec.md)); design approved 2026-09-19 ([design.md](design.md));
  ADR-016 and ADR-017 accepted.
- Delivery order **Stage 1 → Stage 3 → Stage 2** (spec D12). Each stage ends committed only when the founder
  says so.
- The design corrections under *Current system → Where the code disagrees with the design* are accepted with
  this plan; the spec and design are amended in Step 0.

## Current system

Read 2026-09-19. API paths are under `apps/api/src/nexovue_api/`, web paths under `apps/web/src/`.

**Authorization**
- `auth/policy.py` (104 lines): `ROLES_THAT_MAY_ADMINISTER` :44, `…END_THE_ORGANIZATION` :52,
  `…COMMIT_SPEND` :61, `require_role` :64 (module-load check of role names :71-73), refusal text :85-92,
  aliases `MayAdminister` :96, `MayEndTheOrganization` :99-101, `MayCommitSpend` :104.
- 45 gated routes: checks (`checks/router.py:48,69,86,112`), `checkruns/router.py:21`,
  `checkfiles/router.py:32`, `checkimports/router.py:23,34`, alerts rules (`alerts/router.py:38,52,61,75`),
  maintenance (`maintenance/router.py:31,40,57`), `sourcemaps/router.py:59`, channels
  (`channels/router.py:35,56,74,87,100,112`), `channels/test_router.py:27`, check secrets/variables
  (`checksecrets/router.py:40,62,82,99`), projects (`projects/router.py:44,62,76,89,101`), deploy secrets
  (`deploys/router.py:99,114`), integrations (`integrations/router.py:33,46`), billing
  (`billing/router.py:177,247`), organization (`organizations/router.py:28`).
- Keys are gated in the service: `apikeys/service.py:74` `ROLES_THAT_MAY_MANAGE_KEYS`, checks at
  :112,148,204,238; routes `apikeys/router.py:54-120` take plain `CurrentIdentity`.
- `tests/test_authorization_coverage.py`: globs `*/router.py` (:28-29), so **`channels/test_router.py` is
  never scanned**; EXEMPT :39-121; one negative assertion :171-183.
- `GET /auth/me` → `IdentityResponse` (`auth/schemas.py:62-66`: user id, email, name, role, timezone,
  email_verified_at; organization id, slug, name). Web: `useSession()` (`features/auth/api.ts:70-75`); **no
  role check anywhere in the web app**.

**People**
- `User` `data/postgres/models.py:163-218` (password_hash nullable :179, invited_by :206-208,
  invited_at :209, sessions_valid_from :193). FKs to users: `invited_by` SET NULL, `user_recovery_codes`
  CASCADE, `audit_log.actor_user_id` SET NULL, `notification_channels.muted_by` SET NULL,
  `alert_rules.muted_by` SET NULL.
- `auth/queries.py:30-49` `load_identity`; `auth/credentials.py:45-53` refuses a NULL password;
  `auth/service.py:123-140` `start_session` (writes `session.created`); password rule is length only
  (`auth/schemas.py:20-23`).
- `profile/service.py:230-232` `_end_every_session` sets `sessions_valid_from`.
- Signup: `signup/service.py:76-148` `provision_tenant` (password required, never commits);
  `signup/router.py:37-89`; rate tier AUTH (`ratelimit/policy.py:126`).
- Email: `Message` enum `nexovue_platform/email/sender.py:39-54`; `TEMPLATES` `templates.py:25-53`;
  `tests/test_notifications_console.py:136` requires a case per message.
- Audit: `audit/service.py` actions :40-118, `_add` :795-815; **no read function**; index
  `ix_audit_log_org_id_at (org_id, at)` `models.py:1094`.
- Scheduler owners query `apps/scheduler/.../alerts/senders/email.py:21-27` (no removed filter).
- Tenancy test `tests/test_postgres_tenancy_structure.py`: `EXEMPT` names :32-65.
- Latest migration **0031**.

**Read surface** (all `GET`, all `CurrentIdentity`; ClickHouse ones also `TenantRepository`, built from
`org_id` only, `auth/dependencies.py:79-83`)
- services `services/router.py:53,156`; traces `traces/router.py:36,171` (cursor); logs
  `logs/search.py:434` (cursor); metrics `metrics/router.py:46,71,92`; error groups
  `errorgroups/router.py:43,98`; incidents `alerts/router.py:81,97`; checks `checks/router.py:31,62`;
  check results `synthetics/router.py:47,103`; deploys `deploys/router.py:64`.
- `require_project` `projects/service.py:251-258` → `NotFoundError("No such project.")`.
- Rate limiting `middleware/ratelimit.py`: identities :191-211 (IP, then bearer fingerprint), concurrency
  keyed on the IP :90,108-111, headers are IETF `RateLimit`/`RateLimit-Policy` (`ratelimit/headers.py:17-35`),
  **no fallback when Redis is down** (the request fails). `tier_for_path` `ratelimit/policy.py:202-215` is
  path-only. `tests/test_ratelimit_coverage.py` forces tenant-repository routers onto TELEMETRY.
- `main.py:64-90` `create_app`, middleware :93-110, routes :113-153, module-level `app` :156;
  `lifespan.py:128-147`.
- `mcp` is not a dependency. Latest release **2.2.0 (2026-09-07)**: `MCPServer` (renamed from `FastMCP`),
  transport options on `streamable_http_app(stateless_http=True, json_response=True, …)`,
  `session_manager.run()` must be entered in the host lifespan, `TransportSecuritySettings` needed off
  localhost (pypi.org/project/mcp; py.sdk.modelcontextprotocol.io/run/asgi/, /migration/).
- Setup docs: any `docs/product/setup/*.md` with a `# ` heading is published at `/docs/<slug>`
  (`apps/web/build/setup-docs-plugin.ts:107-131`).

**Admin**
- `suspend.py` `close_valve` :42, `open_valve` :64, CLI :116-131; **no test of it**.
- Plan in force: receiver `apps/receiver/internal/allowance/postgres.go:26-55` (LATERALs on `plan_prices`
  and `billing_subscriptions`); Python reads of `organizations.plan` in `usage/service.py:101-103,159-167,215`,
  `billing/service.py:294,305,384-392,453`; the only writer `billing/service.py:252-263`. New organizations
  get `plan = 'free'` by server default (`models.py:140-145`).
- TOTP: `auth/encryption.py:60-101` `CredentialBox` (purpose fixed `"totp"`), `auth/totp.py:95-111`
  `new_secret`, `provisioning_uri`, `code_matches`, `SecondFactorStore` :140 — reusable; `second_factor.py`
  flows are typed to `User`.
- Settings `settings.py:177-455`; `app_base_url` :331; no host setting.
- Compose: dev `api` `deploy/compose/docker-compose.yaml:239-358`; `docker-compose.app.yaml:241-293`;
  **Caddy runs only in `docker-compose.onprem.yaml`** (`deploy/caddy/Caddyfile:104-137`).
- Web build: `apps/web/vite.config.ts` has no `rollupOptions.input` (one entry); nginx
  `deploy/nginx/web.conf` falls back to `/index.html`.
- Usage read reusable: `usage/service.py:65` `usage_for_organization(session, *, organization, …)`.

### Where the code disagrees with the design

| # | Design said | Code says | This plan |
|---|---|---|---|
| C1 | Plan in force is read in "exactly two statements" | Go allowance SQL, plus ~6 Python reads of `organizations.plan` | One Python function `plan_in_force` used by every Python read; the Go statement gains the grant LATERAL (T37, T38) |
| C2 | Surface has "metrics (query)" | No generic query route; catalogue, histogram, duration heatmap | Surface = those three (spec FR17 amended) |
| C3 | `X-RateLimit-*` headers | The API emits IETF `RateLimit` / `RateLimit-Policy` + `Retry-After` | Keep the IETF headers (spec FR19 amended) |
| C4 | "Redis down: the rate limiter's existing fallback applies" | No fallback — the request fails | Design's failure table corrected; tokens behave like sessions |
| C5 | All surface lists page by cursor | Traces and logs page by cursor; the rest are capped lists | Row lists gain a cursor (deploys, check runs, error groups); bounded snapshots (services, metrics catalogue, incidents, checks) state `truncated` (spec FR18 amended) |
| C6 | `CurrentReader` checks `project_id` from the request | `GET /alerts/incidents/{id}` and `GET /checks/{id}` take no `project_id` | Those services check the loaded row's project against the reader's scope (T46) |
| C7 | MCP via `FastMCP(stateless_http=…)` | `mcp` 2.x renamed it `MCPServer`; options moved to `streamable_http_app()` | Pin `mcp>=2.2,<3` (T52) |
| C8 | Admin host routed by Caddy | Hosted cloud compose has no Caddy | Dev: `admin-api` on its own port and the admin build on the dev server; production routing is the deploy's (Q2) |
| C9 | — | `channels/test_router.py` escapes the authorization coverage test | Fixed in T3 |

## Change required

The approved [specification](spec.md) and [design](design.md), with C1–C9 above.

## Impact

| Area | What changes |
|---|---|
| Files | ~45 API modules touched or added; one Go file; ~35 web files; compose; docs |
| Services | `api`; new `admin-api`; `scheduler` (owners query); `receiver` (allowance SQL) |
| Schema / migrations | Postgres 0032 (Stage 1), 0033 (Stage 3), 0034 (Stage 2). No ClickHouse |
| UI | Members, Accept invite, Audit log, Support access, API tokens, closed signup, support banner; every screen hides by permission; admin console (4 screens) |
| Tests | Authorization, tenancy, rate-limit coverage tests extended; live suites per step; web screen tests; E2E per stage |

## Reuse

- `start_session`, `_end_every_session` epoch, `load_identity`, `SessionStore` (with an `impersonation` field).
- `provision_tenant`, `usage_for_organization`, `billing.overview`, `close_valve`/`open_valve`.
- `CredentialBox`, `code_matches`, `new_secret`, `provisioning_uri`, `SecondFactorStore`.
- `signup/apikeys.py` key generation and hashing; `apikeys/service.py` list/revoke patterns.
- Web: `SettingsLayout`, `settings-nav.ts`, `ApiKeysScreen` shown-once `MintedKey` panel, `ResetPasswordScreen`
  token-route pattern, `AuthScreenLayout`, `TextField`, `SelectField`, `CopyButton`, `EmptyState`,
  `InlineFailure`, `SkeletonRows`, render harnesses under `features/*/render-harness.test-util.ts`.
- Existing cursor encoding in `logs/search.py` and `traces/` for the new cursors.

## Gaps

Action table and `Allowed[...]`; invitations and removal; members API and screen; audit read API and screen;
staff accounts, admin app and admin build; onboarding with a region; payment record; plan grants and
`plan_in_force`; support grants and *view as*; signup switch; API tokens, `CurrentReader`, token rate tier,
usage counts; cursors on three lists; `/docs/api`; MCP server.

## Steps and tasks

### Step 0 — Documents match the code

*Judgeable:* the spec and design read true against *Where the code disagrees with the design*.

- [x] **T0** — `docs/features/team-and-api-access/{spec,design}.md` · amend FR17 (C2), FR18 (C5), FR19 (C3),
  design §3/§6 and failure table (C1, C4, C6, C7, C8) · proved by review

## Stage 1 — Team

### Step 1 — One action table, enforced on every route

*Judgeable:* nothing changes for the owner; `GET /auth/me` lists permissions; the coverage test fails if a gate
widens. Run `just check`.

- [x] **T1** — `auth/policy.py` · `Action` enum (FR1 rows), `ALLOWED` table, `Allowed[...]` dependency;
  delete the three role sets and aliases · proved by `tests/test_policy.py` (every action × role, unknown
  role rejected at import)
- [x] **T2** — every gated router listed under *Current system* + `apikeys/router.py` · declare
  `Allowed[Action.…]`; remove `ROLES_THAT_MAY_MANAGE_KEYS` checks from `apikeys/service.py` · proved by T3
- [x] **T3** — `tests/test_authorization_coverage.py` · scan every module under the source (not only
  `router.py`, C9); drop the four key handlers from `EXEMPT`; assert each mutating route's action equals a
  checked-in table · proved by the test itself, seen red with one gate widened
- [x] **T4** — `auth/schemas.py`, `auth/service.py:183-190` · `permissions` on `IdentityResponse` ·
  proved by `tests/test_auth_me.py`
- [x] **T5** — `features/auth/api.ts`, new `shared/permissions.ts` · `permissions` on the session type;
  `useCan(action)` · proved by `shared/permissions.test.ts`

### Step 2 — Invite a teammate, who accepts and signs in

*Judgeable:* Settings → Members → invite `teammate@example.test` as Member; open the link from
`docker logs nexovue-api-1`; set a password; the teammate is in, and the owner sees them *Active*.

- [x] **T6** — `apps/api/migrations/versions/0032_people.py` · `users.set_password_token_hash` (partial
  unique), `set_password_expires_at`, `invite_sent_at`, `removed_at`; index `audit_log (org_id, at DESC, id
  DESC)` · proved by `tests/test_people_migration_live.py` (up, down, up)
- [x] **T7** — `data/postgres/models.py` · the four columns · proved by T6's model/migration parity check
- [x] **T8** — `packages/py-platform/.../email/sender.py`, `templates.py` · `Message.INVITE`,
  `Message.WELCOME_OWNER` · proved by `tests/test_notifications_console.py` new cases
- [x] **T9** — new `members/` (`router.py`, `service.py`, `queries.py`, `schemas.py`) · list, invite, resend,
  withdraw; D1 refusal; 7-day link; `invite_sent_at` · proved by `tests/test_members_live.py`
- [x] **T10** — new `auth/set_password.py` + `auth/router.py` · `GET /invitations/{token}` preview,
  `POST /invitations/accept` (sets password, verifies email, starts session); lookup by hash added to the
  tenancy `EXEMPT` with its reason · proved by `tests/test_invitations_live.py` (once, expired, resent,
  withdrawn — one message for the last three)
- [x] **T11** — `ratelimit/policy.py` · `/invitations/` on AUTH · proved by `tests/test_ratelimit_policy.py`
- [x] **T12** — `audit/service.py` · `member.invited/invite_resent/invite_withdrawn/joined` · proved by T9/T10
- [x] **T13** — web `features/members/` (`MembersScreen.tsx`, `InviteForm.tsx`, `api.ts`),
  `shared/settings/settings-nav.ts`, `app/router.tsx` · Members screen · proved by `MembersScreen.test.ts`
- [x] **T14** — web `features/auth/AcceptInviteScreen.tsx`, `features/auth/search.ts`, `app/router.tsx` ·
  accept route · proved by `AcceptInviteScreen.test.ts` (valid, expired, invalid)
- [x] **T15** — `tools/e2e/tests/members.spec.ts` · invite → accept (link read from the API log) → sign in ·
  proved by running it

### Step 3 — Roles bite: change, remove, last owner, hidden controls

*Judgeable:* make the teammate a Viewer — their open tab loses every edit control on its next request; remove
them — they are signed out; try to demote the only owner — refused.

- [x] **T16** — `members/service.py`, `members/router.py` · `PATCH /members/{id}`, `DELETE /members/{id}`
  (tombstone + epoch), last-owner `FOR UPDATE`, BR3 · proved by `tests/test_members_live.py` (AC5–AC8, AC12)
- [x] **T17** — `auth/queries.py:30-49` · `load_identity` refuses `removed_at` · proved by
  `tests/test_members_live.py` (removed session → 401 with the removal message)
- [x] **T18** — `apps/scheduler/.../alerts/senders/email.py:21-27` · `AND removed_at IS NULL` · proved by
  `apps/scheduler/tests/test_alert_email_recipients_live.py` (second owner included, removed excluded, AC11)
- [x] **T19** — `audit/service.py` · `member.role_changed`, `member.removed` · proved by T16
- [x] **T20** — web: every mutating control listed by the code read (ApiKeysScreen, SiteKeysSection,
  Synthetics/NewCheck/CheckForm/CheckDetail/Import, AlertsScreen/RuleEditor/AlertsStrip,
  ChannelsScreen/ChannelDetail, ProjectsScreen/ProjectSettings and its three sections, BillingScreen) ·
  wrap in `useCan` · proved by one `*.test.ts` per screen asserting a viewer sees no control (AC4)
- [x] **T21** — web `shared/settings/SettingsLayout.tsx`, new `shared/components/Refused.tsx` · rail
  filtered by permission; refused state for a URL opened anyway · proved by `SettingsLayout.test.ts`
- [x] **T22** — `tools/e2e/tests/members.spec.ts` · role change applies mid-session; removal signs out ·
  proved by running it

### Step 4 — Audit log screen

*Judgeable:* Settings → Audit log shows the invite, the role change and the removal, and narrows by person.

- [x] **T23** — `audit/queries.py` (new), `audit/router.py` (new), `audit/schemas.py` · `GET /audit` with
  actor/kind/from/to and `(at,id)` cursor, `Action.READ_AUDIT` · proved by `tests/test_audit_read_live.py`
  (filters, cursor completeness, member refused — AC36, AC38)
- [x] **T24** — web `features/audit/AuditScreen.tsx`, `api.ts`, nav + route · proved by
  `AuditScreen.test.ts`

## Stage 3 — Admin console

### Step 5 — Staff sign in to their own app

*Judgeable:* `just staff create you@nexovue.com` enrols TOTP in the terminal; sign in at
`http://localhost:15173/admin.html`; search finds `acme`.

- [ ] **T25** — `apps/api/migrations/versions/0033_admin.py` · `staff_accounts`, `organizations.region`
  (default `india`, CHECK), `support_access_grants`, `website_payments`, `plan_grants` · proved by
  `tests/test_admin_migration_live.py`
- [ ] **T26** — new `staff/` (`cli.py`, `service.py`, `sessions.py`) · create (enrols TOTP; purpose
  `staff-totp` on `CredentialBox`), disable; staff sessions in Redis `staff-session:`, cookie `nexovue_staff` ·
  proved by `tests/test_staff_live.py` (no account without a confirmed factor — AC26)
- [ ] **T27** — `main.py` · extract `build_app(settings, routes)` from `create_app`; new `admin/main.py`
  (`admin_app`) with admin routes only · proved by `tests/test_admin_app.py` (a customer session refused by
  every admin route, a staff cookie ignored by the customer app — AC27)
- [ ] **T28** — new `admin/router.py`, `admin/service.py`, `admin/queries.py` · sign-in (password → TOTP),
  organization search and page (reusing `usage_for_organization`), each admin statement named in the tenancy
  `EXEMPT` as cross-tenant by design · proved by `tests/test_admin_live.py`
- [ ] **T29** — `deploy/compose/docker-compose.yaml`, `docker-compose.app.yaml`, `justfile` · `admin-api`
  service (port 18001), `just staff …` · proved by `just dev` + `/healthz`
- [ ] **T30** — web `apps/web/admin.html`, `src/admin/` (`main.tsx`, `SignInScreen`, `OrganizationsScreen`,
  `OrganizationScreen`), `vite.config.ts` second input, dev proxy `/admin-api` → `admin-api:8000`,
  `deploy/nginx/web.conf` · proved by `src/admin/*.test.ts` and a structural test that `src/admin` imports
  only `shared/`

### Step 6 — Onboard a paying customer; close hosted signup

*Judgeable:* Onboard *Test Co* with an owner address and a payment id; the owner's *set your password* email
appears in the API log; following it lands them in *Test Co*. With `SIGNUP_ENABLED=false`, `/signup` explains.

- [ ] **T31** — `signup/service.py:76` · `password: str | None`, `region` · proved by
  `tests/test_signup.py` and `tests/test_onboarding_live.py` (same rows as signup, table by table — AC24)
- [ ] **T32** — `admin/service.py` · onboard (provision + set-password link + `WELCOME_OWNER` mail +
  `website_payments` + audit), resend owner link · proved by `tests/test_onboarding_live.py` (AC23, AC25)
- [ ] **T33** — `settings.py`, `signup/router.py`, new `GET /signup/status`, compose app env · signup switch ·
  proved by `tests/test_signup_switch.py` (AC31)
- [ ] **T34** — web `features/auth/SignupScreen.tsx`, admin `OnboardScreen.tsx` · proved by their tests

### Step 7 — Suspend, resume and comp from the console

*Judgeable:* suspend `acme` with a reason — the receiver refuses its data; resume; comp *pro* until next month —
the Usage screen shows the pro allowance.

- [ ] **T35** — `suspend.py` · extract `close_valve`/`open_valve` into `organizations/valve.py` shared by CLI and
  admin · proved by new `tests/test_valve_live.py` (first tests of the valve) and T36
- [ ] **T36** — `admin/router.py` · suspend/resume with reason, audit · proved by `tests/test_admin_live.py`
  (AC28)
- [ ] **T37** — `usage/queries.py` new `plan_in_force(session, *, org_id, on_day)`; replace every Python read
  listed in C1 · proved by `tests/test_plan_in_force_live.py` (grant active, ended, none)
- [ ] **T38** — `apps/receiver/internal/allowance/postgres.go:26-55` · active-grant LATERAL · proved by
  `allowance_test.go` new cases (grant widens allowance; ended grant does not)
- [ ] **T39** — `admin/router.py` · grant / end comp, audit · proved by `tests/test_admin_live.py`

### Step 8 — Support access and *view as*

*Judgeable:* as the `acme` owner grant 72 h; in the admin console *View as* the owner with a reason; a new tab
shows the console with the banner and no edit controls; any write is refused; the owner's Audit log shows every
page viewed.

- [ ] **T40** — new `support/` (`router.py`, `service.py`) · grant/withdraw (owner, `Action.GRANT_SUPPORT`) ·
  proved by `tests/test_support_access_live.py`
- [ ] **T41** — `admin/service.py`, `auth/sessions.py` (`impersonation` on `Session`), new
  `GET /support/view-as` · 60 s single-use ticket → session · proved by `tests/test_view_as_live.py`
- [ ] **T42** — `auth/dependencies.py:44-73` · on an impersonation session: re-check the grant, refuse
  non-GET/HEAD/OPTIONS; `middleware/support_audit.py` writes `support.view_request` rows · proved by
  `tests/test_view_as_live.py` (AC29, AC30: every mutating route in the app refused)
- [ ] **T43** — web `app/layout/SupportBanner.tsx`, `features/support/SupportAccessScreen.tsx`, audit screen
  marks view-as rows; admin *View as* button · proved by their tests and `tools/e2e/tests/support.spec.ts`

## Stage 2 — Read API and MCP

### Step 9 — An API token reads services and incidents

*Judgeable:* create a token for *production*; `curl -H "Authorization: Bearer nvt_live_…"
localhost:18000/api/v1/services?project_id=…` answers; the same token on another project answers 404; a
`POST` answers 403; revoke — 401.

- [ ] **T44** — `apps/api/migrations/versions/0034_api_tokens.py`, models · `api_tokens`,
  `api_token_usage_daily` · proved by `tests/test_api_tokens_migration_live.py`
- [ ] **T45** — new `apitokens/` (`router.py`, `service.py`, `queries.py`, `schemas.py`) · create (shown once),
  list, revoke, `Action.MANAGE_API_TOKENS`; hash lookup in tenancy `EXEMPT` · proved by
  `tests/test_api_tokens_live.py` (AC13, AC16, AC17)
- [ ] **T46** — `deps.py:172-187`, `auth/dependencies.py` · `nvt_live_` branch; `CurrentReader`;
  `CurrentIdentity` refuses a token 403; ingest keys refused; scope check for query-param routes and in
  `alerts/service.get_incident` and `checks/service.get_check` (C6) · proved by
  `tests/test_read_surface_live.py` (AC14, AC15, AC21)
- [ ] **T47** — the surface routes (services, traces, logs, metrics ×3, error groups, incidents, checks,
  check results/runs, deploys list) · `CurrentReader`; new `tests/test_read_surface_structure.py` enumerates
  `CurrentReader` routes against a checked-in list · proved by that test
- [ ] **T48** — `ratelimit/policy.py`, `middleware/ratelimit.py` · `TOKEN` tier (300/min, 8 concurrent) chosen by
  credential, concurrency keyed on the token; Redis `token-use`/`token-seen` and the minute fold into
  `api_token_usage_daily` · proved by `tests/test_ratelimit_policy.py`, `tests/test_api_tokens_live.py`
  (AC18, AC19)
- [ ] **T49** — web `features/settings/ApiTokensScreen.tsx`, `api.ts`, nav + route · proved by
  `ApiTokensScreen.test.ts`

### Step 10 — Pages, and the documentation a customer follows

*Judgeable:* the `/docs/api` page's CI example, pasted into a terminal against the dev stack, prints the error
rate and exits non-zero when it is over the threshold.

- [ ] **T50** — `deploys/`, `synthetics/` (runs), `errorgroups/` · `(at,id)` cursor and `next_cursor`; bounded
  lists gain `truncated` · proved by each area's live test (AC20)
- [ ] **T51** — `docs/product/setup/api.md` (+ link from `index.md`) · tokens, surface, pagination, limits,
  errors, CI example · proved by `tools/e2e/tests/api-docs.spec.ts` running the example (AC22)

### Step 11 — An AI assistant reads nexovue

*Judgeable:* `claude mcp add --transport http nexovue http://localhost:18000/api/mcp/ --header
"Authorization: Bearer nvt_live_…"`, then ask "what is failing in production?" — it answers from the dev stack.

- [ ] **T52** — `apps/api/pyproject.toml` · `mcp>=2.2,<3`; `uv lock` · proved by `just check`
- [ ] **T53** — new `mcp/` (`server.py`, `tools.py`, `bounds.py`, `auth.py`) · `MCPServer`, stateless
  `streamable_http_app(json_response=True)`, `TransportSecuritySettings` from settings, token wrapper; tools call
  the surface services; 100 rows / 32 KiB bounds · proved by `tests/test_mcp_tools.py` (every tool a read —
  AC34; bounds — AC35) and `tests/test_mcp_live.py` (scope parity with the endpoints — AC33)
- [ ] **T54** — `main.py`, `lifespan.py`, `ratelimit/policy.py` · mount `/api/mcp`, enter
  `session_manager.run()`, TOKEN tier · proved by `tests/test_mcp_live.py`
- [ ] **T55** — `docs/product/setup/api.md` MCP section; web *Connect an AI assistant* panel · proved by
  `ApiTokensScreen.test.ts`; AC32 run by hand with Claude Code and recorded in `status.md`

### Step 12 — Close out

- [ ] **T56** — `docs/product/feature-catalog.md` (F26, F31, F36 rows and sections),
  `docs/architecture/current-system-reference.md` (sessions index, receiver join, gates, tokens, admin app),
  `docs/features/account-access/spec.md` pointer · proved by review
- [ ] **T57** — `docs/operations/production-readiness.md` · assessment for the three stages · proved by
  `/readiness-check`

## New risks

- **Widening member gates changes who may do what.** Today only owners exist, so no one is affected; the
  checked-in action table in T3 is what keeps a later change deliberate.
- **The coverage test scanning every module (T3) may surface other ungated mutating routes** besides
  `channels/test_router.py`. Each will be reported, not silently gated.
- **`mcp` 2.x is ten days old.** Pinned below 3; its behaviour under the API's middleware (rate limit,
  exception boundary) is proved by T54, not assumed.
- **The admin build ships inside the customer web image** (a second entry). It is inert without the admin API,
  but its JavaScript is public. It contains no secret and no cross-tenant data until a staff session loads it.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | Accept the design corrections C1–C9 | Yes — approving this plan answers it | spec.md / design.md (T0) |
| Q2 | Production routing of the admin host for the hosted cloud (no Caddy there today) | No — dev works on its own port; production is the Terraform/deploy work of ADR-006 | ADR-016 consequence note |
| Q3 | Should self-hosted installs run `admin-api`? Assumed **no** — it is for nexovue staff | No | ADR-016 |

## Gates

- [x] Specification approval
- [x] Design approval
- [x] **Plan approval — before the first edited file** (Stage 1, 2026-09-19)
- [x] Implementation — Stage 1
- [x] Tests — Stage 1
- [ ] Review
- [ ] Design QA — the built screens against the approved design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- Per step: the named tests; `just check` once at each stage end, reading counts.
- Per stage: its E2E (`members.spec.ts`; `support.spec.ts`; `api-docs.spec.ts`) and the stage's *Judgeable* line
  done by hand on the dev stack.
- Stage 2: AC32 with Claude Code by hand.
- `/review-diff` before each stage is offered for commit.

## Rollout and rollback

Pre-deployment. The real risk is size: three stages of work reviewed at the end would be unreviewable. Each
stage is therefore offered for review and commit on its own, and Stage 1 is useful alone. Rollback is per
migration (`downgrade`) and per commit.

## Outcome

### Stage 1 — Team (built 2026-09-19)

Built as planned, with these changes, each for a reason found while building:

- **Named policies, not `Allowed[Action.X]`.** Each action has a named dependency (`MayOperateChecks`,
  `MayManageMembers`, …) — the code's existing style, and one the type checker can verify. The table itself
  moved to `auth/permissions.py` (no web-framework import) so the identity response can read it without an
  import cycle; `auth/policy.py` holds only the gates.
- **The coverage test pins every route to its policy**, not only "has one": `POLICY_OF` names each handler's
  exact policy, and it now scans every module, which is how `channels/test_router.py` (Send test) came under
  it. `GET /deploys/webhook-secrets` returns secrets, so it is gated too (`SECRET_READS`) — with viewers now
  possible, an ungated read of a secret became a hole.
- **`audit_log.detail` (JSONB) added in 0032.** The audit screen must show *Member → Viewer*; the log had
  only an action and a target.
- **The accept screen lives in `features/auth`** beside the other link screens (a feature may not import
  another), and preview and accept take the token in a POST body so it never appears in a logged URL.
  `/invite` joined the query client's signed-out screens: a 401 there is an answer, not a lapsed session.
- **`useCan` lives in `shared/identity.ts`** with the session query it reads (moved down from
  `features/auth`), not a separate `shared/permissions.ts`.
- **`Permitted` (new shared component)** refuses a whole screen opened by address — the new-check, import
  and rule-editor screens — without breaking the rules of hooks.
- **Test harnesses answer `/auth/me` as an owner by default** (`shared/identity.test-util.ts`); role tests
  pair every "viewer sees no X" with "owner sees X", which caught two wrong labels in the tests themselves.
- **Not done:** a render test of the billing screen by role (the gate is in place; its overview is a large
  fixture to stub). Recorded in `status.md`.


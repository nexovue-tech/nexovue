# Design: Team, API and admin access (F26 + F31 + F36)

## Status

**Approved** by the founder, 2026-09-19. Written against the approved [specification](spec.md) and read from the
code; two decisions are recorded as ADRs ([ADR-016](../../decisions/ADR-016-staff-work-on-their-own-host.md),
[ADR-017](../../decisions/ADR-017-a-read-token-is-its-own-credential.md)).

## Context and goal

Three callers, one question — *may this caller do this, here?* Today the only caller is a signed-in
person, and every one of them is an owner. This design adds three more — a person with any of four roles,
an API token, and nexovue support viewing as a person — and answers the question for all of them in
one place, `auth/policy.py`, on every request.

What is already there and is reused, not rebuilt:

| Built | Where | Used for |
|---|---|---|
| Session per request, re-reading the user row | `auth/dependencies.py:44-73` | Roles apply on the next request (BR4) with no cache to invalidate |
| `users.sessions_valid_from` epoch | same, `_issued_before_reset` | Removal ends every session without enumerating Redis |
| Role enum `owner/admin/member/viewer` | `models.py:62-63`, migration 0001 | FR1 — no enum change |
| `require_role` + coverage test | `auth/policy.py:64-104`, `test_authorization_coverage.py` | Becomes one action table (FR1, FR2) |
| `users.password_hash NULL`, `invited_by`, `invited_at` | `models.py:178-209` | An invited person, and an onboarded owner |
| `Authorization: Bearer` read before the cookie | `auth/sessions.py:269-276` | The token path branches here |
| Per-token-fingerprint rate limiting | `middleware/ratelimit.py:191-211` | Tokens are counted per token (FR19) |
| `audit_log.actor_label`, `impersonation_context` | migration 0004 | Staff actions and *view as* (BR13, FR29) |
| `provision_tenant` | `signup/service.py:76` | Onboarding creates the same rows as signup (AC24) |
| Ingest valve `ingest_suspended_at` + CLI | `suspend.py` | The console writes the same columns (FR26) |
| Owners channel resolved at send time | `scheduler/.../senders/email.py:21-45` | A second owner is picked up (FR12) |

## User experience

**A team lead brings a teammate in.** Settings → *Members* → *Invite*: an address and a role. The
teammate gets an email, opens it, types a name and a password, and is in — on the Overview, with the
controls their role allows and no others. The lead sees them move from *Invited* to *Active*.

**Someone leaves.** Settings → *Members* → the row → *Remove*, confirmed by typing their name. Their
open tab signs out on its next request. Their checks and rules keep running.

**CI gates a deploy.** Settings → *API tokens* → *Create*: a name, *staging* or all projects, 90 days.
The secret is shown once beside a copyable `curl` that reads the service's error rate. The token list
shows it used a minute later.

**An engineer asks their assistant.** The same screen's *Connect an AI assistant* panel gives the MCP
URL and a one-line `claude mcp add …` with the token filled in.

**An owner checks what happened.** Settings → *Audit log*: newest first; narrow by person, kind or date.
A support session reads *nexovue support (Raj) viewing as Priya — "checkout shows no traces" — 14:02–14:19*.

**nexovue onboards a paying customer.** On the admin host, after signing in with a second factor:
*Onboard* → organization name, owner email, region (India) and the website payment id. The owner gets a
*Set your password* email; the organization page shows *Owner invited*, then *Active*.

**nexovue helps a customer.** The owner, asked on email, grants support access for 72 hours in Settings →
*Support access*. Staff open the organization, choose *View as Priya*, write why; a new tab opens the
customer console as Priya with a banner — *Support view, read-only, 29 min left — end now* — and every
button that would change something is gone.

## UI structure

Every screen below owns its own loading, empty, error and refused states (`docs/design/principles.md`
rule 3). *Refused* is new: a screen a role cannot use is absent from the rail, and a URL opened anyway
renders "Your role (Viewer) cannot open this. Ask an owner." rather than a failed request.

**Customer console (`apps/web`)**

| Screen | Route | Who | What it shows / does not show |
|---|---|---|---|
| Members | `/settings/members` | all read; owner/admin act | People and pending invites (name, email, role, status, invited by, joined, last active); invite form; per-row role select, remove, resend, withdraw, make owner. Never shows a role above the viewer's own as a choice (BR3) |
| Accept invite | `/invite?token=` | signed out | Organization name and inviter; name + password (signup's password rules). Expired/invalid states per the spec's error table |
| API tokens | `/settings/api-tokens` | owner/admin | Token list (name, scope, created by, last used, requests today, expires, status), create dialog showing the secret once, revoke; *Connect an AI assistant* panel. Does not show a secret after creation |
| Audit log | `/settings/audit` | owner/admin | Filters (person, kind, from–to); rows (when, who, what, target, change); *view as* rows marked and expandable to the session |
| Support access | `/settings/support` | owner | Grant (24 h / 72 h / 7 days) or withdraw; the active grant and every *view as* in it |
| Signup, closed | `/signup` | signed out | "nexovue cloud is set up after purchase", link to the website; shown when `GET /signup/status` says closed |
| Support banner | every screen | — | Fixed top strip during *view as*: staff name, person viewed, minutes left, *End* |

Every existing screen hides mutating controls through one hook, `useCan(action)`, fed by the
`permissions` list `GET /auth/me` returns (FR3). The settings rail (`shared/settings/settings-nav.ts`)
filters its entries by the same list.

**Admin console** — a second build entry of `apps/web` (`admin.html`, `src/admin/`), importing only
`shared/` (ADR-016):

| Screen | What |
|---|---|
| Sign in | Email, password, then the TOTP code — no path without the code |
| Organizations | Search box (name, slug, id, member email); results with plan, region, last ingest, suspended |
| Organization | Members, projects, plan and comp, usage against allowance, last ingest per project, suspension (with suspend/resume + reason), website payment, support grant, *View as* per person, recent audit |
| Onboard | Organization name, owner email and name, region, website payment id and amount |

## Proposed design

### 1. One action table (FR1, FR2, FR3)

`auth/policy.py` replaces the three role sets with an `Action` enum — one member per row of FR1 — and a
table `ALLOWED: dict[Action, frozenset[Role]]`. A route declares what it does, not who may do it:

```python
@router.post("/checks")
async def create_check(principal: Allowed[Action.OPERATE_CHECKS], ...)
```

`Allowed[...]` is a dependency factory over `CurrentIdentity` that refuses with `PermissionDeniedError`
naming the roles that may. `MayAdminister`, `MayCommitSpend` and `MayEndTheOrganization` are deleted.
The key and site-key routes, gated inside `apikeys/service.py:74,112-238` today, move to route gates and
leave the coverage test's `EXEMPT` list (FR2). The coverage test grows a second assertion: the action a
route declares matches a table checked into the test, so a gate that silently widens fails the build
(AC3).

`GET /auth/me` adds `permissions: [action…]` and, during *view as*, `support_view: {staff, until}`.

### 2. People: invite, accept, change, remove, transfer

**Invite (FR4, FR6).** `POST /members/invitations {email, role}` creates a `users` row in the caller's
organization with `password_hash NULL`, `invited_by`, `invited_at`, and a **set-password link**:
32 random bytes, of which the row stores only `set_password_token_hash` (SHA-256) and
`set_password_expires_at` (7 days). The link lives in Postgres, not in the Redis one-time store, because
a pending invite must be listed with its expiry and a resend must kill the previous link — the Redis
store can do neither (`auth/tokens.py:139-148`: tokens there are "bounded by their TTL, not by
enumeration"). Resend overwrites the hash; withdraw deletes the row (nothing references a never-accepted
user). `invite_sent_at` records whether the email went; `NULL` renders *not sent*.

**Accept (FR5).** `POST /invitations/accept {token, name, password}` finds the row by hash, checks
expiry, sets the password, clears the link, stamps `email_verified_at`, and starts a session. The same
endpoint serves an onboarded owner (FR7): the link is one primitive.

**Change role (FR9, BR3).** `PATCH /members/{user_id} {role}`. The service refuses a role above the
caller's, and any change to or from `owner` by a non-owner.

**Remove (FR9, FR11, BR5, BR6).** `DELETE /members/{user_id}` does not delete the row — `audit_log`,
`alert_rules.muted_by`, `notification_channels.muted_by` and `users.invited_by` reference it with
`SET NULL`, which would erase who did what. It sets `removed_at`, sets `sessions_valid_from = now()` (the
existing epoch ends every session on its next request, AC6), rewrites `email` to
`removed+<user_id>@removed.invalid` so the address is free (FR11), and keeps `name`. `load_identity`
refuses a removed user. Nothing the person created is touched.

**Last owner (BR2, FR10).** Every change that could remove an owner — demote, remove — runs
`SELECT … FROM users WHERE org_id = :org AND role = 'owner' AND removed_at IS NULL FOR UPDATE` first
and refuses if the target is the last. The row lock makes two owners demoting each other at once
serialize instead of both succeeding. *Transfer* is *make owner* followed by the previous owner's own
*step down*; no special operation.

**One organization per person (BR1).** An invite to an address that exists in another organization is
refused with the D1 message; `users.email` is already unique (`models.py:177`).

### 3. API tokens (FR13–FR19, ADR-017)

New table `api_tokens`: `id, org_id, project_id NULL (all projects), name, role ('read'),
token_prefix (12, unique), token_hash, created_by, created_at, expires_at NOT NULL, revoked_at,
last_used_at`. Format `nvt_live_` + 32 base62 characters (190 bits), SHA-256 hashed like ingest keys
(`signup/apikeys.py:28-30`). A token belongs to the organization; `created_by` is a label for the list,
not a dependency (BR9, AC17).

**Authentication.** `get_current_session` already reads the Bearer header first. A value starting
`nvt_live_` takes a new branch: look up by hash, refuse revoked (401 *revoked*), expired (401 *expired
on …*), or a deleted project, and yield a **reader** principal: `org_id`, `project_scope`, `role`, and
`token_id` — no `user`. Anything that is not an `nvt_` value continues to the session path unchanged; an
`nvk_`/`nvp_` value gets 401 "ingest keys cannot read" (BR8).

**The read surface (FR17, D8).** A route is on the surface **if and only if** it depends on
`CurrentReader` instead of `CurrentIdentity`. `CurrentReader` accepts a person *or* a token; a token
reaching any route that asks for `CurrentIdentity` is refused 403 *"API tokens are read-only and reach
only the documented read API"*. So the surface is exactly the set of routes wired to `CurrentReader`,
which a structural test enumerates against a checked-in list — adding a route to the surface is a
visible, reviewed change, and the list is what the documentation publishes. Proposed first list, each an
existing `GET`: services (list, one), traces (search, one), logs (search), metrics (catalogue, query),
error groups (list, one), incidents (list, one), checks (list, one, results), deploys (list).

**Project scope (BR11, AC15).** `CurrentReader` compares a request's `project_id` to the token's scope
and raises the same `NotFoundError` that `projects.require_project` raises for a project that does not
exist, so the two responses are byte-identical. `GET /alerts/incidents/{id}` and `GET /checks/{id}` take
no `project_id`; their services compare the loaded row's project to the scope instead.

**Pagination (FR18).** Surface list endpoints that page today by offset or not at all gain an opaque
cursor (`base64(json({"at":…, "id":…}))`) and a `next_cursor` field; which ones need it is a plan task
read from each query.

**Rate limit (FR19).** A `TOKEN` tier keyed by the token's fingerprint — the middleware already keys by
bearer fingerprint — at **300 requests a minute and 8 concurrent**, with concurrency keyed on the token (the
middleware keys it on the IP today), emitting the IETF `RateLimit`/`RateLimit-Policy` headers the API
already sends and `Retry-After` on 429. The tier is chosen by credential, not path — `tier_for_path` is
path-only today and gains the credential as an input. The telemetry tier's per-IP
limit (60/min) is not applied to tokens: a nightly pull from one CI runner is not a browser.

**Last used and daily counts (AC18).** Each token request does `INCR token-use:<id>:<day>` and
`SET token-seen:<id> NX EX 60` in Redis; when the `SET` wins (at most once a minute per token) the
request also writes `last_used_at` and folds the Redis counter into
`api_token_usage_daily(token_id, day, requests)`. One Postgres write per token per minute, not per
request.

### 4. MCP server (FR31–FR35, D13)

Mounted in the API process at `/api/mcp` with the MCP Python SDK's Streamable HTTP app, **stateless**
and JSON-response, so any API replica serves any request and there is no session store to add. Its
lifespan joins the API's (the SDK requires it; `nexovue_api/lifespan.py`). `mcp` 2.x (2.2.0, 2026-09-07):
`MCPServer` with `streamable_http_app(stateless_http=True, json_response=True)`, `session_manager.run()`
entered in the API lifespan, and `TransportSecuritySettings` naming the public host. Pinned `>=2.2,<3`.

**Auth.** A thin ASGI wrapper on the mount runs the same token lookup as §3 and places the reader in a
context variable; no token, or a bad one, is a 401 with a sentence an assistant can repeat. Rate limiting
is the existing middleware: `/api/mcp` is in the `TOKEN` tier.

**Tools** call the same service functions the surface routes call, with the reader's `org_id` and scope —
not HTTP loopback — so a tool cannot see what its endpoint cannot (AC33). Each tool: a model-facing
description, a time window defaulting to the last hour, **at most 100 rows and 32 KiB** of text per
result; past either bound the result ends with *"Cut at 100 of 4,210 rows — narrow by service or
time."* (FR34). Customer text (log bodies, attributes, messages) is returned inside a clearly delimited
field; there is no tool that writes (FR35, AC34).

### 5. Audit log screen (FR36–FR38)

`GET /audit?actor=&kind=&from=&to=&cursor=` behind `Action.READ_AUDIT` (owner, admin). `kind` maps to
action prefixes (`user.`/`member.` → people; `api_key.`/`api_token.`/`site_key.`/`deploy.webhook_secret`
→ credentials; `session.` → sign-in; `check.`/`alert_rule.`/`notification_channel.`/`project.`/
`maintenance_window.`/`integration.` → configuration; `support.` → support). Cursor on `(at, id)`,
newest first, 50 a page, over a new index `audit_log (org_id, at DESC, id DESC)`. Rows join `users` for
names — kept after removal (§2) — and render `actor_label` for staff.

The audit vocabulary already covers configuration (`audit/service.py:40-118`: checks, rules, channels,
projects, windows, integrations, keys). New actions: `member.invited`, `member.invite_resent`,
`member.invite_withdrawn`, `member.joined`, `member.role_changed`, `member.removed`,
`api_token.created`, `api_token.revoked`, `organization.onboarded`, `organization.payment_recorded`,
`organization.plan_granted`, `organization.plan_grant_ended`, `organization.ingest_suspended`,
`organization.ingest_resumed`, `support.access_granted`, `support.access_withdrawn`,
`support.view_started`, `support.view_ended`, `support.view_request`. `audit_log` has no retention job;
rows are kept (FR38).

### 6. Admin console (FR21–FR30, ADR-016)

**Staff are a table, not users (BR13).** `staff_accounts(id, email, name, password_hash, totp_secret
sealed, created_at, disabled_at)`. A staff account is created by
`python -m nexovue_api.staff create --email …`, which enrols TOTP in the terminal (prints the
`otpauth://` URI, requires a code back) — there is no row without a confirmed factor (AC26, D9).

**Its own host, app and cookie (NFR4).** `nexovue_api.admin.main:app` is a second FastAPI app in the
same package — reusing services, queries and the database session — served as a compose service
`admin-api` on its own port. In development it is reached directly and the admin build is served by the
web dev server; the hosted cloud has no Caddy today, so routing its own host in production is the deploy
work of ADR-006 (plan Q2). Self-hosted installs do not run it (plan Q3).
Staff sessions live in Redis under `staff-session:`, cookie `nexovue_staff`, host-only, 8 hours. The
customer app never reads `nexovue_staff`; the admin app never reads `nexovue_session` — a session for one
is not even parsed by the other (AC27). Every admin route requires a staff session; the admin app has no
unauthenticated route but sign-in.

**Onboarding (FR22–FR24).** `POST /admin/organizations` calls `provision_tenant` with `password=None`
(its signature gains that: an owner who will set their own) plus `region`, then issues the set-password
link (§2) with a new email message, *welcome owner*. `organizations.region TEXT NOT NULL DEFAULT 'india'
CHECK (region IN ('india','india-south'))` (ADR-009). `website_payments(id, org_id, razorpay_payment_id
UNIQUE, amount_minor, currency, recorded_by_staff, recorded_at)` — one per organization at onboarding;
after it, `billing/` owns renewals (ADR-008 step 8).

**Suspend / resume (FR26)** write `ingest_suspended_at`/`_reason` exactly as `suspend.py` does, through
one shared function both call; the CLI stays.

**Comps (FR27).** `plan_grants(id, org_id, plan_slug, services, starts_at, ends_at, reason,
granted_by_staff, ended_at)`. *The plan in force* is an active grant if there is one, else the paid plan.
The receiver reads it in one statement (`apps/receiver/internal/allowance/postgres.go:26-55`), which
gains a `LEFT JOIN LATERAL` on active grants; the API reads `organizations.plan` in about six places
(`usage/service.py`, `billing/service.py`), which all move onto one function, `plan_in_force`. No job flips plans back; a grant simply stops being active at `ends_at`.

**Support access and *view as* (FR28, FR29, BR14, BR15).** `support_access_grants(id, org_id,
granted_by, granted_at, expires_at, withdrawn_at)`; `POST/DELETE /support-access` in the customer API
(owner only).

*View as* crosses from the admin host to the customer host without sharing a cookie:

1. Staff `POST /admin/organizations/{id}/view-as {user_id, reason}`. The admin API checks an active
   grant, writes `support.view_started`, and stores a **ticket** in Redis (`view-as-ticket:<hash>`,
   60 s, single use) holding `{user_id, org_id, staff_id, staff_label, reason, grant_id,
   impersonation_id, until: now+30 min}`.
2. The admin UI opens `https://{app host}/support/view-as?ticket=…` in a new tab.
3. The customer API spends the ticket and creates an ordinary session record for the viewed user, with an
   `impersonation` field and a Redis TTL of the remaining minutes, and sets the customer cookie.
4. `get_current_identity`, on a session with `impersonation`: re-checks the grant (withdrawn or expired →
   401 *support access ended*), puts `impersonation` on `Identity`, and **refuses every method but
   GET/HEAD/OPTIONS** with 403 *"Support view is read-only"* — enforced here, below every route, so no
   route can forget (BR15, AC29). A middleware writes one `support.view_request` audit row per request
   in the session, with `impersonation_context`, staff label and path (AC30).
5. *End* or expiry deletes the session and writes `support.view_ended`.

**Signup switch (FR30, D10).** `Settings.signup_enabled` (env `SIGNUP_ENABLED`, default `true`);
`docker-compose.app.yaml` for the hosted cloud sets `false`. `POST /signup` refuses when off;
`GET /signup/status` lets the page say why.

## Data flow and interfaces

**New customer API** (all under `/api/v1`):

| Route | Gate |
|---|---|
| `GET /members`, `POST /members/invitations`, `POST /members/invitations/{id}/resend`, `DELETE /members/invitations/{id}`, `PATCH /members/{id}`, `DELETE /members/{id}` | read: any; write: `MANAGE_MEMBERS` (+ BR3 in service) |
| `POST /invitations/accept`, `GET /invitations/{token}` (preview: org, inviter, expiry) | unauthenticated, `AUTH` rate tier |
| `GET/POST /api-tokens`, `POST /api-tokens/{id}/revoke` | `MANAGE_API_TOKENS` |
| `GET /audit` | `READ_AUDIT` |
| `GET/POST/DELETE /support-access` | `GRANT_SUPPORT` (owner) |
| `GET /support/view-as` (ticket redeem) | ticket only |
| `GET /signup/status` | unauthenticated |
| `/api/mcp` | API token |

**New admin API** (`admin-api`, `/admin/v1`): sign-in (password, then TOTP), sign-out; organization
search and page; onboard; resend owner link; record payment; suspend/resume; grant/end comp; start/end
*view as*.

**Email:** two new `Message` members and templates — `INVITE` (inviter, organization, link, expiry) and
`WELCOME_OWNER` (organization, link, expiry).

## Data and migration impact

One Postgres migration per stage, each stating its reasoning in the file (`docs/templates/migration.md`):

- **Stage 1:** `users` + `set_password_token_hash`, `set_password_expires_at`, `invite_sent_at`,
  `removed_at`; partial unique index on `set_password_token_hash`; `audit_log (org_id, at DESC, id DESC)`.
- **Stage 3:** `organizations.region`; `staff_accounts`; `support_access_grants`; `website_payments`;
  `plan_grants`.
- **Stage 2:** `api_tokens`; `api_token_usage_daily`.

No backfill: every existing organization is `india`, every existing user is active and has a password.
Nexovue is pre-deployment, so each migration is the correct end state, not a compatible one.
No ClickHouse change.

## Performance and scale

- Per request: unchanged for people (one Postgres read already loads the user). A token request is one
  indexed lookup by hash instead of a Redis session read, plus two Redis commands; one Postgres write per
  token per minute.
- The action table is a dict lookup.
- MCP tool calls cost what their endpoint costs; the result bounds cap what is serialized.
- The audit screen pages 50 rows over its index.

## Failure handling

| Failure | Behavior |
|---|---|
| Email provider down at invite | Invite row exists, `invite_sent_at NULL`, shown *not sent*, resend offered |
| Redis down | Every request fails as today — the rate limiter has no fallback — so tokens and sessions fail alike |
| Postgres down | 503 for everything that reads identity, as today |
| Ticket spent twice / expired | Second redeem 401 *"This support link has already been used"* |
| Grant withdrawn during *view as* | Next request 401 *support access ended*; banner explains |
| Two owners demote each other concurrently | Row lock serializes; the second is refused as last owner |
| MCP result too large | Cut to bound, with a narrowing hint (FR34) |

## Security, tenancy, and privacy

- **Tenancy.** New Postgres statements take `org_id` explicitly (`test_postgres_tenancy_structure.py`).
  Two lookups are cross-tenant by nature and join its exemption list with a stated reason, as the
  receiver's key lookup does: the token lookup by hash, and the set-password link lookup by hash. The
  admin API is deliberately cross-tenant — it is staff's — and lives in its own module the structural
  test treats as a named exception.
- **Secrets.** Invite links and tokens are stored only as SHA-256 of ≥128-bit values; shown once;
  never logged (log the 12-character prefix). TOTP secrets for staff are sealed like customers'
  (`models.py:194-205`).
- **Escalation paths closed:** a role above one's own (BR3, service check); a token writing (surface is
  `CurrentReader`-only, non-GET refused); *view as* writing (refused in `get_current_identity`); a staff
  cookie on the customer app or vice versa (different cookies, different apps, different hosts).
- **Privacy.** *View as* requires the customer's grant; every request in it is audited and visible to the
  customer's owners and admins.
- **MCP prompt injection.** Customer data returned to a model could carry instructions. The server has no
  tool that acts, so an injected instruction can at most ask for another read the same token could make.

## Observability and operations

- Counters: `invites_sent_total{result}`, `api_token_requests_total{route}`, `mcp_tool_calls_total{tool,
  result}`, `support_view_sessions_total`.
- Logs name token prefixes, staff labels and impersonation ids; never secrets.
- Operations: `python -m nexovue_api.staff create|disable`; `NEXOVUE_ADMIN_HOST` and `SIGNUP_ENABLED` in
  the deploy env; the admin host can be IP-restricted in Caddy (documented, off by default).

## Alternatives and tradeoffs

- **Memberships table and an organization switcher** — rejected by D1.
- **Scopes on the existing `api_keys`** — rejected (ADR-017): the receiver's hot path reads that table,
  and one row meaning both "may write telemetry" and "may read data" is the credential every vendor
  splits.
- **Personal access tokens** — rejected by D3.
- **"Every GET is readable by a token"** — rejected by D8; the surface is the set of `CurrentReader`
  routes instead, which is enforced by construction rather than by a list the code can drift from.
- **Staff as `users` with a flag, inside the customer app** — rejected (ADR-016): one bug in a role check
  would make a customer staff, and staff sessions would share the customer cookie and origin.
- **A separate admin service and database** — rejected: it would need its own copy of provisioning,
  usage and suspension logic; a second app over the same package reuses them.
- ***View as* by minting an ordinary session** — rejected: nothing would mark it read-only or audit it.
- **Comps by rewriting `organizations.plan` and a job to restore it** — rejected: a job that fails leaves
  a customer on a free plan forever; a grant that ends by its own date cannot.
- **OAuth for MCP** — deferred by D13.
- **Redis one-time tokens for invites** — rejected: cannot list pending invites or kill a resent link.

## Specification impact

None required. Two clarifications, folded into the spec on approval: FR17's first list is fixed to the existing
`GET` routes named in §3; FR29's "every request carries `impersonation_context`" is satisfied by one
`support.view_request` audit row per request (§6).

## ADRs

- [ADR-016 — Staff work on their own host](../../decisions/ADR-016-staff-work-on-their-own-host.md)
- [ADR-017 — A read token is its own credential](../../decisions/ADR-017-a-read-token-is-its-own-credential.md)

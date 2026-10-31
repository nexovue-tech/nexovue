# Feature: Team, API and admin access (F26 + F31 + F36)

## Status

**Amended 2026-09-20** — Stage 3's separation is reversed by
[ADR-018](../../decisions/ADR-018-the-operator-is-a-user-in-the-console.md): one operator, a `users`
row signing in on the ordinary sign-in page, admin screens inside the console, and a 30-minute
step-up re-authentication in place of the separate host. **BR13, FR21 and NFR4 below are rewritten**;
everything else about Stage 3 — what the operator can do, and the consent and read-only rules on
*view as* — is unchanged. See `docs/changes/super-admin-in-the-console/`.

**Approved** — by the founder, 2026-09-19: every open decision as recommended, except **D6** (an MCP
server is built in this feature) and **D7** (the audit-log screen is built in this feature). D13 (how
the MCP server authenticates) was added with those two and follows its recommendation. See
*Decisions taken*.

Proposed 2026-09-19. F26 (RBAC / user management), F31 (the internal admin console) and F36
(customer read API) are specified as one feature by the founder's decisions of 2026-09-19: all three
answer *who may do what* — a customer's people, our own staff, and a customer's machines — and
designing them apart would invent three permission models. They also share one primitive: a person
with no password and a one-time link that sets one, which is both an invite (F26) and how a paying
customer's first owner is onboarded (F31, ADR-008). Delivery is staged (*Scope*); the specification
is one.

## Problem

**An organization is one person, a machine cannot read anything, and we cannot onboard or support a
customer without a database prompt.**

- **Nobody can be invited.** There is no invitations table, no invite token, no invite email, and no
  members route or screen. `users.invited_by` and `users.invited_at` exist and nothing writes them;
  `users.password_hash` is nullable "until an invited user sets one" (`data/postgres/models.py:178-209`).
  Signup is the only way a user comes to exist, and it always creates an `owner`
  (`signup/service.py:50,107`).
- **The role gates enforce roles nobody can hold.** `owner`, `admin`, `member` and `viewer` have been in
  the `user_role` enum since migration 0001. `MayAdminister` (owner, admin), `MayCommitSpend` and
  `MayEndTheOrganization` (owner) gate every mutating route (`auth/policy.py:44-104`), but no code path
  creates or changes a role, and `member` and `viewer` appear in no enforcement code at all. The console
  shows every control to everyone; it reads `role` only as a string (`apps/web/src/features/auth/api.ts:18`).
- **A team shares one login, or does without.** A second engineer who needs to look at an incident at
  3 a.m. has the founder's password or nothing. Shared credentials defeat the second factor, the session
  list and sign-out-everywhere (F32), all of which assume one person per login, and the audit log names
  one actor for what several people did.
- **Alerts reach one person.** The *Organization owners* channel resolves to verified owners at send time
  (`apps/scheduler/.../alerts/senders/email.py:21-45`) — which is, today, always exactly the signup user.
- **A script cannot read.** The receiver's ingest keys are write-only and project-scoped
  (`nvk_live_`, SHA-256 hashed, `api_keys` has no column for what a key may do). The console API accepts
  `Authorization: Bearer`, but the only token it knows is a Redis *session* token that login never hands
  out (`auth/sessions.py:269-276`, `auth/router.py:72-74`). So a customer cannot fail a deploy on their
  own error rate, pull last week's numbers into their warehouse, post a summary to their own channel, or
  point an AI assistant at their incidents.
- **We cannot onboard a paying customer.** ADR-008 (accepted 2026-09-16) says a cloud customer pays on
  the website, and is then onboarded *by hand through a platform admin console* that creates the
  organization and its owner in a chosen region (ADR-009) and emails the owner a one-time link to set
  their own password. None of it exists: no console, no staff identity, no `organizations.region`
  (ADR-009, *Prepared now* #3, "when F31 is built"), and hosted signup is still open to anyone.
- **We cannot see or act on a customer except from a terminal.** Suspending ingest is a CLI
  (`apps/api/src/nexovue_api/suspend.py`) that says, of itself, "the screen belongs with" a later task,
  because "naming *who* closed a valve needs an identity for platform operators". Finding an organization,
  its usage against its plan, or granting a complimentary plan means SQL.
- **We cannot see what a customer sees.** "It shows nothing for me" can only be answered by asking for
  their password or screenshots. `audit_log.impersonation_context` and `audit_log.actor_label` were
  added for exactly this (migration 0004) and nothing sets them.

### What a customer compares us against

Researched 2026-09-19 from vendor documentation and pricing pages. **✓** the vendor's own documentation
states it; **✗** the opposite; **?** not verified.

| Expectation | Datadog | Sentry | Honeycomb | Grafana Cloud | New Relic | Better Stack | Axiom |
|---|---|---|---|---|---|---|---|
| Fixed built-in roles | ✓ Admin, Standard, Read Only | ✓ Member … Owner | ✓ Owner, Member, Read-only | ✓ Admin, Editor, Viewer | ✓ six | ✓ five | ? |
| Custom roles | Enterprise | ✗ | ? | paid tiers (sources conflict) | Pro/Enterprise | ? | $50/mo add-on |
| Email invite with expiry | ✓ 48 h | ✓ | ✓ owners only | ✓ (1 week, OSS default) | ✓ | ✓ | ? |
| Inviter limited to their own role | ✓ | ? | n/a | ? | ? | ? | ? |
| Per-seat price | ✗ | ✗ from Team | ✗ unlimited | ✓ $8/user past 3 | ✓ | ✓ responders | ✗ |
| Ingest and read are different credentials | ✓ | ✓ | ✓ (query API Enterprise) | ✓ scoped tokens | ✓ | ✓ | ✓ |
| An org-owned token that survives its creator leaving | ✓ service accounts | ✓ org tokens | ✓ | ✓ service accounts | ✗ user keys | ? | ✓ API tokens |
| Token expiry | ✗ none built in | ? | ? | ✓ optional, org maximum, warning email | ✗ | ? | ✓ |
| Secret shown once; last used shown | ✓ ✓ | ✓ | ✓ | ✓ | ? | ? | ✓ |
| Distinct token prefix for secret scanning | ? | ✓ `sntrys_`, GitHub partner | ? | ✓ `glc_`, GitHub partner | ✓ `NRAK-` | ✗ | ? |
| Hosted MCP server for AI assistants | ✓ GA Mar 2026 | ✓ | ✓ all plans | ✓ Jul 2026 | ? | ✓ | ✓ |

**What this says:** fixed roles, email invites and unlimited seats are what small-team products do;
custom roles, SAML/SCIM and audit-log *viewing* are where vendors charge. Every vendor separates the
credential that writes from the credential that reads. The failure small teams actually hit is **CI
breaking when the person whose token it used leaves** — which is why an organization-owned token is the
one to build first. An MCP server is now expected, and it is only as good as the read API and the token
under it.

**For the admin console** (researched the same day; WorkOS, Pigment engineering, AppMaster — *Sources*):
support impersonation is done without the customer's password, is **read-only by default**, lasts
**about 30 minutes**, shows a banner, needs the customer's consent (or a second approval, or is refused,
for owners and billing), and records who, whom, when and **why** on every request. Staff sign in
separately from customers, with a strong second factor.

## Goal

A team works in one organization, each person under their own login and a role that decides what they
can change; and a machine reads that organization's data with a token the organization owns, can scope
to a project, sees last used, and can revoke at once. Our own staff onboard a paying customer, find an
organization, suspend or comp it, and — with the customer's consent — see exactly what one of its
people sees, all without a database prompt and all on the record.

## Target users

- **P1 — Dev**, and the rest of their team: invited, signs in as themselves, sees what they are allowed
  to change. Creates a read token for CI.
- **P2 — Indie / small team lead (owner)**: invites, chooses roles, removes people, hands ownership on.
- **P3 — Non-technical viewer** (`vision.md:45-49`): invited as a **viewer**; reads every screen,
  changes nothing.
- **Platform engineer / automation**: scripts, CI deploy gates, warehouse pulls and AI assistants, through
  a token.
- **nexovue staff** — today the founder alone: onboards customers, answers "why does it look like
  this", suspends and comps. Uses the admin console only; never a customer login.

**Not served:** people who belong to **more than one organization** under one login (D1); enterprises
needing SAML, SCIM or custom roles; **a staff member changing a customer's data on their behalf** —
impersonation here is read-only (D11).

## Scope

**Stage 1 — Team.** Invite by email; accept by setting a password; list members and pending invites;
change a role; remove a member; transfer ownership; the four roles enforced on every route and reflected
in the console; audit of every membership event.

**Stage 2 — Read API and MCP.** Organization-owned API tokens (create, list, revoke; project scope;
expiry; last used); a documented, stable read surface over `/api/v1`; per-token rate limits; audit of
token lifecycle; a hosted, read-only **MCP server** over the same surface, authenticated by the same
token, so an AI assistant can read a customer's services, traces, logs, errors and incidents.

**Across stages — the audit log screen.** Owners and admins read their organization's audit log in
Settings: every event BR12 records, filtered by who, what and when, with support's *view as* sessions
marked. It grows with each stage's events.

**Stage 3 — Admin console (F31).** Staff identities with a mandatory second factor; onboard an
organization and its owner in a region; find an organization and see its members, projects, usage against
plan, last ingest and suspension; suspend and resume ingest; grant or end a complimentary plan;
consented, read-only, time-bound *view as*; close hosted self-signup (D10).

Stages are numbered by dependency, not delivery. Stage 1 comes first and is judgeable on its own: a
second person can be invited and work; it also builds the set-password link Stage 3 needs. The proposed
delivery order is **1 → 3 → 2**: the admin console is what lets the first paying customer in (ADR-008),
and a read API has nobody to serve before that (D12).

## Out of scope

- **Belonging to several organizations under one login** — D1. The one-organization-per-user model
  (`models.py:164-168`) stays.
- **Custom roles, per-project permissions for people, teams within an organization.** Every competitor
  that has them charges for them; a 1–20 person team does not need them yet.
- **SSO**: Google/GitHub sign-in, SAML, SCIM. Google/GitHub sign-in is table stakes for small teams
  (Sentry, Grafana, Better Stack give it free) and belongs to F32 (account access), not here.
- **Personal access tokens** that carry a user's own role. Org-owned tokens first (D3).
- **Writing through the API** — creating checks, rules or channels from Terraform or a script. Stage 2 is
  read-only (D4).
- **OAuth sign-in for the MCP server** (the MCP specification's standard for remote servers). The server
  takes the API token as a Bearer header, which Claude Code, Cursor and Claude connectors accept (D13).
- **MCP tools that change anything.** The MCP server is read-only, as the tokens are (D4).
- **Staff changing a customer's data while viewing as them**, and break-glass access without consent (D11).
- **A second staff role, staff SSO, and hardware-key-only sign-in.** One staff member exists (D9).
- **Moving an organization between regions** (ADR-009's per-customer migration) and any second region.
  The console records the region an organization is created in; there is one to choose.
- **Taking the website payment** (ADR-008, steps 1–4, the Firebase site) — only *recording* it against
  the organization it paid for is here.
- **Exporting the audit log** or streaming it to a customer's SIEM.
- **One audit row per API read.** Token use is shown as last used and a daily request count, not as a row
  per request.
- **Per-person alert preferences and a notification centre** (F21 spec, *Out of scope*): still later.
- **GitHub's secret-scanning partner programme.** The token prefix is chosen so that joining later needs
  no change to tokens; joining needs a public endpoint and is an operations task.

## User requirements

### Stage 1 — Team

- **US26.1** — As an owner (P2), I invite a teammate by email and choose their role, so that they sign in
  as themselves instead of sharing my password.
- **US26.2** — As an invited person, I open the link in the email, choose my name and a password, and
  land in the organization — without an account to create first.
- **US26.3** — As an owner or admin, I see everyone in the organization and every invite still pending:
  who, which role, invited by whom, since when, last signed in.
- **US26.4** — As an owner or admin, I resend or withdraw a pending invite, so a mistyped address or a
  changed mind costs nothing.
- **US26.5** — As an owner or admin, I change someone's role, and the change applies to their very next
  action — not at their next sign-in.
- **US26.6** — As an owner or admin, I remove someone, and from that moment they can do nothing: not in
  an open tab, not from a session on another device.
- **US26.7** — As an owner, I make someone else an owner or hand ownership on, and I can never leave the
  organization without one.
- **US26.8** — As a viewer (P3), I read every screen and am never shown a button that will refuse me.
- **US26.9** — As a member (P1), I do the day-to-day work — checks, alert rules, muting, maintenance
  windows — without being able to change who is in the organization, where money goes, or its secrets.
- **US26.10** — As an owner, I can see afterwards who invited, removed, promoted or demoted whom, and when.

### Stage 2 — Read API

- **US36.1** — As an owner or admin, I create an API token, name it, choose one project or all of them,
  and choose when it expires; I see the secret once.
- **US36.2** — As a platform engineer, I fail a deploy in CI when the service's error rate or an open
  incident says so, using that token and a documented endpoint.
- **US36.3** — As a platform engineer, I pull a day of traces, logs or metrics into my own warehouse
  nightly, page by page, without tripping a limit meant for a browser.
- **US36.4** — As an owner, I see every token, what it may read, who made it, when it was last used, and
  when it expires; I revoke one and it stops working at once.
- **US36.5** — As an owner, a teammate leaving does not break our CI: the token belongs to the
  organization, not to them.
- **US36.6** — As a platform engineer, a token for *staging* cannot read *production*, and a request for
  production says "not found", not "forbidden".
- **US36.7** — As a Dev (P1), I add nexovue to my AI assistant with one URL and a token, and ask it
  "what is failing in checkout right now?" — it answers from my services, errors, incidents and traces,
  and can change nothing.
- **US36.8** — As an owner, I can see which token an assistant used and when, and revoke it like any
  other.

### Audit log

- **US26.11** — As an owner or admin, I open Settings → Audit log and see who did what and when —
  invites, role changes, removals, keys and tokens, sign-ins, channel and rule changes, support access and
  every *view as* — and narrow it by person, kind of event and time.

### Stage 3 — Admin console

- **US31.1** — As staff, after a customer pays on the website, I create their organization in a region
  and name its owner's email; the owner receives the set-password link and lands in their organization.
- **US31.2** — As staff, I record which website payment this organization's plan came from, so there is
  one answer to "has this customer paid" (ADR-008, step 8).
- **US31.3** — As staff, I find an organization by name, slug, id or any member's email, and see its
  members, projects, plan, usage against the plan, when it last sent data, and whether ingest is
  suspended and why.
- **US31.4** — As staff, at 3 a.m., I suspend one organization's ingest with a reason, and resume it,
  from a screen — not by finding a UUID for a SQL prompt.
- **US31.5** — As staff, I give an organization a complimentary plan until a date, with a reason, and end
  it.
- **US31.6** — As an owner, I grant nexovue support access for a limited time from Settings, see whether
  it is active, and withdraw it.
- **US31.7** — As staff, while support access is granted, I view the console as one of that
  organization's people — read-only, for at most 30 minutes, with a reason — and see exactly what they
  see.
- **US31.8** — As an owner, I can tell afterwards that support viewed my organization, when, as whom, and
  why.

## Business rules

- **BR1 — One organization per person.** A user belongs to exactly one organization (D1). An invite to an
  address that already has an account in another organization is refused, and says so.
- **BR2 — Always an owner.** An organization always has at least one owner who can sign in. No removal,
  demotion or departure may leave it with none.
- **BR3 — Nobody grants more than they hold.** An inviter or role-changer can give at most their own
  role; only an owner makes or unmakes an owner.
- **BR4 — A role is read on every request.** A role change or removal takes effect on the next request of
  every session and token that person or credential holds; nothing caches a role past it.
- **BR5 — Removal is immediate and complete.** A removed person's sessions end, their pending invites
  lapse, and they cannot sign in. What they made — checks, rules, channels, tokens — stays with the
  organization and keeps working.
- **BR6 — History keeps what happened, not the person.** Audit entries, incident mutes and "created by"
  labels stay, but a removed person is named "Former member" and every audit entry names their user id
  instead of their address or name. *Amended 2026-10-11 by the personal-data spec (BR2, BR4); it said
  history kept their name.*
- **BR7 — The server decides.** The console hides what a role cannot do (US26.8), but every refusal is
  the API's; a hidden button is a courtesy, not a control.
- **BR8 — A credential that writes never reads, and one that reads never writes.** Ingest keys and site
  keys stay write-only; API tokens are read-only (D4). A token is never accepted by the receiver, and an
  ingest key is never accepted by the API.
- **BR9 — A token belongs to the organization.** It carries its own role and scope, not its creator's;
  its creator leaving changes nothing about it (D3).
- **BR10 — A secret is shown once and stored only as a hash.** Invite links and API tokens, like ingest
  keys today.
- **BR11 — Out of scope looks like absent.** A token scoped to one project that asks about another gets
  the same answer as for a project that does not exist.
- **BR12 — Every membership and credential event is audited**: invite sent, resent, withdrawn, accepted,
  expired-on-use; role changed; member removed; ownership transferred; token created, revoked, expired
  in use; organization created, owner link sent, payment recorded; ingest suspended and resumed; plan
  comped and ended; support access granted and withdrawn; view-as started and ended. With actor,
  target, before and after, and a reason where the action requires one.
- **BR13 — The operator holds nothing inside an organization** *(amended 2026-09-20, ADR-018)*. The
  operator is a `users` row with `role = "super_admin"` in a reserved organization of its own; the role
  grants no permission in any customer organization, and no customer role can reach the platform
  surface. Its actions are audited as itself, as any user's are.
- **BR14 — Seeing as a customer needs the customer's consent.** Without an active support-access grant
  from an owner, no *view as* can start. A grant is time-limited and an owner can withdraw it; withdrawal
  ends every *view as* in it.
- **BR15 — Viewing as a customer changes nothing.** During *view as* every request that would change
  anything is refused, whatever the viewed person's role; the viewed person's permissions bound what is
  read, never staff's.
- **BR16 — Every platform action states why.** Suspending, comping, and starting a *view as* require a
  written reason, stored with the audit row.

## Functional requirements

### Roles

- **FR1** — Four roles, fixed. What each may do:

  | Action | Owner | Admin | Member | Viewer |
  |---|---|---|---|---|
  | Read every screen and every read endpoint | ✓ | ✓ | ✓ | ✓ |
  | Create, edit, pause, delete, run checks; check files | ✓ | ✓ | ✓ | ✗ |
  | Create, edit, mute, delete alert rules; maintenance windows | ✓ | ✓ | ✓ | ✗ |
  | Upload source maps | ✓ | ✓ | ✓ | ✗ |
  | Alert channels, their secrets and Send test | ✓ | ✓ | ✗ | ✗ |
  | Check secrets and variables | ✓ | ✓ | ✗ | ✗ |
  | Projects, sampling and masking configuration | ✓ | ✓ | ✗ | ✗ |
  | Anomaly detection: sensitivity, mutes, new normals, *anomaly finding* alert rules (F6 FR23) | ✓ | ✓ | ✗ | ✗ |
  | Ingest keys, site keys, deploy webhook secrets, integrations | ✓ | ✓ | ✗ | ✗ |
  | API tokens (Stage 2) | ✓ | ✓ | ✗ | ✗ |
  | Invite, change roles, remove — up to their own role (BR3) | ✓ | ✓ | ✗ | ✗ |
  | Make or unmake an owner, transfer ownership | ✓ | ✗ | ✗ | ✗ |
  | Billing: checkout, cancel | ✓ | ✗ | ✗ | ✗ |
  | Delete the organization | ✓ | ✗ | ✗ | ✗ |
  | Their own profile, password, second factor, sessions | ✓ | ✓ | ✓ | ✓ |

  Today every row a member may do is gated owner-or-admin (`MayAdminister`); this table widens those
  gates to members (D2).
- **FR2** — The authorization coverage test (`test_authorization_coverage.py`) keeps failing the build
  for a mutating route with no role gate; the key routes that are gated inside their service today move
  to an explicit route gate, so the table above is enforced in one place.
- **FR3** — `GET /auth/me` returns the caller's role and the actions it permits, so the console hides
  what it cannot do without restating the table.

### Invitations

- **FR4** — An invite names an email address and a role. It creates a user in the inviting organization
  with no password, `invited_by` and `invited_at` set, and a single-use link valid for **7 days** (D5),
  sent by email through the existing email port with a new *invite* message.
- **FR5** — Accepting sets the person's name and password (through the same password rules as signup)
  and marks their address verified — following a link sent to it proves it — then signs them in. The
  link is spent.
- **FR6** — A pending invite can be resent (a new link; the old one stops working) or withdrawn (the
  user row is removed; nothing it did exists). An expired invite is shown as expired and can be resent.
- **FR7** — The set-password link is one primitive, shared with F31's onboarding of a new owner
  (ADR-008, step 7): a user with no password, and a link that sets one.

### Members

- **FR8** — The members list shows every person and every pending invite: name, email, role, status
  (active, invited, expired), invited by, joined, last active.
- **FR9** — Changing a role or removing a person ends none of their work (BR5) and takes effect on their
  next request (BR4). Removal also ends every session they hold (the session index already lists them,
  `auth/sessions.py:52`).
- **FR10** — Removing the last owner, demoting it, or an owner leaving while the only owner, is refused
  (BR2). Ownership transfer promotes another member to owner; the previous owner may then step down.
- **FR11** — A removed person's email address is free again: they can sign up anew, or be invited back,
  as a new person (BR1, BR6 — their history names their id).
- **FR12** — The *Organization owners* alert channel keeps meaning verified owners, resolved at send
  time; a second owner is included with no change to the channel.

### API tokens (Stage 2)

- **FR13** — A token has: a name; a scope of **one project or all projects**; a role of **read** (the only
  one in this feature, D4); an expiry of 30, 90 (default) or 365 days, chosen at creation (D5); its
  creator; created, last used, expires, and revoked times.
- **FR14** — Token format `nvt_live_` + 32 base62 characters, distinct from `nvk_` (ingest) and `nvp_`
  (site) so a leaked one is recognisable and a scanner can match it; stored as a hash with a short
  display prefix, like ingest keys.
- **FR15** — A request authenticates with `Authorization: Bearer nvt_live_…`. A token reaches only the
  documented read surface (FR17) and only `GET`; anything else is refused as forbidden, and says a token
  is read-only.
- **FR16** — Revoking is immediate: the next request with the token is refused. An expired token is
  refused with a message that says it expired, and when.
- **FR17** — **The read surface** is an explicit, documented list of `/api/v1` endpoints, versioned and
  kept stable — not "every GET the console happens to call", which changes with every screen (D8).
  First list (design §3): services (list, one); traces (search, one); logs (search); metrics (catalogue,
  histogram, duration heatmap — there is no generic metric query); error groups (list, one); incidents
  (list, one); checks (list, one, results, runs); deploys (list).
- **FR18** — Row lists on the read surface (traces, logs, deploys, check runs, error groups) page by an
  opaque cursor and say whether there is another page. Bounded snapshots (services, metrics catalogue,
  incidents, checks) return everything up to their cap and say when they were cut (`truncated`).
- **FR19** — Tokens are rate-limited **per token**, not per address, at a level that permits a nightly
  pull; every response carries the IETF `RateLimit` and `RateLimit-Policy` headers the API already emits,
  and a refusal carries `Retry-After`.
- **FR20** — Customer documentation at `/docs` covers creating a token, authenticating, the read
  surface, pagination, limits and errors, with a CI deploy-gate example (US36.2), and connecting an AI
  assistant (FR31).

### MCP server (Stage 2)

- **FR31** — A remote MCP server over the Streamable HTTP transport at one documented URL, authenticated
  by an API token as `Authorization: Bearer` (D13). It is served by nexovue — hosted and self-hosted
  alike — not installed by the customer.
- **FR32** — Its tools mirror the read surface (FR17) and nothing else: list services and their health;
  search traces and read one; search logs; query a metric; list error groups and read one; list incidents
  and read one; list checks and their recent results; list deploys. Each tool has a description written
  for a model and a bounded time window that defaults to the last hour.
- **FR33** — A tool call is exactly a read-surface request made with the same token: the same project
  scope (BR11), the same rate limit (FR19), the same refusals. It updates the token's last used.
- **FR34** — Every tool result is bounded — a row limit and a size limit per call, and it says when it
  was cut short and how to narrow — so one question cannot pull a day of logs into a model's context.
- **FR35** — Tool results carry customer data (log bodies, span attributes, error messages) as data: the
  server never follows instructions found in them and has no tool that could act on one (D4).

### Audit log screen

- **FR36** — Settings → Audit log, for owners and admins: newest first, paged, filtered by actor, by
  event kind (people, credentials, sign-in, configuration, support) and by time range. Each row: when,
  who (a person, a token, or *nexovue support* with the staff member), what, target, and before → after
  where there is one.
- **FR37** — Entries made during a *view as* are marked, and link to the session: who, whom, when, for how
  long, and the reason given.
- **FR38** — The screen reads the existing `audit_log` table; audit rows are kept at least 365 days.
  Where configuration events (channels, rules, checks, projects) are not audited today, this feature adds
  them.

### Admin console (Stage 3)

- **FR21** *(amended 2026-09-20, ADR-018)* — The operator signs in on the ordinary sign-in page with
  the ordinary session. The platform area is offered in the console only to `super_admin`, and every
  `/admin` route is refused unless the session has been re-verified — password or second-factor code —
  within the last 30 minutes. The account is seeded from `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD`
  at API boot, never created by a screen or a request; both unset seeds nobody.
- **FR22** — Onboarding creates, in one transaction: the organization (with its `region`, default
  `india`, from ADR-009's list), a default project, the owner as a user with no password, and the
  owner's set-password link (FR7), sent by email. It reuses signup's provisioning, so an onboarded
  organization has exactly the rows a self-signed-up one does.
- **FR23** — `organizations.region` is added, default `india` (ADR-009, *Prepared now* #3). Nothing reads
  it yet but the console; its existence is the point.
- **FR24** — A payment reference (the website's Razorpay payment id and amount) can be recorded against
  an organization once; the organization's plan is then the product's billing's to renew (ADR-008,
  step 8).
- **FR25** — The platform screen **lists every customer, newest first**, capped, and says so when the
  cap cut the list; a search narrows that list by name, slug, id, or member email. nexovue's own
  organization is not a customer and appears on neither. The organization page shows members
  and roles, projects, plan and comp, usage against allowance (from the existing usage read), last ingest
  per project, suspension with reason, support-access state, and its recent audit rows.
- **FR26** — Suspend and resume write the same `ingest_suspended_at` / `ingest_suspended_reason` the CLI
  writes today, and the CLI remains, for when the console is down.
- **FR27** — A comp sets a plan and an end date with a reason; when it ends the organization returns to
  its paid plan, or to none.
- **FR28** — Support access: an owner grants it for 24 hours, 72 hours or 7 days from Settings; the grant
  is shown to every owner while active, and can be withdrawn.
- **FR29** — *View as*: staff choose a person in an organization with active support access and give a
  reason; a session starts, lasting **30 minutes** or until ended, in which the customer console renders
  as that person with a permanent banner naming the staff member and the time left. Every request in it
  carries `impersonation_context` — one `support.view_request` audit row per request (design §6); every
  mutating request is refused (BR15).
- **FR30** — Hosted self-signup can be switched off by a setting; with it off, `/signup` says how to buy
  and the only way in is an invite or onboarding (D10). Self-hosted installs keep signup on by default.

## Non-functional requirements

- **NFR1** — A role or token check adds no network round trip beyond what `CurrentIdentity` does today
  (one Postgres read per request).
- **NFR2** — Revocation, removal and role change take effect within **one request** — no cache holds a
  role or a token's validity past it.
- **NFR3** — Invite and token secrets have at least 128 bits of entropy.
- **NFR5** — An MCP tool call answers within the same bounds as its read-surface endpoint; the server
  adds no store of its own.
- **NFR4** *(amended 2026-09-20, ADR-018)* — The platform surface is not reachable from an ordinary
  console session: the role is checked first, so a customer's session is refused before any credential
  is examined, and the operator's own session reaches it only inside the step-up window. The stamp
  lives on the session record, so signing out ends it.

## Error cases

| What happens | What the person sees | Recover? |
|---|---|---|
| Invite to an address that is already a member | "Already a member of this organization." No email sent | — |
| Invite to an address with an account in another organization | "This address already belongs to another nexovue organization." (D1) | Use another address |
| Invite link expired | "This invite expired on …; ask {inviter} to send a new one." | Resend |
| Invite link withdrawn, spent, or resent since | "This invite is no longer valid." Same answer for each, so a link reveals nothing | Ask again |
| Invitee already signed in as someone else | Told who they are signed in as, and to sign out first | Yes |
| A member tries an admin action (by URL or API) | The action is refused with the role it needs; nothing changed | — |
| Role lowered while a tab is open | The next change is refused and the screen redraws for the new role | — |
| Removed while signed in | The next request signs them out: "You are no longer a member of {org}." | — |
| Removing or demoting the last owner | Refused: "{org} needs at least one owner. Make someone else an owner first." | Transfer |
| Admin tries to make someone an owner | Refused: only an owner can | — |
| Token revoked, expired, or unknown | 401, saying which of *revoked* or *expired* when it is one of ours — never echoing the token | New token |
| Token used for a write, or outside the read surface | 403: "API tokens are read-only" | — |
| Token for project A asks for project B | 404, identical to a project that does not exist (BR11) | — |
| Token over its rate limit | 429 with `Retry-After` | Wait |
| An ingest key sent to the API, or a token to the receiver | Refused, saying which credential that door takes (BR8) | Use the right one |
| Invite email cannot be sent (provider down) | The invite is still created and marked *not sent*; resend offered | Resend |
| Onboarding an owner email that already has an account | Refused, naming the organization it belongs to (to staff) | Use another address |
| Owner's set-password link expired before first sign-in | Staff resend it from the organization page | Resend |
| Staff start *view as* with no active grant | Refused: "{org} has not granted support access." | Ask the owner |
| Grant withdrawn or expired during *view as* | The session ends on the next request; banner says why | — |
| Staff try to change something while viewing as | Refused: "Support view is read-only." | — |
| A customer session sent to the admin surface, or staff to the customer console | Refused at the door | — |
| MCP client connects without a token, or with a revoked one | 401 with a message the assistant can repeat: which token, and where to create one | New token |
| An assistant asks a tool for a whole day of logs | A bounded result that says it was cut short and how to narrow | Narrow |
| A member or viewer opens Settings → Audit log | The entry is not shown to them; the route refuses | — |
| Signup reached while hosted signup is off | "nexovue cloud is set up after purchase — see nexovue.com" | Buy / be invited |

## Acceptance criteria

### Stage 1

- **AC1** — An owner must be able to invite an address with a role; the invitee must receive a link that,
  opened, lets them set a name and password and lands them signed in, verified, with that role.
- **AC2** — An invite link must work once, must stop working when resent or withdrawn, and must be
  refused after 7 days with the expiry message.
- **AC3** — Every mutating route must be refused to a role FR1 does not grant, and allowed to one it does;
  a test must enumerate the routes and fail when one is ungated or gated differently from FR1.
- **AC4** — A viewer must see no control that the API would refuse, on every screen.
- **AC5** — Changing a role must change what the person's *existing* session may do on its next request.
- **AC6** — Removing a person must end every session they hold within one request, and must leave every
  check, rule, channel and token they created working.
- **AC7** — The organization must never be left without a signed-in-capable owner: removing, demoting,
  or the departure of the last owner must be refused.
- **AC8** — An admin must not be able to grant owner, nor any role above their own.
- **AC9** — An invite to an address belonging to another organization must be refused with the D1
  message and must create nothing.
- **AC10** — Every event in BR12 must write one audit row naming actor, target and the change.
- **AC11** — A second verified owner must receive *Organization owners* alert email.
- **AC12** — A removed person's address must be able to sign up again, and the old audit rows must still
  name them.

### Stage 2

- **AC13** — An owner or admin must be able to create a token with a name, project scope and expiry, and
  must see its secret exactly once.
- **AC14** — A token must read every endpoint on the documented read surface for its scope, and must be
  refused on every other route and every non-`GET` method.
- **AC15** — A project-scoped token asking about another project must get the same 404 as a
  non-existent project.
- **AC16** — A revoked token must be refused on its very next request; an expired one with the expired
  message.
- **AC17** — A token must keep working after its creator is removed.
- **AC18** — The token list must show last used within a minute of use.
- **AC19** — Rate limiting must count per token and return the documented headers; a refusal must carry
  `Retry-After`.
- **AC20** — A list endpoint on the read surface must return every row across pages, each once.
- **AC21** — The receiver must refuse an API token, and the API must refuse an ingest key.
- **AC22** — The documentation's CI example must run, as written, against a dev stack.

### MCP and audit log

- **AC32** — Claude Code (`claude mcp add --transport http … --header "Authorization: Bearer nvt_live_…"`)
  and Cursor must connect to the MCP server with an API token, list its tools, and answer from a dev
  stack's data.
- **AC33** — Every MCP tool must return only what its read-surface endpoint returns for the same token,
  and must be refused for another project exactly as the endpoint is.
- **AC34** — No MCP tool may change anything; a test must enumerate the tools and prove each is a read.
- **AC35** — A tool result over its bound must be cut, say so, and say how to narrow.
- **AC36** — Every BR12 event must appear on the audit log screen with actor, action, target and change,
  and the filters must narrow by actor, kind and time.
- **AC37** — Entries made during *view as* must be marked on the screen and must name the staff member and
  the reason.
- **AC38** — A member and a viewer must be refused the audit log screen and its endpoint.

### Stage 3

- **AC23** — Onboarding must create an organization, a default project and an owner with no password, and
  must send a set-password link which, followed, signs the owner in with a verified address.
- **AC24** — An onboarded organization must have the same set of rows as a self-signed-up one (compared
  table by table).
- **AC25** — `organizations.region` must exist, default `india`, and onboarding must record the chosen
  region.
- **AC26** — A staff account must not be able to sign in without its second factor, and there must be no
  way to create one without it.
- **AC27** — A customer session must be refused by every admin route, and a staff session by every
  customer route.
- **AC28** — Suspending from the console must stop the receiver accepting that organization's data
  exactly as the CLI does, and must record who and why.
- **AC29** — *View as* must be refused without an active support-access grant; must end at 30 minutes;
  must end when the grant is withdrawn; and every mutating route must refuse inside it.
- **AC30** — Every request made during *view as* must write, or be attributable through, an audit row
  carrying its `impersonation_context`, the staff member and the reason.
- **AC31** — With hosted signup switched off, `POST /signup` must be refused and the invite and
  onboarding paths must still work.

## Dependencies and constraints

- **Email** (existing port): a new *invite* message and template.
- **ADR-008**: onboarding by hand (Stage 3), the set-password link (FR7), recording the website payment
  (FR24). Taking the payment on the website is not here.
- **ADR-009**: `organizations.region` (FR23). Moving between regions is not here.
- **`suspend.py`** (P1-52): Stage 3 puts a screen on the same valve and keeps the CLI.
- **F21**: the owners channel (FR12) — no change to its code expected.
- **F32**: sessions, the session index, sign-out-everywhere and the second factor are reused as they are.
  A person invited here enrols a second factor from their own profile, as today.
- **`vision.md:153` — never per seat.** Nothing here counts or limits people.
- **ADR check**: one organization per user is recorded in `models.py:164-168`, not in an ADR. D1 keeps
  it; if it is ever reversed, that reversal is the ADR.
- **Doc drift found while reading** (to correct with this feature): `feature-catalog.md` F36 says the
  console API is "session-cookie only" (a Bearer session token is also accepted);
  `current-system-reference.md` says there is no Redis session index (there is), that the receiver does
  not join `projects` (it does), and that every mutating route carries a route gate (the key routes are
  gated inside their service).

## Decisions taken

Approved by the founder 2026-09-19. Each is written as it was put, with what was decided.

- **D1 — One organization per person, kept.** A consultant working for two customers uses two
  addresses, as today. *Alternative:* memberships and an organization switcher — the table, the switcher,
  and org context on every session, which is F26's biggest cost and serves a persona this product does
  not target yet. **Recommended: keep.**
- **D2 — What a member may do.** FR1 lets members run the day-to-day (checks, rules, maintenance, source
  maps) and keeps secrets, destinations, keys, projects and people to admins. *Alternative:* members
  read-only like viewers, and admins do everything — simpler, but then nearly everyone on a team is an
  admin and the roles mean nothing. **Recommended: FR1 as written.**
- **D3 — Organization-owned tokens, not personal tokens.** A token carries its own role and survives
  its creator. *Alternative or addition:* personal access tokens carrying the user's role — they die when
  the person leaves, which is exactly what breaks a small team's CI. **Recommended: org-owned only now.**
- **D4 — Read-only in this feature.** *Alternative:* read-write tokens now, for Terraform-style setup of
  checks and rules — a larger surface, and every write route would need a stable contract.
  **Recommended: read-only; writes are a later feature.**
- **D5 — Lifetimes.** Invites expire after 7 days. Tokens expire after 30, 90 (default) or 365 days, and
  there is no "never" — a token nobody remembers is the one that leaks. **Recommended as written.**
- **D6 — MCP server.** Every competitor in the table ships one. Recommended: its own feature, next after
  this one. **Decided: built in this feature, in Stage 2** (founder, 2026-09-19).
- **D7 — Audit-log screen.** Every event is recorded (BR12). Competitors charge $50–250 a month for
  audit-log viewing. Recommended: record now, screen later. **Decided: the screen is built in this
  feature** (founder, 2026-09-19), growing with each stage's events.
- **D8 — The read surface is an explicit list, not every GET.** *Alternative:* every `GET` under
  `/api/v1` — nothing to maintain, but the console's endpoints change whenever a screen does, and every
  change would break a customer's script. **Recommended: the explicit list in FR17, versioned.**
- **D9 — One staff role, created from a terminal.** Staff sign in with a password and a mandatory TOTP
  second factor on a separate host. *Alternatives:* staff SSO (Google Workspace) or hardware keys only —
  better, and worth it when there is a second staff member. **Recommended: as written for one person;
  revisit at the second.**
- **D10 — Close hosted self-signup.** ADR-008 says cloud customers pay first and are onboarded by hand,
  and that "hosted signup is still open to anyone". **Recommended: a setting, off for the hosted cloud
  and on for self-hosted installs.**
- **D11 — *View as* is read-only and needs consent; no break-glass.** *Alternatives:* read-write support
  sessions, or staff access without consent in an emergency (with a reason and an alert). Both are
  common; both are the kind of power a one-person company should not hold before it needs it — the
  founder can still reach the database in a true emergency, and that is logged by the infrastructure.
  **Recommended: read-only, consent required, no break-glass.**
- **D12 — Delivery order 1 → 3 → 2.** Team first (and the set-password link), then the admin console that
  lets the first paying customer in, then the read API. *Alternative:* 1 → 2 → 3, if a read API is wanted
  before the first customer. **Recommended: 1 → 3 → 2.**
- **D13 — The MCP server authenticates with the API token, not OAuth.** The MCP specification's standard
  for remote servers is OAuth 2.1 with PKCE, protected-resource metadata and an authorization server —
  a sign-in-and-consent service of its own. Claude Code (`--header`), Cursor (`headers`) and Claude
  connectors (static `Authorization` header) all accept a Bearer token today. **Recommended and decided:
  Bearer API token now; OAuth sign-in is a later feature**, when a client that cannot send a header
  matters.

## Sources

Researched 2026-09-19. Not verified, and so marked **?** above: Datadog's key prefixes and SAML tier;
Grafana Cloud's SAML/RBAC tier (sources conflict); Honeycomb's and Axiom's prefixes; Better Stack's
custom roles and token expiry; every vendor's last-owner rule.

- Datadog: docs.datadoghq.com/account_management/{api-app-keys,rbac,users}/, /api/latest/rate-limits/;
  MCP GA — globenewswire.com, 2026-03-09
- Sentry: docs.sentry.io/organization/membership/, /account/auth-tokens/, /api/pagination/;
  sentry.io/pricing/, /changelog/github-secret-scanning; github.com/getsentry/sentry-mcp
- Honeycomb: docs.honeycomb.io/configure/teams/{manage-permissions,manage-api-keys}/;
  honeycomb.io/pricing, /blog/hosted-mcp-now-available
- Grafana: grafana.com/docs/grafana-cloud/security-and-account-management/authentication-and-permissions/access-policies/;
  grafana.com/docs/grafana/latest/administration/{service-accounts,user-management/manage-org-users}/;
  grafana.com/pricing/; grafana.com/blog/grafana-labs-partners-with-github-to-enable-secret-scanning/;
  grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/
- New Relic: docs.newrelic.com/docs/apis/intro-apis/new-relic-api-keys/;
  docs.newrelic.com/docs/accounts/accounts-billing/new-relic-one-user-management/user-type/
- Better Stack: betterstack.com/pricing; betterstack.com/docs/uptime/team-management/manage-roles/;
  betterstack.com/docs/getting-started/integrations/mcp/
- Axiom: axiom.co/docs/reference/tokens, /restapi/api-limits, /console/intelligence/mcp-server; axiom.co/pricing
- Checkly: checklyhq.com/blog/user-scoped-api-keys/; feedback.checklyhq.com/changelog/introducing-service-api-keys
- MCP: modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization;
  descope.com/blog/post/mcp-auth-spec; claude.com/docs/connectors/building/authentication;
  cursor.com/docs/mcp; github.com/anthropics/claude-ai-mcp/issues/112
- Admin impersonation: workos.com/blog/support-impersonation-delegated-sessions;
  engineering.pigment.com/2026/04/08/safe-user-impersonation/;
  appmaster.io/blog/secure-admin-impersonation-controls-audit-scope;
  github.com/codefly-dev/module-saas-starter/issues/776 (justification missing from the audit trail)
- GitHub secret scanning partner programme: docs.github.com/en/code-security/tutorials/secret-scanning-partner-program

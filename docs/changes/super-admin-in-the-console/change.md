# Change: super-admin-in-the-console — one console, one sign-in, a role that unlocks the platform

## Classification

Refactor (with a schema change and a security control added).

## Current behavior and problem

nexovue's staff surface is a second application (ADR-016, built 2026-09-20): staff are rows in
`staff_accounts`, they sign in with a password *and* TOTP on `admin-api` (its own compose service, its own
port, its own `nexovue_staff` cookie), and the console is a second browser entry, `admin.html`. Crossing
into a customer's console is a single-use ticket that mints a read-only, audited session.

That is a defensible shape and it is not the shape the founder wants to operate at this stage. Today there
is exactly one staff member — the founder — and the separation costs a second sign-in, a second
credential to enrol, a second console with its own plainer layout, a second app to deploy, and a second
build artifact. The work it protects against is one an origin boundary genuinely stops (an XSS in the
console riding a staff session) and several it does not: every documented breach of a support tool in the
last five years — Twitter 2020, Mailchimp twice, Okta/Sitel, Robinhood, Twilio, Shopify, Coinbase, MGM —
reached a tool that was *already* separate, through a stolen or bribed staff credential.

**Founder decision, 2026-09-20.** One console, one sign-in page, one session. A single super-admin user
seeded from the environment; the console sends it to an admin layout; the API enforces the role. The
separation is reconsidered when there is more than one person with platform powers.

## Intended behavior

- **One account kind.** The super admin is a `users` row with `role = "super_admin"`, seeded from
  `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` into a reserved internal organization. It signs in at
  `/login` like anyone else, with the same sessions, the same password rules, the same optional 2FA and
  the same audit log.
- **The console decides where you land.** A super admin signing in is shown the admin area; every other
  role sees today's console, unchanged.
- **The API enforces it.** Admin routes move into the customer API under `/api/v1/admin/…` behind one
  named policy. A route that forgets the policy fails the authorization-coverage test, as every mutating
  route does today.
- **Entering the admin area re-authenticates.** This is the control that replaces the origin boundary and
  the reason the merge is defensible: an admin request is refused unless the session was re-verified —
  password or TOTP — within the last 30 minutes. A ridden or stolen console session reaches the customer
  console and nothing else. GitLab's "Admin Mode" is the same idea.
- **Support view stays exactly as it is.** An owner grants time-boxed consent; the view is read-only,
  capped at 30 minutes, and every page is audited. It is the control every company that publishes its
  practice converged on, and nothing here changes it.

Two things the founder also asked for are **new features, not this change**, and each needs its own
specification: a **support ticket** surface, and **platform settings and feature flags**. They are named
here so the boundary is on the record.

## Affected specifications and architecture

- **ADR-016 is superseded**, not edited: its reasoning was sound and the decision is being reversed on
  different grounds. A new ADR records the reversal, what it costs, and what must be true to revisit it.
- `docs/features/team-and-api-access/` — Stage 3 described the admin console as a second app. Its spec
  (FR21, NFR4), design §6, plan and status all need the amendment; the plan's Outcome is history and
  keeps its original text.
- `docs/architecture/current-system-reference.md` §6.5 — "three principals, not one" becomes two.
- `docs/product/feature-catalog.md` F31 — the row describes a separate host.

## Risks and rollback

- **An XSS in the console can reach platform powers.** This is the risk the merge creates, and step-up
  re-authentication is the mitigation. Without it this change should not ship.
- **`user_role` is a Postgres enum**, so adding a value is `ALTER TYPE … ADD VALUE` — which in Postgres
  cannot run inside a transaction that then uses the new value, and cannot be dropped again. The migration
  must account for both.
- **Cross-tenant reads move into the customer API.** The admin queries deliberately have no `org_id`
  filter; they are named in the tenancy structural test's exemptions with their reasons. Moving them means
  those exemptions now sit in the same package as every tenant-scoped query, where the rule is stricter by
  default. Each keeps its named exemption.
- **Rollback** is the previous commit: the admin app, `staff_accounts` and the separate console are
  deleted in this change, so reverting is a `git revert` plus a migration down, not a configuration
  switch. Nexovue is pre-deployment and no staff account exists outside development.

## Plan and validation

[plan.md](plan.md). Validation and residual risk in [status.md](status.md).

## Status

Built 2026-09-20 and uncommitted, awaiting the founder's review of the diff. Validation in
[status.md](status.md); what changed from the plan is in [plan.md](plan.md) → *Outcome*.

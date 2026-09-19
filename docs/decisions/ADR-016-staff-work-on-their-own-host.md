# ADR-016: Staff work on their own host, as their own kind of account

## Status

Accepted 2026-09-19 — founder decision, with the F26 + F31 + F36 design (`docs/features/team-and-api-access/design.md`).

## Context

F31 gives nexovue's staff — today the founder alone — the power to onboard organizations, suspend
ingest, grant plans, and view a customer's console as one of its people. ADR-008 requires it before the
first paying customer can be let in. The customer console already has a role model (F26) and a session
cookie; the cheapest build would add a *staff* flag or role to `users` and put the admin screens inside
the customer app.

That puts the most powerful account in the system one role check away from every customer account, on
the same origin and cookie. An authorization bug that promotes a customer's role — the class of bug
`test_authorization_coverage.py` exists to catch — would then make a customer staff.

## Decision

1. **Staff are rows in `staff_accounts`, never in `users`.** No foreign key, role or flag connects the
   two. Staff actions are audited through `audit_log.actor_label` (migration 0004).
2. **A staff account always has a confirmed TOTP second factor.** Accounts are created from a terminal
   command that enrols the factor before the row exists; there is no screen that creates staff.
3. **The admin console is a second FastAPI app** (`nexovue_api.admin.main:app`) in the same package —
   reusing its services, queries and database session — **served as its own compose service on its own
   host** (`NEXOVUE_ADMIN_HOST`) with its own session cookie (`nexovue_staff`, host-only). Neither app
   parses the other's cookie.
4. **Crossing into a customer's console (*view as*) is a single-use, 60-second ticket** redeemed on the
   customer host, which creates a read-only, 30-minute, audited session — never a shared cookie and never
   a staff session on the customer origin.

## Consequences

- A customer-side authorization bug cannot yield staff powers: the powers live in another app, behind
  another cookie, on another host.
- One more service and one more Caddy site to deploy; the admin host can be IP-restricted without
  touching the customer site.
- Staff SSO or hardware-key-only sign-in is a change to `staff_accounts` alone, when a second staff
  member exists (spec D9).
- The admin app is cross-tenant by design, and is named as such to `test_postgres_tenancy_structure.py`
  rather than hidden from it.
- **Built 2026-09-20 (F31, Stage 3).** Two consequences settled while building, each an assumption until the
  deployment work of ADR-006 confirms it:
  - *Production routing (plan Q2).* The production replica runs `admin-api` on its own port (8001) and never
    routes it from the customer host. Its own hostname, and any IP restriction on it, are the deployment's
    to add; nothing in the code assumes a name.
  - *Self-hosted installs (plan Q3).* `admin-api` is not in the on-premise Compose file: a self-hosted
    install has no nexovue staff, and its owners already hold every customer-side power.
  - The admin page (`admin.html`) is built into the customer web image as a second entry. It holds no
    secret; it is inert without a staff session and the admin API behind it.

## Rejected

- **Staff as a role on `users`, inside the customer app** — the reason for this record.
- **A separate admin service with its own database** — it would reimplement provisioning, usage and
  suspension, and drift from them.
- **Staff signing in to the customer console with a customer's credentials** — ADR-008 already forbids
  staff knowing a customer's password.

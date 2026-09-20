# ADR-018: nexovue's operator is a user in the console, behind a step-up

## Status

Accepted 2026-09-20 — founder decision, with the change record
`docs/changes/super-admin-in-the-console/`. **Supersedes [ADR-016](ADR-016-staff-work-on-their-own-host.md).**

## Context

ADR-016 put nexovue's own staff in their own table, their own FastAPI app, their own cookie and their
own browser entry, one day before this record. Its reasoning was that the most powerful account in the
system should not be one role check away from every customer account on the same origin.

What building it showed is what it costs at this stage, with exactly one staff member — the founder. A
second sign-in with a mandatory second factor to enrol from a terminal; a second console with its own
plainer layout, which is a second front-end to keep working; a second service and a second hostname to
deploy and restrict; a second build artifact shipped inside the customer image; and a second session
kind, whose bug the first one had already been fixed for.

Against that, the origin boundary stops one attack honestly — an XSS anywhere in the customer console
riding a staff session — and does not stop the attack that actually happens. Every publicly documented
breach of a support tool in the last five years (Twitter 2020, Mailchimp twice, Okta/Sitel, Robinhood,
Twilio, Shopify, Coinbase, MGM) reached a tool that was *already* separate, through a stolen, phished or
bribed staff credential. Separation was not the control that failed; it was never the control.

## Decision

1. **The operator is a `users` row** with `role = "super_admin"`, in a reserved organization with a
   fixed id (`superadmin/seed.py`). Same sessions, same password rules, same optional second factor,
   same audit log as any customer. The role grants nothing inside any organization.
2. **It is seeded from the environment**, `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD`, at API boot and
   re-hashed every boot, so rotation is an edit and a restart. No screen and no request can create it.
   Both unset — a self-hosted install — seeds nobody.
3. **The admin routes are part of the customer API**, `/api/v1/admin/…`, each behind one named policy
   (`MayAdministerThePlatform`). A route that forgets it fails `test_authorization_coverage.py`, like
   every other mutating route.
4. **Step-up re-authentication replaces the origin boundary.** An admin request is refused unless the
   session was re-verified — password or TOTP — within the last 30 minutes. The stamp lives on the
   session record, so signing out revokes it and a session cannot outlive it. This is GitLab's "Admin
   Mode", and it is the condition on which this reversal is defensible: without it, do not ship it.
5. **The admin screens are console screens**, inside the same shell, offered in the sidebar to the
   operator alone.
6. **Support view is unchanged** from ADR-016 §4: an owner's time-boxed consent, read-only, capped at 30
   minutes, every page audited.

## Consequences

- **An XSS in the console can now reach platform powers**, within the step-up window, if one exists and
  the operator has stepped up. That is the price, stated plainly. It is bounded by 30 minutes, by the
  audit log, and by the console's own CSP.
- One app, one cookie, one front-end, one deployment. `staff_accounts`, `nexovue_admin_api`, the
  `admin-api` service, `admin.html` and the `just staff` command are deleted in the same change.
- **The mandatory second factor is gone.** `staff_accounts` could not exist without one; a `users` row
  can. The operator should enrol one — the step-up accepts a TOTP code — but nothing forces it. This is
  the largest thing lost, and it is the first thing to fix when the platform holds customer data.
- The cross-tenant admin queries now live beside every tenant-scoped query, where the structural tests
  are stricter by default. Each keeps a named exemption with its reason rather than being hidden.
- `user_role` gained an enum value, which Postgres cannot remove. The down migration says so.

## Revisit when

**More than one person holds platform powers.** One operator is what makes this proportionate: there is
no least privilege to express among one person, and no staff directory to manage. A second means roles
within the platform surface, a mandatory second factor, and quite possibly the separate host ADR-016
described — which is why that record is superseded rather than deleted.

## Rejected

- **Keeping the separate app** — the reason for this record.
- **A `super_admin` role with no step-up** — it makes an ordinary console session, and any XSS riding
  one, the whole boundary.
- **A separate password for the admin area** — a second credential to steal, with none of the origin
  separation that justified the first one.

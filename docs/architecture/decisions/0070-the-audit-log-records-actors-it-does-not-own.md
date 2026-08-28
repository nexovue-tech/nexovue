# 0070 — The audit log records actors it does not own

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

Writing to `audit_log` began on 2026-08-28. Its shape assumed every actor is a row in `users` and
every event belongs to an organization, and three things it must eventually record fit neither
assumption.

`requirements.md` §F31 anticipated one of them precisely:

> **Schema hook, cheap now and expensive later:** … Writes made during an impersonated session need
> an impersonation context recorded on them; adding that field when audit entries are first written
> costs nothing, retrofitting it means backfilling a security record that cannot be reconstructed.

That moment was the day entries began, and it was nearly missed.

## Decisions

### A refused sign-in is recorded, and against an unknown address it records no address

`org_id` becomes nullable. A sign-in attempt against an address matching no account belongs to no
tenant, and a `NOT NULL` column meant those attempts could not be written at all — leaving brute
force against the login endpoint entirely invisible.

Two cases, deliberately different:

- **The address matches an account.** Recorded with its organization and user. Repeated failures
  against one real account are the signal a customer needs.
- **The address matches nothing.** Recorded with no organization, no actor, and **no address** —
  the target is the constant `unknown_account`. The row is a count, and counts are what reveal a
  sweep. Storing the address would collect personal data on people who are not our customers and
  build a list of addresses somebody once guessed.

### The refusal commits outside the request transaction

This is the one place in the control plane that commits for itself, against the rule in
`audit/service` that an entry joins the transaction of the action it records.

That rule exists so an action and its record are atomic. **A refusal has no action to be atomic
with** — nothing else happened — and the `UnauthenticatedError` that follows would roll the entry
back, which would leave exactly the attempts worth recording unrecorded.

### An actor may be named rather than referenced

`actor_label` names an actor who is not a row in `users`. `actor_user_id` is already nullable and
already means *"the system did it"*, so our own staff impersonating a customer under F31 would have
been indistinguishable from an automated action — the most security-sensitive entries in the table
would have been the least attributable.

### Impersonation context is carried now, before F31 exists

`impersonation_context` holds the impersonation session id on every entry written while one is open.
No code sets it yet. It is here because the table is empty enough today that adding it is free, and
because the record it protects cannot be reconstructed later. Independent guidance on impersonation
design arrives at the same field: flag every write during an impersonated session with the session
id, rather than building a separate action log.

### Revoking a key requires owner or admin

`USER_ROLES` has existed since the first migration and was enforced nowhere — any member of an
organization could revoke any of its keys, and revocation is irreversible and stops a project's
ingest. A `viewer`, the non-technical persona F26 introduces, could have stopped production.

This is not role enforcement in general, which belongs to F26. It is one destructive action that
should never have been available to everyone.

## Consequences

The audit log can now answer "who tried to get in and failed", which it could not before, without
holding addresses of non-users.

`ApiKeySummary` gained `key_hint` in the same change: the scheme plus the last four characters of the
stored prefix. A customer who finds a leaked key needs to know which row to revoke, and the list
previously showed nothing to match against. Four of the secret's twenty-two random characters are
disclosed; the audit log itself still records no part of a prefix.

The rollback path deletes entries with no organization before restoring `NOT NULL`. Those rows are
refused sign-ins against unknown addresses. The alternative is a downgrade that fails outright once
one has been recorded.

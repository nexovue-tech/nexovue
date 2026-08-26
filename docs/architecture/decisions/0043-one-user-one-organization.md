# 43. Identity — one user belongs to one organization

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

The alternative is many-to-many membership, which brings a join table, an organization switcher in
the UI, and organization-scoped sessions on every screen.

## Decision

One user, one organization; an organization has many users. One-to-many, so no join table —
`users.org_id` is a plain foreign key and email is globally unique. Someone working with two
companies uses two email addresses; accepted deliberately.

**Phase 1:** signup collects organization details plus one email, and that user becomes owner. One
transaction creates the organization, the owner, a `production` project and the first API key.

**Phase 2 (F26):** the user-management screen where the owner invites others. The schema supports
it now (`users.role`, invite columns), so nothing migrates.

## Consequences

Every session is unambiguously scoped, and no screen carries an organization selector. The cost is
paid by consultants and anyone genuinely working across two companies, who need two accounts —
which is why the trade is recorded rather than assumed. Because the schema already carries roles
and invites, Phase 2 adds a screen and no migration.

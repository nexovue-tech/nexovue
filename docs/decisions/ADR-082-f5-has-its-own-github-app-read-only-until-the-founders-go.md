# ADR-082: F5 has its own GitHub App, read-only until the founder's go

## Status

Accepted 2026-11-21: proposed with the F5 design (2026-11-02, *Proposed ADRs*, fourth) and approved
with it; filed with F5 Step 3, built against the simulated GitHub only (foundation item 28).

## Context

GitHub permissions belong to an App, not to a repository: an installation grants every repository it
reaches what the App asked for. F22's App holds `issues: write` and `metadata: read` for issue sync. A
suggested fix must read a service's code, and later (Step 7, behind the founder's written go, foundation
item 33) may write a branch and a draft. F22's App also keeps no OAuth client secret, which the person's
GitHub link (D6, D20) needs.

## Decision

1. **A second private App per install, "Nexovue suggestions"** (`connections.kind = 'github_fix'`), set up
   through F22's manifest flow and connection row, with `contents: read` and `metadata: read` only, no
   webhook, and a callback route for the account link.
2. **Its key and OAuth client secret are sealed** under `CONNECTION_SECRET_KEY` (`keys.SEALED_COLUMNS`:
   `connections.sealed_credential`, `connections.sealed_client_secret`); its grant is read hourly into
   `granted_permissions`, so no page view calls GitHub.
3. **Its client offers one method per route on a fixed host, and no write in Step 3**
   (`nexovue_scheduler/connections/fix_github.py`, `ROUTES`, `test_fix_github_client.py`). Write routes and
   the raised permissions come with Step 7.

## Consequences

Issue-sync repositories never gain code access, and a Jira customer can still use suggested fixes. One more
App in the customer's GitHub. F22 changed only in small, named places: its queries take a kind, its
overview lists tracker kinds only, and three helpers became public. Once raised, write still reaches every
repository the App is installed on; Nexovue's per-repository limit is its own code, not GitHub's, which the
screen says before anyone raises it.

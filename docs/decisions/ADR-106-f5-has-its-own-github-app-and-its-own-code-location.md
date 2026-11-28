# ADR-106: F5 has its own GitHub App and its own code location

## Status

Accepted with F5's design, approved by the founder on 2026-11-02 (design, *Proposed ADRs*,
fourth). Filed 2026-11-22 with F5 Step 9 (plan T9.9), as built in Step 3. Awaiting the
founder's review with Step 9's diff.

## Context

GitHub grants permissions to an App, not to a feature. F22's App (issue sync) holds issues only,
and F22's mapping of a service to one place may be a Jira project, not a repository.

## Decision

1. F5 creates a **second private App per install**, the *Nexovue suggestions* App
   (`connections.kind = 'github_fix'`), starting at `contents: read` and `metadata: read`,
   raised to write only when an owner wants drafts.
2. A new table, `service_code_locations`, maps a service to a repository and folder, apart from
   F22's mapping.

## Consequences

Issue-sync repositories never gain code access, and a Jira customer can use F5. One more App in
the customer's GitHub. F22 needed almost no change. **Write still reaches every repository the
App is installed on,** so an owner's per-repository allowance (`fix_repository_settings`) is
Nexovue's limit, not GitHub's; the confirm and the customer's page say so, and tell owners to
protect `main` and release branches first.

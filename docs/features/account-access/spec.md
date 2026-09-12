# Account, organization, project, and session access

## Status

**Verified current-state baseline.** This is not the future RBAC specification; membership and roles
remain unbuilt under F26.

## Implemented behavior

- Signup creates an organization, owner user, first project, and initial secret API key in one
  control-plane transaction.
- Users can sign in/out, view the signed-in identity, verify email, request and complete password
  recovery, change profile details/password, and invalidate sessions.
- Session state is Redis-backed; password reset ends sessions it did not issue.

## Out of scope / not implemented

Inviting members, roles, owner-viewer accounts, and organization membership management are not
implemented.

## Code evidence

`apps/api/src/nexovue_api/signup/router.py`, `auth/router.py`, `auth/recovery.py`,
`profile/router.py`, `auth/cookies.py`, and `apps/web/src/features/auth/` implement this flow.
Migration `0019_a_password_reset_ends_every_session_it_did_not_issue.py` records the session-reset
rule.

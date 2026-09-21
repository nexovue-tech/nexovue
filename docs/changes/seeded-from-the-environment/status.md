# Status: seeded-from-the-environment

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- `FIRST_ORGANIZATION_NAME`, `FIRST_OWNER_EMAIL`, `FIRST_OWNER_PASSWORD`, seeded at boot through
  signup's own `provision_tenant` so the first tenant gets every default a real customer gets.
- **Read once**, unlike the operator's, which is rewritten every boot. Held by a test that changes
  the password the way a customer would and then restarts the seed.
- **The sign-up form's password minimum applied to both seeded accounts.** Neither had it, and both
  are accounts nobody could create through a screen.
- Both Compose targets carry the variables: passed through on the hosted replica, demanded on-prem.
- `docs/operations/first-accounts.md` — which account applies to whom, what gets created, and what
  each start-up log line means when nothing was.

## Next action

Founder: review the diff and say **push**.

## Open decisions

- **Whether a self-hosted install should also close signup.** This gives such an install a way in
  that does not depend on open signup, but it does not *close* it — production-readiness blocker 3
  stands, and on a self-hosted deployment it means a stranger can still create an organization
  inside somebody else's install. The two belong together and only one is built.

## Blocked

Nothing.

## Risks

- **Correct and unrehearsed.** The on-premise Compose target declares itself a skeleton and has
  never been run, so nothing here has met a real self-hosted install. The documentation states this
  rather than implying a tested procedure.
- **The first-run check is "no customer organization exists".** It is a whole-table count, so an
  install that somehow held an organization before its first boot would seed nothing and say so in
  the log. That is the safer direction, but it is a decision rather than an obvious truth.
- **`already_in_use` is faked in five of the tests.** The development database holds the dev tenant
  and every organization the isolation suites ever created, so it can never be a first run, and
  emptying it inside a transaction would cascade across thirty tables. The two halves are tested
  separately and both are real: what "first run" means is asserted against the real rows, and what
  happens on one is asserted with the emptiness faked. The fixture says so at the point of faking.

## Validation

- `just check`, exit 0: **4143 Python passed, 2 skipped**; **1335 web** (123 files); **120** browser
  SDK; Go, format, lint, `mypy` (574 files) and the contract checks clean.
- **Seen red before green**, three times:
  - the read-once rule, by making it rewrite the password like the operator's — *"a restart reset
    the customer's password to the one in the environment"*;
  - both password minimums, by disabling the guard.
- **Grafana's behaviour was checked rather than assumed** before copying it: its
  `GF_SECURITY_ADMIN_PASSWORD` is read when the initial admin is created and changing it later does
  not change the password.
- **The tenancy guard refused the first draft**, and it was right to: a pre-check asking whether any
  user anywhere held the address named no tenant. Fixed by deleting the query rather than by adding
  an exemption — `provision_tenant` already owns that uniqueness rule, and a second copy of it
  here could drift from the first.
- **The password minimum was read from the code** (`auth/schemas.PASSWORD_MIN_LENGTH` is 8), not
  guessed — a first draft of the Compose file said twelve.

## Specification impact

None. No specification describes how an install gets its first account; `docs/operations/
first-accounts.md` is now where that is written down.

## Documentation impact

`docs/operations/first-accounts.md` (new), `docs/operations/deployment.md`,
`docs/operations/README.md`.

## Last updated

2026-09-21

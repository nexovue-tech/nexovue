# Change: signup-closed-by-default — a deployment that says nothing gets a closed door

## Classification

Security — a default that fails open.

## Current behavior and problem

Production-readiness **blocker 3** read: *"Signup is public. Anyone who finds the signup page
creates an organization and sends telemetry to our servers at our cost… An invite-only beta cannot
be enforced."* Its evidence was `auth/policy.py:5`, *"there is no invite flow"*, and *"no setting
closes signup"*.

**Both had been untrue for days.** F26 shipped invitations; F31 shipped `SIGNUP_ENABLED`,
`require_signup_open` and `GET /system/signup`, and the hosted Compose file already passed
`:-false`. The blocker outlived what it described — the second such stale record found today, after
a fixed commit-ordering race was reported as pending from a status file.

So the work was not what the blocker said. **What was actually wrong was the default.**

`signup_enabled` defaulted to **True**, and the reason was written at the declaration: *"On for
self-hosted installs; off for nexovue's cloud."* That was correct while a self-hosted install had no
other way in — closing signup there would have left it with no account at all.

`docs/changes/seeded-from-the-environment/` removed that reason earlier the same day. A self-hosted
install now gets its owner from `FIRST_OWNER_EMAIL`, and the on-premise Compose file set
`SIGNUP_ENABLED` nowhere — so it inherited **open**. A stranger who found the page would create an
organization inside somebody else's deployment and send telemetry at their expense.

A security control whose default is "open" is a control that protects whoever remembered it.

## Intended behavior

**Closed unless a deployment opens it**, and every target states which it wants rather than
inheriting one:

| Target | Setting | Why |
|---|---|---|
| development (`docker-compose.yaml`) | `"true"` | The e2e suite and `test_tenant_isolation_live` create tenants through the public endpoint, the way a customer would |
| hosted (`docker-compose.app.yaml`) | `${SIGNUP_ENABLED:-false}` | A customer pays on the website and is onboarded by staff (ADR-008) |
| self-hosted (`docker-compose.onprem.yaml`) | `${SIGNUP_ENABLED:-false}` | The owner comes from `FIRST_OWNER_EMAIL`; teammates arrive by invitation |

Overridable everywhere: an install that wants to be a small multi-tenant service of its own is a
legitimate thing to want. It is just not what anybody should get without asking.

## Affected specifications and architecture

None. F31's FR30 and AC31 already specify the switch; this changes which way it points when nobody
says. `docs/operations/production-readiness.md` clears blocker 3 and corrects the row whose evidence
was stale.

## Risks and rollback

- **A test that drives signup must now say so.** `test_signup.py` did not, and failed — correctly.
  It sets `SIGNUP_ENABLED=true`, which is the same sentence `docker-compose.yaml` says. Any future
  suite that posts to `/signup` will fail the same way, which is the intended cost.
- **An operator who wanted an open install and set nothing gets a closed one.** That is the point,
  and the direction of the mistake is the safe one: a closed door is visible on the first attempt,
  an open one is discovered by whoever walks through it.
- **This does not close the door on an install already running.** The setting is read at start-up;
  an existing deployment stays as configured until it restarts.
- Rollback is one word in `settings.py`.

## Plan and validation

1. `settings.py` — `signup_enabled: bool = False`, and the reasoning rewritten, since the old one
   named a constraint that no longer exists.
2. `deploy/compose/docker-compose.yaml` — `"true"`, with why development needs it.
3. `deploy/compose/docker-compose.onprem.yaml` — `:-false`.
4. `apps/api/tests/test_signup_switch.py` — the default is closed, and the suite's own docstring
   corrected where it said a self-hosted install keeps signup open.
5. `apps/api/tests/test_signup.py` — states that it wants signup open.
6. `docs/operations/production-readiness.md` — blocker 3 cleared, the stale row corrected.

## Status

See `status.md`.

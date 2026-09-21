# Change: seeded-from-the-environment — a self-hosted install can reach its own console

## Classification

Feature — a new capability, delivered as a change record rather than through the
specification gate by the founder's instruction ("now do it"). Recorded here so the choice is
visible rather than implied.

## Current behavior and problem

nexovue has two kinds of administrator and only one of them can be created before anyone signs in:

- **nexovue's operator** — `super_admin`, seeded at boot from `SUPER_ADMIN_EMAIL` and
  `SUPER_ADMIN_PASSWORD`. Built, works.
- **a customer's owner** — the person who administers an organization. Created **only by signing
  up**.

On the hosted service that is right: customers arrive through signup. **On a self-hosted install it
is a dead end.** The person who installed nexovue is the first person in the system, so there is
nobody to invite them, and the only way in is to leave signup open to the internet — which is
production-readiness blocker 3, and which on a self-hosted install means anyone who finds the page
creates an organization inside somebody else's deployment.

`seed_super_admin`'s own docstring already names the gap without filling it: *"Absent settings seed
nothing, which is the right answer for a self-hosted install — it has no nexovue to administer
it."* True, and it leaves the install with no account at all.

## Intended behavior

A mirror of the operator's seed, for the customer's own first account.

```
FIRST_ORGANIZATION_NAME   optional, defaults to "My organization"
FIRST_OWNER_EMAIL         the address that signs in
FIRST_OWNER_PASSWORD      at least 8 characters, as the sign-up form demands
```

On the first start, `provision_tenant` — **signup's own path** — creates the organization, the
owner, a `production` project, an ingest key, a deploy webhook secret, the owners channel and the
default alert rule. A bespoke path here would make the one tenant on a self-hosted install the one
tenant whose defaults nobody tested.

**Three deliberate differences from the operator's seed:**

1. **It is read once.** The operator's password is rewritten every boot, so rotation is a restart.
   This one is not. After the first start the account is the customer's: they change the password in
   the console, and a restart that reset it to whatever is still in a compose file would take their
   own account away from them and pin it to a file on disk for ever. This is the convention a
   self-hoster expects — Grafana's `GF_SECURITY_ADMIN_PASSWORD` behaves the same way.
2. **It refuses an install that is already in use.** Any customer organization means this is not a
   first run. The platform organization does not count — it holds nexovue's operator and no
   customer. A deleted organization *does* count: recreating one the customer deleted would be the
   installer overruling them.
3. **The ingest key is not logged.** It is a live credential. The owner signs in and mints one under
   Settings → Ingest keys.

**And one rule applied to both:** the sign-up form's eight-character password minimum. Nothing stood
between a compose file and the password hash for either account — `provision_tenant` and
`seed_super_admin` both take a plain string — so the two accounts nobody could create through a
screen were the two that could be weaker than one that could.

## Affected specifications and architecture

None directly. `docs/operations/deployment.md` gains a section and
`docs/operations/first-accounts.md` is new. The on-premise Compose target demands the two variables
with `:?`, because an install that starts with no account has no way to reach its own console and
start-up is a better time to learn that than after the stack is up.

## Risks and rollback

- **Unrehearsed.** The on-premise Compose target still declares itself a skeleton and has never been
  run, so this is correct-and-untested against a real self-hosted install. The documentation says so
  rather than implying otherwise.
- **A password in an environment file.** Readable by whoever can already read the host's
  configuration — the same person who could read the database — so it grants nothing they did not
  have. The documentation tells the owner to change it and remove the variables after the first
  sign-in.
- **An install that sets both `SUPER_ADMIN_EMAIL` and `FIRST_OWNER_EMAIL` to the same address** gets
  the operator and a warning, not a crash. The operator is seeded first, deliberately, so the
  refusal happens in that direction.
- Rollback is unsetting the variables; nothing is migrated and nothing else changed.

## Plan and validation

1. `settings.py` — three settings, with the read-once reasoning at the declaration.
2. `signup/first_run.py` — the seed, the first-run test, the taken-address refusal, the password
   minimum.
3. `superadmin/seed.py` — the same password minimum.
4. `lifespan.py` — called after the operator, in its own transaction so one failure cannot roll back
   the other.
5. `deploy/compose/docker-compose.{app,onprem}.yaml` — passed through on the hosted replica,
   demanded on-premise.
6. `docs/operations/first-accounts.md` — both accounts, which applies to whom, and what to do when
   nothing was created.

## Status

See `status.md`.

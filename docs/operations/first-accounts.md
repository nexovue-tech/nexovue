# The first accounts: who exists before anyone signs in

Two accounts in nexovue are created from **environment variables** rather than
through a screen, because in both cases there is nobody yet who could use a
screen to create them. They are mirror images of each other, and an install uses
**one or the other, never both**.

> **Status.** Built, tested, and rehearsed on 2026-10-28: `nexovue quickstart` created the first
> owner from `FIRST_OWNER_*` and that owner signed in. `nexovue install` asks for the email and
> generates the password if none is given ([install](../self-hosting/install.md)). Not yet run on a
> real server.

---

## Which one applies to you

| You are | You set | You get |
|---|---|---|
| **Running nexovue yourself** (self-hosted / on-premise) | `FIRST_OWNER_*` | Your own organization, and the owner account you sign in as |
| **nexovue, operating the hosted service** | `SUPER_ADMIN_*` | The operator account that administers customers |

A self-hosted install sets **no** `SUPER_ADMIN_*`. There is no nexovue inside
your install to administer it, and with those unset the whole platform area is
unreachable rather than merely hidden.

The hosted service sets **no** `FIRST_OWNER_*`. Customers there arrive through
signup, and an organization conjured at boot would belong to nobody.

---

## Self-hosted: your first account

Set these before the first start:

```bash
FIRST_ORGANIZATION_NAME="Acme Inc"          # optional; defaults to "My organization"
FIRST_OWNER_EMAIL="you@acme.example"        # the address you will sign in with
FIRST_OWNER_PASSWORD="at least 8 characters"
```

On the first start, nexovue creates **exactly what a customer who signed up
would get** — through the same code, so your install is not the one tenant whose
defaults nobody tested:

- your organization, on the **free** plan
- your user, with the **owner** role and the address already verified
- a project called **`production`**
- an **ingest key**, for sending telemetry
- a **deploy webhook secret**
- a notification channel, **"Organization owners"**
- two alert rules wired to that channel: **"Any check down"**, and **"Critical
  problems"**, which sends every critical anomaly-detection finding

That channel and those rules are the point: the first check that goes down, and the
first critical finding, email you, with nothing to set up.

### Three things to know

**It runs once.** The variables are read on the first start and never again.
Change your password in the console afterwards — a later restart will **not**
reset it. This is deliberate: after the first start the account is yours, and a
restart that reset your password to whatever is still in a compose file would
take your own account away from you.

You can remove the two variables from your environment once you have signed in.

**Your ingest key is not printed.** It is a live credential, and a secret written
to a log file is a secret. Sign in and create one under **Settings → Ingest
keys**, which is what that screen is for.

**The password rule applies.** Shorter than eight characters and nothing is
created — the log says so and the install comes up with no account, rather than
with a weak one. Fix the variable and restart.

### If nothing was created

The start-up log says which of these it was:

| Log line | What happened |
|---|---|
| nothing at all | Both variables were empty. Nothing was attempted |
| *"this install already has one"* | Not a first run — an organization already exists here. Sign in with the account it was created with |
| *"shorter than the minimum the sign-up form enforces"* | The password is under eight characters |
| *"that address already signs in here"* | Some other account already uses that address — often the same address set for `SUPER_ADMIN_EMAIL` too |
| *"the first organization could not be seeded"* | Something else failed. The API still starts: an API that will not boot is worse than an install whose first account has to be made another way |

An install that already has a customer organization is **not** a first run,
nothing is created, and the log says so. Deleting your organization and restarting does not bring it
back — that would be the installer overruling a decision you made.

---

## nexovue's own operator

```bash
SUPER_ADMIN_EMAIL="operator@nexovue.com"
SUPER_ADMIN_PASSWORD="at least 8 characters"
```

This creates a reserved organization holding one `super_admin` user and nothing
else: no projects, no telemetry, no bill. The role grants **nothing** inside any
customer organization and everything on the platform surface — and only while
the session has been re-authenticated in the last 30 minutes.

**Like the account above, this password is read once**, when the operator is
created. Rewriting it on every start undid a password changed in the console and
un-removed a removed operator (group 9, A3). Change it in the console, or reset
it by email. With two-factor sign-in enrolled, admin mode takes a code, never the
password alone.

The same eight-character minimum applies, for the same reason: nothing stands
between a compose file and the password hash, and this is the account that can
see every customer.

---

## Why environment variables at all

The most powerful account in a system should not be creatable by anything a
request can reach. Both of these exist before there is any way in:

- a self-hoster has nobody to invite them — they are the first person in the
  system, and the alternative is leaving signup open to the internet
- nexovue's operator must not be creatable by a signup form, however well guarded

An environment variable is readable by whoever can already read the host's
configuration — which is the same person who could read the database. It grants
nothing they did not already have.

## Related

- `docs/changes/super-admin-in-the-console/` — why the operator is a role and a
  re-authentication rather than a separate application
- `docs/changes/seeded-from-the-environment/` — this change
- `docs/operations/production-readiness.md` — what still stands between this and
  a rollout

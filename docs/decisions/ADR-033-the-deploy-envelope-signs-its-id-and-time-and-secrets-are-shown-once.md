# ADR-033: The deploy envelope signs its id and time, and secrets are shown once

## Status

Accepted 2026-11-02 with the F28 design ([`design.md`](../features/deployment-release-tracking/design.md),
*Proposed ADRs*, the third), which the founder approved; filed with plan Step 2, 2026-11-08, which builds it
(Postgres migration `0057`, renumbered at commit if needed).

## Context

The deploy webhook (`POST /api/v1/deploys/webhook/{project_id}`) is the only unauthenticated write in the
control plane. Before Step 2 it used GitHub's signature scheme as it stands:

- **GitHub's signature carries no timestamp,** and the delivery id travelled in the unsigned
  `X-GitHub-Delivery` header. A captured, correctly signed body could be sent again with a new header id and
  was stored as a new deploy, forever.
- **Secrets were stored in the clear and shown on every read** of the settings, so a database read or a stolen
  admin session yielded every project's signing secret, and each read had to be audited as "viewed".

## Decision

1. **Each item carries its own `delivery_id` and `deployed_at` inside the signed body** (F28 D6, FR6). A repeated
   id for the project answers with the stored deploy and changes nothing, in a race too (FR7). No header id is
   read.
2. **The claimed time is bounded:** at most 5 minutes ahead of our clock and 7 days behind it (FR8). That ends
   any replay of a capture whose row was deleted, and it means past deploys older than a week cannot be
   imported, which the setup page says.
3. **One body for one service or a hundred** (`{"deploys": [...]}`, 1 to 100 items, 512 KiB), checked in every
   way before anything is stored; one bad item refuses the whole body, naming the item and field, and a signed
   refusal is recorded for the Deploys section (FR9, FR10, D22). `"dry_run": true` checks and stores nothing
   (FR11, D21).
4. **Secrets are sealed and shown once** (D7, FR2, FR3): `project_webhook_secrets.sealed_secret`, AES-GCM under
   `CREDENTIAL_ENCRYPTION_KEY`, which only the API holds, with purpose `deploy-webhook` and owner
   `project_id:secret_id`, so a value copied to another row fails to open. The id is read from `uuidv7()` before
   the insert so the column can be NOT NULL. It is in `SEALED_COLUMNS`, so key rotation re-seals it. Only the
   creation response carries the value; afterwards a secret shows its last four characters, when it was created
   and last used. A project holds at most 5 live secrets, since every request opens each one (FR4).
5. **The hourly limit is counted after the signature, for new items only,** in Postgres, and a batch that would
   cross it is refused whole and charges nothing (FR13).

## Consequences

- A lost secret means minting another; signup no longer mints one, since nobody could ever be shown it.
- `deploy.webhook_secret_viewed` is gone, and `deploy.recorded` per deploy is replaced by one
  `deploy.batch_recorded` per request, naming the secret, count and first deploy.
- **Native GitHub delivery, if added later,** must use GitHub's own header id on its own path, and is weaker on
  replay, which must be stated then. F22's webhook must do the same, with a compensating dedupe on the signed
  payload's event id.
- Each request costs up to 5 secret openings and 5 HMACs over at most 512 KiB: measured at 1.8 ms median on
  the development machine (status, *Step 2*), bounded by 60 requests a minute per address.
- Recipes sign with `python3` reading the secret from the environment, since `openssl dgst -hmac` takes the key
  as a process argument.

## Rejected

- **A separate signed timestamp header:** the signed id already closes the hole, and the bound on the signed
  `deployed_at` covers a deleted row; a second timestamp adds nothing.
- **Sealed and re-readable:** keeps the "viewed" audit and the exposure of a stolen admin session, for the one
  convenience of reading a secret twice.
- **Accepting the GitHub header id:** unsigned, so it is what the replay changed.

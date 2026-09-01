# Tenancy & Control-Plane Data Model

> **Type:** Architecture reference · **Status:** Decided 2026-08-26, not yet implemented
> **Companion docs:** `ingestion-envelope.md` (what carries these ids) · `system-architecture.md` (storage) · `../delivery/phase-1-plan.md` (build order)

## 1. The hierarchy

```
Organization  ─── tenant_id     billing · membership · isolation boundary
    └── Project ── project_id   ingest keys · retention · sampling · quotas
          └── services          DISCOVERED from telemetry, never configured
```

**Organization = `tenant_id`.** The isolation boundary that sits first in every ClickHouse `ORDER BY` and is injected into every query by the repository layer. One per company.

**Project = an environment.** The convention is one project per environment — *Acme production*, *Acme staging*. That is the boundary where retention, sampling, quotas and ingest keys genuinely belong, and it keeps staging noise out of production findings. Multiple applications inside one environment stay together deliberately: the service map is more useful when it shows the whole environment.

**Services are never configured.** `service.name` arrives on every span from the OTel SDK; services appear when telemetry appears. This is load-bearing for the ten-minute onboarding promise — a user who must register services before seeing data has already lost the ten minutes.

*Rejected:* Project = application with environment as a tag (Sentry's model). Better for companies running several unrelated apps, but staging and production then share retention, quotas and alerting, so staging noise reaches production findings unless every query remembers to filter it.

## 2. Identity

**An organization has many users. A user belongs to exactly one organization.** One-to-many, so there is no join table — `users.org_id` is a plain foreign key, and email is globally unique.

Someone who genuinely works with two companies uses two emails. Accepted deliberately: the many-to-many model costs a join table, an org-switcher in every screen, and org context on every session, to serve a case our three personas do not have.

### Phase 1 scope

Signup collects **organization details plus one email**, and that user becomes the organization's owner. That is the whole of identity in Phase 1.

Signup creates, in one transaction:

| Created | Value |
|---|---|
| Organization | from the signup form |
| User | the signup email, `role = owner` |
| Project | `production`, by default |
| API key | one live key for that project |

The user never sees the words "organization" or "project" during onboarding. They see two environment variables.

**Deferred to Phase 2 (F26):** the user-management screen where an owner invites additional users into the organization. The schema supports it now — `users.role` and the invite columns exist — so nothing needs migrating when that screen is built.

## 3. Schema

```sql
organizations   id uuid pk · slug · name · plan · created_at · deleted_at
users           id uuid pk · org_id fk · email unique · password_hash · name
                · role · email_verified_at · invited_by · invited_at · created_at
projects        id uuid pk · org_id fk · slug · name · retention_days
                · sampling_config jsonb · created_at · deleted_at
api_keys        id uuid pk · org_id fk · project_id fk · name · key_prefix
                · key_hash · last_used_at · created_at · revoked_at
site_keys       id uuid pk · org_id fk · project_id fk · name · public_key
                · allowed_origins text[] · last_used_at · created_at · revoked_at
checks          id uuid pk · org_id fk · project_id fk · type · target
                · interval_seconds · config jsonb · enabled · created_at
integrations    id uuid pk · org_id fk · project_id fk · kind
                · config_encrypted bytea · status · last_sync_at
deploy_events   id uuid pk · org_id fk · project_id fk · version · service_name
                · deployed_at · source · metadata jsonb
usage_daily     org_id fk · project_id fk · day · spans · logs · metric_points · bytes
sessions        id uuid pk · user_id fk · expires_at · created_at
audit_log       id uuid pk · org_id fk · actor_user_id · action · target · at
```

`role` is `owner | admin | member | viewer`. The `viewer` role is what the P3 non-technical owner gets in Phase 2.

### Five decisions inside this schema

**1. `org_id` is denormalized onto every table**, even where it is reachable through `project_id`. Deliberate: it puts the tenant filter one column away everywhere, so a single rule — *every query filters `org_id`* — holds identically on the Postgres and ClickHouse sides. One rule, enforced in one repository layer, on both stores.

**2. A key's kind is the table it lives in.** `api_keys` holds the secret,
server-side kind; `site_keys` holds the public, browser-side kind, stored in the
clear because it ships inside the page that carries it. Neither table holds both,
so the receiver's server-door lookup cannot return a public key even if a filter
were forgotten — see [ADR 0086](decisions/0086-a-keys-kind-is-the-table-it-lives-in.md).

**3. API keys hash with SHA-256; passwords hash with argon2.** Argon2 is *designed* to be slow, which is right for a password typed by a human and catastrophic for a credential verified on the ingest path thousands of times per second. API keys are high-entropy random strings, so key stretching buys nothing against brute force. Lookup by `key_prefix`, verify by hash, cache the result in Redis with invalidation on revoke.

**4. One key lookup resolves both ids.** The `api_keys` row carries `org_id` and `project_id`, so the receiver's hot path is a single cached lookup rather than a key→project→org chain.

**5. UUIDv7 for primary keys** (RFC 9562) — time-ordered, so index locality behaves like a sequence while remaining globally unique and safe to expose. Stored native in Postgres, rendered as canonical strings in ClickHouse's `tenant_id` / `project_id` columns.

### API key format

```
nvk_live_<22 chars base62>
└──┬───┘ └────────┬───────┘
 prefix        secret (shown once, never stored)
```

`key_prefix` stores the first 16 characters in plaintext for the lookup — the nine-character scheme plus seven of the secret — and only the hash of the whole key is persisted. Seven rather than three: the prefix is unique, and three random characters gave 238,328 values, a coin-flip collision at roughly 500 live keys, which would fail one customer's signup because of another customer's key. Multiple live keys per project so rotation never requires downtime.

## 4. Deletion

Organizations and projects soft-delete (`deleted_at`). Telemetry purge is asynchronous, since it means dropping ClickHouse partitions and objects from the cold tier. The GDPR-facing delete/export surface is F27 in Phase 2; the schema supports it now.

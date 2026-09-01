# 0086 — A key's kind is the table it lives in, and the public one is not hashed

- **Status:** Accepted
- **Date:** 2026-08-31
- **Enables:** [P1-38](../../delivery/phase-1-plan.md) — public site keys, and the browser door M5 builds on
- **Builds on:** [ADR 0081](0081-a-leaked-key-has-a-way-out-and-last-used-is-throttled-by-the-cache.md), which built the secret key's create, revoke and `last_used_at` one day earlier

## Context

**M5 needs a credential that is the opposite of the one we have.** Every ingest
key today is secret: twenty-two random characters, stored as a SHA-256 hash,
compared in constant time, disclosed exactly once, and carrying full ingest
rights for a project. P1-38 needs a key that is *printed in a web page* — read by
anyone who views source, shipped to every visitor, and impossible to keep.

**The brief for this work assumed both kinds would share `api_keys` and asked for
a discriminator column. The code already disagreed.** `site_keys` — `id`,
`org_id`, `project_id`, `public_key` unique, `allowed_origins text[]`,
`created_at`, `revoked_at` — has existed since
[migration 0001](../../../apps/api/migrations/versions/0001_control_plane_schema.py),
is declared in `models.py`, and is drawn in
[tenancy-and-data-model.md §3](../tenancy-and-data-model.md). **Nothing has ever
read or written it.** Verified: `select count(*) from site_keys` on the dev
database returns `0`, and a repository-wide search finds the model, the migration
and the doc — no query, no route, no receiver code.

That is the same shape of gap ADR 0081 found in `api_keys.last_used_at`: a
schema that recorded an intention nobody implemented. Adding `kind` to `api_keys`
would have created the duplication rule 11 exists to stop, and then required
deleting a correct design to make room for a weaker one.

## Decision

### 1. The kind is the table, not a column

`api_keys` holds the secret kind. `site_keys` holds the public kind. Nothing
holds both, at any layer:

| layer | secret | public |
|---|---|---|
| table | `api_keys` | `site_keys` |
| API route | `/api/v1/api-keys` | `/api/v1/site-keys` |
| service routine | `create` / `revoke` / `list_for_project` | `create_site_key` / `revoke_site_key` / `list_site_keys_for_project` |
| receiver type | `auth.Authenticator` over `auth.KeyReader` | `auth.SiteAuthenticator` over `auth.SiteKeyReader` |
| key format | `nvk_live_<22 base62>` | `nvp_live_<22 base62>` |

**The decisive argument is one line of Go.** The receiver's server door ends with

```go
if subtle.ConstantTimeCompare([]byte(HashKey(presented)), []byte(stored.KeyHash)) != 1 {
	return Identity{}, ErrUnknownKey
}
```

A discriminator column makes that comparison *conditional* — `if kind == secret`
— because a public key has no hash to compare. The failure the brief named,
"a public key accepted wherever a secret one is expected", is exactly what a
wrong branch there produces: a value published in every page's source
authenticating the full server ingest path. With two tables the comparison stays
unconditional and the server door's `FROM api_keys` **cannot** return a public
key. That is a structural invariant rather than a discipline one, and this
codebase has already chosen structure over discipline once for the same reason —
ADR 0081 rejected a free optimisation because "a security property was hiding
inside a performance decision".

A discriminator would also have cost `api_keys.key_hash` its `NOT NULL`,
weakening the secret table's own invariant to accommodate the kind that does not
need it.

**The kind is still end-to-end, and named.** Every response carries a fixed
`kind` — `Literal["secret"]` and `Literal["public"]`, per class, not a settable
field — so the console reads the kind off the value rather than inferring it from
the route it called. The receiver's `Identity` carries `auth.Kind` the same way.
The label travels with the value, exactly as `SecretStr` makes redaction travel
with the secret.

### 2. What each kind may do

A **public** key may accept browser telemetry for its own project. That is the
whole list. It may not: reach another project or organization; read any stored
telemetry; touch anything in the control plane; or open the server OTLP doors —
`Identity.MayIngestServerTelemetry()` is false on it, and both predicates are
written as allow-lists so a zero-value or future kind is refused rather than
admitted by a missing case.

A **secret** key keeps what it had, and gains one refusal: it may not open the
browser door. `MayIngestBrowserTelemetry()` is false on it, and
`checkSiteKeyFormat` rejects `nvk_live_…` before any lookup runs. A product that
accepted a server key from a page would be teaching customers to put one there.

`allowed_origins` restricts a public key further, and **is not an authentication
boundary** — see the research below. It is recorded as abuse mitigation in the
model, in the `Identity` field comment, and in the console copy.

### 3. The public key is not hashed

Stored whole, in the clear, uniquely indexed. Three reasons, in order of weight:

1. **A hash at rest protects a value that is already published.** The threat a
   key hash defends against is a database dump disclosing credentials. This
   credential is in the HTML of every page that uses it.
2. **It must be readable back.** "Shown once" is meaningless for a value that
   lives in a customer's page source, and a customer redeploying their site needs
   the key again. Hashing would make the console unable to answer a question the
   customer can answer by pressing Ctrl-U.
3. **It buys a better lookup.** The secret path indexes a 16-character *prefix*
   and then compares a hash, a two-step dance that exists only because the secret
   half must not be stored. With nothing to withhold, the browser door is one
   exact-match probe on a unique index — no prefix split, no hash computation, no
   constant-time compare.

**Removing a comparison from a credential path deserves suspicion, so it is
stated rather than assumed:** the constant-time compare exists to stop a guessed
key being reconstructed one hash byte at a time. There is nothing to reconstruct
here, because the value is already public. Reaching the browser door's success
line already means the caller holds the whole key.

### 4. The public key may travel in a query string; the secret one may not

**`navigator.sendBeacon` cannot set request headers at all.** It is also the only
way a browser SDK can flush telemetry as a page unloads — which is precisely when
a visitor's session is most likely to be lost — so a header-only browser door
would silently drop the most valuable exports. Sentry and Datadog both answer
this by allowing the key in the query string.

So the two doors read from different places, and the asymmetry is the decision:

| door | header | query string |
|---|---|---|
| server (`nvk_live_`) | yes | **never** |
| browser (`nvp_live_`) | yes | yes, `?nexovue_site_key=` |

A secret key in a URL is a secret key in an access log, in a proxy's log, and in
anything that records request lines — permanently, in plaintext, in systems whose
retention nobody chose for this purpose. That is unacceptable regardless of how
convenient it would be. A **public** key in a URL discloses nothing that viewing
the page's source did not already disclose.

The rule lives in `auth/presented.go`, next to the credential contract rather
than in whichever handler happens to call it. `KeyFromRequest` reads the header
and nothing else. `SiteKeyFromRequest` reads the header first, then the query
parameter — **and returns a query-string value only if it carries the public
scheme**. A secret key pasted there is therefore *invisible* to the browser door
rather than merely rejected by it, so a caller that logs what it read cannot log
one. That defence is load-bearing: `logging.RedactCredentials` only rewrites
`user:pass@host` in connection strings and would **not** catch a key in a URL.

The parameter is named `nexovue_site_key` — for the kind it carries, so pasting a
secret key into it reads wrong before it fails.

### 5. What happens to existing rows

Every `api_keys` row is secret, and [migration 0014](../../../apps/api/migrations/versions/0014_site_keys_are_the_public_kind.py)
**says so rather than defaulting silently** — as a `COMMENT ON TABLE` on both
tables, naming which kind lives where and pointing at this ADR. There is no
backfill and no default to choose, because the statement is true of the table
rather than of a column; a reader of the schema is told anyway.

0014 also makes `site_keys` usable: `name` (a list of five keys nobody can tell
apart is five keys nobody dares retire), `last_used_at`, and `public_key`
narrowed from the placeholder `varchar(64)` to `varchar(31)` — `nvp_live_` plus
twenty-two characters, exactly. `name` is added `NOT NULL` through a server
default that is dropped immediately; the default is expected to name **zero**
rows, and it was confirmed to name zero.

### 6. How the two appear on screen

A customer who confuses them either publishes a server key or wastes an afternoon
on a browser key their backend rejects. So the split is carried by every signal
available, not by a label:

- **Two sections with inverted framing.** The secret half's copy is about
  protecting a secret; the public half's says plainly that this key is *meant* to
  be public, that anyone viewing source can read it, and that it can do one thing.
- **A `Secret` / `Public` badge on every row**, so a row read in isolation is
  labelled.
- **The public key is on screen in full, always.** No hint, no reveal, no "copy
  it now". Showing it plainly is the clearest statement that there is nothing to
  hide; the secret half keeps `key_hint` and the one-time disclosure.
- **Different actions.** The public half has no **Rotate** and no *"Revoke now —
  this key leaked"*, because a key that was never secret cannot leak. It has
  **Retire**, whose confirmation says explicitly: retire this when the site is
  gone or has moved, not because the value was seen — it was always visible.

`last_used_at`'s four-state reading is shared, with one honest difference: a site
key is public, so a stranger who read the page can make it look busy. That errs
towards "still in use" and never towards "safe to revoke", so it cannot mislead
the decision it informs, and the console words the public variant accordingly.

## Research

Four vendors were read before choosing. Three converge; one refuses the whole
category, which is worth recording.

**PostHog is the closest match and the only one where storage is observable.**
Its docs say the project token is "a **public**, client-side key" and that "it's
safe to expose in client-side code such as web apps, mobile apps, and CLI tools"
([feature-flags/installation](https://posthog.com/docs/feature-flags/installation)),
scoped so "it can only be used to send events and to evaluate flags"; the
personal key "(starts with `phx_`), however, should **NOT** be public"
([privacy](https://posthog.com/docs/privacy)). Because PostHog is open source,
the storage asymmetry is directly checkable in one codebase:
[`team.py`](https://github.com/PostHog/posthog/blob/master/posthog/models/team/team.py)
declares `api_token = models.CharField(max_length=200, unique=True, …)` — **plaintext,
uniquely indexed**, while
[`personal_api_key.py`](https://github.com/PostHog/posthog/blob/master/posthog/models/personal_api_key.py)
holds a `secure_value` hash plus a short `mask_value` for display. Same vendor,
public plaintext and indexed, secret hashed and masked. That is decision 3 above,
already shipped by somebody else.

**Sentry** dropped its secret half entirely: the DSN's secret key is "optional and
effectively deprecated", and DSNs are "safe to keep public because they only allow
submission of new events"
([dsn-explainer](https://docs.sentry.io/concepts/key-terms/dsn-explainer/)).
Publicness is carried structurally — the key sits in the userinfo position of a
URL meant to ship to clients — with no prefix at all.

**Datadog** states the rule most bluntly: "API keys cannot be used to send data
from a browser, mobile, or TV app", and a client token "must be used"
([api-app-keys](https://docs.datadoghq.com/account_management/api-app-keys/),
[javascript log collection](https://docs.datadoghq.com/logs/log_collection/javascript/)).
The client token "is only used to send data to Datadog"
([RUM data security](https://docs.datadoghq.com/data_security/real_user_monitoring/)).

**Honeycomb is the outlier and it is the one that argues against us.** It declares
*no* public key class. Ingest keys narrow scope — writing telemetry only — but
Honeycomb still says "you can access the value of your API key only while creating
it" for **all** keys, ingest keys included, and for browsers recommends a proxy:
"In production, we recommend running an OpenTelemetry Collector"
([manage-api-keys](https://docs.honeycomb.io/configure/environments/manage-api-keys/),
[javascript-browser](https://docs.honeycomb.io/send-data/javascript-browser/)).
Rejected for this product: a proxy is a component the customer has to run before
they can see a single web vital, which defeats the point of a script tag.

**Stripe supplies the prefix convention.** [docs.stripe.com/keys](https://docs.stripe.com/keys)
carries a literal "Safe to expose" column — `pk_…` yes, `sk_…` no — and encodes
environment in the same prefix (`_test_` / `_live_`). Our `nvk_live_` already had
that shape, so the public key is `nvp_live_`: one letter apart, same length, and
a distinct greppable prefix so a secret scanner can be taught which is which
without raising a false alarm on a value that is *meant* to be in page source.
PostHog documents that payoff — a detected `phx_` key is "automatically rolled and
the user is notified" ([api.md](https://posthog.com/docs/api.md)).

**The pattern all of them share: capability, not secrecy, is the control.** Every
"safe to expose" claim is justified by narrow scope — submission only, no read.
That is decision 2, and it is why the origin list is not asked to carry the
weight.

**One correction the research forced.** An earlier draft of this work described
`allowed_origins` as "the whole of this key's security". It is not. Sentry's own
staff-filed documentation issue records that a page sending no `Origin` or
`Referer` header "aren't subject to the `Allowed Domains` filter"
([sentry-docs#4014](https://github.com/getsentry/sentry-docs/issues/4014)) — the
filter is header-based and a non-browser caller simply omits the header. The
comment was corrected in `models.py`, in `auth.Identity`, and in the console copy
before this ADR was written.

**Not found, stated as not found:** no vendor *documentation* page says whether
its public credential is stored hashed or plaintext. Only PostHog's source
answers it. Stripe's "Publishable API keys aren't sensitive, so we show them by
default" versus "Save the key value. You can't retrieve it later" for secret keys
is consistent with plaintext-versus-hashed, but Stripe never says so, and it is
recorded here as inference rather than evidence.

## Alternatives considered

**A `kind` column on `api_keys`, as the brief asked.** One table, one list
endpoint, one screen. Rejected on the constant-time comparison above: it forces
a branch on a credential path, makes `key_hash` nullable, and turns a structural
guarantee into a filter somebody has to remember in every query. It would also
have obliged us to delete `site_keys` — a table designed for this in 0001 — to
avoid holding two answers to the same question.

**Hash the public key anyway, "for consistency".** Rejected: it protects nothing
(the value is in the page), prevents the console showing a key the customer needs
to read back, and replaces a single unique-index probe with a prefix scan plus a
digest on the browser hot path. Consistency with a threat model that does not
apply is not consistency.

**One `Authenticator` that tries both tables.** Fewer types, one wiring point.
Rejected: it reintroduces the branch the two tables removed, one level up. The
failure mode is the same and the code that expresses it is harder to read.

**A shared, parameterised service routine taking `kind`.** Rejected for the same
reason. Two sets of routines that share only a role check is not duplication
worth removing; a `kind` argument threaded through create, list and summarize is
precisely the shape in which the wrong one gets passed.

**Honeycomb's answer: no public key, use a Collector.** Named above. It is a
defensible position and it is not ours, because a customer's first web vital
should cost them a script tag.

**Header-only on the browser door, with the SDK using `fetch(..., {keepalive: true})`.**
The tidier rule — one place a credential is ever read from — and it was the
starting position. Rejected because it moves a real cost onto P1-38 rather than
removing it: `keepalive` is capped at 64 KiB across all in-flight keepalive
requests per page, is the fetch spec's own allowance rather than a guarantee, and
has a thinner history of surviving unload than `sendBeacon`. Trading the most
valuable exports for a neater rule is the wrong trade when the value in question
is not a secret.

**One query parameter serving both kinds.** Rejected: the name is the first
warning a customer gets, and `nexovue_key` would invite exactly the paste that
must never happen. A parameter that can only mean "public" is a parameter a
secret key looks wrong in.

**Wildcard origins.** Kept, matching the receiver's `*.example.com` support, with
a two-label floor so `https://*.com` cannot be written. The API validator mirrors
`otlp/origin.go` deliberately: accepting an entry the receiver will never match
would leave a customer staring at a configured origin and a refused request with
nothing connecting them.

## Consequences

**`site_keys` stops being a table that documents an intention.** It now has a
writer, a reader, a route, a screen and a receiver lookup. The receiver's
`PostgresSiteKeys` is a second type over the same pool rather than two more
methods on `PostgresKeys`, so a door handed one cannot reach the other's table —
the two-table argument applied one level up.

**One credential cache, now generic.** `cache[V]` serves both kinds, so there is
one answer to "how stale may our view of a key be", one bounded map, and one
place the hit/miss TTL split lives. `Credential` and `SiteCredential` differ by
exactly the hash field, which is the difference that matters.

**The receiver writes to a second control-plane table.** ADR 0081 named
`api_keys.last_used_at` as the exception to "the API owns Postgres". This adds
`site_keys.last_used_at` on the same terms — one column, forward-only, off the
request path, throttled by the same cache.

**The browser door is not wired into `cmd/receiver/main.go`.** `SiteAuthenticator`
and `PostgresSiteKeys` exist, are tested, and compose with the origin gate another
change is adding in `internal/otlp` — that gate's `SitePolicyFor func(auth.Identity)
(SitePolicy, bool)` reads `MayIngestBrowserTelemetry()` and `AllowedOrigins`
straight off the identity. Constructing them in `main.go` is deliberately left to
whichever change mounts the browser route, so the two are not wired twice.

**`Identity.AllowedOrigins` has a consumer, so its shape is now fixed.** The
origin gate in `internal/otlp` reads it directly — exact match per RFC 6454, one
narrow `*.` wildcard, `null` refused — rather than through the intermediate seam
it was originally going to invent. Changing the field's semantics is no longer
free.

**Nothing mints a site key at signup.** A project gets its server key during
onboarding and its browser key when a customer asks for one, because browser
ingest is opt-in and an origin list cannot be guessed.

**Not verified.** Three things. The browser door has **never served an HTTP
request** — `SiteAuthenticator` is proven by unit tests and its SQL by the live
suite against real Postgres, but no browser, and no receiver process, has
authenticated with a site key. The **screen has not been opened**; the section is
proven by type checking and by unit tests over its copy and ordering, not by
being looked at. And `last_used_at` on a site key has **no writer running** —
the statement is proven forward-only and revocation-aware against real Postgres,
but nothing in a deployed receiver calls `MarkUsed` until the browser route is
mounted.

## Open question — a public key's own rate ceiling

**Deliberately unanswered, because answering it means inventing a number.**

The receiver rate-limits per credential: `gates.go` calls
`Limiter.Allow(ctx, identity.KeyPrefix)`. (Worth stating because it was recorded
wrongly once during this work: the ingest path has **never** limited per IP. The
per-IP limiter is the control plane's, on the API, and never sees ingest.)

Per-key is the right dimension for a secret key, where one key stands for a
handful of server processes whose traffic the customer controls. **It is the
wrong shape for a public key, where one key stands for an entire visitor
population** — and, once scraped, for anyone who wants to spend that budget. On
today's defaults a scraped site key costs its owner the full per-key ceiling,
1000 requests/s × 16 MiB, indefinitely, until they retire it. The origin gate
narrows this — it runs *before* the limiter precisely so an off-origin request
does not spend the owner's budget — but it is header-based and a non-browser
caller simply omits the header, so it is not the answer here either.

The obvious shape is `SitePolicy.RatePerSecond`, zero meaning the receiver's
default, fed by a column on `site_keys`. The limiter change for it was written
and then deliberately reverted, because a parameter no column can feed is
speculative.

**The column is not added here, and that is the decision this section records.**
Adding a nullable column nothing writes is the exact mistake ADR 0081 documented
in `api_keys.last_used_at` — a schema recording an intention for a year with no
writer — and this change already avoided it once by refusing to add
`site_keys.last_used_at` without wiring the write. What is missing is not the
column but the number, and the number is not derivable from anything measured:
it depends on a page's beacon cadence, a customer's traffic, and what a browser
SDK actually batches, none of which exist yet.

**What is needed to close it:** one real browser SDK sending real traffic
(P1-39), and a measured events-per-visitor-per-minute figure. Then the ceiling
can be set from a distribution rather than from taste, and the column added with
the writer that fills it in the same change.

# 0087 — The Origin is what ties a public key to its owner, and it is abuse control, not authentication

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

Every ingest client today is a server holding a secret. P1-38 changes that: a browser SDK sends
OTLP from a customer's web page, carrying a key **printed in the page source**. Anyone who views
source holds the credential. The receiver had no CORS or origin handling of any kind — no file
under `apps/receiver/internal/` mentioned either — so a browser could not have exported at all,
and if it could, nothing would have distinguished the customer's own page from anyone else's.

Two things have to be decided before P1-38 has anything to build on: what a public key is bound
to, and what a browser is told when the answer is no. The second matters more than it looks.
**A CORS rejection is invisible to page script by design** — the browser reports a generic failure
and swallows the status and the body — so a design that refuses in the wrong place leaves a
customer with a broken site and no diagnosis.

This record covers the **transport** only. The key kinds themselves — `site_keys` beside
`api_keys`, and `Identity.Kind` / `Identity.AllowedOrigins` — are ADR 0086's, and the door reads
what that work produces rather than inventing a parallel notion of a public key.

## Decision

### 1. The origin list belongs to the key, and the door only matches against it

`auth.Identity.AllowedOrigins`, filled by whichever authenticator resolved the credential. Not
configuration: an allowlist is per-customer data, and one receiver serves every tenant. The
receiver's existing credential cache carries it, so a changed origin list takes effect within the
same window a revocation does — at most `RECEIVER_API_KEY_CACHE_TTL`, itself capped at a minute.

This is the field's own answer. Sentry checks "the `Origin` or `Referer` HTTP header of each
inbound request against your project's allowed domain list"
([Sentry data filtering](https://docs.sentry.io/product/data-management-settings/filtering/), read
2026-08-31), configured per project, because the Sentry DSN is likewise public: "DSNs are safe to
keep public because they only allow submission of new events"
([DSN explainer](https://docs.sentry.io/concepts/key-terms/dsn-explainer/)). Datadog draws the same
line at the credential rather than the origin — "API keys cannot be used to send data from a
browser, mobile, or TV app, as they would be exposed client-side. Instead, end user facing
applications use client tokens"
([Datadog API and app keys](https://docs.datadoghq.com/account_management/api-app-keys/)) — and
documents **no** origin restriction on RUM intake at all. We take Sentry's position, and take it
because of what makes the header worth anything: **`Origin` is a forbidden request header name, so
page script cannot set it**, and a browser stamps it on every POST including same-origin ones
([MDN `Origin`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin)).

**What it does not protect against, stated plainly and repeated in the source: a non-browser client
can send any header it likes.** `curl -H 'Origin: https://customer.example'` defeats this
completely. It bounds abuse by *other people's web pages*, which is the realistic threat for a key
lying in public, and it is not an authentication boundary. Sentry says the same of theirs.

### 2. Matching is exact, with one narrow wildcard

Origins compare as RFC 6454 compares them — scheme, host, port — with case and a default port
carrying no meaning, so `https://Shop.Example.com:443` and `https://shop.example.com` are one
origin, and a stored entry with a trailing slash still matches. An entry may begin `*.` to cover
subdomains, which preview deployments genuinely need. `null` never matches, **not even a list that
spells it out**: it is what a sandboxed frame, a `data:` URL and a `file://` page all send
([MDN `Origin`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin)), so it
names no site and a list cannot mean it.

`https://*.example.com` does **not** match the apex `https://example.com`, and a wildcard over
fewer than two labels — `https://*.com`, `https://*` — matches nothing. That second rule is blunt
and it is not a public-suffix check: nothing stops a customer writing `https://*.co.uk` and harming
only themselves. The OpenTelemetry Collector's own CORS support takes a general glob and warns
about exactly this shape ([`confighttp`
README](https://github.com/open-telemetry/opentelemetry-collector/blob/main/config/confighttp/README.md));
ours is narrower, and an entry that is not a legal origin matches nothing, so a typo fails closed.

### 3. The preflight is permissive on purpose, and this is the load-bearing part

An OTLP export from a browser is a cross-origin POST whose content type is outside the CORS
safelist — the safelist is `application/x-www-form-urlencoded`, `multipart/form-data` and
`text/plain` ([MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)) — carrying
`x-nexovue-key`, a header no browser has heard of. Either alone forces a preflight `OPTIONS`.
Nothing reaches the door until that is answered.

**A preflight carries no credential.** The browser announces the key's header *name* in
`Access-Control-Request-Headers` and never its value, so the receiver cannot know whose key this
will be, let alone which origins that key allows. So `OPTIONS` on the three signal paths answers
for the *shape* of the request — `Allow-Methods: POST`, an `Allow-Headers` list of exactly what the
door reads, the origin echoed, `Max-Age: 7200` — for any origin, without authenticating anything.

Refusing here would be the tempting mistake, and it is the one that makes the product
undiagnosable: every browser would be turned away before the request that can actually be judged is
ever sent, and the page would get an opaque CORS failure. **The preflight says yes to the shape;
the POST behind it says yes or no to the origin, in words the page can read.**

`Allow-Headers` is a fixed list rather than an echo of what the browser asked for. Echoing would
answer "yes" to headers we do not accept, which is an allowlist that allows everything.

`Max-Age: 7200` is Chromium's own ceiling — Firefox allows 86400, and the default when the header
is absent is **five seconds**
([MDN `Access-Control-Max-Age`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Access-Control-Max-Age)).
A browser SDK flushing every few seconds would otherwise send two requests for every export, for
the whole session.

### 4. Every reply to an origin-bearing request carries CORS headers — the refusals included

`Access-Control-Allow-Origin` echoing the request's origin, `Vary: Origin` on every reply so no
cache mixes two origins up, and `Access-Control-Expose-Headers: Retry-After` so a throttled browser
SDK can read how long to wait instead of backing off on its own schedule into a door already saying
stop.

Echoed on the **refusals too**, and that is deliberate: a 403 with no `Allow-Origin` is invisible
to the page that sent it. The difference is a customer being told their origin is not on the list
versus a customer with nothing to go on. What echoing exposes is bounded by what the door returns,
which is a constant either way — an empty OTLP response, or a fixed `google.rpc.Status`. **The
receiver is write-only and holds nothing readable.**

**Never `*`, and never `Access-Control-Allow-Credentials`.** The Collector sets
`Allow-Credentials: true` and therefore has to warn "do not use a plain wildcard `["*"]`"
([`confighttp` README](https://github.com/open-telemetry/opentelemetry-collector/blob/main/config/confighttp/README.md)).
We avoid the problem instead of documenting it: allowing credentials would carry a visitor's
cookies and client certificates to us on every export, and we want none of them.

### 5. A key that is not a browser key, presented from a page, is refused

`403 PERMISSION_DENIED`, counted as `forbidden_origin`. A secret key arriving with an `Origin`
header means the secret is in a web page; accepting it would make a leak look like ordinary
traffic. And a public key arriving *without* an `Origin` did not come from a browser, because a
browser sends one on every POST — so it is refused too, which is also what a public key presented
over gRPC hits.

Sentry cannot do this: one DSN serves their server and browser SDKs alike, so "requests without one
— common for server-side SDKs and direct API submissions — are always accepted, even when an
allowed domain list is configured"
([Sentry data filtering](https://docs.sentry.io/product/data-management-settings/filtering/)).
Because 0086 gives the two kinds separate keys over separate tables, we can close that gap. It
stops a lazy replay of a scraped request; it does not stop anyone who sets the header.

The three refusals say **more** than the credential refusals do, and on purpose. The caller already
holds the key, so naming what is wrong tells an attacker nothing and tells a customer onboarding a
site everything:

- `this ingest key is not allowed from this origin`
- `public ingest key presented without a browser Origin`
- `this ingest key cannot be used from a browser; a public site key is what a web page sends`

### 6. The gate runs after authentication and **before** the limiter

The list belongs to the key, so it cannot run earlier. It must run before the limiter, and that
ordering is the security decision, not a performance one: **a request from an origin the key does
not cover must not spend the owner's rate budget.** If it did, a scraped key would become a denial
of service against the customer it was scraped from — an attacker could exhaust the victim's
per-second allowance with requests that were going to be refused anyway.

### 7. The bound stays the key. It was never the address, and the address is not available here

The brief for this work assumed the receiver rate-limits per IP. **It does not, and never has.**
`ratelimit.Limiter.Allow` counts against `identity.KeyPrefix`; the per-IP limiter is the *control
plane's* (`apps/api/.../middleware/ratelimit.py`, scopes `ip`/`email`/`session`) and does not sit
on the ingest path at all. So for browser traffic there is nothing to replace: the subject is
already the customer rather than the visitor, which is the property that per-IP would have lost.

Per-IP is also **not available** at this door, for two independent reasons:

- The receiver sits behind Caddy (`deploy/caddy/Caddyfile`, `reverse_proxy receiver:4318`), so
  `RemoteAddr` is the proxy's for every browser request. `gates.go` refuses to read
  `X-Forwarded-For` on principle — "a header can be set to anything by whoever set it, and an
  address that can be chosen names nobody" — and trusting it would need a trusted-hop count nobody
  has decided for this service.
- Even with correct client addresses, carrier-grade NAT puts thousands of real visitors behind one
  IPv4. Any per-visitor bound is then wrong in both directions: low enough to catch an abuser, and
  it throttles a mobile carrier's users.

**What a scraped key costs its owner, in the worst case.** An attacker who forges the `Origin`
header can send up to the key's per-second limit indefinitely, at up to
`RECEIVER_MAX_DECODED_BODY_BYTES` per request, until the key is rotated. On today's defaults that
is 1000 requests/second × 16 MiB — a ceiling nobody would call a bound, and it is the ceiling a
*secret* key already has. Everything above raises the effort (you must forge a header, or find many
sites) and lowers none of it.

**One global `RECEIVER_RATE_LIMIT_PER_SECOND` is the wrong shape for a public key**, and that is
the finding rather than a fix: a server key is one process, a public key is a whole visitor
population, and the two differ by orders of magnitude in both directions. The answer is a per-key
ceiling stored on the key row, which is what Sentry does — per-DSN rate limits set in project
settings, "the maximum volume of error events a project key will accept during a period of time"
([Sentry quota management](https://docs.sentry.io/pricing/quotas/manage-event-stream-guide/)).
**The column does not exist and the number is a product decision, so neither was invented here.**
The interface needed is one field, `Identity.RatePerSecond`, with zero meaning "the receiver's
default"; the door then passes it to the limiter and nothing else changes.

## Alternatives considered

**A receiver-wide allowlist in configuration.** Rejected outright — one receiver serves every
tenant, so a global list is either empty or useless.

**Enforcing the origin at the preflight.** Rejected; see 3. It cannot be done (no credential) and,
if it could, it would make every misconfiguration undiagnosable.

**Refusing browser requests silently, with no CORS headers on the 4xx.** Rejected. It is what a
naive CORS implementation does, and it converts every customer mistake into a support ticket.

**General glob matching on origins, as the Collector does.** Rejected in favour of exact match plus
a two-label-minimum `*.` prefix. `https://*.com` is a list that allows the internet.

**A second global default for browser keys.** Rejected. It would be a number with nothing behind
it, and it would sit in the way of the per-key ceiling that is actually right.

**A per-(key, source address) sub-counter, so one abusive visitor cannot drain the customer's
aggregate.** Rejected *for now* rather than on principle — it is the right idea and it is blocked on
the proxy-hop decision above. Building it against `RemoteAddr` today would count every browser
visitor as one Caddy.

## Consequences

**What this buys.** P1-38 has a transport to build on: a browser can complete a preflight, a
customer's own page can export, another site's page cannot without forging a header, and every
refusal a browser receives is readable by the script that caused it. A secret key that turns up in
a web page is refused rather than served. `forbidden_origin` is its own counter in the ingest
summary, so a customer's misconfigured site shows up as a number in a 60-second window rather than
as silence.

**What this costs.**

- **The refusal log carries the origin, and it had to.** The line gained `peer_address` and
  `user_agent` earlier today, and for browser traffic **neither one identifies anybody**: behind a
  TLS terminator the peer is the proxy and the user agent is some visitor's browser. The `origin`
  field is the only one that names the site to go and fix. It is truncated to 128 bytes, like the
  user agent, because a stranger chooses its length.
- **A customer testing a public key with curl gets a 403** they may find surprising. The message
  says why.
- **A customer who pastes a URL rather than an origin** — `https://example.com/app` — is silently
  not matched. Validating that at the point of entry belongs to the control plane's key API, not to
  the door, and is not done here.
- **`*.co.uk` is accepted and is a bad idea.** No public-suffix list is consulted.
- **Origin checking is one more thing on the hot path**, though it is a list walk over an
  in-memory field with no allocation and no I/O. **Not measured.**

**What was verified.** `just check` run and watched: **114 tests passed and 0 failed in
`internal/otlp`**, 34 of them new — 15 test functions and 19 subtest cases — and every Go package in
the workspace reported `ok`. Six
negative controls were broken deliberately and watched to fail, then restored: removing the CORS
wrapper failed 4 tests; removing the `OPTIONS` route failed 2; making an empty allowlist allow
everything failed the empty-list case; letting the wildcard match the apex failed the apex case;
blanking the origin log field failed the diagnosis test; and dropping `PERMISSION_DENIED` from the
reason table failed the summary counter.

**Live against the running dev receiver** (that container restarted, nothing else), all four
watched: a preflight answered `204` with the origin echoed, `Allow-Methods: POST`, the four allowed
headers and `Max-Age: 7200`; a real secret key POSTed from `https://shop.example.com` answered
**403** with `Access-Control-Allow-Origin` echoed and the body
`{"code":7,"message":"this ingest key cannot be used from a browser; ..."}`; the same key with no
`Origin` answered **200**, and the demo fleet's server exports kept flowing throughout; and a bare
`OPTIONS` answered `204 Allow: POST, OPTIONS` with no CORS headers. The refusal line read
`status=403 origin=https://shop.example.com reason=forbidden_origin`, and the summary carries
`refused_forbidden_origin`.

**What was not verified.**

- **No browser has ever exported to this receiver.** Every live check above was curl. Nothing has
  exercised a real preflight cache, a real `sendBeacon`, or the OTel JS exporter.
- **The public-key accept path is unit-tested only**, at the gate. The door still authenticates
  every request with the secret authenticator, so there is no wiring yet from a `site_keys`
  credential to an HTTP request — that seam is 0086's and is not built. The end-to-end browser
  success path therefore has no test at any level.
- **The 429-to-a-browser path is not exercised end to end**, for the same reason: reaching the
  limiter with an origin needs a public key the door can authenticate.
- **Nothing here was measured under load**, and no origin list has ever held a real customer's
  domains.

**Still open.**

- **The per-key rate ceiling** — the column, the field, and the number. Until it exists a public key
  is bounded exactly as a server key is, which is the wrong bound in both directions.
- **Whether a public key should be allowed to reach the gRPC door at all.** It is refused today as a
  side effect of having no `Origin`, which is the right outcome by an argument that would stop
  working if gRPC-Web were ever served.
- **`navigator.sendBeacon` cannot set headers**, so an SDK that uses it on page unload cannot send
  `x-nexovue-key` at all — and with a JSON body it sets credentials mode to *include*, which our
  refusal to allow credentials will reject
  ([opentelemetry-js#3062](https://github.com/open-telemetry/opentelemetry-js/issues/3062)). Sentry
  and Datadog both answer this by carrying the key in the query string. **Whether our public key may
  travel in the URL is a decision for P1-38 and is not taken here**; it would change where the door
  reads the credential from, which is auth's boundary, not the transport's.
- **The trusted-proxy hop count for the receiver**, without which no address-derived bound is
  possible. The Caddyfile is still a skeleton and carries an open TODO about whether ingest even
  shares a hostname with the app.

# Phase 1 v2 — what is left before Phase 2

**Status:** live. Opened 2026-09-06.

`phase-1-plan.md` is the record of what was built and why; it is long because it carries the
reasoning. This file is the shorter question: **what still has to be true before Phase 2 starts,
in what order, and what is already true that the plan still calls open.**

Everything below was checked against the code on 2026-09-06, not read off a status column. Where a
row in `phase-1-plan.md` disagrees with what the code does, this file says so and the code wins.

---

## Status — updated 2026-09-08

**Tracked here rather than in a status column elsewhere, so this file answers "what next"
without a second file to reconcile.** A row is `done` only when something was run and watched,
never when the code was written.

| | Item | Status | Landed |
|---|---|---|---|
| **A1** | authorization seam — `require_role`, 8 routes, coverage test | ✅ **done** | `7e930d5` |
| **A2** | contract tests — auth mode + ClickHouse table names | ✅ **done** | `e18713c` |
| **A3** | `packages/py-platform` — 299 duplicated lines removed | ✅ **done** | `f4291d8` |
| **G1** | TLS edge — two site blocks, HSTS/nosniff on both, console headers on the app | ✅ **done** | `10ae52f` |
| **G2** | account recovery — P1-69, P1-70, P1-71 | ✅ **done** | `a6ef9cd` `9febb8a` `3a76dda` `fa3c11d` |
| **G3** | poller SSRF boundary | ✅ **done** | `2f06a98` |
| **G4** | poller certificate verification | ✅ **done** | `f26ec33` |
| **G5** | ingest kill switch — both halves, proved end to end | ✅ **done** | `10ae52f` `58986ea` |
| **G6a** | paid-tier browser cap | ⬜ **and larger than this file said** — see §5a | — |
| **G7** | SDK distribution | ⬜ blocked on a deployment target | — |
| **C4** | P1-72 profile & security | 🟡 **API done 2026-09-08**; screen, email change and timezone not built | `pending` |
| **C1–C3, C5–C7** | the rest of the commercial layer | ⬜ **unblocked now G2 is done**; C2 also needed G6c, which is decided | — |
| **M1** | four blank §4 budgets | ✅ **done 2026-09-08 — all four pass** | `pending` |
| **M2** | P1-50, the 24-hour soak | ⬜ **last thing before Phase 2** | — |
| **M3** | pprof on the admin listener | ✅ **done** | `10ae52f` |
| **M4** | RUM breakdowns by page and device | ✅ **done** | `10ae52f` `58986ea` |
| **F1** | T1-5 repricing guard | ✅ **done** | `10ae52f` |
| **F2** | T1-6 rig cleanup | ✅ **done**, by deleting it | `10ae52f` |
| **F3** | the three tests that cannot fail | ✅ **done** | `10ae52f` |
| **F4** | re-run and capture the Tier 2 figures | ⬜ T2-1, T2-2, T2-3, T2-6 | — |
| **F5** | the three cheap T2 corrections | ✅ **done** | `10ae52f` |
| **D1** | `url.full` trimmed to the path — ADR 0103 | ✅ **decided and fixed** | `0e97d65` |
| **D2** | limiter fail-open — ADR 0104 | ✅ **decided and built** | `10ae52f` |
| **D3** | sampling and masking are customer controls — ADR 0105 | ✅ **decided**; the writes land in C5/P1-76 | `0e97d65` |
| **D4** | network monitoring and SIEM out of scope — ADR 0106 | ✅ **decided** | `0e97d65` |

**Five open questions from the agent round were answered on 2026-09-07**, and one of them was a
product decision rather than a technical one:

| | question | answer |
|---|---|---|
| **G6c** | block or bill when a paid tier exceeds its volume? | **stop and say so, with a ceiling the customer may raise** — [ADR 0107](../architecture/decisions/0107-over-the-allowance-we-stop-and-say-so-and-the-customer-may-raise-the-ceiling.md). ADR 0056's own fourth promise wins; its "overage stays live" clause is withdrawn. **C2 / P1-74 is unblocked.** |
| evidence in an immutable ADR | supersede, edit, or leave? | a third permitted edit — an `**Evidence corrected:**` line under the status block, body untouched. Applied to 0096 and 0097 |
| `fallbackBurstMultiple` | constant or environment variable? | **stays a constant.** Nothing is deployed, so there is no incident to tune during and no measurement to tune against. Adding the knob now is designing around an absent constraint (rule 14); the number should be set from what a real Valkey outage looked like |
| the stop valve's operator half | screen, endpoint, or hand-written SQL? | **`just suspend-close/open/show`**, plus `ingest_suspended_reason` and a `CHECK` binding it to the timestamp. Not an endpoint: naming *who* closed a valve needs an operator identity §9's single-operator scoping does not have. The screen belongs with P1-76 |
| `url_path` templating | carry `http.route`, derive one, or leave it? | **carry the customer's own template through, falling back to the path** — [ADR 0108](../architecture/decisions/0108-a-web-vital-is-grouped-by-the-customers-route-template-and-falls-back-to-the-path.md), built. Deriving one invents data, and `pagecontext.ts` already holds the rule: *"a route template is the customer's knowledge, not something derived from a path"*. **Building it found a silent data-loss defect** — `route` was in the rollup's sort key and not in its insert-deduplication token, so the minute a customer first set a template ClickHouse would have dropped the insert |

**D1 is the row worth reading twice.** It was filed as a decision needing no code, and the first
thing that happened on taking it was a query against the dev store that found a page-view record
carrying a customer's postal address in `url.full` — on a project whose configuration said no query
string leaves the page. It is fixed, in the SDK and in the probe, with negative controls on both.

### What "partly done" means for the two 🟡 rows

**G1.** Both decisions it deferred are now taken: two hostnames — `app.nexovue.com` and
`ingest.nexovue.com` — and no public gRPC in Phase 1. ADR 0089 made the hostname a privacy
requirement rather than a preference, and the cookie half is settled by an omission that now carries
a comment: `auth/cookies.py` sets no `Domain`, which makes the session cookie host-only.

~~Both decisions are *written into* `deploy/caddy/Caddyfile`.~~ **Corrected 2026-09-07 — they were
written into it as a comment, not as configuration.** `git show 42c3b48:deploy/caddy/Caddyfile`
has one site block, `{$NEXOVUE_PUBLIC_HOST}`, under a comment beginning *"Two names, decided
2026-09-07"*. The commit replaced a TODO with the reasoning behind the answer and left the config
serving a single name, so a reader taking this row at its word would have believed a two-hostname
edge existed when one did not. The two site blocks — `{$NEXOVUE_APP_HOST}` and
`{$NEXOVUE_INGEST_HOST}` — are being built now, alongside the headers. This is the plainest form of
the rule this file opens with: where the document and the config disagreed, the config was right.

**G2 is closed.** P1-69's infrastructure was already verified — provider, `mail.nexovue.com`
carrying SPF, DKIM, DMARC and a bounce path all confirmed against a public resolver, the port, the
adapter, the composition root, two real messages accepted. **P1-70 and P1-71 landed 2026-09-08.**

Three things that were not obvious until the code was read:

- **The sender was never wired into the app.** `build_email_sender` existed and was tested;
  `Resources` had no `email` field and nothing called it. G2's remaining half started with wiring,
  not with features.
- **"A reset ends every live session" had nothing to run.** Redis keys sessions by token
  fingerprint and `SessionStore.revoke` takes a *token*, so there was no query for "all of this
  user's". `users.sessions_valid_from` (migration 0019) is the answer: one write invalidates every
  session, nothing to enumerate and nothing to drift, and the comparison rides a user row
  `get_current_identity` already loads on every request.
- **A1's authorization guard caught the new routes**, which is what it was built for. Four are
  exempt with their reasons written down — three are followed from a mail client and have no
  session to judge, and `resend_verification` is deliberately open to any role because it can only
  ever mail the caller's own address.

---

## 0. What the plan says is open and is not

Four things in `phase-1-plan.md` are stale, and two of them are the reason P1-53 looks worse than
it is.

**P1-53's browser-SSRF finding is fixed.** The row says *"browser synthetic checks have no SSRF
boundary at all (`page.goto` bypasses the socket guard the HTTP check uses)"*. `browser.py:322`
now wraps every flow in `PublicAddressProxy`, which is [ADR 0096](../architecture/decisions/0096-a-browser-flows-only-way-onto-the-network-is-a-proxy-that-judges-the-address.md)
shipped, extended by [0098](../architecture/decisions/0098-a-probe-reaches-a-private-address-only-when-two-settings-both-say-development.md).

**P1-53's site-key finding is fixed.** *"A scraped site key can now spend a free org's whole
month"* was closed by the per-door cap — open-decisions §1a, decided and shipped as the plan's M5 task zero,
with `allowance_test.go` holding the browser cap at 25% of the free tier.

**P1-53's `TRUSTED_PROXY_COUNT` finding is fixed for the deployment that has an edge.**
`docker-compose.onprem.yaml:164` sets `TRUSTED_PROXY_COUNT: ${TRUSTED_PROXY_COUNT:-1}`. The
setting exists in `settings.py` and defaults to 0, which is correct for a deployment with no proxy.

**§9's "sharpest gap of the four" is closed.** The scoping note says
*"there is no `POST /api-keys`… a customer with a leaked key chooses between stopping their own
ingest and leaving the leak live"*, and calls it a security gap rather than a convenience one. The
endpoint exists — *"Mint an ingest key, leaving existing ones live"* — and `ApiKeysScreen` mints
and rotates. Nothing in §9 says so.

**P1-54 is started.** `docs/product/database-monitoring-setup.md` covers both database onboarding
routes, verified end to end. The rest of P1-54 — the OTLP setup guide for doors 1 and 3 — is not
written.

**So P1-53's real remainder is one thing: TLS and security headers at the edge.** That is smaller
than the row implies and it is still a blocker, because it is the only row here that makes the
product unsafe to expose rather than merely unfinished.

---

## 1. The gate — nothing ships to a paying customer until these are true

Ordered by what blocks what, not by size.

### G1 · The edge terminates TLS and sets security headers · *P1-53 remainder*

**As this section was written**, `deploy/caddy/Caddyfile` was labelled **SKELETON** in its own first
line and carried two TODOs. It reverse-proxied four routes correctly and did nothing else: **no
HSTS, no `X-Content-Type-Options`, no `Referrer-Policy`, no CSP, and no frame protection.**

**Read the two recommendations below as the record of a decision, not as a description of the
file.** `42c3b48` answered both of them *in a comment* and left the config serving one name — see
the G1 note in the status section — so between that commit and the edge being built, this section
and the Caddyfile disagreed and the Caddyfile was right.

**Built 2026-09-08.** Two site blocks, `{$NEXOVUE_APP_HOST}` and `{$NEXOVUE_INGEST_HOST}`; an
`edge_headers` snippet carrying HSTS and `X-Content-Type-Options` on **both** hosts, and a
`console_headers` snippet with `Referrer-Policy`, `X-Frame-Options`, COOP, CORP,
`Permissions-Policy` and a CSP on the **app host alone**. `caddy validate` clean and the headers
curled off a running edge.

**The app-only split is a real constraint, not tidiness.** `Cross-Origin-Resource-Policy:
same-origin` on ingest would make the edge refuse door 3's export, which is cross-origin by design
(ADR 0089) — a CSP on ingest is merely pointless, CORP on ingest is an outage.

**Two defects only running it could find.** Caddy marks a whole `header` block *deferred* when it
contains a deletion and skips it on an upstream failure, so `-Server` was silently dropping HSTS
from every 502; and `reverse_proxy`'s own `Via: 1.1 Caddy` was undoing `-Server` anyway.

**One thing left, and it needs a browser rather than a commit.** `style-src 'self'` ships as
`Content-Security-Policy-Report-Only` because no browser has loaded the console under it. Load the
app once, and either drop `'unsafe-inline'` from the enforced policy or keep it with the reason
recorded.

Two decisions it defers and this plan should not:

- **One hostname or two.** Its TODO asks whether ingest and the app share a name. **Recommendation:
  two — `ingest.nexovue.com` and `app.nexovue.com`.** Ingest is machine traffic at volume and the
  app is browser traffic; splitting them lets the two be rate-limited, logged, cached and moved
  apart later without a migration, and it keeps a browser-facing CSP off the OTLP path where it
  means nothing. It also matters for door 3: the SDK's `endpoint` is a public string in every
  customer's page, and a name that only ever serves ingest is one that can never accidentally serve
  a session cookie.
- **OTLP/gRPC on 4317.** The second TODO. **Recommendation: do not expose gRPC publicly in
  Phase 1.** OTLP/HTTP already carries every door, `h2c` through Caddy needs its own site block and
  its own port decision, and no customer has asked. Record it as deliberate rather than missing.

**Root fix, not a patch:** the headers belong in the Caddyfile as a shared snippet applied to the
app site only, because a header block copied per route drifts the moment a route is added.

### G2 · A customer can recover their account · *P1-69, P1-70, P1-71*

Nothing in the repository sends email. `users.email_verified_at` has existed since migration 0001
and nothing writes it. **A customer who mistypes their address at signup owns an account nobody can
recover** — there is no reset either.

This is the single worst state in the product, because it is silent: signup succeeds.

**Vendor decision, researched rather than assumed.** Three candidates, and the axis is
deliverability against cost at scale:

| | Position |
|---|---|
| **Postmark** | transactional-only, so shared-IP reputation stays clean; strongest deliverability reputation; ~$15/mo for 10k, expensive past 100k |
| **Resend** | best developer experience, 3,000/mo free capped at 100/day, ~$20 entry; shorter public deliverability record |
| **Amazon SES** | cheapest at scale, ~93.8% measured average deliverability, most operational work |

**Recommendation: Postmark.** For this product the only mail is verification, reset and invoices —
low volume, and every one of them is a message whose non-arrival is a support ticket or a lost
account. Postmark refusing to carry marketing mail is the feature: it is why the shared pool stays
clean, and we will never need the marketing side. Cost only turns against it past 100k/month, which
is far beyond any volume Phase 1 produces. Revisit if invoices ever become bulk mail.

**Not just a provider.** The row also needs a sending domain with SPF, DKIM and DMARC, a bounce and
complaint path, and a decision about what happens to a flow when delivery fails — *"a verification
link that silently never arrives is worse than a visible error"*, which the plan already says and
which should become a visible failure state rather than a silent one.

### G3 · The database poller cannot be pointed at anything · *T1-2*

`database_target_from` takes `host` and `port` from tenant integration config and validates only
`isinstance(host, str)`. **No address rule at all.** A tenant can name any host and the scheduler
will open a TCP connection to it from inside our network.

This is the third unguarded SSRF surface and the only one still open. Doors 2's HTTP check goes
through `ConnectGuard`; its browser flow goes through `PublicAddressProxy`; door 4b's database
poller goes through neither.

**Root fix:** thread the same `may_connect_to` boundary through `connect_read_only`, exactly as
[ADR 0098](../architecture/decisions/0098-a-probe-reaches-a-private-address-only-when-two-settings-both-say-development.md)
threads it through the probes — explicitly, per connection, not read from the environment inside
the boundary. The same two-condition development escape applies, for the same reason.

**Done 2026-09-07, and one sentence below turned out to be wrong.** The recommendation was to *"pin
the address and pass the hostname for verification"*. asyncpg does not expose `server_hostname` —
`connect_utils` passes `server_hostname=host`, so the value used to connect is the value the
certificate is checked against, and handing it a judged IP literal makes TLS fail exactly as T3-6's
objection said. So the host is resolved, every address judged, and the connection made **by name**.
That leaves a rebinding window, and `verify-full` is what closes it: an attacker who rebinds the
name cannot present a certificate valid for the customer's database hostname. Resolution-time
judgement stops the honest misconfiguration; certificate verification stops the deliberate attack.
**G4 was not merely a convenient thing to do first — without it this would have been a boundary in
name only.**

Small, and it closes a hole that survives every other boundary this project has built.

**Do G4 first, because it removes G3's stated blocker.** T3-6's full text records that
*"connecting to a judged IP literal would break TLS"* is the reason T1-2 was left unfixed — and
that blocker does not exist, because `ssl="require"` never verified the certificate in the first
place. Fixing G4 to `verify-full` makes the objection real for the first time and settles how to
answer it: pin the address and pass the hostname for verification, which is the standard shape and
what `PublicAddressProxy` already does for door 2.

### G4 · The poller verifies the certificate it connects to · *T3-6*

`RDS_SSL_MODE = "require"`. In asyncpg, `require` means TLS with **no certificate verification** —
`check_hostname` is False. The connection carries a 15-minute IAM token, and the whole point of
[ADR 0076](../architecture/decisions/0076-door-4b-holds-no-customer-credential-it-can-avoid-holding.md)
is that credential never being interceptable.

**Root fix:** `verify-full` with the RDS CA bundle. The constant's comment says *"IAM database
authentication requires TLS; RDS rejects the token otherwise"* — true, and it is why nobody noticed
that `require` is the weakest mode that satisfies that sentence.

### G5 · One runaway customer cannot take the platform down · *P1-52*

No kill switch exists — I grepped for one. The allowance cap refuses a tenant that has spent its
month, which is a billing boundary and not an operational one. There is no way to stop a tenant who
is inside their allowance and still hurting the cluster.

**Recommendation: a per-tenant stop valve read on the same path as the allowance**, so it costs no
extra lookup and inherits the cache TTL that already exists. Postgres column, receiver-side,
refused with its own reason and its own counter — the shape `377a9c5` already established for
deletion.

**Done 2026-09-07, both halves.** `organizations.ingest_suspended_at` with
`ingest_suspended_reason` beside it and a `CHECK` binding the two, read in the allowance's existing
statement so closing it costs the hot path no extra query. The operator half is
`just suspend-close <slug> "<reason>"` / `suspend-open` / `suspend-show` rather than an endpoint,
because naming *who* closed a valve needs an operator identity Phase 1 does not have.

**Proved end to end rather than by unit test.** With the dev key sending OTLP: valve open → `200`;
closed by hand → `200` at t+5s, t+10s, t+15s, then **`429` with `code 8` and the message
"ingestion is suspended for this organization; contact support"** at t+20s, inside the 30-second
allowance cache TTL; reopened → `429` for fifteen seconds, then `200`. Both directions bite within
the TTL and nothing else changed.

**One hole, inherited rather than introduced:** the allowance gate admits the request when the
control plane cannot be read, so a Postgres outage reopens every valve. The kill switch is fast,
not absolute.

### G7 · A customer can actually obtain the browser SDK · *open-decisions §1d*

`packages/browser-sdk` is the product for door 3 — the script a customer pastes into their page —
and **there is nowhere to paste it from.** `dist/` is gitignored and built by hand; nothing
publishes it; no CDN exists; `docs/architecture/technology-choices.md` mentions a CDN exactly once
and only to say fonts are *not* loaded from one.

So door 3 works end to end in the demo, where the collector mounts the bundle off the local
filesystem, and is uninstallable for anybody else. That is a launch blocker in the plainest sense:
**P1-54's setup guide cannot be written, because the `<script src=…>` line has no URL to contain.**

§1d holds the four questions with the research behind them. Only the second is a decision rather
than a task:

1. **Where the file lives** — object storage as origin, a CDN in front. Blocked on the deployment
   target being settled, and no earlier.
2. **The version path scheme, and what the pasted snippet defaults to.** A single unversioned URL
   forces a choice between a cache worth having and a fix that reaches customers this year. The
   escape both comparable products take is a versioned immutable URL. **What the snippet defaults
   to is the real decision**: pin, and a fix we ship reaches nobody without the customer editing
   their HTML; do not pin, and a bad release reaches everyone at once.
3. **Whether we publish SRI hashes** — and note `integrity` and auto-updating are mutually
   exclusive, so offering both and letting the customer choose is the shape that works.
4. **What publishes `dist/`** — small, and blocked on the three above.

**One constraint already settled and easy to lose:** the CDN must send
`Access-Control-Allow-Origin`. A cross-origin script that throws is replaced by the browser with
the bare string `Script error.`, so an error-reporting SDK that cannot read errors is the *default*
outcome, not the failure case. A CDN that cannot set that header is disqualified, not inconvenient.

### G6 · A paid plan's failure mode must not be an unbounded bill on us · *open-decisions §1b*

**This is the item most easily missed, because it looks like a pricing question and is a financial
one.** Verified against the live `plan_prices` table today:

```
free   0        2 GiB    max_billed_services 3      browser_share 25
pro    600      10 GiB   max_billed_services NULL   browser_share NULL
scale  500      25 GiB   max_billed_services NULL   browser_share NULL
enterprise — no row at all
```

`allowance/postgres.go` reads NULL as **not enforced**, and its comment gives the right reason:
that line *"keeps a customer who pays from ever being refused for volume"*. Good — but it means the
same stolen-site-key attack has two different victims:

| Plan | A scraped site key does |
|---|---|
| free | burns the allowance, customer goes blind — **operational** harm, closed by ADR 0097 |
| paid | nothing stops it — **unbounded ingest on our cost**, and it cannot be billed back |

It cannot be billed back because that was never settled. Migration 0015 says so itself: ADR 0056
states both that we block above the allowance *and* that overage stays live and bills — two
different products — and nothing resolved it.

**You cannot sell a plan whose worst case is an unbounded bill landing on you.** Three decisions,
and only the first is safe to take now:

1. **Do paid tiers get a browser cap?** §1b recommends yes and it is the only thing between a
   scraped key and an unbounded bill. A cap works on an unlimited plan where a partition cannot —
   a lid on one tap needs no limit on the tank — and the guarantee that a paying customer's servers
   are never refused is untouched. **Take this one.**
2. **What number?** Free is 25% of a 6 GiB pool; paid has no pool. §1b is explicit that no usage
   data exists and it must not be invented — the honest earliest moment is after P1-47 ships and
   real RUM traffic has been seen. **Blocked on data, and correctly so.**
3. **Block or bill on overage.** ADR 0056's contradiction, now load-bearing because everything
   above assumes paid tiers stay unenforced. **This is a product decision that gates P1-74**, since
   a plan-change screen has to say what happens when you exceed it.

---

## 2. The commercial layer — required for revenue, not for correctness

M8 in the plan. **Everything here is gated on G2**, because verification and invoices both need
mail — and P1-74 is additionally gated on G6.3, because a plan-change screen cannot be
built before someone decides what exceeding a plan does.

### The payment decision, researched

The plan calls this *"a vendor decision, a compliance question and a data question"* and leaves it
open. The axis is who is legally the seller.

- **Stripe** — you are the merchant of record. Lower fees, best API. **You** register for VAT/GST in
  each jurisdiction and file the returns; Stripe Tax calculates but does not remit.
- **Paddle / Lemon Squeezy** — they are the merchant of record and carry global tax compliance
  entirely. Higher headline fee, no tax registrations.

**Recommendation: a merchant of record, and Paddle over Lemon Squeezy.** The reasoning is not fees.
An observability platform sells to engineering teams anywhere, so the first ten customers can
plausibly be in five tax jurisdictions, and a one-person company registering and filing in each is
the failure mode the research names bluntly. Paddle over Lemon Squeezy because Lemon Squeezy is now
Stripe-owned and its independence is an asterisk rather than a guarantee, and because Paddle's
shape suits B2B invoicing — which is what a company buying monitoring expects.

The higher fee buys back the thing this project has no capacity for: **compliance work that is
invisible until it is a penalty.** Consistent with ADR 0076's posture — hold nothing we can avoid
holding, and that extends to tax liability.

**What we store stays minimal:** the processor's customer id and nothing else avoidable. No card
data, no PAN, no address we are not required to hold.

### The rows, in dependency order

| | Row | Gate |
|---|---|---|
| 1 | P1-69 transactional email | — |
| 2 | P1-70 email verification · P1-71 password reset | P1-69 |
| 3 | P1-73 payment processor | — |
| 4 | P1-74 plan change · P1-75 billing screen | P1-73 |
| 5 | P1-72 profile & security · P1-76 project management | — |
| 6 | P1-77 account & organization deletion | needs the ClickHouse purge path below |

**P1-77 has a dependency the plan flags and nothing has built:** `ALTER TABLE spans DELETE` does
not cascade to `span_trace_lookup`, `trace_summaries`, `endpoint_stats_1m` or `service_edges_1m`.
A deletion that leaves derived rows behind is not a deletion, and GDPR does not accept "mostly".
**That purge path is its own piece of work and should be scoped before P1-77, not inside it.**

---

## 3. Exit criteria that are measurements, not features

**How the numbers in this file are read, so "M4" cannot mean two things again.** `M1–M4`, `G1–G7`,
`A1–A3`, `C1–C7`, `D1–D4`, `F1–F5` and `X1–X3` are **this file's own series** — the ones §5a counts.
`P1-nn` is always a task number from `phase-1-plan.md`, and where a *plan milestone* is meant this
file names it as one ("M8 in the plan"). Corrected 2026-09-07: §3's headings had been carrying the
plan's milestone numbers while §5a counted a different M-series, so `M4` named the budgets here and
the RUM breakdowns there.

### M1 · ~~Four budgets in §4 are still blank~~ — **measured 2026-09-08, all four pass**

| Budget | Target | Measured |
|---|---|---|
| Endpoint stats, 24h | p95 < 300 ms | **53.4 ms** |
| Log search, 1h | p95 < 2 s | **126.0 ms** |
| First screen load | < 2 s | **LCP 775 ms** (dev-mode Vite, so conservative) |
| Data loss under broker restart | zero | **zero** — 299 accepted, 299 stored, 299 distinct |

Taken on a freshly seeded store with the demo as traffic, which is why they were taken that day:
**a clean store is what makes the number trustworthy, and it stops being clean as test rows
accumulate.**

**The last one was the point of the exercise.** It is a Phase 1 invariant that had no measurement
behind it at all — a promise rather than a fact. Kafka was restarted mid-run; one export came back
`503` while the broker was down, which is the door declining rather than losing, because the
receiver acks only after the broker accepts. Nothing was lost and nothing was duplicated.

**Two things went wrong first, and both are worth keeping.** A rate-limited request answers in
about 6 ms, so the first latency run counted `429`s as fast successes and reported p50 11.1 ms
where the truth is 22.5 ms — a latency harness that ignores status codes measures its own rate
limiter. And the broker probe was first written with spans, which read **zero stored against 299
accepted**: not loss, but tail sampling doing its job, because `sample/policy.go:keeps` discards
healthy fast traces and a probe's traces are nothing else. Logs are not tail-sampled, so the
equality there is clean.

**The re-encode budget is no longer blocked** — M3 landed `net/http/pprof` on the receiver's admin
listener, so the one remaining blank is now unmeasured rather than unmeasurable.

### M2 · P1-50, the 24-hour soak

Never run. The short-run numbers are all five-minute runs, and the failures a soak finds — leaks,
unbounded growth, cache eviction behaviour, disk — are precisely the ones a five-minute run cannot.

**This should be the last thing before Phase 2**, because it is the only test that exercises
everything else at once.

### M3 · pprof on the receiver's admin listener

The re-encode budget is marked permanently unmeasurable *"until the receiver exposes a profile
endpoint"*. **Recommendation: add `net/http/pprof` to the receiver's existing admin listener**,
which is not exposed publicly. It is a few lines, it closes a budget the plan calls permanently
open, and it is the same endpoint you would want the first time a receiver misbehaves in
production. It is counted apart from M1 because it is a separate change that unblocks one of the
four, not one of the four.

### M4 · P1-47's RUM breakdowns by page and device

`rum/queries.py` groups by `vital_name` alone. `url_path`, `device_class` and `session_id` are
promoted columns populated on every row and read by nothing — the row asks for *"Core Web Vitals by
page/geo/device"*. `country` has no producer at all, because there is no GeoIP in the system.

**Recommendation: ship page and device; drop geo from the row's wording.** The columns exist so
this is a `GROUP BY`, not a migration. Geo needs a GeoIP decision nobody has taken and should not
be smuggled in as part of a screen change. `MIN_READINGS = 10` is open in the same area.

**Done 2026-09-07/08.** Page and device shipped over the existing rollup; geo dropped from the row's
wording, because `country` has no producer. `MIN_READINGS` stays at 10 and stays out of SQL — a page
visited three times is a fact, and hiding it would report no traffic; a thin group renders as a dash
rather than a confident percentile.

**And the breakdown grouped by the wrong thing until [ADR 0108](../architecture/decisions/0108-a-web-vital-is-grouped-by-the-customers-route-template-and-falls-back-to-the-path.md).**
`url_path` is stored as the browser sent it, so a real shop got one group per order. The SDK already
collected the customer's `route` template and put it on the page-view span only; it now reaches
every vital and error record, and the breakdown groups route-then-path. **Three things this did not
fix, named rather than left to be discovered:** the rollup still mints one row per URL per minute,
so the screen is bounded and storage is not; `route` is a single `init()` option, so a single-page
app stamps one template across a whole session; and no row proving any of it came from a real
browser.

### X1 · P1-57, the S3 cold tier — *deferred in §5, and counted in the X row, not the M row*

No ClickHouse storage policy exists — I checked `deploy/clickhouse/`. SeaweedFS is in compose and
serves source maps through `ObjectStore`, not ClickHouse tiering.

Without this, storage grows forever at the hot tier's cost. It is not a launch blocker for a small
number of customers; it becomes one quickly.

**Corrected 2026-09-07.** It sat under an `M6` heading in this section while §5 deferred it, and
§5a counted it *twice* — once in the measurements row and once in the deferred row. It is deferred
work, so it is X1 and the M row lost an item.

---

## 4. Evidence defects — the rule 12 tier

Open-decisions Part 2's Tier 2 is **seven** findings — ~~six~~, corrected 2026-09-07 by counting
the rows — and they share one root: **numbers were published from runs whose output was never
saved.** T2-2 states it plainly — every headline survives only as prose, and nobody can re-derive
it.

The process fix landed on 2026-09-06 (`26a3d9d`): `rebalanceprobe`'s header now carries the capture
instruction above its cleanup SQL. **The figures themselves are still unverifiable**, and the
honest options are to re-run and capture, or to mark them as unreproduced. Either is fine; leaving
them as confident prose is not.

~~**T2-4, T2-5 and T2-7 are cheap and should just be corrected** — a stated baseline of 169 against
a suite of 117, three different numbers for one test count, and a claim about `ipaddress` that the
interpreter contradicts.~~ **Done 2026-09-07, and one of the three was already right.** Each was
settled by running the thing rather than by reasoning about it; open-decisions Part 2 carries the
commands and their output.

- **T2-4 was stale as a finding.** ADR 0096 does not state 169 — it states 117 → 186 and withdraws
  169 in its own text, in the commit it landed in (`a4bb331`). Its numbers reproduce exactly:
  `test_browser_proxy.py` **43**, `test_browser_flow.py` **19**, `test_browser_egress_live.py` **7**
  (all three files untouched since), and the scheduler suite collects **216** today, which is 186
  plus the 30 tests added since. Nothing to correct in the ADR; the finding's wording was what was
  wrong.
- **T2-5 stands and the method was the defect.** `-k "not live"` is a substring match: run today it
  deselects **183** where `--ignore-glob='*_live.py'` leaves **1536**, and the 19-test gap is
  offline tests with `live` inside a longer word — `test_a_replayed_delivery_is_refused`,
  `liveness`, `test_minting_leaves_existing_keys_live`. The repo has a designed switch for this,
  `NEXOVUE_SKIP_LIVE_TESTS=1` in `tests/helpers/live.py`, and neither published number used it.
  **The 2026-09-04 figures themselves are UNREPRODUCED** — the suite has grown by ~380 tests since
  and re-deriving them needs a checkout of that commit.
- **T2-7 stands and is contradicted by the interpreter.** On this project's Python,
  `ipaddress.ip_address("::ffff:127.0.0.1").is_loopback` is **True**. There is no hole —
  `is_publicly_routable` requires `is_global` *and* the named flags — but ADR 0096 §2a's stated
  reason for the second test is wrong.

**ADRs are immutable here** (`docs/architecture/decisions/README.md`: *"Once an ADR is accepted it
is never edited"*, the status line being the one permitted edit), and git confirms the rule is kept
— the only post-acceptance ADR edits in this repository are supersession links. So T2-5's and
T2-7's corrections are recorded in open-decisions Part 2 and, for T2-5, in the decisions-log row in
`system-architecture.md`, which is an index rather than a record and is editable. **Whether an
evidence error in an accepted ADR also warrants its own ADR is a decision for Raj**, not something
to assume either way.

**Three findings are one gate defect, and it is the same shape as rule 12 itself.** T3-4 —
`PROXY_BYPASS_NOTHING` and the WebRTC flags are the only two lines whose removal produces a real
leak, and the only two with no behavioural test, so deleting either leaves every live test green.
T3-5 — the Python statement guard asserts both substrings are present but nothing about position,
so it passes on a statement with the two `func.sum` arguments transposed, where the total returns
the browser part and vice versa. And: `storeprobe_test.go` is
behind a `//go:build storeprobe` tag and `just check` passes no `-tags`, so it is invisible to `go
test`, `go vet` and the linter. It can stop compiling without anyone learning. This is the same
class as the two green-but-empty results CLAUDE.md rule 12 was written from.

---

## 3a. One Tier 1 finding that is neither fixed nor scheduled

**T1-5 — the browser split can vanish silently on a repricing.** The allowance gate reads exactly
one `plan_prices` row — newest `effective_from <= today` — with no fallback for a NULL column. The
house convention is that a repricing is *a new row restating every column*, so a future `free` row
that omits `browser_share_percent` silently reverts to the pooled rule.

**No constraint, no test, no error.** It is a convention held only by whoever writes the next
migration, guarding the cap that G6 is about to extend to paid tiers — so it gets more load-bearing,
not less.

**Root fix:** a `CHECK` that a `free`-tier row carries a share, or a test that fails when the
newest effective row for any tier with a cap has a NULL in that column. The second generalises
better, because the tiers that carry a cap are about to change.

~~**T1-6 is smaller and worth doing with it.** `cleanup94.sh` takes unvalidated positional arguments
and can wipe `span_ingest_offsets` — the durable counted-through watermark whose loss produces
exactly the silent permanent over-count ADR 0094 exists to describe. A measurement rig that can
cause the defect it measures is a rig whose results cannot be trusted. It lives in a scratchpad and
should either be validated or deleted.~~ **Closed 2026-09-07 by deleting it (F2).**

The finding is accurate. Its two DELETEs are keyed
`ALTER TABLE span_ingest_offsets DELETE WHERE startsWith(partition_key, '$TOPIC')`, and `$TOPIC` is
the second positional argument with no check on it: an empty string matches every partition key in
the table, and so does the real topic name `spans`, because `startsWith` is a prefix test. Either
one silently deletes the counted-through watermarks for real traffic, and `usage/counting.go`'s
`alreadyCounted` then stops skipping records the meter has already counted — the permanent
over-count ADR 0094 exists to describe, caused by the tool that measures it.

**It was deleted rather than validated**, because the cleanup it performs already lives in the
repository: `tools/rebalanceprobe/main.go`'s package header carries every statement, and commit
`26a3d9d` moved the capture instruction there deliberately — *"`rebalanceprobe`'s header is [a place
people read], because that is where the cleanup SQL lives"*. The scratchpad script was a second copy
of a contract that is supposed to have one, in a session-scoped temp directory outside the
repository that nothing references and no future run would find. Validating it would have meant
maintaining the duplicate that rule 11 says should have gone when the header replaced it.

**One residual, and it is in the repository rather than the scratchpad.** That header documents the
same unbounded predicate — `ALTER TABLE span_ingest_offsets DELETE WHERE startsWith(partition_key,
'<topic>')` — so a person substituting `spans` for `<topic>` reaches the same outcome by hand.
`ingeststate/keepermap.go` says the key is `"{topic}:{partition}"`, so a prefix test is the wrong
shape for it twice over: `spans` matches the real `spans:0` *and* every probe topic named
`spans-probe-…`. **The narrow fix is equality on the topic component** —
`WHERE splitByChar(':', partition_key)[1] = '<topic>'` — which cannot reach a topic that merely
starts the same way. ~~**Not done here: `tools/` is code and this pass was scoped to
documentation.**~~ **Fixed 2026-09-08**, and more simply than that: the key is `topic:partition`,
so appending the separator — `startsWith(partition_key, '<topic>:')` — is exact on the topic
boundary and `'spans:'` cannot match `spans-probe:0`. The reason is in the header beside it.

**And one step the deleted script had that the header does not**, recorded so deleting it loses
nothing. A run leaves a consumer group and a topic behind as well as rows, and the header stops at
the rows:

```sh
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --delete --group '<group>'
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --delete --group '<group>-usage'
kafka-topics.sh          --bootstrap-server localhost:9092 --delete --topic '<topic>'
```

Deleting the topic is also what makes the watermark question moot: with the topic gone, the rows
keyed `<topic>:<partition>` are unreachable by any consumer, so the two `ALTER TABLE … DELETE`
statements are tidiness rather than necessity — and tidiness is a poor reason to run an unbounded
predicate against a `KeeperMap` guard. ~~**These three lines belong in `rebalanceprobe`'s header beside the rest of the teardown.**~~
**Added 2026-09-08.** They were still missing on the day this said they belonged there, which is
the ordinary way a "belongs in" sentence becomes a thing nobody did.

## 4a. Carried forward from the plan's own follow-up sections

`phase-1-plan.md` §7c and §7d hold follow-ups that never became rows. Most are struck through as
resolved. These are not, and two of them matter here.

**The receiver's rate limiter fails open when Valkey is unreachable.** ~~It belongs in the same
decision as G6~~ — **wrong, and corrected 2026-09-07 by D2 / [ADR 0104](../architecture/decisions/0104-a-limiter-that-cannot-count-leaves-the-door-open-and-bounds-it-in-process.md).**
The allowance bounds volume and therefore the bill; the limiter bounds rate and therefore the
platform. A paid tenant sending at exactly their limit forever produces an unbounded bill with the
limiter working perfectly, so this is **G5's** question and never was G6's. The door stays open —
losing telemetry because our Valkey is down is the worse failure — and gains a bounded in-process
fallback engaged only on the error path, because `allowance.go` is explicit that *"no paying
customer is ever refused for volume"*, so nothing at all bounds a paid plan while the counter is
unreachable. **One build row**, sized as a multiple of the configured limit, per receiver instance
and described as such.

**No profile endpoint on the receiver.** Already named in §3 as what blocks the re-encode budget.
Recorded here too because §7c calls it permanent rather than pending, and it is neither — it is one
decision about who may reach the admin port.

**Two mobile UX items** — the waterfall hides the `detached` and `skew` tags for want of room, and
the span-detail sheet is a fixed 55dvh with no drag-to-resize. Neither loses information; both are
cramped. Not gate items, and worth doing whenever those screens are open anyway.

**The `ALTER TABLE spans DELETE` cascade gap** appears in §7c as a trace-detail correctness note
and again under P1-77 as a deletion obligation. It is one defect with two consequences, and §2's
scoping note is where it should be solved.

## 4c. ~~The other open decisions~~ — D1–D4, all four taken 2026-09-07

**The four decisions this file counted as "no code" are decided.** They were the cheapest items on
the list and the ones most easily deferred forever, and one of them turned out not to be a decision
at all.

| | the question | answer | ADR |
|---|---|---|---|
| **D1** | `url.full` carries the query string and the fragment while the option that governs the query is off (§1c) | **a live defect, not a question.** One switch governs everything after the path, off by default | [0103](../architecture/decisions/0103-a-url-leaves-the-page-trimmed-to-its-path-unless-the-page-asked-otherwise.md) |
| **D2** | the receiver's rate limiter fails open when Valkey is unreachable (§4a) | stays open, bounds itself in process — and **it belongs with G5, not G6** | [0104](../architecture/decisions/0104-a-limiter-that-cannot-count-leaves-the-door-open-and-bounds-it-in-process.md) |
| **D3** | are sampling and masking customer controls or support actions (§9 q4) | both are customer controls; only masking's off-switch is guarded; the API validates on write | [0105](../architecture/decisions/0105-sampling-and-masking-are-the-customers-settings-and-only-the-off-switch-is-guarded.md) |
| **D4** | network monitoring and SIEM have no home in any phase (§4b) | both out of scope: network telemetry is accepted, never collected; SIEM is never a screen | [0106](../architecture/decisions/0106-network-telemetry-is-accepted-and-never-collected-and-siem-is-out-of-scope.md) |

**D1 was not a decision.** It is the only one that was checked against the store rather than
reasoned about, and the check found a page-view record carrying a 2.4 KB query string with a
customer's postal address in it, on a project configured to send no query string. It is now a fixed
Tier 1 defect with tests on both sides, and the reason it sat in a "decisions with no code" row for
a fortnight is that nobody had looked.

**Two things this section said are corrected rather than quietly re-filed:**

- It said *"three of open-decisions' Part 1 sections"* and listed four. The count was stale.
- **§1f is not open and was not when this was written.** `conventions.py` reads both the
  specification's `container.*` names and `dockerstats`' spellings for the same five quantities, so
  no producer had to be picked, and `InfrastructureScreen.tsx` already ships the flat list with a
  kind filter that the recommendation asked for. The code had answered it; the document had not
  been re-read. It is struck in open-decisions with the measurement kept.

**§1e — what we accept at the front door — stays open and stays right.** It already recommends
adding nothing to the ingest surface until Phase 1 closes, and that is an ordering answer whose
inputs do not exist yet. It is not counted as an undecided D row; it is a decision scheduled for
after Phase 2 scoping.

## 4b. Three categories with no home in any phase — **two now have one**

§8 of the plan recorded the first two and nothing had moved them. The third was found on 2026-09-07.

- ~~**Network monitoring (category 10)**~~ **DECIDED 2026-09-07, D4 / [ADR 0106](../architecture/decisions/0106-network-telemetry-is-accepted-and-never-collected-and-siem-is-out-of-scope.md).**
  The contradiction is not accepted: we build no agent. Network telemetry is *accepted* as OTLP
  from a producer the customer runs — OBI, Beyla, Hubble, Tetragon — and that is the whole
  commitment. No screen.
- ~~**SIEM / security (category 11)**~~ **DECIDED 2026-09-07, D4 / ADR 0106.** Out of scope for
  Phase 1 and Phase 2, and not re-opened as a screen. The nearest thing we ever build is export.
- **F29 — LLM / AI-app observability.** In `requirements.md` as *TO SPECIFY*, and in no phase's
  F-list. **Still the only homeless one.** See below: it is cheaper than it looks and narrower than
  it sounds.

**None blocks Phase 2.** Two are answered before Phase 2 scoping rather than during it, which was
the point: "no home in any phase" is how a category quietly becomes a commitment nobody made. F29
is the one that still needs the call, and it is the one where the answer is *build a narrow thing*
rather than *decline*.

### F29, checked rather than assumed

**No door is needed, and that is the first thing to get right.** `spans.span_attrs` is a ClickHouse
`JSON` column, so an application instrumented with OpenTelemetry sends `gen_ai.request.model`,
`gen_ai.usage.input_tokens` and the rest, and those **land, store and are queryable today**. They
already appear in Traces and on the Service map.

Doors are organised by *how* data arrives — pushed OTLP, probes we run, browsers, pollers — never
by what the data is about. `gen_ai` is a kind of span, not a kind of entrance, and a door for it
would be a second way into a room that is already open. What is missing is a **screen and a metric
contract**, the same shape as `INFRASTRUCTURE_METRIC_NAMES` and `web_vitals`.

**The customer side is nearly automatic too.** Dedicated auto-instrumentation packages exist —
`opentelemetry-instrumentation-genai-openai`, `opentelemetry-instrumentation-google-genai`, one for
the Claude Agent SDK advertising *"zero code changes required"*, and coverage for OpenAI Agents,
LangChain and LlamaIndex. It is a `pip install` rather than wrapping every call.

**So the honest claim today is narrow and true:** *"if your app is instrumented, its LLM calls
arrive and are visible in Traces alongside everything else."* Not *"we do LLM observability"*,
which implies a tokens-and-cost screen that does not exist. Three of the four links work; the
fourth is the screen.

### MLflow is a producer, not a competitor

Worth recording because the opposite conclusion is the obvious one and it is wrong.

**MLflow can export in OTLP `gen_ai.*` format** — `MLFLOW_ENABLE_OTEL_GENAI_SEMCONV` plus an OTLP
endpoint. Its own documentation states the intent: *"AI observability data is never locked into a
proprietary format and any compliant tool can produce or consume it."* So a customer keeps MLflow
for building models and sends their production traces here, through door 1, with no code from us.

### The scope line, so this is not re-opened as "should we build MLflow"

| | Whose question | Ours? |
|---|---|---|
| An app **calling** a model in production — tokens, cost, latency, errors, in the same trace as the HTTP request and the database call | an engineer running a product | **yes — this is F29** |
| **Building** models — experiment tracking, model registry, artifacts, evaluation | a data scientist during development | **no** |

The second is not an extension of this product. It is a different data model (runs, experiments,
gigabyte artifacts), a different buyer, and not observability — it is the workshop where a model is
made, not the production system it runs in. MLflow's GenAI tracing is a recent addition to it, not
what it is for.

**The differentiator for the half that is ours is the same one as everywhere else here:** an LLM
call sits in the same trace as the request that triggered it and the query beside it. A dedicated
LLM tool cannot do that, because it does not know the rest of the customer's system exists.

### When to build it

**When `gen_ai.*` stabilises, or when a real customer sends those spans and asks why nothing reads
them.** The conventions are still Development status as of 2026 and the transition plan says
stability is pending.

That timing is not caution for its own sake. `INFRASTRUCTURE_METRIC_NAMES` holds five `container.*`
names written from a specification, and `docker_stats` emits none of them — a contract built
against a moving spec that no producer satisfies, discovered a year later. That cost has been paid
once already this week.

## 4d. Structure — what a deep read found, and what it did not

Asked whether the codebase needs refactoring before Phase 2 closes the door on it, the honest
answer is **no broad refactor, and three specific seams.** The architecture was read rather than
assumed on 2026-09-07.

### What holds, checked rather than trusted

| Check | Result |
|---|---|
| a `service.py` importing FastAPI or raising `HTTPException` | none |
| a `router.py` containing SQL | none |
| a `service.py` containing raw SQL | none |
| mutable module-level state in a service | none |
| largest non-test source file | 754 lines |

The patterns are present and correctly chosen. **Repository** — `data/clickhouse/base.py`, and it
is the strongest thing here: a structural test parses every module and rejects the three near-misses
that contain the tenant text and still leak. **Strategy with a template method** — `Processor[S any]`
writes the poll/offset/commit/heartbeat loop once and lets each signal supply four methods, so a
Phase 2 pipeline is an implementation rather than a fork. **Adapter** — `packages/contracts`, one
envelope before the broker, storage never learning that doors exist. **Facade** —
`shared/api-client.ts` behind fourteen per-feature modules.

**Singleton and Factory are absent and should stay absent.** Dependencies arrive through FastAPI's
`Depends`; adding either would be a step backwards.

**Phase 2 has somewhere to attach**, which is the thing a pre-launch codebase usually gets wrong.
Services take a repository rather than a `Request` — `seed.py` already calls
`signup.service.provision_tenant` outside HTTP, so a findings engine can call any of them. The
Celery scheduler is shaped for periodic evaluation already. A new worker pipeline is an interface
implementation.

### A1 · The authorization seam — the only item that gets harder after Phase 2

**47 routes. A role is read in exactly one module** — `apikeys/service.py`, four call sites, one
`frozenset`.

Correct today, because §9 decided Phase 1 is single-operator and every user is an owner. It stops
being correct at the plan's M8 (P1-76) or Phase 2's F26, and then **43 routes have no authorization at all** —
creating checks and integrations, posting deploys, rotating webhook secrets.

**Why it cannot wait:** it is a cross-cutting concern retrofitted into 47 places, after a phase of
feature work has made it eighty. That is the change that never gets made.

**The seam already exists.** Every route takes
`CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]`, so the fix composes with
what is there rather than replacing it:

```python
RequireOwner = Annotated[Identity, Depends(require_role("owner", "admin"))]
```

One line per route, no service changes, and the policy lives in one module instead of four
`if actor.role not in …` checks scattered through one service. **Today it changes no behaviour** —
every Phase 1 user is an owner — which is exactly what makes it safe to introduce now and unsafe to
introduce later.

### A2 · The contract-test pattern is not applied everywhere its root exists

Three constants are restated across the API/scheduler boundary, deliberately, because *"the two
deploy separately"* — sound reasoning, and `test_synthetics.py` states the mitigation exactly:

> *"Restating a name is how two services drift apart; this is the assertion that turns a rename
> into a failing test instead of an empty chart."*

Door 2 and door 4b's metric names both have that test. **The third does not: `SUPPORTED_POSTGRES_AUTH`
in `integrations/service.py`, added on 2026-09-06, restates the scheduler's `AUTH_RDS_IAM` with a
comment saying a change must change both, and nothing enforces it.** Same root, fixed in two of
three places — rule 5, and the omission is mine.

Three lines to fix, and worth a sweep beyond it: **31 metric-name constants cross a service
boundary**, and the two tested pairs cover the API against the scheduler. Nothing checks the Go
worker's ClickHouse column names against the API's readers, which is the same drift with a longer
fuse.

### A3 · `packages/py-platform`, smaller than §7b implies

§7b calls for a shared workspace member. Measured: **299 lines**. `context.py` and `redaction.py`
are byte-identical between `api` and `scheduler`; `logging.py` differs in **two lines**, both import
prefixes.

A two-hour job, and it matters for what it prevents rather than what it removes — a redaction rule
fixed in one copy and not the other is a privacy bug that passes every test in both services.

### Deliberately not changed

**The restated-contract pattern itself.** It looks like duplication and is a correct decision: a
scheduler that cannot start because a control-plane package moved is a scheduler running nobody's
polls. Keep it, test it.

**One worker container, six pipelines** (ADR 0050). §7b says split when measurement says to.
Measurement has not.

**Fourteen per-feature `api.ts` modules.** The right amount of duplication — the alternative
couples every screen to one generated schema.

**No logic was found that is wrong by design.** All three items above are structural.

## 4e. Read this file against rule 14

**Nothing is deployed and nobody is a customer.** Recorded as `CLAUDE.md` rule 14 on 2026-09-07,
and it changes how several items above should be read — because a recommendation shaped by a
constraint that does not exist is answering the wrong question.

**What it does *not* change.** Every gate item stands. G1–G7 are all things that must be true
*before* the first customer, and being pre-deployment is exactly why they are cheap now. A1 is the
clearest case: the argument for doing the authorization seam first is *entirely* that no behaviour
depends on it yet.

**What it does change, and it is worth being explicit:**

- **Migrations are not append-only yet.** Wherever this file or `open-decisions` reasons about a
  deploy window, a column arriving before the code that writes it, or a customer part-way through
  their allowance when a migration lands — none of that applies. **Rewrite the migration.** T1-3 was
  removed by adopting 1a; it could equally have been removed by editing 0016, and that is the
  cheaper answer while `alembic downgrade` costs nothing.
- **`integrations.config_encrypted` should be renamed.** ADR 0076 records that the column holds
  plain UTF-8 JSON and that *"the column name predates this decision and is now misleading"*, and
  lists renaming under Consequences. There is no data to migrate and no reader outside this repo.
  It is a rename, and rule 6 asks for intention-revealing names.
- **G7's version-default question is smaller than it reads.** *"A fix we ship reaches nobody without
  the customer editing their HTML"* describes customers who do not exist. Pick the scheme that is
  right for the end state — versioned immutable paths with an auto-updating default — and ship it
  once, rather than treating it as a trade-off to be balanced.
- **The paid-tier cap number (G6b) is still genuinely blocked**, and for a reason rule 14 does not
  dissolve: there is no usage data because there is no usage. Being pre-deployment is *why* it is
  blocked, not an argument for guessing.

**The one thing rule 14 sharpens rather than relaxes:** anything a customer will paste into their
own configuration — the ingest hostname, the SDK URL scheme, the ingest key format — is a one-way
door the moment someone uses it. Those are worth more care now, not less, precisely because
changing them is free today and expensive on the day after launch.

## 4f. Found while building, and not yet rows anywhere

**Everything above was found by reading. This section is what was found by *running*** — nine items
that did not exist as work on 2026-09-06 and are not counted in §5a, because inventing rows for
them mid-count would make the totals unauditable. They are listed so the next person does not have
to rediscover them.

**Two block a customer using what was just built:**

- **There are no `/verify-email` or `/reset-password` screens.** The endpoints exist and are
  tested; `apps/web` has no route for either, so every link mailed today lands on a 404. The link
  points at the app deliberately — a customer clicking in a mail client must reach a screen, not
  JSON — so the screens are the other half of P1-70 and P1-71, not a nicety.
- **`APP_BASE_URL` has no default and must be set.** Deliberate: a link built against a guessed
  host is a link to nowhere, and the failure would be a customer who cannot verify rather than a
  stack trace. `.env.example` carries the dev value.

**Three are consequences of ADR 0108, written into it and repeated here:**

- **The rollup's row count is still unbounded.** Grouping is fixed; storage is not. A customer with
  per-id URLs mints one `web_vitals_1m` row per order per minute even with a route configured.
- **`route` is a single `init()` option**, so a single-page app that navigates client-side stamps
  one template across a whole session. There is no per-navigation route API.
- **No row proving any of it came from a real browser.** The schema facts are from
  `system.columns`; every row was written by the writer under test.

**One was found by a test failing for a reason nobody had considered**, and it is the most
interesting thing in this section:

- **A worker crash duplicated metric rows, and block-level deduplication did not stop it.**
  `test_synthetics_live.py::test_the_phases_of_a_probe_sum_to_the_duration_it_reported` failed with
  `81.026 == 40.513 ± 4.05` — **exactly 2×**, which is a double-count and not flakiness.
  `CHECK_RUNS_SQL` groups by the exact nanosecond and reads the duration with `anyIf` and the phases
  with `sumIf`, so duplicate rows double one and not the other. Two probe instants were found
  carrying **10 phase rows where a check writes 5**, at 19:39:38 and 19:59:38 — both inside the
  window in which ClickHouse was wedged at its memory ceiling and the worker died mid-insert with
  *"pipeline ended; the worker cannot continue"*.

  **Every telemetry table already sets `non_replicated_deduplication_window = 1000`** — checked on
  the live server, and `trace_summaries` is the one exception. So the protection was on and did not
  help, because block deduplication catches an *identical block*: a worker that dies mid-batch and
  re-consumes from its last committed offset rebuilds batches on different boundaries, so the same
  records arrive inside a block with a different hash and are inserted again.

  **Three pipelines already solve this and the rest do not.** `derive`, `serviceedges` and `vitals`
  carry an explicit `insertdedup` token derived from the row content; `metricwriting`, and the span
  and log writers, rely on the block hash alone. Phase 1's invariant is *"no data loss between
  receiver and storage"* — this is its mirror image, data **gained**, and nothing in the suite was
  watching for it.

  Not fixed here. It is a design question about idempotent writes across a restart, it wants its own
  measurement, and the honest thing today is that it was found rather than that it was handled. The
  failing test will pass once its window rolls past 19:59; that is the residue ageing out, not the
  defect being fixed.

**Four are smaller:**

- ~~**The schemaprobe suite is once-per-minute** … a second run reads 0 — **green, having verified
  nothing**.~~ **Wrong, and corrected 2026-09-08 by running it. FIXED the same day.**

  The mechanism was right and the consequence was not. `rollupToken` folds the minute bucket and
  every other fixture field was a literal, so a second run inside one wall-clock minute derived an
  identical `insert_deduplication_token`; `web_vitals_1m` carries
  `non_replicated_deduplication_window = 1000`, so ClickHouse **dropped the insert and returned
  success**. But the suite did not go green — all eleven assertions expected a non-zero value, so
  it went **red, with a misleading error**: `quantilesMerge` over an empty set raises
  `Unexpected inf or nan to integer conversion`, which reads as a quantile or schema bug and names
  deduplication nowhere. That is arguably worse than a false green, because it is the message that
  could talk somebody into weakening the token — and the token's stability is what stops a retried
  batch double-counting a customer's usage.

  **I recorded the false-green claim from an agent's report without running it**, which is the
  thing rule 12 exists to prevent, in the section written to catch things nobody had run.

  Fixed with both shapes rather than either: a per-run discriminator in the probe's tenant (which is
  in the sort key, so the rows differ honestly and the token differs with them), and assertions that
  refuse a zero expectation outright plus an `assertInsertLanded` check that names
  `insert_deduplication_token` as the cause. `rollupToken` and the dedup setting are untouched.
  **Verified here: two runs at 19:41:34 and 19:41:35 UTC, the same minute, both `ok`.** Note the
  suite needs `PROBE_CH_ADDR=127.0.0.1:19000`, which its own header documents.

  **The raw insert path never had it** — `readingToken` folds `ReceivedAt` at nanosecond precision,
  so it differs every run; on the failing run every raw assertion passed while every rollup one read
  zero. `metricwriting`'s probe has no `insert_deduplication_token` at all, so it is not affected —
  read rather than run.
- **G1's CSP has not met a browser** — see §1.
- **The worker image has not been rebuilt** since ADR 0108, so the running dev receiver still
  writes empty routes.
- **The demo serves a stale SDK bundle.** A single-file bind mount keeps the old inode, so
  `image-provider` has the pre-0103 build until it is restarted.

## 5. The order I would actually take

**~~First — A1, the authorization seam.~~ Done 2026-09-07, with A2 and A3 alongside it.** The
reasoning stands as written: it was the only item that gets materially harder after Phase 2, it
changed no behaviour, and it took a day rather than the fortnight it would have later.

**Now — the safety gate.** G4 then G3, in that order: G4 is small, and it removes the stated
reason G3 was never fixed. Both are open holes, and G3 is an SSRF surface every sibling boundary
already closes. T1-5 belongs here too — it is a `CHECK` or a test, and it guards the cap G6 extends. Then G1, because TLS is what makes exposure
possible at all.

**Next — the account gate.** G2, and P1-70/71 behind it. This is the largest block of work in the
list and the one whose absence is invisible until a customer is locked out.

**Then — the commercial layer.** G6.1's paid-tier browser cap first, because it is small and it is
the only thing between a scraped key and a bill we cannot pass on. Then P1-73 and the rows behind
it. G6.3 — block or bill on overage — has to be answered before P1-74, and G6.2's number is
correctly blocked on real RUM traffic, so it waits for P1-47.

**Then — the measurements.** G5, then M1's four blank budgets, M3's pprof on the receiver, and
finally M2, P1-50's soak, which is the last thing before Phase 2 because it exercises everything the
rest of this list just changed. (M4, the RUM breakdowns, is in the parallel list below rather than
here — it is a screen change, not a measurement of the platform.)

**Alongside the account gate, because it blocks the same documentation:** G7's SDK distribution.
P1-54 cannot write door 3's setup guide until the script tag has a URL, and the version-default
question inside it is a decision rather than a task.

**In parallel, whenever a screen is being touched anyway:** P1-47's breakdowns, P1-54's remaining
docs, and the Tier 2 corrections. *(§1c's `url.full` question was on this list and is not work any
more — it was decided and built on 2026-09-07 as D1 / [ADR 0103](../architecture/decisions/0103-a-url-leaves-the-page-trimmed-to-its-path-unless-the-page-asked-otherwise.md),
and §4c records what the decision found.)*

**Deliberately not before Phase 2 — the X series, and there are three of them:**

- **X1 · P1-57's cold tier** — real, but not a launch blocker at small scale. §3 has the detail.
- **X2 · P1-68's retention downgrade path** — the plan already records it as not a one-way door,
  and today's fixed `pro`-shaped retention over-serves rather than under-serves.
- **X3 · P1-26 self-instrumentation** — §7a already defers it until the platform runs somewhere
  real.

**Corrected 2026-09-07: the count row said `X1–X4 … 4` and this paragraph named three.** The whole
file was searched for a fourth deliberately-deferred item and there is not one — §4b's network
monitoring and SIEM are *out of scope entirely* (D4 / ADR 0106) rather than deferred, F29 is
recorded as homeless rather than deferred, and §1e is a decision scheduled for after Phase 2
scoping which this file already says it does not count. So the count is 3, not 4, and the members
are numbered here so the next reader can check it by counting them.

---

## 5a. The count

**37 items. 30 are build work; 4 are decisions with no code; 3 are deliberately deferred past
Phase 2.** Sizes are relative — S is under a day, M is a few days, L is a week or more and usually
carries a decision inside it.

**Taking the D-series moved build work from 28 to 30 and did not move the item count.** D1 turned
out to be a live defect and is now a fixed build item; D2's decision spawns the limiter's in-process
fallback. A decision list nobody works through is a list that hides work, not one that has none —
but the work it hides was already on the list as a decision row, so it converts a row rather than
adding one.

| | Group | Items | Build work | Done |
|---|---|---|---|---|
| **G1–G7** | Gate — nothing ships to a paying customer until these are true | 11 | 9 | **7** |
| **A1–A3** | Architecture — cheap now, expensive after Phase 2 | 3 | 3 | **3 — complete** |
| **C1–C7** | Commercial layer (M8 in the plan) | 7 | 7 | 0 |
| **M1–M4** | Measurements and exit criteria | 4 | 4 | **2** |
| **F1–F5** | Findings and gate defects | 5 | 5 | **4** |
| **D1–D4** | Decisions — no code was expected, two rows appeared | 4 | 2 *(found by deciding)* | **4 decided — complete**, 2 of 2 built |
| **X1–X3** | Deferred past Phase 2 deliberately | 3 | 0 | n/a |
| | **totals** | **37** | **30** | **18 built** |

**18 of 30 build items done, as of 2026-09-08**, up from 8 the day before. Three series are
complete or nearly so: **A1–A3** the architecture seams, **D1–D4** the four decisions and both
build rows they turned out to hide, and **F1–F5** bar one — only F4's re-run remains. The gate is
**7 of 9**, with G6a and G7 left.

**The status board above was stale until 2026-09-08 and said so in two directions at once** — §1
recorded G5 as *"Done 2026-09-07, both halves"* while the board still showed it unstarted, and G1,
M3, M4, F1, F3, F5 and D2's fallback had all landed without the board moving. It is rewritten
item by item rather than by series, because a row covering four items cannot say that two of them
are done.

**Three numbers in this section were wrong until 2026-09-07 and are corrected above.** The headline
said ~~41 items~~ while the rows summed to 39, and said ~~6 decisions with no code~~ where the rows
carry 4 (G6b, G6c, D3, D4) — taking D1 and D2 had converted two decision rows into build rows and
the headline had added them instead. On top of that, P1-57's cold tier was counted in **both** the
measurement row and the deferred row, and the deferred row claimed a fourth member §5 never named.
Removing the double count and the phantom leaves 37 items and 30 build items, and the totals row
above exists so the next reader can add the column up rather than trust the sentence.

**What taking them found, which is the argument for taking the cheap rows early:** D1 was not a
decision at all but PII sitting in the store — a page-view record carrying a customer's postal
address on a project configured to send no query string — and it is now fixed with negative-
controlled tests on both sides. D2's real content was that the file had filed it under the wrong
gate item. D4 removed two categories from the product that had been quietly accumulating an
obligation since August. Only D3 was the shape it looked.

### Inside the gate, which is the only series still in progress

| | | |
|---|---|---|
| G2a · P1-69 email | ✅ | provider, DNS, port, adapter, composition root, real send |
| G3 · poller SSRF boundary | ✅ | `2f06a98` |
| G4 · poller certificate verification | ✅ | `f26ec33` |
| G1 · TLS edge | ✅ | two site blocks and the header snippets; `caddy validate` clean, headers curled |
| G2b · P1-70 verification | ✅ | writes the column that had existed unwritten since 0001 |
| G2c · P1-71 password reset | ✅ | single-use token, and every live session ended via `sessions_valid_from` |
| G5 · ingest kill switch | ✅ | valve closed by hand → `429` at t+20s; reopened → `200` |
| G6a · paid-tier browser cap | ⬜ | **the last unblocked gate item** |
| G7 · SDK distribution | ⬜ | blocked on a deployment target |
| G6b · the cap number | ⛔ | blocked on real RUM traffic, correctly |
| ~~G6c · block or bill on overage~~ | ✅ | **decided 2026-09-07 — [ADR 0107](../architecture/decisions/0107-over-the-allowance-we-stop-and-say-so-and-the-customer-may-raise-the-ceiling.md).** Stop and say so, with a ceiling the customer may raise. **P1-74 unblocked** |

~~**The safety half of the gate is closed**~~ — **the gate is 7 of 9.** The edge sets headers, the
account can be recovered, the stop valve exists and has been watched biting. **What is left is a
cap and a URL**: G6a, which needs an allowance that does not depend on a service-count ceiling, and
G7, which needs somewhere to publish a file to.

~~**D1–D4 has moved not at all**~~ — **all four taken 2026-09-07**, and they were the cheapest
items on the list exactly as this said. §4c has the four answers and the two things the file itself
had wrong. What is left of them is one build row: the limiter's in-process fallback, which belongs
beside G5.

**Five are large**, and every one carries a decision rather than only code: P1-69 email, P1-73
payments, P1-76 project management, the ClickHouse purge path, and G7's SDK distribution.

~~**Eleven are small**~~ — **ten**, since G6a was struck on 2026-09-08 for not being one. Three of
them were the safety gate — G3, G4 and A2 — and all three are done. **The cheapest quarter of this
list closed the two open holes and the untested contract**, which is what the sentence claimed and
is now true rather than predicted.

### The ten that are under a day

| | |
|---|---|
| G3 | poller SSRF boundary — ✅ **done** |
| G4 | asyncpg `verify-full` — ✅ **done** |
| ~~G6a~~ | ~~paid-tier browser cap~~ — **struck 2026-09-08: it is not under a day.** `allowance/postgres.go:132` returns unenforced when `max_billed_services` is NULL and the browser cap lives *inside* the enforced branch, so a paid plan takes an early return no cap can be reached from. It needs an allowance that does not depend on a service-count ceiling — a design change, recorded in [ADR 0107](../architecture/decisions/0107-over-the-allowance-we-stop-and-say-so-and-the-customer-may-raise-the-ceiling.md) |
| A2 | contract test + cross-service sweep — ✅ **done** |
| A3 | `packages/py-platform` — ✅ **done** |
| M3 | pprof on the admin listener — ✅ **done** |
| M4 | RUM breakdowns by page and device — ✅ **done** |
| F1 | T1-5 repricing guard — ✅ **done** |
| F2 | T1-6 rig cleanup — ✅ **done 2026-09-07**, by deleting the script |
| F3 | the three tests that cannot fail — ✅ **done** |
| F5 | the three cheap T2 corrections — ✅ **done 2026-09-07** (T2-4, T2-5, T2-7). T2-1/2/3/6 are **F4**, not F5: they need ADR 0094's measurement re-run and captured, which is not a day's work |

## 6. What "ready for Phase 2" means, stated as a test

A stranger can sign up, prove they own the address, recover a lost password, connect a service over
TLS *and paste a script tag that resolves*, see their data, be charged for it on a plan whose
overage behaviour is decided, change plan, and leave — and the platform survives a
24-hour soak with a per-tenant stop valve, no unguarded egress surface, and every §4 budget carrying
a number somebody watched.

Nothing in Phase 2's F-list is blocked by anything above. Every item here is the ordinary product
surface around an engine that already works.

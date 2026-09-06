# Phase 1 v2 — what is left before Phase 2

**Status:** live. Opened 2026-09-06.

`phase-1-plan.md` is the record of what was built and why; it is long because it carries the
reasoning. This file is the shorter question: **what still has to be true before Phase 2 starts,
in what order, and what is already true that the plan still calls open.**

Everything below was checked against the code on 2026-09-06, not read off a status column. Where a
row in `phase-1-plan.md` disagrees with what the code does, this file says so and the code wins.

---

## 0. What the plan says is open and is not

Four things in `phase-1-plan.md` are stale, and two of them are the reason P1-53 looks worse than
it is.

**P1-53's browser-SSRF finding is fixed.** The row says *"browser synthetic checks have no SSRF
boundary at all (`page.goto` bypasses the socket guard the HTTP check uses)"*. `browser.py:322`
now wraps every flow in `PublicAddressProxy`, which is [ADR 0096](../architecture/decisions/0096-a-browser-flows-only-way-onto-the-network-is-a-proxy-that-judges-the-address.md)
shipped, extended by [0098](../architecture/decisions/0098-a-probe-reaches-a-private-address-only-when-two-settings-both-say-development.md).

**P1-53's site-key finding is fixed.** *"A scraped site key can now spend a free org's whole
month"* was closed by the per-door cap — open-decisions §1a, decided and shipped as M5 task zero,
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

`deploy/caddy/Caddyfile` is labelled **SKELETON** in its own first line and carries two TODOs. It
reverse-proxies four routes correctly and does nothing else: **no HSTS, no
`X-Content-Type-Options`, no `Referrer-Policy`, no CSP, and no frame protection.**

Two decisions it defers and this plan should not:

- **One hostname or two.** Its TODO asks whether ingest and the app share a name. **Recommendation:
  two — `ingest.nexovue.tech` and `app.nexovue.tech`.** Ingest is machine traffic at volume and the
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

### M4 · Four budgets in §4 are still blank

`Endpoint stats 24h`, `Log search 1h`, `First screen load`, and **`Data loss under broker restart:
zero`** — the last of which is a Phase 1 invariant with no measurement behind it.

The re-encode budget is marked permanently unmeasurable *"until the receiver exposes a profile
endpoint"*. **Recommendation: add `net/http/pprof` to the receiver's existing admin listener**,
which is not exposed publicly. It is a few lines, it closes a budget the plan calls permanently
open, and it is the same endpoint you would want the first time a receiver misbehaves in
production.

### M5 · P1-50, the 24-hour soak

Never run. The M1 numbers are all short runs, and the failures a soak finds — leaks, unbounded
growth, cache eviction behaviour, disk — are precisely the ones a five-minute run cannot.

**This should be the last thing before Phase 2**, because it is the only test that exercises
everything else at once.

### M6 · P1-57, the S3 cold tier

No ClickHouse storage policy exists — I checked `deploy/clickhouse/`. SeaweedFS is in compose and
serves source maps through `ObjectStore`, not ClickHouse tiering.

Without this, storage grows forever at the hot tier's cost. It is not a launch blocker for a small
number of customers; it becomes one quickly.

### P1-47 · The RUM screen owes its breakdowns

`rum/queries.py` groups by `vital_name` alone. `url_path`, `device_class` and `session_id` are
promoted columns populated on every row and read by nothing — the row asks for *"Core Web Vitals by
page/geo/device"*. `country` has no producer at all, because there is no GeoIP in the system.

**Recommendation: ship page and device; drop geo from the row's wording.** The columns exist so
this is a `GROUP BY`, not a migration. Geo needs a GeoIP decision nobody has taken and should not
be smuggled in as part of a screen change. `MIN_READINGS = 10` is open in the same area.

---

## 4. Evidence defects — the rule 12 tier

Open-decisions Part 2's Tier 2 is six findings that share one root: **numbers were published from
runs whose output was never saved.** T2-2 states it plainly — every headline survives only as
prose, and nobody can re-derive it.

The process fix landed on 2026-09-06 (`26a3d9d`): `rebalanceprobe`'s header now carries the capture
instruction above its cleanup SQL. **The figures themselves are still unverifiable**, and the
honest options are to re-run and capture, or to mark them as unreproduced. Either is fine; leaving
them as confident prose is not.

**T2-4, T2-5 and T2-7 are cheap and should just be corrected** — a stated baseline of 169 against a
suite of 117, three different numbers for one test count, and a claim about `ipaddress` that the
interpreter contradicts.

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

**T1-6 is smaller and worth doing with it.** `cleanup94.sh` takes unvalidated positional arguments
and can wipe `span_ingest_offsets` — the durable counted-through watermark whose loss produces
exactly the silent permanent over-count ADR 0094 exists to describe. A measurement rig that can
cause the defect it measures is a rig whose results cannot be trusted. It lives in a scratchpad and
should either be validated or deleted.

## 4a. Carried forward from the plan's own follow-up sections

`phase-1-plan.md` §7c and §7d hold follow-ups that never became rows. Most are struck through as
resolved. These are not, and two of them matter here.

**The receiver's rate limiter fails open when Valkey is unreachable.** Deliberate, and the
reasoning is sound today: losing telemetry costs more than briefly losing the limit, with body caps
and the broker still bounding damage. The plan's own note says it is *"worth revisiting once a
paying tenant can be harmed by it"* — which is precisely the moment M8 arrives. **It belongs in the
same decision as G6**, because both are about what happens when a paid tenant's traffic is
unbounded.

**No profile endpoint on the receiver.** Already named in §3 as what blocks the re-encode budget.
Recorded here too because §7c calls it permanent rather than pending, and it is neither — it is one
decision about who may reach the admin port.

**Two mobile UX items** — the waterfall hides the `detached` and `skew` tags for want of room, and
the span-detail sheet is a fixed 55dvh with no drag-to-resize. Neither loses information; both are
cramped. Not gate items, and worth doing whenever those screens are open anyway.

**The `ALTER TABLE spans DELETE` cascade gap** appears in §7c as a trace-detail correctness note
and again under P1-77 as a deletion obligation. It is one defect with two consequences, and §2's
scoping note is where it should be solved.

## 4c. The other open decisions, and why they are not gates

Three of open-decisions' Part 1 sections are deliberately *not* in the gate above, and each has a
reason rather than an oversight:

- **§1c** collects the decisions raised and not made — `url.full` carrying the query string and
  fragment, migration 0016's `_EFFECTIVE_FROM`, the counter residual at the shipped sampling
  window, the probe's throwaway org, and what sample revises the 25% split. **The one worth pulling
  forward is `url.full`**, not because it breaks anything but because it is the answer to a
  security questionnaire, and it is the same key-versus-value gap [ADR 0101](../architecture/decisions/0101-spans-carry-query-text-and-the-database-poller-does-not.md)
  just settled for `db.query.text`. Cheap to decide, expensive to be asked cold.
- **§1e** — what we accept at the front door — already recommends adding nothing until Phase 1
  closes, for the ordering reason this whole file argues. It becomes live *after* Phase 2 scoping,
  not before.
- **§9's still-open question 4** — are sampling and masking customer controls or support actions?
  Both are SQL `UPDATE`s by us today. Not a gate, and it stops being theoretical when P1-76 brings
  the screen that would host them. The two must be answered separately: sampling is reversible and
  only changes what is kept next, where masking is irreversible on data already written — and a
  malformed masking document has already once silently stopped masking a project's custom keys.
- **§1f** — which container producer we support, and whether the Infrastructure list becomes a
  tree. Not a gate: the screen fills from host and container metrics today, and the tree question
  cannot be observed until the producer is chosen.

## 4b. Two categories with no home in any phase

§8 of the plan records these and nothing has moved them:

- **Network monitoring (category 10)** needs eBPF kernel agents, which contradicts the standing
  decision not to build collection agents. Parked.
- **SIEM / security (category 11)** is *"evaluate later"* in the vision doc and appears in no plan.

**Neither blocks Phase 2**, but both should be answered *before* Phase 2 scoping rather than during
it, because "no home in any phase" is how a category quietly becomes a commitment nobody made.

## 5. The order I would actually take

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

**Then — the measurements.** G5, the four blank budgets, pprof on the receiver, and finally P1-50's
soak, which is the last thing before Phase 2 because it exercises everything the rest of this list
just changed.

**Alongside the account gate, because it blocks the same documentation:** G7's SDK distribution.
P1-54 cannot write door 3's setup guide until the script tag has a URL, and the version-default
question inside it is a decision rather than a task.

**In parallel, whenever a screen is being touched anyway:** P1-47's breakdowns, P1-54's remaining
docs, §1c's `url.full` question, and the Tier 2 corrections.

**Deliberately not before Phase 2:** P1-57's cold tier (real but not a launch blocker at small
scale), P1-68's retention downgrade path (the plan already records it as not a one-way door, and
today's fixed `pro`-shaped retention over-serves rather than under-serves), and P1-26
self-instrumentation, which §7a already defers until the platform runs somewhere real.

---

## 6. What "ready for Phase 2" means, stated as a test

A stranger can sign up, prove they own the address, recover a lost password, connect a service over
TLS *and paste a script tag that resolves*, see their data, be charged for it on a plan whose
overage behaviour is decided, change plan, and leave — and the platform survives a
24-hour soak with a per-tenant stop valve, no unguarded egress surface, and every §4 budget carrying
a number somebody watched.

Nothing in Phase 2's F-list is blocked by anything above. Every item here is the ordinary product
surface around an engine that already works.

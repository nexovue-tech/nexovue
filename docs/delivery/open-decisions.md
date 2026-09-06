# Open decisions and the findings backlog

**Status:** live. Opened 2026-09-05, after the P1-53 security pass and the three
workstreams it produced.

This file holds two things that have nowhere else to live:

1. **Decisions waiting on Raj** — questions raised during work, with the evidence
   behind them and a recommendation, but *not decided*. Nothing here is settled.
   When one is decided it moves to an ADR and leaves this file.
2. **A findings backlog** — defects that were demonstrated but not yet fixed,
   ranked. These are not open questions; they are known work.

An ADR records a decision. This file records the absence of one. If you are an
agent reading this to orient yourself: **do not treat anything in Part 1 as
decided**, and do not implement a recommendation here without asking.

---

# Part 1 — Decisions waiting on Raj

## 1a. ~~The allowance split should be a cap, not a partition~~ — DECIDED, built

**Status: decided 2026-09-05 and shipped as M5 task zero.** Kept here rather than
deleted because two things the implementation found are worth carrying into the
ADR's next reader, and because this is the worked example of a question leaving
this file.

**What shipped is the cap**, and it needed two corrections the recommendation
below did not have:

- **A bare cap is not enough.** An organization could spend the whole plan at the
  server door and a further share at the browser door — 7.5 GiB of a 6 GiB plan.
  Both doors are judged against the total as well as their own.
- **That total must be the *charged* figure**, `min(browser spent, cap)`, not the
  raw one. The meter lags the door by design, so a burst through a scraped key
  can be metered past the cap before the door refuses; charging that overshoot to
  the pool would close the server door and hand the attacker the exact outcome
  the cap exists to deny.

Both were found by tests failing, not by review. The reasoning below is otherwise
as it was.

### What was built first, and replaced

ADR 0097 and migration 0016 originally split the free tier into two boxes —
browser 1.5 GiB, server 4.5 GiB — with `budgetFor` picking one box per door and
`spent` judging that box alone.

### The problem Raj found

> *"if customer not use browser check the 1.5 gb where its? if customer not use,
> we don't split, still 6gb pool?"*

It was stranded. For a customer sending only server telemetry:

```
server spend  = total − browser = 6 GiB − 0 = 6 GiB
server budget = 4.5 GiB
→ refused at 4.5 GiB, with 1.5 GiB unused
```

A quarter of the free tier lost for nothing — and nobody could reach that
quarter, because P1-39, P1-58 and P1-47 were all `todo`. A 25% cut to the free
tier, delivered as a security fix.

### What shipped instead

| Door | Rule |
|---|---|
| Browser | refused past its cap, **and** past the plan's charged total |
| Server | refused past the plan's charged total |

where the charged total is `server bytes + min(browser bytes, cap)`.

- **No browser use** — nothing is charged for the browser, so the server door
  reaches the whole 6 GiB. Nothing stranded.
- **Scraped key floods the browser door** — it stops at its cap, and because only
  the cap is ever charged, the server keeps its 4.5 GiB however far the meter
  overshoots.
- **Nobody exceeds the plan** — both doors are judged on the charged total, so
  the server door cannot be filled and then topped up through the browser door.

### What it cost to change

`Usage.spent` / `budgetFor` / `browserChargedBytes` in
`apps/receiver/internal/allowance/allowance.go`, the Python mirror (now
`billing.browser_cap_bytes`, with `server_included_bytes` deleted from the
response — under a cap it is not a limit, it is a number that moves with usage),
and ADR 0097's §1. The migration and the `browser_share_percent` column are
unchanged: still a share, still a `plan_prices` column, still provisional.

---

## 1b. What paid plans get

**Status: open. No recommendation is binding; the numbers do not exist yet.**

### The question

> *"if customer based plan its different — free have 6 gb, we split 1.5, for
> other plan what?"*

### What is true today

```
plan   price   included/svc   max_billed_services   browser_share_percent
free   $0      2 GiB          3                     25
pro    $6.00   10 GiB         NULL                  NULL
scale  $5.00   25 GiB         NULL                  NULL
enterprise — no price row at all
```

`max_billed_services` is NULL on every paying tier, and
`apps/receiver/internal/allowance/postgres.go` makes that mean **not enforced**.
Its own comment states the reasoning, and it is right:

> this line is what keeps a customer who pays from ever being refused for
> volume — which would be a much larger failure than the hole this gate closes.

**So paid customers are never refused, there is no pool to split, and nothing is
stranded.** The 1a problem does not arise for them.

### But there is a different hole for paid plans

The same stolen-site-key attack produces a different harm on each tier:

| Plan | What a scraped site key does |
|---|---|
| free | burns the allowance → **customer goes blind**. Operational harm. Fixed by ADR 0097. |
| paid | nothing stops it → **unbounded ingest on nexovue's own cost**. Financial harm, landing on us. |

And it cannot currently be billed back, because that was never settled.
Migration 0015 says so in its own words:

> ADR 0056 says both that we block above the allowance and that overage stays
> live and bills — two different products — and nothing here resolves that…
> what happens when a paid tier exceeds its included volume is still undecided.

### Why the cap shape (1a) matters here too

A **partition** cannot protect a paid tier: splitting a pool requires a pool, and
paid plans have none. A **cap** works fine on an unlimited plan, because a lid on
one tap does not require a limit on the tank. That makes this possible:

| Plan | Browser door | Server door |
|---|---|---|
| free | cap 1.5 GiB | refuse past 6 GiB |
| pro / scale | **capped** | **never refused** |
| enterprise | by agreement | never refused |

The guarantee that a paying customer's servers can never be turned away is
untouched.

### What is actually open

1. **Do paid tiers get a browser cap at all?** Recommended yes — it is the only
   thing between a scraped key and an unbounded bill. Not decided.
2. **What number?** Free is 25% of a 6 GiB pool. Paid has no pool, so it needs
   either an absolute figure or a percentage of the per-service allowance.
   **No usage data exists to choose one, and it must not be invented.** The
   honest earliest moment to pick it is after P1-47 ships and real RUM traffic
   has been observed.
3. **Block or bill on overage** — the old ADR 0056 question, now load-bearing.
   Everything above assumes paid tiers stay unenforced. If paid tiers ever
   block, they inherit the free tier's blindness problem and need the whole
   treatment.

---

## 1c. Other decisions raised and not made

- **`_EFFECTIVE_FROM` for migration 0016.** Currently `2026-09-04`, already in
  the past, so on a real deployment the split is live the instant the migration
  lands. See T1-3. Adopting 1a removes most of the harm but not the question.
- **The counter residual on the shipped configuration.** The live rebalance
  measurement found a **+0.19% counter over-count** at the shipped 10s sampling
  window. ADR 0094 reasoned this "should not arise at all"; it does. Accept it
  or fix it — the root is that `traceassembly`'s `Stored()` gates a batch's
  offsets on three conditions and the bucket can close after the offsets move.
- **Should the probe run against a throwaway org and project?** Today the rig
  writes into `usage_daily` for the seeded Production project, which is an
  **input to the receiver's refuse-or-accept decision**, not bookkeeping. There
  is no cleanup path for it by design.
- **What sample and what distance revise the 25% split?** The observable is
  `usage_daily.browser_bytes / bytes` per free organization per calendar month.
  Nothing reported it before migration 0016; it now does. The sample size and
  the trigger threshold are deliberately not invented.
- **Exposing browser synthetic checks to customers.** Recommended per
  deployment, not per feature: safe once ADR 0096's proxy change is committed;
  unsafe on any deployment running the proxy-less code.
- **Whether `url.full` should be truncated when `captureUrlQuery` is off.**
  `captureUrlQuery` defaults to false and its comment gives the right reason —
  *"masking matches attribute keys, not values, so a token in a query string
  would be stored verbatim for the whole retention period."* Two lines above it,
  `pagecontext.ts` sets `url.full` to `location.href` unconditionally, on every
  record. `href` carries the query, so the option removes a dedicated
  `url.query` attribute while the same data ships anyway. The README says so —
  *"`url.full` carries the same exposure and cannot be avoided for a page
  view"* — so this is documented rather than hidden, and the disputed word is
  *cannot*: sending `origin + pathname` when the option is off is available and
  was not chosen. Masking does not close it. `Rules.masksValues()` is true only
  for `card_numbers` and `authorization_credentials`, so a card number or a
  `Bearer` credential inside the URL is masked, while `?session_token=abc123`
  survives — `credential_keys` matches the attribute *key*, which here is
  `url.full`. **The sharper half is the fragment.** A browser never puts `#…` in
  an HTTP request, so a customer's own access logs cannot contain it, but
  `location.href` includes it and the SDK reads it from JavaScript — an OAuth
  implicit-flow access token reaches us that the customer's own server never
  saw. Against all of that: a genuinely full URL is what makes a reported bug
  reproducible, which is why it is a decision and not a defect. Not recorded in
  Part 2 for that reason.
- **What the RUM screen should show beyond one number per vital.** Migration
  000030 promoted five attributes to columns for the stated reason that a screen
  filters and aggregates on them, and `queries.py` groups by `vital_name` alone.
  So `url_path` and `device_class` are populated on every row and read by
  nothing, `session_id` likewise, and `country` has no producer at all. The
  screen renders five p75 cards and a thin-data warning below `MIN_READINGS`.
  The gap is the useful part — *p75 by page and device over time* needs a
  `GROUP BY` and no migration, which is what promoting the columns bought. Two
  questions inside this and neither is answered: which breakdown ships first,
  and whether `MIN_READINGS = 10` is the right floor for calling a p75
  believable. Both want real customer traffic to settle, and there is none.

---

## 1d. How a customer's page gets the browser SDK

**Nothing decides this today, and ADR 0099 says so on purpose.** Its scope line
reads *"`tools/otel-demo/` only. This decides how a demo page carries our
script; it says nothing about how a customer's page does, which is a script tag
they add."* That sentence is accurate and leaves a hole: door 3 has a working
SDK, a working ingest path and a working demo integration, and no answer to
"where does the file live".

### What exists, and what it is not

`packages/browser-sdk` builds two artifacts, and the split is already the two
distribution channels: `index.js` (ES module) for a customer with a bundler, and
`nexovue-rum.min.js` (IIFE, global `nexovue`) for a customer who can only paste
a script tag — a Shopify or WordPress storefront has no build step, so the
script tag is the one that decides whether door 3 is sellable.

The demo proves the *integration* and not the hosting. It plays all three roles
locally: `dist/` is the origin, `image-provider` is the edge, the storefront is
the customer's page. In production those are three separate systems and none of
them is built.

`dist/` is gitignored and built by hand — `otel-demo.sh` fails with
`pnpm --filter @nexovue/browser-sdk build` when it is missing. Nothing publishes
it anywhere.

### Four questions, and only the last one is small

**Where the file lives.** Object storage as origin plus a CDN in front. Not
researched here, because the deployment target is not settled and choosing a CDN
before that is choosing in the wrong order.

**The version path scheme, which is the one that matters.** A single unversioned
URL forces a choice between a long cache — where a bug fix reaches nobody for a
year — and a short one, which throws the CDN away. The escape both comparable
products use is that the URL carries the version and versioned URLs are
immutable: PostHog serves `/static/array.js` following latest v1 with
`/static/1/array.js` and `/static/1.380.0/array.js` beside it, and Sentry's
loader works the same way. The frozen path takes
`Cache-Control: max-age=31536000, immutable`, which tells the browser not to
revalidate at all.

**Which of those the pasted snippet defaults to is the actual decision**, and it
is not a technical one: default to a pinned exact version and every customer is
frozen on whatever they pasted, so a fix we ship reaches nobody without asking
them to edit their HTML. Default to the auto-updating path and a bad release
reaches every customer at once.

**Whether we publish SRI hashes.** Subresource Integrity lets a customer pin a
SHA-384 of the exact bytes, and the browser refuses to run the file if they do
not match. This is the honest answer to the risk we are asking customers to
take — our code on their checkout page, so a compromise of our CDN account is a
compromise of every customer at once. The tension is structural: a hash is a
hash of specific bytes, so `integrity` and an auto-updating URL cannot both be
true. Offering both and letting the customer choose is the shape that works.

**What publishes `dist/`.** Small, and blocked on the three above.

### One constraint that is already settled and must not be lost

The CDN must send `Access-Control-Allow-Origin`. This is not a preference: a
script served cross-origin that throws is replaced by the browser with the bare
string `Script error.` — no file, no line, no `Error` object — so an
error-reporting SDK that cannot read errors is the default outcome. It needs
`crossorigin="anonymous"` on the tag *and* the header from the serving origin.
The SDK already detects that exact shape and prints the fix once; the README's
"three things the customer must configure" documents it. Any CDN that cannot set
that header is disqualified rather than inconvenient.

### Recommendation

**Do not build this yet, and do record it.** It is deploy-time work whose inputs
— the deployment target, the release process, whether `dist/` is built in CI —
are all unsettled, and every question above answers more cheaply once they are.
Nothing about door 3 is blocked in the meantime: the demo is the customer until
there is a real one.

What would change that: the first customer who is not us.

---

## 1e. What we accept at the front door, and what we should

Raised while mapping our shape against Grafana's. Nothing here is a defect; the
question is product scope, and it has never been decided in one place.

### What the door accepts today, read off the receiver

**OTLP and nothing else.** `otlp/signals.go` serves `/v1/traces`, `/v1/logs` and
`/v1/metrics`; compose publishes gRPC on 4317 and HTTP on 4318; `codec.go`
accepts protobuf and JSON, plus `text/plain` on the browser door only, because
it is CORS-safelisted. There is no Prometheus scrape, no remote-write, no
StatsD and no syslog.

That is broader than it sounds. Every OTel SDK in every language reaches us with
no work on our side, so does any OTel Collector, and so does anything carrying a
native OTLP exporter — nginx's `ngx_otel_module` and Envoy both do, which the
demo proves rather than assumes.

**What it excludes is the Prometheus exporter ecosystem** — `node_exporter`,
`redis_exporter`, `postgres_exporter`, `kube-state-metrics`, hundreds more, none
of which speaks OTLP. A customer already running Kubernetes almost certainly has
Prometheus.

**A bridge already exists and costs us nothing.** The OTel Collector has both a
`prometheus` receiver, which scrapes exporters, and a `prometheusremotewrite`
receiver. So a Prometheus customer can reach us today by running a collector —
which is the "own collector" onboarding pattern P1-54 already plans to document.
**The open question is therefore not whether they can, but whether they must
install something first.**

### What the research showed

By 2026 the platforms we would be compared against accept both OTLP and
Prometheus rather than OTLP alone, specifically to give a migrating customer a
path — OpenObserve documents Prometheus remote-write alongside OTLP, and Metoro
accepts OTLP plus remote-write for custom metrics. A remote-write door fits our
architecture cleanly: it is a new door that normalizes to the same envelope
before the broker, so the Phase 1 invariant holds unchanged.

### Three tiers, and the cheapest is not a protocol

**Widen door 4b, no new protocol at all.** `cloudwatch.py` is pinned to
`AWS/RDS` with four metrics, and everything around it — `GetMetricData`, the
`RdsMetric` shape, the STS auth from ADR 0076, the envelope — is namespace
agnostic. `AWS/EC2`, `AWS/ApplicationELB`, `AWS/Lambda` and `AWS/ElastiCache`
are a metric list each. Azure Monitor and GCP Cloud Monitoring pollers are the
same shape again, and that is exactly how Grafana reaches all three clouds.

**Prometheus remote-write**, as a native door, is the one protocol worth adding.

**Not worth building:** native StatsD, syslog or Fluentd parsers, because Fluent
Bit and Vector both ship OpenTelemetry outputs and the bridge already exists;
Prometheus *scrape*, because scraping means reaching into a customer's network
where remote-write has them push to us; and a "PostgreSQL as a data source"
feature in Grafana's sense, which is a dashboard builder (F20) and not ingest.

### Recommendation

**Add nothing to the ingest surface until Phase 1 closes**, and the reason is
ordering rather than difficulty. Seven rows still block a paying customer —
P1-69/70/71 leave us with no transactional email, so no password reset and no
verification; P1-53 leaves TLS unbuilt at the edge and three security findings
open; P1-52, P1-54 and P1-50 are unstarted. A wider front door on a product
nobody can sign up to is breadth nobody uses.

Then, in this order:

1. **Document the collector bridge**, as part of P1-54. Zero code, and it turns
   "we do not support Prometheus" into a config snippet. The best return per
   hour anywhere in this list.
2. **Run door 4b against real AWS.** Both pollers are marked done and neither
   has ever made a real call — `botocore.stub.Stubber` throughout. Breadth we
   already claim is worth more than breadth we might add.
3. **Widen the CloudWatch namespaces**, then Azure and GCP.
4. **Prometheus remote-write** as a native door.
5. **Resolve ADR 0076**, which is already blocking a screen that exists.

---

# Part 2 — Findings backlog

All of these were **demonstrated** — an agent ran something that showed them —
during the adversarial pass over the three workstreams. Reasoned-but-unproven
findings were discarded rather than recorded here.

## Tier 1 — security and correctness

| # | Finding | Where |
|---|---|---|
| ~~T1-1~~ | **FIXED 2026-09-05** (commit `377a9c5`). Deletion is now a column the gate reads, refused with its own message and tally; a genuinely missing row is still admitted. The key still authenticates — revoking it belongs with account deletion in M8. Original finding: **soft-deleting an organization turns the byte cap off.** `readMonthlyUsage` ends `WHERE org.id = $1 AND org.deleted_at IS NULL`; the gate then reads `if !held.Found { return Verdict{}, nil }` and admits. The key lookups never join `organizations`, so a deleted org's keys still authenticate. Result: unlimited free ingest. **Pre-existing — it is in the committed ADR 0093 code, not introduced by these workstreams.** The comment's reasoning is sound for a *broken database* and never separates that from *org deleted*. | `apps/receiver/internal/allowance/{allowance.go,postgres.go}` |
| T1-2 | **A third unguarded SSRF surface.** `database_target_from` takes `host` and `port` straight from tenant integration config and checks only `isinstance(host, str)`. No address rule. Reachable today by any tenant who has completed ordinary AWS onboarding. Rule 5: the same root was fixed in two of three places. | `apps/scheduler/src/nexovue_scheduler/pollers/integrations.py:187` |
| ~~T1-3~~ | **REMOVED 2026-09-05 by adopting 1a** — during the gap where `browser_bytes` is still zero, the charged total equals the raw total, so the rule is byte-identical to the one before the split and no customer loses a door. Original finding: **the migration is strictly worse than what it replaces during the deploy window.** 0016 adds `browser_bytes` *and* seeds the split in one transaction, with `_EFFECTIVE_FROM` in the past. Until the new worker writes the column, unattributed browser bytes are charged to a server share that just shrank 25% — the customer is refused at 4.5 GiB instead of 6 GiB. Also: an existing customer already 75–100% through their allowance loses their server door the day it lands. **Adopting 1a removes this.** | `apps/api/migrations/versions/0016_*.py` |
| T1-4 | **Narrowed 2026-09-05.** The divergence is now deliberate and documented in `browser_cap_bytes`: an uncapped row has no allowance in the receiver and enforces nothing, while the API reports a figure so the screen can show a paying customer their included volume. `allowance_enforced` is what says it is not a boundary. Original finding: **Go and Python disagree on an uncapped price row.** On `max_billed_services IS NULL` *with* a `browser_share_percent`, Python returns a split and the API puts it on the wire; Go returns no split and a zero allowance. The screen would state a limit the door does not apply. Unreachable today (only `free` carries a share) but ADR 0097 §1 says any tier may. | `allowance.go` vs `nexovue_api.usage.billing` |
| T1-5 | **The split can vanish silently on a repricing.** The gate reads exactly one row — newest `effective_from <= today` — with no fallback for a NULL column. The house convention is that a repricing is a *new row restating every column*, so omitting `browser_share_percent` in a future `free` row silently reverts to the pooled rule. No constraint, no test, no error. | `readMonthlyUsage`, `queries.find_effective_price` |
| T1-6 | **The measurement rig's cleanup can cause the defect the rig measures.** `cleanup94.sh` takes unvalidated positional arguments and can wipe `span_ingest_offsets` — the durable counted-through watermark. Losing it makes the worker recount, producing exactly the silent permanent over-count ADR 0094 exists to describe. It also passed a clean bill of health on a run where its DELETEs matched nothing. | `scratchpad` rig, referenced by ADR 0094 |

## Tier 2 — evidence is overstated (rule 12 defects)

These matter because they sit in sections that promise every number was read from
real output.

| # | Finding | Where |
|---|---|---|
| T2-1 | **The "120× better" claim compares two variables at once.** It sets the *after* binary at a 10s sampling window against the *before* binary at 60s. The ADR's own data shows the window alone moves the residual 18-fold with the binary held constant — so most of the improvement is the window, not the fix. | ADR 0094 |
| T2-2 | **The raw measurement output was never saved and the rows were deleted.** Every headline number now survives only as prose in the document that cites it. Nobody can re-derive it. Re-run capturing to a file. | ADR 0094 |
| T2-3 | **Edge-count figures are wrong in the direction that flatters the fix.** The after arm's `service_edges_1m` is exact (7,199 = 7,199 achievable), not −0.01%; the before arm is exact too; the shipped-window arm is +14, not +13. | ADR 0094 |
| T2-4 | **A stated test baseline of 169 against an actual suite of 117.** | ADR 0096 |
| T2-5 | **Three different numbers for one test count**, none of which is what the command prints. `-k "not live"` gives 1184, `--ignore-glob='*_live.py'` gives 1202, and the real deselected count is 149 — not 138, not 141. `-k "not live"` is a substring match and excludes more than intended. | ADR 0097, and the decisions-log row in `system-architecture.md` |
| T2-6 | **A proof table with no negative arm.** §2a shows blocked attempts but never ran the same test with the proxy off. Chromium 151's own Local Network Access checks produce byte-identical results with no boundary at all — so the table proves nothing leaked, not that our proxy is what stopped it. | ADR 0096 |
| T2-7 | **An interpreter-contradicted claim.** §2a says `ipaddress` does not report an IPv4-mapped address as loopback. On the Python this project runs, `is_loopback` *is* True for it. The address is still refused three ways over, so there is no hole — the recorded reasoning is what is wrong. | ADR 0096 |

## Tier 3 — hardening

| # | Finding | Where |
|---|---|---|
| T3-1 | **Request framing is too permissive.** The head is split on CRLF only, so a header line containing a bare LF is one line to us and two headers to the origin — request splitting. And `_content_length` uses Python `int()`, which accepts `+5`, `05`, ` 7 ` and `1_0` (ten); RFC 9110 allows `1*DIGIT` only. A framing disagreement between proxy and origin is the desync the section claims to prevent. **Unreachable through Chromium, which sanitises header values** — downstream hygiene, not a live boundary defect. | `browser_proxy.py` |
| T3-2 | **A refusal is reported for the wrong reason.** Inside one attempt, every non-public candidate is appended to `refused`; if the *public* candidate then fails for an ordinary reason — the customer's site is simply down — the loop reports the unrelated refusal. | `browser_proxy._open_checked` |
| T3-3 | **A test nothing runs.** `storeprobe_test.go` is behind a build tag and `just check` passes no `-tags`, so it is invisible to `go test`, `go vet` and the linter. It can stop compiling and every gate stays green — while ADR 0097 names it as the thing closing the write-path gap. | `apps/workers/internal/pipeline/usage/` |
| T3-4 | **Tests that assert a constant equals itself.** `PROXY_BYPASS_NOTHING` and the WebRTC flags are the only two lines whose removal produces a real reproducible leak, and they are the only ones with no behavioural test. Delete either and every live test still passes. Same trap that let a byte-cap test stay green with the feature broken. | `test_browser_flow.py` |
| T3-5 | **A guard that passes on transposed arguments.** The Python statement guard asserts both substrings are present but says nothing about position, so it passes on a statement with the two `func.sum` arguments swapped — where the total returns the browser part and vice versa. | `apps/api/tests/test_usage.py` |
| T3-6 | **`asyncpg` hostname verification is off.** `connect_read_only` passes `ssl="require"`, which is below `verify_full`, so `check_hostname` is False and the certificate is not verified. This matters because "connecting to a judged IP literal would break TLS" is the *stated reason* T1-2 was left unfixed — and that blocker does not exist. | `pollers/postgres_connect.py` |

---

# What is committed and what is not

At the time of writing: **8 commits on `main`, unpushed**, covering the docs
verification pass, the formatter consolidation, the CGNAT address fix, the
counter handover fix, backup/restore, and the trusted-proxy fix.

The three workstreams above — ADR 0096 (browser proxy), ADR 0097 (allowance
split), and ADR 0094's measurement — are **built and uncommitted**. They compile;
209 receiver tests and 391 worker tests pass. They are held back because Part 1
question 1a would change ADR 0097's arithmetic, and because Tier 1 is unfixed.

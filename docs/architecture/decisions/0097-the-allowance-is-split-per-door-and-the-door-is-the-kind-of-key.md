# 0097 — The month's allowance is split per door, and the door is the kind of key that opened it

- **Status:** Accepted
- **Supersedes in part:** [0093](0093-the-free-tier-ends-on-bytes-and-says-so.md) — its allowance is
  one pooled number refused at every door. It stays one number and stays pooled across the
  organization's projects; what changes is that a plan row may now divide it between the browser
  door and the server door, and each door is judged against its own share. Everything else in 0093
  — the month, the bytes, the uncapped tiers, `RESOURCE_EXHAUSTED` with no `RetryInfo`, the gate's
  position — stands.
- **Corrects in part:** [0087](0087-the-origin-is-what-ties-a-public-key-to-its-owner.md) — its
  paragraph *"What a scraped key costs its owner, in the worst case"* costs a scraped site key at
  the per-key rate ceiling, recovering in about a second. That was true the day it was written.
  0093 turned the same attack into **31 days** without either ADR referencing the other, and this
  one turns it back into the browser door's share of the month. The rest of 0087 — the Origin as
  abuse control rather than authentication, the preflight, the CORS on refusals — stands unedited.
- **Date:** 2026-09-04

## Context

A **site key** is the public ingest key printed in a customer's own page source so their visitors'
browsers can export telemetry. Anyone who views source has it. That is by design and cannot be
fixed: you cannot hide a credential you ship to browsers. 0087 said so, 0086 gave it its own table
so it can do exactly one thing, and both are still right.

**Two ADRs that did not know about each other turned that into a denial of service.** The security
audit demonstrated the whole chain live:

- The origin check is **not an authentication boundary** — both ADRs say so — and `curl` with a
  forged `Origin` header returned HTTP 200 and the span was stored.
- The allowance gate used **one `allowance.Checker` for both doors**, keyed on `identity.OrgID`,
  with a comment saying so deliberately: *"a customer cannot buy more month by changing which
  entrance they send to."*
- Free is `included_bytes_per_service` 2 GiB × `max_billed_services` 3 = **6 GiB a month, pooled
  org-wide**, and it is the default plan for every organization signup creates.
- At the 16 MiB decoded-request cap that is roughly **384 requests** — under a second of sending —
  after which the victim's **entire organization** is refused at every door, their servers
  included, until the 1st of the next month.

Rotating the site key does not help. The bytes are already in `usage_daily`, and the allowance is
read from `usage_daily`.

So the cost of a scraped key was not the rate ceiling 0087 costed. It was the customer's whole
month, taken by anyone who read their page source, with their production telemetry going dark
first.

## Decision

### 1. A plan row may cap what the browser door spends, inside the one allowance

`plan_prices.browser_share_percent`, a whole-number percentage: the most the browser door may
spend of the month.

**A cap, not a partition, and the difference is the whole design.** The first shape of this
decision split the allowance into two boxes and gave the server door `included_bytes - browser`.
That reserved the browser's share whether or not any browser sent anything, so an organization
sending only server telemetry was refused at 4.5 GiB of a 6 GiB plan with 1.5 GiB it could not
reach — **and nobody could reach it**, because the browser SDK is P1-39 and unbuilt. It withdrew a
quarter of the free tier for a door that does not open yet.

Under the cap the browser's share comes out of the allowance **only as it is actually spent**
(`browserChargedBytes`), so unused browser capacity stays the server's. Both doors are then judged
against that charged total, which is what keeps 0093's invariant: a customer still cannot buy more
month by changing entrance.

The charged figure is `min(browser spent, cap)` rather than the raw browser total, and that `min`
is load-bearing in the other direction. The meter lags the door — it reads Kafka at its own pace —
so a burst through a scraped key can be metered past the cap before the door starts refusing.
Charging that overshoot to the pool would close the server door, handing the scraped key exactly
the outcome this ADR exists to deny. **The two guarantees only hold together because of it**, and
a test asserts each: `TestABrowserShareNobodyUsesIsNotStrandedFromTheServer` and
`TestThePlansWholeAllowanceSpentClosesBothDoors`.

`NULL` is *"this row records no split"*, and both doors then spend the one allowance exactly as
they did before. Every row seeded before this one reads that way and still means that. `NULL` is
not a share of zero, which is a plan the browser door may send nothing through and is honoured as
written — the same distinction migration `0013` and 0093 §3 already make between an absent
allowance and an allowance of nothing.

**A column, not a constant**, for the reason migration `0005` gave when it made retention a row:
*"the values most likely to change after contact with customers, and changing a row should not
need a deploy."* A repricing is a **new row with a later `effective_from`, never an edit**, so a
past invoice is never restated
([0083](0083-the-period-is-the-month-and-the-allowance-is-bytes.md),
[0077](0077-the-billing-unit-is-a-table-and-the-price-is-a-dated-row.md)).

### 2. The property being protected

**A customer's backend keeps reporting no matter what happens to their website.** Their marketing
site being scraped must never blind them to a production outage. That sentence is the whole reason
this ADR exists; every other consequence below is subordinate to it.

### 3. The free tier's split is 25% browser / 75% server, and it is provisional

Migration `0016` seeds 0015's free row again with `browser_share_percent = 25`: 1.5 GiB for the
browser door, 4.5 GiB for the server door. Pro and scale get no row — they enforce no allowance
(`max_billed_services IS NULL`), so a split on them would be a number nobody decided.

**25% is a guess dressed as a ratio** — Raj's words, and they are recorded here rather than
smoothed over. It is the first number, not a measured one.

**What would revise it.** The observable is `usage_daily.browser_bytes / usage_daily.bytes` over a
calendar month, per organization, across free organizations that have installed the browser SDK.
**Nothing reports that today**: this migration is what starts recording it, so the first month
after it lands is the first evidence that has ever existed. Two readings would say 25% is wrong:

- the median organization's browser fraction sitting **above** 25% — the split is refusing pages
  that are inside the plan they were sold;
- `browser_allowance_spent` refusals concentrated in organizations whose **total** month is
  nowhere near spent — the same finding seen from the door instead of the table.

Browser fractions clustered far below 25% say the opposite: the server door is being held to less
than it needs for no benefit. **How many organizations make a sample, and what distance from 25%
triggers a new price row, are not chosen** — see the open questions.

### 4. The door is the kind of key, not the URL path

`ingest.DoorFor(identity)` is the one place that decides it: `auth.KindSecret` → `DOOR_OTLP`,
`auth.KindPublic` → `DOOR_RUM`.

This was the question to settle before anything else, because a billing split must not rest on a
field nothing sets correctly. Today's docs pass found `DOOR_RUM` emitted by nothing, `DOOR_WEBHOOK`
unused, and two committed tests asserting **contradictory** things about what the browser path
stamps (`packages/contracts/roundtrip_test.go` expected `DOOR_RUM`;
`apps/receiver/internal/otlp/browserdoor_test.go` asserted `DOOR_OTLP`).

The kind of key is the right axis, and it is not a guess.
`apps/receiver/internal/auth/presented.go` says each mounted door is handed exactly one
`Gatekeeper` — *"nothing on the request path ever chooses an authenticator by inspecting the key
presented"* — and the browser door's gatekeeper resolves only the `site_keys` table, which always
stamps `KindPublic` (`auth/site.go`). So the kind is **equivalent** to the path prefix and
additionally covers gRPC, where there is no path to read. The contradiction is settled in the
fixture's favour: `door3-rum.bin` had been right all along.

**Flipping the browser door's stamp is safe**, and that was checked rather than assumed: every
non-test, non-generated reader of the envelope's `door` field across `apps/`, `packages/` and
`tools/` is one of the three this change added — `allowance.budgetFor`, `ingest.DoorFor`,
`usage.browserBytesOf`. Nothing in storage, sampling, dedupe or the API branches on it, so
[0045](0045-envelope-contract-frozen.md) is untouched: the envelope's shape did not change, only
which value one field carries.

**Every door but the browser's is judged on the whole allowance** — the server door, the scheduler's
`DOOR_SYNTHETIC` and `DOOR_POLLER`, and an envelope naming no door at all. A budget the browser
cannot reach is the point; traffic we cannot place must not be handed the small share anyone can
spend. `auth.Kind` has no safe zero, so an unset kind is an authentication defect rather than an
entrance.

### 5. The meter records which door the bytes came through

`usage_daily.browser_bytes` is the part of `bytes` whose envelope named `DOOR_RUM` — **beside**
`bytes`, never instead of it, so the total the usage screen shows does not move and the server
door's spend is the difference between the two. A check constraint keeps `0 <= browser_bytes <=
bytes`, which is what stops a meter defect from inventing browser traffic.

The receiver only *reads* an allowance; the meter in `apps/workers/internal/pipeline/usage` is what
writes it. So the split needed both halves, and the gate asks about `ingest.DoorFor(identity)` —
the same function that stamps the envelope — so the budget judged at the door is the budget the
meter later charges.

### 6. One read of the month serves both doors

The allowance cache stays keyed on the organization. The month is read whole and split in process,
so the split adds **no** Postgres load on the ingest path and the two doors cannot see two
different months. The statement gained two columns; it did not gain a query.

### 7. Three refusals, because a refusal nobody can read is the defect

Extending 0093 §7. The message names the share that ran out — *"the included monthly **browser**
ingest volume for this plan is spent; ingestion resumes on 2026-10-01"* — and the tally is its own:
`browser_allowance_spent` beside `allowance_spent`. A window of server refusals says the customer's
free tier is over. A window of browser refusals says their site, or whoever copied its key, spent
the site's share **while the backend kept reporting**. Folding them together would hide the exact
case this ADR exists for.

### 8. The API sends the browser's cap; the screen has not been changed yet

`period.browser_included_bytes`, `period.server_included_bytes` and `period.browser_bytes_ingested`
are on the usage response, and `price.browser_share_percent` beside `included_bytes_per_service`.
The API decides them for the reason 0093 gave for `allowance_enforced`: which door is held to what
is policy, and a browser computing it would be a second place for it to be wrong.

**`apps/web` still shows only the pooled figure.** That is named as a cost below, not glossed.

## Alternatives considered

**Making browser bytes free.** Rejected. It gives away unlimited ingest through a credential
printed in a page — the main cost of the product, handed to anyone who views source. It also
removes the only thing that makes a scraped key visible on the usage screen.

**A per-site-key budget.** Rejected. It needs a number nobody has: how much one key may spend, in a
product with no per-key ceiling anywhere. It is the same shape as this decision with more parts,
more rows, and one more thing to get wrong on the ingest path — and 0087 already recorded that a
per-key rate ceiling is the missing piece, which is a separate decision from this one.

**Branching on the URL path rather than the key kind.** Rejected as strictly narrower: gRPC has no
path, and the path and the kind cannot disagree anyway (§4). One function reading one field is
simpler than two rules that must be kept in step.

**Two byte columns instead of a percentage.** Rejected. Two amounts can be edited into a pair that
does not sum to the plan's total, and then a customer really can buy more month by changing
entrance. A share cannot: the server's budget is the remainder by construction, and a repricing of
the total carries the split with it.

**Keeping the pooled allowance and rate-limiting the browser door harder.** Rejected. A rate
ceiling bounds bytes per second; the harm here is bytes per month, and 384 requests is already
under a second. Lowering the rate enough to matter would throttle a real visitor population, which
0087 already found is the wrong shape for a public key.

## Consequences

**What this buys.** A scraped site key costs its owner the browser door's share of the month, and
nothing else. Their servers keep reporting. The refusal says which share ran out, and the two are
counted apart, so the support conversation starts from the right question.

**What it costs.**

- **The customer's browser telemetry still stops.** The split bounds the blast radius; it does not
  make the attack free. A scraped key can still end the *browser* month in under a second, and the
  customer's RUM data is gone until the 1st. The remedy — a per-key rate ceiling — is 0087's open
  finding and is still open.
- **The free tier's server door is now 4.5 GiB, not 6.** A free organization that sends nothing
  from a browser has lost a quarter of the allowance it had yesterday, for a protection it is not
  using. That is the price of the shares summing to the total, and it is the reason 25% is
  provisional rather than settled.
- **The scheduler's own traffic is judged on the whole allowance.** Synthetic checks and pollers publish
  straight to Kafka, never through the receiver's allowance gate, and their bytes are metered as
  `DOOR_SYNTHETIC` / `DOOR_POLLER` — so they land in a share that is now 25% smaller. A free
  organization running synthetic checks quietly has less headroom than before. Whether that is
  intended or wants a third budget is an open question below.
- **The policy is now written in three languages and fixed in two.** The Go gate and
  `nexovue_api.usage.billing.allowance_split` compute the same two numbers from the same row with
  the same rounding, and matching test figures are all that keep them in step — 0093 already
  recorded that risk for the pooled number and this doubles it. **`apps/web` is the third**, and it
  has not been changed: `usage-format.ts` still computes `spent = bytes_ingested > included_bytes`
  against the pooled figure, so a free customer refused at the server door at 4.5 GiB opens the
  usage screen and reads *"4.5 GB of 6.4 GB included"* with no warning. **They are being turned
  away while the product tells them they have room.** The API now sends everything that screen
  needs; until it reads them, this is a known, named divergence rather than a hidden one.
- **`plan_prices` gains a column that stops ingest.** 0093 already made that table load-bearing at
  the door. A `browser_share_percent` set wrong now silences one of a customer's two doors, bounded
  by the same 5-minute ceiling on the cache TTL.
- **A mid-month `effective_from` moves two lines at once.** The gate reads the row in force *today*
  against the *whole* month's metered bytes, and rows metered before `browser_bytes` existed hold
  zero, so they all read as the server's. On the day the split lands, a free organization that has
  already spent 75–100% of its 6 GiB is refused at the **server** door immediately — the harm this
  ADR exists to prevent, inflicted by the fix — and one already past 6 GiB has its browser door
  reopened for 1.5 GiB, making the transition month effectively 7.5 GiB. **Which date is wanted is
  an open question below and the migration is waiting on it.** In the dev control plane the choice
  costs nothing either way — read on 2026-09-04, its one organization had spent 11.88 MB of 6 GiB,
  0.18% of the month — but that is a development stack on one day and says nothing about who is
  75–100% through their month in production.

**What was verified**, and watched:

- **Go, offline, `-count=1`, counts read from the output**: `receiver/internal/allowance` 30 tests,
  `receiver/internal/otlp` 163, `receiver/internal/ingest` 14,
  `workers/internal/pipeline/usage` 54, `packages/contracts` 28. Both full Go modules and
  `go vet ./...` are clean.
- **Python, offline**: 1,184 passed, 138 live tests deselected. `apps/api/tests/test_usage.py`
  alone is 62 tests, 9 of them new here.
- **Seven negative controls run and watched to fail.** Making the browser door stamp `DOOR_OTLP`:
  6 tests fail across `ingest` and `otlp`. Making `budgetFor` always return `Pooled`: 4 allowance
  tests and 5 `otlp` tests fail. Making `browserBytesOf` always return 0: 2 workers tests fail.
  Making `allowance_split` always return `None`: 4 Python tests fail. Summing `bytes` in place of
  `browser_bytes` in `queries.bytes_in_range`: 1 Python test fails. Deleting
  `browser_bytes = existing.browser_bytes + excluded.browser_bytes` from the `ON CONFLICT` clause:
  1 test fails — see below. Deleting the same line for `spans`: 1 test fails, which is what shows
  the guard covers the class rather than the one column.
- **The trap 0093 recorded caught this change too, and in a new place.** The mutation that deleted
  `browser_bytes` from the `ON CONFLICT` clause left the **entire** usage suite green: the test
  written to cover it exercised `columnsOf`, a Go slice builder, not the statement. The failure
  that models is the worst available — browser bytes recorded on the first flush of a tenant-day
  and never accumulated again, so the browser door never refuses anyone while every customer's
  server share stays 25% smaller, and the change looks like it worked. The guard is now a test that
  reads the statement itself and requires **every** counter it inserts to be accumulated on
  conflict. The same shape caught the Python half: swapping `browser_bytes` for `bytes` in the
  usage query changed no behaviour test at all, because the offline store sums the rows in Python.
  Both are now asserted on the statement.

**What was proved against the running stack**, on 2026-09-04, each result watched rather than
inferred from an exit code.

- **The migration applies and reverses.** The broken state was reproduced first: `GET /api/v1/usage`
  returned 500 with `asyncpg.exceptions.UndefinedColumnError: column usage_daily.browser_bytes does
  not exist`. After `alembic upgrade head` the same request returned **200**, carrying
  `browser_included_bytes` 1610612736 and `server_included_bytes` 4831838208 against
  `included_bytes` 6442450944 — the cap inside the whole, from the real row.
- **Both downgrades were executed, with a decoy in place to make them prove something.** The chain
  ran `0016 → 0015 → 0014 → 0015 → 0016`. With `pro @ 2026-09-04` seeded alongside, `0016`'s
  downgrade deleted only its own free row; with `enterprise @ 2026-09-01` seeded, `0015`'s deleted
  only its own three. The predicates they replaced would have taken 2 and 4 rows respectively. This
  was the first execution of either downgrade, and of `0015`'s `plan_slug = ANY(:slugs)` form.
- **Both SQL statements ran against Postgres.** The workers' `addCounters` was exercised by
  `usage/storeprobe_test.go` — two flushes on one tenant-day, read back from the column — and its
  negative control fired *at the column*: removing
  `browser_bytes = existing.browser_bytes + excluded.browser_bytes` gives `browser_bytes = 100, want
  140`, the second flush silently lost. The receiver's `readMonthlyUsage` was executed by hand
  against the seeded organization and returned the split. The live Python suite is **26 passed**,
  and swapping `browser_bytes` for `bytes` in `bytes_in_range` fails
  `test_the_browser_bytes_are_summed_from_their_own_column` with `assert 1250 == 400` — the control
  the offline store, which sums rows in Python, cannot give.
- **The end-to-end demonstration was made.** Ten exports through `/browser/v1/traces` on a real site
  key metered **exactly 1220** bytes into `usage_daily.browser_bytes`; five through the server door
  added **600** to `bytes` and nothing to it. With the browser share spent, the browser door
  answered **429** *"the included monthly browser ingest volume for this plan is spent"* while the
  server door answered **200** — the property this ADR exists to protect, watched rather than
  argued. Deleting the split row returns the gate to 0093's pooled rule and reproduces the audit's
  finding on the same stack: **both doors 429**. The receiver's own windows keep the two apart —
  `refused_allowance_spent=0, refused_browser_allowance_spent=1` under the split;
  `refused_allowance_spent=2, refused_browser_allowance_spent=0` pooled.

**What is still not verified.**

- **Nothing was timed.** The split adds no Postgres round trip — one read of the month still serves
  both doors — but that is read off the code, not measured.
- **The receiver has no live-Postgres suite**, so `readMonthlyUsage` was proved by running the
  statement, not by a test that would catch the next change to it. ADR 0093 recorded the same gap;
  this change widened the statement by two columns without closing it.
- **The month boundary has still only ever rolled over against a fake clock.**

## Open questions

These are decisions, not gaps in the work. None of them has been invented into the code.

1. **When does the split take effect?** The migration pins `2026-09-04`. The alternatives are the
   1st of the next month — nobody is cut off mid-month, but a scraped site key can end a customer's
   month until then — or today with a grandfather rule spending the month's pre-split bytes against
   both shares proportionally, which is safest and needs a rule nobody has written and probably a
   code path in the gate. **This is the one the migration is waiting on.**
2. **What sample, and what distance, revises 25%?** §3 names the measurement; the number of
   organizations that make a reading and the threshold that triggers a new price row are not chosen.
3. **Does the split have to reach the usage screen before it ships?** The API sends the figures;
   `apps/web` does not read them, so the screen states a limit the door does not apply.
4. **What does a customer do when only the browser share is spent?** Today: an HTTP 429 saying so,
   and a screen showing neither share. Is there meant to be an upgrade path, a rotate-the-key path,
   or is "wait until the 1st" the whole answer?
5. **Should the scheduler's own traffic spend the whole allowance?** It does today, and a free
   organization running synthetic checks loses headroom the split never considered.
6. **Is a hard-coded `effective_from` the settled convention?** `0016` pins a date the way `0015`
   did. Applied in production weeks later, the row is retroactively in force from a date in the
   past — probably deliberate, but it interacts with question 1.

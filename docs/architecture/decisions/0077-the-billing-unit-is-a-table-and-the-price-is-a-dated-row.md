# 0077 — The billing unit is a table, and the price is a dated row beside it

- **Status:** Accepted
- **Amends:** [0056](0056-per-service-pricing.md) — its closing *Schema follows* paragraph, and the
  question of where a price lives, which 0056 did not answer
- **Date:** 2026-08-30

## Context

[0056](0056-per-service-pricing.md) fixed the pricing unit — **one service monitored**, with a
volume allowance per service and a ceiling on the service-count component — and ended by saying the
schema would follow. It did not. Four days later:

- **The billable quantity did not exist anywhere.** `usage_daily` counts spans, logs, metric points
  and bytes: the four numbers a per-GB bill would want, and none that the chosen model needs. The
  quantity 0056 prices — *distinct `service.name` per project per day* — was not stored, not
  derivable from those counters, and not computable from anything else in Postgres.
- **`plans` held no money at all.** The M6 note in `../../delivery/phase-1-plan.md` recorded this as
  still open, in exactly the terms below: *"where a price lives — a column on `plans`, or a table
  with effective dates so a repricing does not rewrite what was already charged"*.
- **Nothing could say whether a day had finished being counted.** The usage meter reads Kafka at its
  own pace and its delivery is at-least-once. "Yesterday" is not the answer to "is this number
  final": a meter that spent the night catching up is still filling in yesterday, and a partition
  nobody has metered could hold a whole day of traffic.

P1-48, the usage screen, was blocked on the first of these. A screen showing a customer what they
consume is a screen a customer reads as a bill, so every one of the three had to be answered before
a number could be put on it.

## Decision

**Three schema objects (Alembic `0010`), and one refusal.**

### 1. `usage_daily_services` — a set, not a counter

One row per service a project sent something for on a day. `(org_id, project_id, day,
service_name)` is the whole primary key; `first_seen_at` is data, not part of the key.

- The **day's** billable quantity is `count(*)` over the rows for that project-day.
- The **range's** quantity is `count(distinct (project_id, service_name))` — a **union, never a sum
  of the days**. A service reporting every day of a month is one service that month; adding the
  daily counts would bill it thirty times. `project_id` is inside the distinct because two teams
  each running a `checkout` are running two of them.

A distinct count cannot be accumulated by adding, so it cannot be a column a flush increments.
Membership is also what makes the meter's at-least-once redelivery harmless here: inserting the same
name twice leaves the same set, so this needs no watermark and none of the machinery
[0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
[0061](0061-counter-overcount-across-a-store-restart.md) needed for the additive counters.

### 2. `plan_prices` — appended, never edited

`(plan_slug, effective_from)` is the key. There is no end date: the next row's start is the previous
row's end, so two rows can neither gap nor overlap. The price in force on a day is the latest row not
after it. `price_per_service_cents` is a whole number of minor units, because money in binary
floating point rounds. `max_billed_services` is nullable, and null is **uncapped** — a different
statement from a cap of zero.

**It ships with no rows, on purpose.** 0056 fixed the unit and not the amounts. An amount nobody
decided is not a number to invent, and the absence is carried all the way to the browser: the API
returns `price: null` and the screen writes *"Not priced"*, never `$0.00`.

### 3. `usage_ingest_offsets.counted_through_received_at` — how far the meter has reached

The offset column says how far the meter read; this says how far it read *in receive time*, which is
what maps onto days. A day is finished only when **every** partition has counted past its last
instant. A partition with a null here has never been metered by a worker that records receive time,
and while one exists **no day is finished** — unknown is reported as unfinished, never as zero.

The response carries this twice, and both are needed. Each day row has its own flag; the response
has one for the whole range, computed from the range's **last** day. The frontier only moves
forwards, so the last day settling means every earlier one has — and a day the meter has not reached
yet has no row in the list to carry the caveat itself, which is exactly the case where a total is
still growing and every day shown looks final.

### 4. No cost is computed — not in the API, not on the screen

The rate and the quantity are returned side by side and never multiplied. Three things would have to
be invented to produce an amount:

- **the billing period** — a calendar month, or an anniversary of signup;
- **which quantity that period bills** — the union over the period, the peak day, or an average;
- **the volume allowance per service**, which 0056 requires and gives no unit for (spans, bytes, or
  all signals together), and which is what overage is measured against.

## Alternatives considered

**A `price_per_service_cents` column on `plans`.** Rejected, and this is the decision the table
shape exists to avoid. A price column is an `UPDATE`, and an `UPDATE` restates what every past
invoice was computed from: repricing `pro` in March would silently change what February's bill was
based on, with no record that the number ever differed. It also cannot express a price that starts
next month, so every repricing becomes a timed deploy. The dated row costs one extra table and an
`ORDER BY … LIMIT 1`, and buys a history that cannot be rewritten by accident.
`test_money_is_not_a_column_on_the_tier` fails if any money-shaped column appears on `plans`.

**A `services` counter column on `usage_daily`, incremented by the meter.** Rejected. Distinct
counts do not add, so the column would have to be a read-modify-write of a set held somewhere else —
and the meter is at-least-once, so a redelivered batch would count a service it already counted. The
whole problem disappears when the set is the storage.

**Counting distinct services from ClickHouse at read time.** Rejected. Telemetry is sampled and
expired: tail sampling keeps roughly 15% ([0010](0010-tail-sampling-defaults.md),
[0054](0054-spans-written-through-until-tail-sampling.md)) and retention drops the rest on a tier
schedule ([0071](0071-plan-tiers-and-retention.md),
[0072](0072-retention-class-partitioning.md)). A bill computed there would report **what survived**,
not what arrived, and would shrink as data expired. The control plane's own record of what was
accepted is the only defensible source for a billing question.

**Computing a projected cost now, assuming a calendar month.** Rejected. It is one multiplication and
it would be the most dangerous number on the screen: a customer reads it as a forecast of their
invoice, and it would rest on a billing period nobody chose. Better to show two honest halves than
one invented whole.

**Calling a day finished by the clock — "yesterday is done".** Rejected. It is only true when the
meter is caught up, which is precisely the condition it is meant to detect. Measured against the dev
stack while writing this: **1 of 20 partitions** in `usage_ingest_offsets` has a null receive time,
so today is correctly reported as unfinished; a clock-based rule would have called it final.

## Consequences

**What this buys.** The billable number exists and is countable by one statement. A repricing is an
insert, so no past invoice can be restated by an edit. A day still being counted says so, on the
screen, per day and per range. And a missing price is visible as missing rather than as free.

**What this costs.**

- **The row count follows service-name cardinality, and nothing caps it.** One row per service per
  project per day: 120 services for a year is 43,800 rows, which is nothing. A customer whose
  `service.name` includes a pod id or a hostname turns that into one row per instance per day and
  **inflates their own bill by the same factor**. The name is truncated to 255 characters and
  otherwise taken as given. No guard exists; it is a real gap and it is billing-visible, not just
  storage-visible.
- **The distinct count is a range scan.** The primary key orders by `(org_id, project_id, day, …)`,
  so a range inside one organization is contiguous, but a 90-day count still reads every row in it.
  **Not measured** — there are 29 rows in the dev database.
- **`price_per_service_cents` is a misleading name.** The column holds minor units, and the yen's
  minor unit is the yen: ¥1000 is stored as `1000`, and anything dividing by a hardcoded hundred
  quotes that bill at a hundredth of its size. The web formatter asks `Intl` for the currency's ISO
  4217 exponent instead of assuming two. The column should be renamed
  `price_per_service_minor_units` in the next migration that touches this table.
- **No invoice can be produced from any of this yet**, and will not be until the three questions
  above are answered. What exists is a defensible quantity and a defensible rate.
- **One unmetered partition blocks every day.** That is the correct reading of "unknown", but it
  means a topic partition that no worker has flushed since this column shipped keeps the whole
  screen labelled unfinished until it does. Self-healing on the next flush, and currently true for
  `metrics-raw` partition 3.

**What was verified.** 27 offline tests and 9 against real Postgres, all run. Two negative controls
were run and watched to fail: removing `distinct` from the range count made real Postgres report
**7 services for one service that reported on seven days** — a bill seven times too large — and
deriving the range's settled flag from the listed days reported a still-growing total as final.

**What was not verified.** No price row has ever existed in any database, so no rate has been read
from real Postgres — only from the offline suite. Nothing has been billed to anyone, and no query
here has been run against a table large enough to time.

# Design: Deployment and release tracking (F28)

## Status

**Approved** by the founder, 2026-11-02. Written and revised the same day after eight persona reviews (`status.md`, *Reviews*).
Lifecycle status is in [`status.md`](status.md). It follows
[`spec.md`](spec.md); decision numbers (D1 and so on) are the spec's.

**How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present. Nothing
was run. "Expected" means read from the code and not reproduced. Every size and limit marked *guess* is a guess.

## Context and goal

The plumbing is sound: raw bytes, signature first, overlapping secrets, "order, not cause". What is missing is the
product around it and one fact: the worker holds the version on every span before sampling
(`traceassembly/flatten.go:143,195`), and drops it before the exact counts (`traceassembly/stages.go:39-59`). Keeping it
gives an exact comparison, a real picture of a rolling deploy, and releases for a customer with no webhook.

The goal is one record, `releases` with its `release_periods`, fed from two doors, with screens that say how they know
what they say.

## User experience

**On-call at 3 a.m.** The notice says "Most recent release on this service: v2.1, 6 minutes before this. Order only, not
cause." The finding opens. Under its first sentence: "v2.1 of checkout started 6 minutes before this. Reported by your CI,
seen in traffic. It came first. That is not evidence that it caused this." Markers on the finding's charts, and a list of
other releases in the project in the last 2 hours. One click opens the comparison, on a window that reaches the finding,
with a link back.

**Developer after shipping.** Opens Releases, then their row. The first sentence states the numbers, from the most recent
settled block: "Since the rollout finished, over 42 minutes: failure share 3.0% on v2.1 against 0.2% on v2.0. p95 412 ms
against 398 ms, about the same." Then rollout share, errors new here, and findings since. Or their pipeline reads the same
thing from `GET /releases/{id}/comparison`, or a summary notice arrives if the project opted in.

**New developer setting up.** Project settings, *Deploys*, mint a secret, copy the recipe, run it with `dry_run`, watch "last
check received 10 seconds ago" appear within 5 seconds, remove `dry_run`. Or read how to pass a build id into
`service.version`: one change per service, and no deploy webhook.

**Admin rotating.** The numbered steps on the screen: mint new, update CI, watch "last used" move to the new one, withdraw
the old. Replacing in one step would drop deploys silently (`deploys/service.py:274-276`). If a secret leaked: withdraw it
and remove what it sent since a chosen time.

## UI structure

Follows `docs/design/principles.md`. Screens use the full window width: sentences are capped for reading, containers
never.

**Rules for every F28 screen.**

- **Time:** the project's zone from `useDetectionSettings`, as the finding page does (`FindingScreen.tsx:60-62`), named once
  per screen, and passed to `TrendChart` and the hover box.
- **Long versions:** one shared formatter shows up to about 24 characters (*guess*) with a middle ellipsis, keeping start and
  end, in monospace. The whole value shows with a `CopyButton` in Facts, using `break-words`.
- **Failure:** each section loads separately and fails separately, with `InlineFailure`, its retry and its correlation id
  (`InlineFailure.tsx:4-10`). The central path is only for a whole-screen 404 or 403. A 5xx as a passing toast would leave a
  blank section that reads as "nothing to show" (`principles.md:81`).
- **Permission:** every setup link goes through `useCan("manage_credentials")`. Others read "An owner or admin can set this up
  in project settings", and the setup-documentation link stays visible to everyone.
- **Screen readers:** the opening sentence is `role=status`. The window chooser is the existing `Tabs` control. A chart's
  description names its versions and their latest shares. While `too_recent` or "as of" applies, the comparison refetches every
  60 seconds.

### `/releases` (new, in Signals after Errors)

- **Header sentence:** "12 releases in this window on 4 services." With a service and version filter and the usual time window.
- **What the window means:** releases with a period that overlaps the window, so a version serving since yesterday appears in
  today's. The console's default window is one hour (`shared/selection.ts:29`), and a list of only what started in it would
  usually be empty.
- **Table, one row per release:** service, version, started, source ("Reported", "Seen in traffic", or both), activity, and flags
  for `failed`, `rolled_back`, and the stored reading: "failure share higher" or "p95 higher", with counts (FR55). A short commit
  shows as plain text.
- **Phone:** service and version on line one; activity and flag on line two; started on line three, with no table.
- **Coverage strip:** services with coverage `none`, from `service_last_seen`, listed in one line with a setup link.
- **Does not show:** a health score, a green or red verdict, or a live ClickHouse read per row. The flag appears only for
  `higher`, read from what a job stored.

| State | What it does |
|---|---|
| Loading | Skeleton rows (`shared/components/Skeleton`) |
| Empty, no rows at all | "No releases yet. Pass a build id into `service.version`, or send deploys from CI." Two links |
| Nothing in this window | "No releases in the last hour. The latest was v2.1 of checkout, 3 days ago." Offers to widen the window |
| Zero results | "No releases of `checkout` in this window." Offers to clear the filter |
| Partial | A row with no traffic shows `not_seen` words, never a blank |
| CI and traffic disagree (FR46) | "Your CI and your traffic name versions differently: CI sent X, traffic sends Y" replaces the `not_seen` words on that service's rows. Read from Postgres, by `GET /releases`: in the last 24 hours (*guess*), at least two of the service's releases have `first_reported_at` and no `first_seen_at`, and at least one has `first_seen_at` and no `first_reported_at`. X and Y are the newest of each |
| Over cap | "Versions were combined on `checkout`: too many in one minute" |
| Failure | `InlineFailure` |

### `/releases/$releaseId` (new)

Takes `?period=` (default the latest) and `?finding=` (a finding id). In order:

1. **Sentence** from the comparison, naming its method. Beneath it: "This compares numbers. It does not say why they moved."
   Beside it: "Back to finding" when opened from one.
2. **Facts:** the CI's link first, labelled "Link from your CI", then service, version, previous version, started, source, arrival
   time and sender's time when they differ by over 15 minutes, commit as text, its periods ("came back" where it did), each deploy
   event in order.
3. **Warnings, where true:** two environments; versions combined; incomplete with its minute count.
4. **Comparison:** up to two blocks, "During the rollout" and "Since the rollout finished". In each, a row for failure share, p95, and
   either traffic share or requests per minute; a column for each side, with counts and the plain word. Window chooser: 1, 6, 24 hours,
   applying to the second block. Last week's column for before-and-after methods. Under the table: "Counts assume you do not sample
   before sending", the warm-up rule, and FR49's sentence under any `about_same`.
   **Phone:** one stacked block per measure: "Failure share: higher. v2.1 3.0% (180 of 6,012), v2.0 0.2% (12 of 5,844)", with last week
   on its own line.
5. **Rollout chart:** `TrendChart` in a new categorical mode with `--series-1` to `-5`, as `design-system.md` allows, and a dash per line
   as a second carrier. Top four versions plus "Other versions"; "No version" and "Combined versions" as labels. A table of the same shares
   sits below for touch and screen readers.
6. **Errors by version:** new here, gone here, in both, with the caution in FR29 written beside the list, not in a tooltip. Each links to its
   group.
7. **Findings since:** "Started after this release", linked.
8. **Sender's details:** read from `sender-details`; `metadata` as plain text in a closed `<details>`, for members and above.

**Does not show:** any word implying cause; per-endpoint numbers; a pass or fail badge; author names.

**Opening sentence, by method and reason.** Exact words, each asserted by `comparison.test.ts` (AC66).

| Case | Sentence |
|---|---|
| `side_by_side` | "Side by side over 42 minutes: failure share 3.0% on v2.1 against 0.2% on v2.0, higher. p95 412 ms against 398 ms, about the same." |
| `side_by_side`, mixed floors | "Side by side over 42 minutes: failure share not clearly different. Too little traffic to read p95 (50 requests each)." |
| `side_by_side`, p95 in warm-up | "Side by side over 3 minutes: failure share not clearly different. p95 is left out for each version's first 5 minutes." |
| `version_before_after` | "Before and after, by version, over 1 hour: failure share 0.4% on v2.1 against 0.3% on v2.0, about the same. p95 310 ms against 290 ms, about the same. Different times of day can move these numbers." |
| `time_before_after` | "Before and after, by time, over 1 hour: failure share 0.8% against 0.2%, higher. No version in your traffic, so a rolling deploy mixes old and new here." |
| `too_recent` | "Under 5 settled minutes so far. Refreshes on its own." |
| `no_previous_version` | "This is the first version we know of on this service, so there is nothing to compare it with." |
| `already_running` | "Running when we started counting. Comparisons start from the next rollout." |
| `too_little_traffic` | "Too little traffic to compare: 12 requests on v2.1, 9 on v2.0." |
| `numbers_expired` | "Numbers for this release have expired (kept 30 days on this project)." The limit is the project's own |
| `no_version_no_deploy` | "No version in your traffic, and no deploy reported, so we cannot compare." |
| `new_version_not_seen` | "Reported, never seen in traffic. This service sends other versions (v2.0). The rollout may have failed, or the version text may differ." |
| `new_version_not_seen`, under 30 minutes | "Waiting for traffic on v2.1." |
| `no_request_traffic` | "We count requests from server traces and queue consumers. This service sends none, so we can mark its deploys but cannot compare them. This is expected for a service that only runs in the browser." |

| State | What it does |
|---|---|
| Loading | Skeleton for each section, separately, so facts appear while ClickHouse reads |
| Failure | Per section, `InlineFailure`; facts stay |
| Error read timed out | "This read took too long. Try the 1-hour window." Never an empty list |
| Error data expired | "Error details aged out on {date}" |
| Incomplete | Label beside the numbers, with minute count |
| Delayed | "As of 14:32" |
| Not found | Central 404 screen. Another tenant's id looks the same |

### Release markers on charts (`TrendChart`, `shared/deploy-markers.ts`)

- **On the canvas:** every marker is the same neutral dash in the axis colour, per the rule that a deploy is context. Dash does not
  carry source, since finding charts already use dash for their three lines (`finding-page.ts:152`, `TrendChart.tsx:41`), and a chart
  is 132 pixels tall (`:21`).
- **The strip:** an HTML strip above the plot, aligned by `valToPos`, carries word tags: "failed", "rolled back", "seen", "3 releases".
  It falls back to a short mark only when space is tight. A marker draws under the series and is clipped to the plot, so a letter painted
  on canvas would be overprinted (`:255-261,361-367`).
- Markers within 8 pixels (*guess*) collapse into one tag with the count.
- **Hover** shows facts only (service, version, source, time), in a React portal fed from the cursor hook's state, so it renders as text.
  A **click** on a marker or tag pins the box, with its link to the release and an Escape or close control. A drag still selects. A box
  following the cursor could not otherwise be reached.
- **Text list:** one per chart group, not per panel, "Releases in this window (3)", the same facts, each row linking to the release. Open
  by default on the finding page, closed elsewhere. This is how touch, keyboard and screen-reader users get them.
- **One always-visible line** under the group replaces Overview's sentence at `OverviewScreen.tsx:165-170`: "Dashed lines mark the 3 releases in
  this window. A tag names failed, rolled-back and seen-only ones."
- **If the read fails:** "Releases could not be loaded for this chart" with a retry, never an unmarked chart.
- **Marks drawn:** a period's start, except `already_running`; and each deploy event, including same-version redeploys.
- Fed by the new windowed read. Overview names no service filter; service and finding charts do
  (`deploy-markers.ts:37-58` keeps that logic).

### Finding page (existing, `FindingScreen.tsx`)

A new *Release before this* section after the header, and `markers` passed at `FindingScreen.tsx:535`. F3's Step 1 folds it in as the first row of F3's section (F3 FR14, A45), and F5 extends that row (F5 FR1).

| Case | Words |
|---|---|
| Under 24 hours | "v2.1 of checkout started 6 minutes before this. Reported by your CI, seen in traffic." |
| Older | Dimmer: "The last known release was 41 days earlier." No comparison prompt |
| CI said failed | "... CI reported it failed." |
| CI said rolled back | "... CI reported it rolled back." |
| Came back | "v2.0 came back 6 minutes before this." |
| Reported, never seen | "v2.1 was reported 6 minutes before this, and we have not seen it in traffic." |
| Already running | "v1.4 was running when we started counting on {date}. No release since." |
| `deploys_and_versions`, `deploys_only` or `versions_only`, nothing before | "No release of this service before this started, from what we can see." With the last report's age where there is one |
| Coverage `none` | "We receive no deploy information for this service." With the setup link for those who can act |
| First report after this started | "We first heard about deploys on this service on {date}, after this started." |
| Project finding | Latest release per affected service within 24 hours, newest first, up to 5, then "and 3 more" |

**These rows carry "It came first. That is not evidence that it caused this.":** under 24 hours, older, CI said failed, CI said rolled back, came back, reported never seen, and a project finding that lists at least one release. **These rows do not, since no release came first or none is known to have:** nothing before, already running, coverage `none`, and first report after this started.

**Elsewhere in this project:** releases in the 2 hours before the finding, with those this service calls marked "this service calls it", from
`service_edges_1m` (`client_service`, `server_service`, `000014`). If that read fails, the releases still list, unmarked. With nothing on this service: "No release on this service. 3 elsewhere in this
project in the last 2 hours."

The link to the release carries the finding, so the comparison opens on a window reaching the finding's start (FR22). This matters: with a release 5
hours earlier, the default hour would compare a healthy first hour and read "about the same" while the service is failing.

### Project settings (`features/projects/DeploysSection.tsx`, new)

Beside `DetectionSection`, in the same pattern as `CheckSecretsSection`, hidden without `MANAGE_CREDENTIALS`
(`ProjectSettings.tsx:66`). The non-admin sentence at `ProjectSettings.tsx:66-74` gains "deploy secrets". Linked with an anchor.

- URL and copy button.
- Secret list: ending, created, last used. Mint button; the new secret shows once with a warning and a copy button, and `UnsavedChanges` warns
  before leaving. Withdraw uses inline confirmation (the convention at `ProjectSettings.tsx:270-289`), stating the last use: "Last used 4 minutes ago.
  Deploys signed with this secret will be refused." It offers "also remove what it sent since {time}".
- **Numbered rotation steps**, as text on the screen.
- `Tabs` for recipes: GitHub Actions, GitLab CI, `curl`. Each is a complete step with the project's URL filled in.
- "Last deploy received", "last check received" and the last signed refusal ("Item 3, field `url`, 2 minutes ago"), in a `role=status` region, polled
  every 5 seconds while the section is open, for at most 10 minutes (*guess*). With nothing arrived: "Nothing received yet. A wrong signature is not recorded
  here: your CI's output shows a 401."
- Each service's current version and when we first saw it, so a fixed value stands out.
- A note: "Already passing a build id into `service.version`? Releases appear with no webhook. CI adds same-version redeploys, commit links and failed states."
- Warnings: two environments; over the release cap.
- An opt-in for the release summary (FR55): off by default, or every release to one chosen channel that people read. It lists channels from the existing `GET /channels`, offers only `READ_BY_PEOPLE` kinds, and saves through `PUT /projects/{project_id}/release-summary`. Deleting that channel turns the summary off.

Validation is inline. Mint refused at 5 secrets explains why.

### Errors (existing)

The cell at `ErrorsScreen.tsx:364` shows `after v2.1`, "none before" or "not told", with the mark only for an unread value. The column stays hidden below `xl`
(`:38`), so `ErrorGroupDetail.tsx:136-143` always states the deploy line, at every coverage value. The comment at `:132-135` goes with it.

## Proposed design

### 1. The version rollup

New ClickHouse table, migration `000038_service_versions_1m`:

```sql
CREATE TABLE IF NOT EXISTS service_versions_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `version` LowCardinality(String) CODEC(ZSTD(1)),
    `request_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `error_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `latency_buckets` AggregateFunction(sumForEach, Array(UInt32)) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, service_name, minute, version)
TTL minute + toIntervalDay(least(derived_retention_days, 35))
SETTINGS index_granularity = 256, non_replicated_deduplication_window = 1000
```

This is `000035`'s shape, plus `version`, so its measured facts (the `sumForEach` form, deduplication) carry over.
Version sits **after** minute because reads ask for one service across a window, then split by version.

**In the worker** (`apps/workers/internal/pipeline/`):

- `derive.Span` gains `Version`. `countable` (`stages.go:39`) sets it through a small function that gives an empty
  string when the envelope's door is `DOOR_RUM` (D12). `stages.go:263` has the envelope in hand.
- **The end state for a browser-door SERVER or CONSUMER span is that it counts in neither table (D55).** The F6 change filters it where both tables are fed, before `observeService`, so AC15's sum and FR28's check keep holding. That change deletes the empty-string branch above. Until it lands, the span counts under the empty version in the version table, and in `service_stats_1m`, so the sum holds then too. If the filter were placed so that only one table lost the span, FR28 would read "incomplete" on every such minute, so the F6 change's test must assert both tables.
- `Counters` gains a fourth map, `versions`, keyed like `services` with the version added
  (`counters.go:191`, `services.go:85`), and fed in `observeService`, so both tables count exactly the same spans.
- `Disown`, `Drained`, `Pending*` and `Derived` extend to it (`counters.go:199-205,293-334,365-400`).
- `versionwriter.go` copies `servicewriter.go`, including its deduplication token.

**The caps, and what they really give.** A rollup with a free-text column needs them. None makes a guarantee about a minute.

| Cap | When | Value (*guess*) | What it gives |
|---|---|---|---|
| At drain, per service, per drain | Drain, in sorted order, ranked by requests then name | 20 versions, the rest folded into `__other__` | Deterministic, so replay gives the same block (`services.go:168-171`). **Local to one drain.** A late span reopens a minute and flushes a second row (`counters.go:215-217`), and rows arrive from every worker that owns a partition (`000035` header). So a minute can hold more than 20 version rows, and one version can be kept in one drain and folded in another. Its count is then short, while totals still match |
| At arrival, per partition, service and minute | `observeService` | 64 distinct versions, then `__other__` | A memory bound between drains. **First come, first served.** A burst of junk early in a minute absorbs a real version, and the later ranking cannot recover it. Per partition, because a partition's order is fixed by its offsets, so replay stays identical |
| At arrival, per tenant, partition and minute | `observeService` | 512 distinct version entries in total, then that service's `__other__` | Bounds one tenant across many services. `MaxServicesPerTenant` applies only at drain (`services.go:55,205`), so it bounds nothing during arrival |

Also folded: a version over 200 characters (the Postgres column's size) or failing the shared name check. A service dropped by `MaxServicesPerTenant` drops
its version rows in the same drain, so FR16's sum holds.

So **the comparison reads `__other__` for its service and window, and whenever that is above zero says "Versions were combined" and marks itself inexact** (FR17).

**Worst-case memory, by arithmetic.** With a guessed 400 bytes per entry (the totals struct alone is 272, by arithmetic on `serviceTotals`, `services.go:116-120`): without the tenant cap, 2,000 services × 64 versions × 12 partitions
(`setup/topics.go:29`) × 2 open minutes × 400 bytes is about 1.2 GB, above the worker's 1,088 MiB `GOMEMLIMIT` (`nexovue.yaml:249`) in a process running every
pipeline. With it: 512 × 12 × 2 × 400 bytes is about 4.9 MB per hostile tenant. Many hostile tenants still add up, and about 100 would hold 490 MB. The gate measures this.

**Insert, but no hold, and no stop.** `insertDerived` (`pipeline.go:703-725`) gains the insert, last, after `service_stats_1m`.

- **A permanent refusal** is counted and stepped over, and does **not** set `anyRefused`, so F6's progress is not held (D13).
- **A transient failure** must not behave as the others do. They return an error after a 2-minute budget (`consume/retry.go:19`), which ends the pipeline for every signal, the very failure `refusal.go:87-88`'s comment says was removed. So this insert gets its own 30-second budget (*guess*), a new `consume` helper taking a budget, and past it is counted and stepped over like a refusal.
  After one such failure the insert is skipped for 5 minutes (*guess*), counting the rows it skipped, so a sustained failure on this one table costs 30 seconds once and not on every drain.
- The insert never returns an error from `insertDerived`. If all of ClickHouse is down, the other inserts fail and block as they do today.

The cost is a hole in version numbers, which FR28 labels, and the hole is not repaired.

### 2. The releases record

New Postgres tables, in one migration. `releases` is the identity with the stable id, and `release_periods` holds each period in service.

| `releases` column | Type | Note |
|---|---|---|
| `id` | UUIDv7, primary key | The id F3 and `/releases/{id}` hold |
| `org_id`, `project_id` | FK, cascade | |
| `service_name`, `version` | `String(NAME_MAX_LENGTH)` | Unique with `project_id`. `service_last_seen` allows 255 (`models.py:132`), so the sync skips what does not fit |
| `first_seen_at`, `last_seen_at` | timestamptz, null | From the rollup. Null if never seen |
| `first_reported_at`, `last_reported_at` | timestamptz, null | From the sender's `deployed_at` |
| `report_count` | integer | Repeat reports of one version, which is what exposes `latest` |
| `last_state` | `String(SHORT_LABEL_LENGTH)`, null | Last deploy's state. `CHECK` on the four values |
| `last_activity_at` | timestamptz, not null | The latest **arrival** of a report or count, never the sender's claim. Decides retention, so a sender cannot shorten it |

| `release_periods` column | Type | Note |
|---|---|---|
| `id` | UUIDv7, primary key | Passed to `/comparison` |
| `release_id` | FK, cascade | |
| `org_id`, `project_id`, `service_name` | | Copied, for the reads below |
| `started_at` | timestamptz, not null | By the one rule in section 3. Not moved once counted |
| `start_basis` | `seen` or `reported` | Which of the two it came from |
| `ended_at` | timestamptz, null | Null while in service |
| `previous_version` | `String(NAME_MAX_LENGTH)`, null | What this period replaced. Set when the period opens, so a rollback's is the version it replaced. Agrees with FR20 |
| `already_running` | boolean | FR43 |
| `is_return` | boolean | Reads "came back" |
| `last_reading` | JSONB, null | The job's stored reading: method, readings and counts, no names. FR55 |
| `mixed_environments` | boolean, null | FR45 |

Indexes on `release_periods`: `(org_id, project_id, started_at DESC, id)` for the list and markers; `(org_id, project_id, service_name, started_at DESC)` for the "before this" read.
On `releases`: `(last_activity_at)` for the retention sweep. An open period overlaps every later window, so the list read adds the partial index `(org_id, project_id) WHERE ended_at IS NULL`.

`release_sync_state(org_id, project_id PK, synced_through)` holds how far the sync has read per project.

`deploy_intake_state(org_id, project_id PK, last_check_at, last_deploy_at, last_refusal_at, last_refusal_position, last_refusal_field, last_refusal_secret_id)` holds what the Deploys section shows, one small row per project. `last_used_at` per secret cannot serve, since a real deploy moves it too.

`deploy_events` gains `commit_sha String(64) NULL`, `url Text NULL`, `state String(SHORT_LABEL_LENGTH) NOT NULL DEFAULT
'finished'` with a `CHECK`, `release_id` FK `ON DELETE SET NULL`, `webhook_secret_id` FK `ON DELETE SET NULL`, and the index `ix_deploy_events_created_at`, since the
sweep would otherwise scan the table (the only time index is `ix_deploy_events_org_id_project_id_deployed_at`). The existing `delivery_id` becomes `NOT NULL`, and
migration-versus-model disagreement (255 at `0007:72`, 200 at `models.py:806`) ends at 200.

`project_webhook_secrets`: `secret` is replaced by `sealed_secret LargeBinary NOT NULL` plus `secret_hint String(8)` and `last_used_at`. Sealed with `SealedBox`, a new purpose
`deploy-webhook`, under `CREDENTIAL_ENCRYPTION_KEY`, which only the API holds (`deploy/compose/nexovue.yaml:288`), bound to `project_id:secret_id` as owner. The owner needs the
id before insert, and `id` is `server_default uuidv7()` (`base.py:30-33`), so the service reads `uuidv7()` first and sets the id explicitly. Notification channels avoid this with a
nullable column filled afterward (`keys.py:113-114`); a NOT NULL column is the stronger choice. Added to `SEALED_COLUMNS` (`keys.py:93`). Pre-deployment, there is nothing to convert.

**Why stored and not a view** (D2). `min(minute)` over a table with a 35-day TTL gives a first-seen time that drifts forward as rows expire. Reported-but-never-seen releases have no
rollup row at all, so a view would be a two-store join on every list request. F3's evidence needs stable ids. And the record must outlive its numbers.

**Why periods and not a moved start** (D15). v2.0 Monday, v2.1 Tuesday, finding Wednesday, rollback to v2.0 Thursday, v2.1 again Friday. With one moving `started_at`, after Friday both rows
start after the finding, so "the release before this" names something older or nothing. Tuesday's marker disappears from a chart of last week, "findings since" changes for a release already
read, and a frozen `previous_version` compares a rollback against the wrong side.

### 3. Filling the record

**One rule for a period's start (FR42).** Once counted, `started_at` is the first counted minute and `start_basis` is `seen`, and only opening a new period creates a new start. Before any
traffic it is the reported time, with `start_basis` `reported`. A webhook never changes a period whose `start_basis` is `seen`, whatever time it claims. So a report at 14:00 and first traffic at
14:03 gives 14:03, and a stolen secret cannot pull a measured start back by 7 days. **`LEAST` applies only to `first_reported_at` and `first_seen_at`. `GREATEST` applies only to the last-* columns.** Neither touches a start.

**From the webhook,** in the same transaction as the deploy:

1. Sort items by `(service_name, version)`, so every writer takes row locks in one order. A deadlock would otherwise abort a transaction across 100 upserts, and `begin_nested` per item (`deploys/service.py:120-134`) handles a duplicate, not this.
2. Look up which `delivery_id`s exist. Repeats are answered and go no further.
3. Count the new items against the hourly limit (below). Past it: 429, whole batch.
4. `INSERT ... ON CONFLICT DO NOTHING` on `delivery_id` for deploys, and one savepoint is no longer needed per item.
5. `INSERT ... ON CONFLICT (project_id, service_name, version) DO UPDATE` on `releases`, by the rule above.
6. If the version has no period: open one, `start_basis` `reported`. If its latest period has ended, and the service sends no version in traffic, and this `deployed_at` is later than the latest other period's start: open one, as a return (FR19). A service with versions in traffic ignores this step: the sync decides.
   *Founder decision, 2026-11-09:* only a report whose `state` is `started` or `finished` takes this step. A `failed` or `rolled_back` report is stored, filed under its release and its state shown, but opens no period, ends none and brings no version back, since that deploy did not replace what was running. Working periods out again after `DELETE /deploys/{id}` or a withdrawn secret's removal follows the same rule. A release reported only as failed therefore has no period, and the list, which shows releases by period, leaves it out; its own page shows its facts.
7. One `deploy.batch_recorded` audit row. Update `deploy_intake_state`.

Apply the release cap (FR56): past 50,000, evict before creating.

**The hourly limit.** `RateLimiter.consume` takes no amount: `ExactWindows` hard-codes `hits=1` and `hits + 1` (`limiter.py:159-190`), and the limiter sits with the middleware, which runs before the route. So `consume` gains a `cost` argument on the exact path, a new exact `Quota` (`deploy-accepted`, 1,000, window 3,600) joins `policy.py`, and the middleware's limiter is put on `app.state` for the router. The window survives the expiry sweep: rows go when `window_start < now() - 1 hour` (`jobs/expiry.py`), which is exactly when an hour-long window ends. A refused batch must charge nothing: today's statement adds first and compares after (`limiter.py:169-186`), so a cost needs a conditional update, or a batch of 100 at 950 would burn the last 50. It is a fixed clock-hour window, so a burst across the boundary can reach 2,000, which is accepted. Fails closed: if Postgres cannot count, it refuses.

**From telemetry.** `sweep:releases`, every 60 seconds, in `apps/scheduler/src/nexovue_scheduler/releases/sync.py`:

1. Lists active projects, reusing `detection/projects.py`'s `active_projects` and `fair_order`. Skips a project whose `service_last_seen` has not moved since its last run.
2. Enqueues one `releases.observe_project` job per project on **its own `RELEASES_QUEUE`,** added to `worker_queues`' default (`__main__.py:80-84`) and to `QUEUE_CAPS` (`jobs/worker.py:72`) with a cap of 2 (*guess*), inside the one floor F3 adds for capped queues on `scheduler-light` (F3 A56: together they hold at most 4 of its 8 slots, taking turns), with a 60-second expiry (as `PROJECT_JOB_EXPIRES_SECONDS`, `coordinator.py:64`), so a later sweep replaces a late job. Ids only (`jobs.py` stores arguments readably). `SHARED_QUEUE` is `light`, which runs the alert sweeps (`beat.py:396-405`), and a slow ClickHouse could otherwise fill all eight slots.
3. The job takes the per-project `session_lock` (`detection/projects.py:154`). If not held, it ends.
4. It reads rollup rows **per minute and version**, from `synced_through` minus 5 minutes, up to 6 hours per run (*guess*), never past `detection_sweep.settled_minute`. Statements live in `releases/store.py`, which binds tenant and project. It skips `''` and `__other__`.
5. It walks each service's minutes in ascending order. Grouping by version would collapse v1, v2, v1 into one min and max, and hide the return. It works out its changes first, then applies them in one transaction sorted by `(service_name, version)`, the webhook's order, so the two take row locks alike.
6. **Skipping:** a row whose service name or version does not fit Postgres is skipped, counted and logged, per row. The webhook's name check does not reach telemetry (`flatten.go:142` takes the attribute as it is), and one bad name must not stop a project for good.
7. **Periods (FR19):** on a version's first counted minute, set `first_seen_at`, and open a period or move a `reported` period's start to it. A period ends when another version holds over 90% of the service's versioned requests for 10 minutes in a row, each with traffic, or when this version has had no requests for 60 minutes while another has had at least 20. An ended version returns when it holds at least 20% of them, and at least 5 requests, over a 10-minute stretch, and its new period starts at that stretch's first minute with traffic. A rolling deploy therefore cannot "return" every minute. A service with a request per version every 15 minutes never ends a period, while a quiet service's real swap still does (*all guesses*).
8. **New period:** `previous_version` is the other non-empty, non-`__other__` version with the most requests in the hour before, failing that the version of the period before. `already_running` when it is the first version counted for that service and no report precedes it.
9. **Caps:** 200 new releases per project per run (*guess*). Past it, `synced_through` stops at the last minute finished, so the next run carries on and nothing is lost.
10. **`synced_through` advances only to the lesser of `settled_minute` and the newest minute read, and only moves forward.** Rollup rows for an old minute can arrive much later: a worker backlog, one partition lagging, or a late span reopening a minute (`counters.go:215-217`). Settled means every partition has counted through it (`detection/settle.py:143`), so this catches a backlog and a lag. A version seen **only** in a span more than 5 minutes later than its settled minute is still missed until it is counted again. That is a stated risk.

**Eviction (FR56).** One cap on both doors, of 50,000 releases per project (*guess*). In order: never-seen releases of unknown services; then the oldest with no open period. A release with an open period is never evicted. Metric, log and a sentence in the Deploys section.

**Lag:** a seen release appears 1 to 3 minutes after its first settled span (*expected*; AC22 times it). F3's evidence job may read just before that. F3's existing "evidence changes materially" run covers it, and F3's design should note this.

**Retention.** Both tables join `jobs/expiry.py`'s `_EXPIRED`, through a new `releases/retention.py` as `detection/retention.py` does, using the same `project_retention` read and shortest-plan guard as `_PAST_DERIVED_RETENTION`: `deploy_events.created_at` and `releases.last_activity_at` past the project's own derived retention. Periods cascade. Since `last_activity_at` is arrival time, a release cannot lapse before its deploy events, and `ON DELETE SET NULL` covers eviction. `sweep()` has no per-table guard (`expiry.py:49-62`), so one failing delete would stop every table after it, and T4.6 tests this does not happen.

`project_retention` excludes deleted projects (`0005:71-80`), so they have no value here. A second clause removes rows of a project deleted longer than the shortest plan's derived retention ago. Reads refuse them from the moment of deletion, through `require_project`. On Free the record lasts about as long as its 30-day rollups, so "numbers expired" matters mainly on paid plans.

### 4. The comparison

`nexovue_platform/releases.py` holds pure code and no store access (D26), so the API today and the scheduler for F3 use one definition:

- `LOG_EDGES` and `bucket_p95` move here from `scheduler/detection/method.py:36,816`, which then imports them. The arithmetic must not exist twice.
- `choose_method(...)`, `compare_failure_share(...)`, `compare_p95(...)`, with the floors and rules from FR23 and FR24.
- `coverage_of(reported_at, seen_at, *, at)`, `DeployFact`, and `DeployHistory`, moved from `deploys/service.py:147-187`. Its `DeployEventSummary` input becomes a plain frozen dataclass, since the shared package must not import API schemas, and that dataclass has no `metadata`.
- SQL fragments, as `readings.py` does. Full statements live in each service's own module: `releases/queries.py` in the API, `releases/store.py` in the scheduler.

**Reads:**

| Statement | Table | Purpose |
|---|---|---|
| `VERSION_MINUTES_SQL` | `service_versions_1m` | One service, one window, per minute and version: requests, errors, buckets. Includes `__other__` and `''` |
| `SERVICE_TOTALS_SQL` | `service_stats_1m` | Same window, per minute, for FR28's check and the time fallback. Also last week's window |
| `ERRORS_BY_VERSION_SQL` | `spans`, `logs`, using `errorgroups/queries.py`'s `_OCCURRENCES`, with one version predicate per table (`deployment_version` on spans, `resource_attrs.service.version` on logs, following `_ONE_SERVICE_PLACEHOLDER`), and a door predicate | Groups per version only. Never a rate (BR6) |
| `ENVIRONMENTS_SQL` | `spans`, `resource_attrs.deployment.environment.name` | Which labels appear for this service in the window. Presence only. Never a rate |

**The algorithm,** an ordered decision list. `choose_method` implements it, with a test per row (AC32, AC66).

1. `to` = lesser of window end and `detection_sweep.settled_minute` (`detection/queries.py:44`). If the settled minute is unknown, `to` = now minus 3 minutes, marked "as of".
2. Window start is older than the project's limit (`least(derived_retention_days, 35)`): `numbers_expired`. This comes first, or an old reported release would get `time_before_after` over nothing.
3. Under 5 settled minutes since the period's start: `too_recent`.
4. Read `VERSION_MINUTES_SQL` from the larger of 1 hour and the chosen window before the start, to `to`, and `SERVICE_TOTALS_SQL` for the same span.
5. **The service has no rows at all:** `no_request_traffic`.
6. **Every row is under the empty version:** if a deploy was reported, `time_before_after` from `SERVICE_TOTALS_SQL` around `deployed_at`, with the failure floor, or `too_little_traffic`. If none was reported: `no_version_no_deploy`.
7. **This version has no rows, and others do:** `new_version_not_seen`. It never shows a time comparison, since that would describe traffic the version did not serve.
8. **No previous version** (FR20): `no_previous_version`, or the `already_running` words.
9. **Rollout block:** minutes where both versions have requests. If each side reaches the failure floor: `side_by_side`. p95 falls back per cell at its floor.
10. **After-rollout block:** the rollout has finished when the previous version holds under 1% for 5 minutes in a row (*guess*), or there was no overlap. The new version from then to `to`, against the previous version's equal stretch before the rollout. If each side reaches the failure floor: `version_before_after`.
11. Neither block reached a floor: `too_little_traffic`.

Which block leads: the second if it exists, otherwise the first. A long-running canary keeps a growing first block.

**Warm-up (FR48).** Each version's first 5 minutes (*guess*) are left out of its p95 sums, by default and stated on the page. New instances start cold, so p95 would otherwise read `higher` after most healthy deploys. With a 3-minute overlap, the rollout block has no p95, and the second block carries it.

**The words (FR24).** Every rule is a *guess*, and the pure functions take them as named constants.

- Let *n1*, *n2* be requests, *p* the pooled share, floored at 1/(n1+n2) so a zero count cannot give zero spread, and *SE* = sqrt(p(1-p)(1/n1 + 1/n2)). Let *d* be the difference in share.
- `higher` or `lower`: |d| / SE ≥ 3.
- `about_same`: not the above, and |d| + 3·SE ≤ 1 percentage point. Traffic has ruled out a change that size. Arithmetic: about 360 requests per side when both shares are near 0.2%, and about 5,200 near 3%.
- `not_clearly_different`: otherwise. 0 of 20 against 5 of 20 gives z of about 2.39, and reads this, where "about the same" would be false. So does 0 of 20 against 0 of 20.
- p95: `higher` or `lower` above a ratio of 1.2, `about_same` inside it, and "too little traffic" under the floor.

This differs from the console's shared `Delta` threshold (D50).

**Completeness (FR28).** Sum each compared minute's requests across all versions, and compare with `service_stats_1m`'s minute. Any difference: `incomplete: true` and `minutes_differing: N`. A read landing between the two tables' inserts could flicker for a moment on a reopened minute. That is accepted, since it clears and it errs toward saying incomplete.

**Errors by version, at the limit.** Neither `spans` nor `logs` holds the version in its sort key (`000001_spans.up.sql:39`), and on logs it is a JSON path. Reads run through the reader account's 30-second limit (`users.d/reader.xml:23`). Step 4 measures this at 24 hours on seeded data. A timeout reads in words, never as an empty list. If the measurement is poor, the section defaults to the 1-hour window.

**Response of `GET /releases/{id}/comparison?period=...&window=...`:**

```json
{
  "period_id": "...",
  "reason": null,
  "lead": "after_rollout",
  "as_of": "...",
  "incomplete": false, "minutes_differing": 0,
  "versions_combined": false,
  "mixed_environments": false,
  "blocks": [
    {
      "kind": "rollout", "method": "side_by_side",
      "from": "...", "to": "...", "minutes_compared": 3,
      "this_version": {"version": "v2.1", "requests": 612, "errors": 2, "p95_ms": null},
      "previous_version": {"version": "v2.0", "requests": 584, "errors": 1, "p95_ms": null},
      "failure_share": {"reading": "not_clearly_different"},
      "p95": {"reading": null, "note": "warm_up"}
    },
    {
      "kind": "after_rollout", "method": "version_before_after",
      "from": "...", "to": "...", "minutes_compared": 42,
      "this_version": {"version": "v2.1", "requests": 6012, "errors": 180, "p95_ms": 412.0},
      "previous_version": {"version": "v2.0", "requests": 5844, "errors": 12, "p95_ms": 398.0},
      "failure_share": {"reading": "higher"},
      "p95": {"reading": "about_same"},
      "last_week": null
    }
  ]
}
```

### 5. Why this method, in one example

A 40-minute rolling deploy. New version fails 3%, old 0.2%, and new takes 20% of traffic at the midpoint. A time-window comparison sees 0.76% (arithmetic: 0.8 × 0.2% + 0.2 × 3%) and reads a small rise. The version split sees 3.0% against 0.2%, in the same minutes. The two versions may not receive the same mix of requests: new instances start cold, old ones drain, and canary routing or sticky sessions can send different users to each. So "same minutes" is what we claim, never "same traffic". AC28 is this test, and it records whether F6 opens a finding on the 0.76%, which is not established.

**Where it does not help:** no `service.version`; same-version redeploys (a marker, but no comparison); config-only changes; low traffic; one broken endpoint inside a healthy service; sampling before sending; two environments mixed into one project. The screen says which, where it can see it.

### 6. The stored reading and the release summary (FR55)

`releases.summarize_period` is enqueued by the sync when a period opens, for 30 minutes later (*guess*), on `RELEASES_QUEUE`. It runs the shared comparison from the scheduler, in `releases/store.py`, and writes `release_periods.last_reading` and `mixed_environments`. The list reads that without touching ClickHouse.

**The opt-in (D53).** `projects.release_summary_channel_id`, a nullable foreign key to `notification_channels` with `ON DELETE SET NULL`. Null is off, and off is the default. `PUT /projects/{project_id}/release-summary` takes `{"channel_id": uuid | null}`, under `MayConfigureProjects`, in `projects/router.py` beside `sampling-config` (`:91`). It answers 422 if the channel is not this organization's or its kind is not in `READ_BY_PEOPLE` (`nexovue_platform/alerts/channels.py:34`). It writes one audit row. `GET /projects/{project_id}` returns the value, so no new read is needed.

**The notification (D52).** If the column is set, the same job writes one `notifications` row of a new frozen kind, `release_summary` (`nexovue_platform/alerts/model.py:47`, with its migration copy and `test_alert_vocabulary.py`). It never pages, since the insert requires a `READ_BY_PEOPLE` channel. It goes out for every release, with no filter on the reading, so it stays a report and not a detector. That includes a period with no reading, which carries the reason's words. A period flagged `already_running` gets none, since it is not a release (FR43). The kind is not added to `FINDING_TOLD_KINDS`.

Today a row cannot carry this. `names_one_subject` (`models.py:1454-1458`) requires an incident or a finding unless the kind is in `PROJECT_NOTIFICATION_KINDS` (`model.py:73`), and a project notice is built with `detail={}` (`outbox.py:708-719`) from a fixed sentence per kind (`messages.py:365-381`). So counts have no path to the message. The changes, all in T5.6:

| Where | Change |
|---|---|
| `notifications` | `release_period_id` nullable, foreign key to `release_periods` with `ON DELETE CASCADE`, as `incident_id` and `finding_id` have. Unique on `(release_period_id, channel_id, kind, sequence)`. `sequence` is always 0, so a retried job inserts nothing twice (`ON CONFLICT DO NOTHING`) |
| `names_one_subject` | `num_nonnulls(incident_id, finding_id, release_period_id) = CASE WHEN kind IN (project kinds) THEN 0 ELSE 1 END`. A new check also requires `release_period_id IS NOT NULL` exactly when the kind is `release_summary`. Replaced the way `0050` replaces its checks |
| `uq_notifications_project_id_channel_id_kind_sequence_project` (`models.py:1446`) | Its condition gains `release_period_id IS NULL`. Otherwise every summary on a channel has both other subjects null and sequence 0, so the second release's summary would be dropped by the unique index |
| `alerts/outbox.py` `_IN_ORDER` (`:95-105`) | Gains `release_period_id IS NOT DISTINCT FROM`. Otherwise all of a project's summaries count as one subject, and a retry on one holds the rest |
| `alerts/outbox.py` `_TAKEN` (`:272-287`) | Selects `notifications.release_period_id`, and left-joins `release_periods` and `releases` by id and org for `release_id`, `service_name`, `version`, `previous_version`, `started_at`, `already_running` and `last_reading` |
| `alerts/outbox.py` `_subject`, `SubjectKind` (`:388`, `:673`) | A new branch before the project fallback: kind `release`, id the period's, key `service:{name}`, label the service, `condition_kind` `release_summary`, `detail` the version, previous version, release id and `last_reading` |
| `alerts/messages.py` `compose`, `compose_batch` | `_compose_release`: "Release summary: v2.1 of checkout", then a line per measure with counts and the plain word, or the reason's words, then "Order only, not cause". Names pass `one_line`, and the channel escapes them after. The link is the release page. No severity tag in the text. `compose_batch` says "3 release summaries" when every notice is one. In a mixed digest a summary is one line, counted among "alerts", and that is a stated limit |
| `alerts/payloads.py` `colour` (`:66-69`) | The `release_summary` kind is neutral. Amber would read as a verdict. The row's severity is `warning`, since `SEVERITIES` has only two values (`model.py:33`), and that lets quiet hours hold it as a digest |

### 7. The line in a finding's opening notice (FR44)

`_compose_finding` (`alerts/messages.py:275`) renders one line when the notice's kind is `opened`. It sees only `Subject.detail` (`alerts/outbox.py:698-705`), so the line goes there.

**Where the read happens (D51).** In `alerts/delivery.py`, not in `outbox.py`. `deliver_due` runs the whole claim in one transaction on one connection (`delivery.py:53-56`), and a Postgres statement that fails or times out aborts that transaction. So neither a lateral join in `_TAKEN`, as F3's cause sentence plans, nor a second read on that connection can fail alone. The read runs after the claim has committed and before `_send` (`delivery.py:60-61`), on its own connection, calling `releases/facts.py`. `asyncio.timeout(0.5)` (*guess*) covers the wait for a connection as well as the statement. On any failure or timeout the line is left out, one metric moves, and the alert goes on. The message is built with the line by `dataclasses.replace`, since `Notice` and `Subject` are frozen.

**What bounds it.** One read per batch, and only for a batch of exactly one notice whose kind is `opened` and whose subject is a finding on a service. `CHANNELS_PER_CLAIM` is 1, so a claim's batch waits at most 500 ms, against a 60-second lease (`outbox.py:57`). A grouped message (`compose_batch`, `messages.py:204`) carries one line per notice, so it leaves this line out. A project finding names no service, so it carries none either. The finding page still states both, and these are stated limits. A retry reads again, so a line can differ between attempts of one batch, which is accepted. The line is never stored, so outbox rows do not change (AC42).

Names pass `one_line`, and the channel escapes them after. It uses no AI.

**The isolation promise, exactly (AC42).** Detection and the alert evaluation sweeps must not reach `nexovue_scheduler.releases`, directly or through what they import. `detection/notify.py:52` imports `alerts.outbox`, so a call from the outbox would have broken this, which is the other reason the read sits in `delivery.py`. Only `alerts/tasks.py` and the tests import `delivery.py`, and `jobs/registry.py:22` names `alerts.tasks` as a string, so nothing in detection reaches it. T1.12's test walks imports transitively, module-level and inside functions. Its roots are every module in `detection`, plus `alerts/` `sweeps`, `thresholds`, `new_errors`, `silence`, `check_state`, `incidents`, `rules`, `outbox` and `messages`. This is about code paths: one worker process still loads both, through the registry.

## Data flow and interfaces

```
Customer's server → receiver → Kafka → worker → service_versions_1m ─┐
                                                                     │  sweep:releases
Customer's CI → POST /deploys/webhook/{project} → deploy_events ─────┤  (RELEASES_QUEUE)
                                                  │ same transaction ▼
                                                  └──────────→ releases, release_periods
                                                                     │
                     console, read tokens ← /api/v1/releases ────────┤
                     F3, notices, scheduler ← releases/facts.py ─────┘
```

### Routes, under `/api/v1`

| Route | Who | Note |
|---|---|---|
| `GET /releases` | `CurrentReader` | `project_id`, `service`, `version`, `from`, `to`, keyset page. Reads Postgres only |
| `GET /releases/{id}` | `CurrentReader` | Record, periods, events (ids, times, state, commit), previous version, later findings. No sender text |
| `GET /releases/{id}/comparison` | `CurrentReader` | `period`, `window`, `finding`. Telemetry tier |
| `GET /releases/{id}/errors` | `CurrentReader` | Errors by version. Telemetry tier |
| `GET /releases/{id}/rollout` | `CurrentReader` | Share over time |
| `GET /releases/{id}/sender-details` | `CurrentIdentity` | `url`, `source`; `metadata` for members and above. **A person only:** refuses tokens and F4's ticket |
| `GET /releases/markers` | `CurrentReader` | Markers for a window, cap 500, with `truncated`. Postgres only, through `markers_in_window`, the function F20 calls too. Fields: `release_id`, `period_id`, `deploy_id`, `service_name`, `version`, `at`, `basis`, `state`. No sender text (AC45) |
| `GET /releases/coverage` | `CurrentReader` | Per service, optional `at`. The only coverage route |
| `POST /deploys/webhook/{project_id}` | Signature | New body. Answers `release_id`, `known`, `version_seen`, recent versions, per item |
| `GET /deploys/webhook-secrets` | `MayManageCredentials` | No secret value now, so it no longer needs "viewed" auditing. Adds `deploy_intake_state`'s times and last refusal |
| `POST /deploys/webhook-secrets` | `MayManageCredentials` | Returns the secret once |
| `DELETE /deploys/webhook-secrets/{id}` | `MayManageCredentials` | Optional `remove_since`. Removes what it signed since then, audited |
| `DELETE /deploys/{id}` | `MayManageCredentials` | Audited. Recalculates or deletes the release and period |
| `PUT /projects/{project_id}/release-summary` | `MayConfigureProjects` | `{"channel_id": uuid \| null}`. Audited. Section 6 |
| `GET /deploys` | `CurrentReader` | Stays, without `metadata`, `url` or `source`. It gains no window: nothing in the console reads it once charts use `/releases/markers` |

Being `CurrentReader`, the reads join the read surface automatically (`readsurface/spec.py:9-13`). The two new deletes go in `POLICY_OF` under `MANAGE_CREDENTIALS`, and the release-summary `PUT` under `CONFIGURE_PROJECTS`, or `test_authorization_coverage.py:331` fails. Every `/releases/{id}` route loads the release by org and id, then calls `require_readable_project` on its `project_id` (`projects/service.py:282`), which checks token scope and deletion. `refuse_out_of_scope` alone checks only token scope (`:272`), so a release of a deleted project would still answer.

**Foundation's interface, delivered.**

| Foundation names | Here |
|---|---|
| `DeployHistory` in `nexovue_platform` | `nexovue_platform/releases.py` |
| `deploys_in_window(project, service, from, to)` | API: `releases/queries.py`. Scheduler: `releases/facts.py`. **Signed deploy events only** |
| `deploy_impact(...)` | Renamed `release_comparison(...)` (D44). Same pair. **By version first (D1),** as the foundation now says |
| `coverage` | `coverage_of` plus both read paths. Postgres alone answers it, since `releases` stores first-seen and last-report times |
| Markers with a real window | `GET /releases/markers` and `markers_in_window` |
| *(new)* | `releases_seen_in_window(...)`, scheduler: telemetry-based facts labelled `telemetry_text` (D54) |

Two read paths exist because the scheduler cannot import the API (`apps/scheduler/pyproject.toml:14`) and the shared package may not touch a database (`test_cross_service_contracts.py:265`). Fragments live in shared constants, as `readings.py` does, so they cannot drift.

**Signatures, for F3.**

```python
async def deploys_in_window(conn, *, org_id, project_id, service, start, end) -> list[DeployFact]:
    ...
    # Signed deploy events only, from Postgres. Each has a stable deploy_id. basis is "reported".


async def releases_seen_in_window(
    conn, *, org_id, project_id, service, start, end
) -> list[SeenFact]:
    ...
    # Periods with start_basis "seen". basis is "seen", F3's class telemetry_text. A secret ingest key can plant these.


async def release_comparison(ch, conn, *, org_id, project_id, period_id, window) -> Comparison:
    ...
    # Reads ClickHouse as well as Postgres, so F3's table must list a ClickHouse read.


def coverage_of(reported_at, seen_at, *, at) -> Coverage: ...  # pure
async def release_before(conn, *, org_id, project_id, service, at) -> PeriodFact | None:
    ...
    # The last period starting before `at`, at any age (FR32, FR44). D56. PeriodFact: period id, service,
    # version, basis, start; no sender text.


async def releases_near(conn, *, org_id, project_id, service, at, lookback) -> list[PeriodFact]:
    ...
    # Every release in the project in `lookback` before `at`, `service` first (FR52). D56. Postgres only:
    # marking the callees needs `service_edges_1m`, which the caller reads (F3 already reads edges).
```

F28 builds `release_before` and `releases_near` in T3.8 (D56). F3's evidence job calls them and adds none.

F3's base rate must read `deploys_in_window`, not periods. A return is not a deploy, and a seen version may be planted.

### The fact F3 receives

The fields below are an exact allow list. A test fails on any addition (AC45).

| Field | Foundation class | Note |
|---|---|---|
| `release_id`, `deploy_id`, `period_id` | Fact | Stable, for evidence ids |
| `started_at`, `deployed_at`, `received_at` | Fact | `deployed_at` is the sender's claim, and is labelled so |
| `state`, `method`, counts, percentages | Fact | Enums and numbers |
| `basis` | Fact | `reported` (signed), or `seen` (rollup, F3's class `telemetry_text`). **No blanket "measured".** A seen version can be planted by a secret ingest key, and must not rank as signed |
| `coverage` | Fact | Gives the `absence` evidence |
| `service_name`, `version`, and the comparison's `this_version.version` and `previous_version.version` | **Name** | F3 aliases them |
| `commit_sha` | Fact | Hex only, checked at the door, so it cannot hold words |
| `metadata`, `url`, `source`, `delivery_id` | **Free text** | **Not given.** AC45 |

**How these map to F3's three bases (D54).** Foundation rule U6 now uses F3's words, because a browser key or a secret ingest key can move a rollup, so "measured" overstated it. F3 must apply this table.

| F28 fact | Basis | Why |
|---|---|---|
| `basis: reported`, from `deploys_in_window` | `verified` | A signed deploy record. The stated time remains the sender's claim |
| `basis: seen`, from `releases_seen_in_window` | `telemetry_text` | It comes from a rollup, but its one claim is a version string anyone with a secret ingest key can write. So alone it can never be the stated likely cause (F3 A75) |
| A comparison's counts and readings | `telemetry` | Rollup numbers, counted from what was sent. A secret ingest key can inflate them, as with every rollup |

### AI safety

F28 calls no model and holds no AI permission, so it has no AI link needing its own injection suite. It feeds one, so:

- Telemetry is attacker-controlled text. A person with a secret ingest key can put any words in `service.version`, and a person with the deploy secret can put any words in `metadata`. Both are handled above: names are aliased by F3, free text is never handed over.
- F28 gives F3's suite a fixture of hostile version names, `metadata`, `delivery_id` and `source`, and `test_detection_has_no_ai.py` gains F28's modules (AC47).
- **With AI off, nothing changes,** since nothing here reads the AI switch. FR44's line needs no AI.
- **F4.** Its ticket is a third kind of `Reader` (foundation section 5), and its model calls these routes. `CurrentReader` routes carry no sender text, and `sender-details` refuses it. F4's design must keep it so.
- **MCP:** `assistant/tools.py:280-281` returns raw rows through `event.model_dump()`, and ignores `answer.truncated`, setting `total=len(rows)`, so `bounded()` reports nothing (`assistant/bounds.py:23,39`). F28 builds rows from an explicit field list, and adds `partial` (D28). F4 still owns labels and the suite for all MCP tools. We cannot control a customer's own AI on the far end.

## Data and migration impact

| Store | Change | Number |
|---|---|---|
| ClickHouse | New table `service_versions_1m` | `000038`; next free number at build |
| ClickHouse | `door` column on `spans` and `logs` (D38), `LowCardinality(String)` | `000039` |
| Postgres | `releases`, `release_periods`, `release_sync_state`; changes to `deploy_events`, `project_webhook_secrets`; `deploy_intake_state` | next after `0050` |
| Postgres | Frozen notification kind `release_summary`; `notifications.release_period_id` and its checks and indexes; `projects.release_summary_channel_id` | Step 5 |
| Inventories | `organizations/tables.py` (`releases`, `release_periods`, `release_sync_state`, `deploy_intake_state`); `erasure/tables.py` `DERIVED_TABLES` and `erasure/statements.py` | Same task as each table, per F6's C20, or the inventory tests fail |
| Sealing | `keys.py` `SEALED_COLUMNS` | Same task |

**Pre-deployment**, `delivery_id NOT NULL`, the sealed column and the new body shape are direct changes with no compatibility path. `tools/otel-demo/doors.py:218-262` changes with them. Any seeded deploys in a dev database are lost, which is fine.

**Tenancy path.** Every Postgres statement carries `org_id` in the statement, as `deploys/queries.py` does. API ClickHouse goes through `TenantScopedRepository`, plus the project bind. That class does not exist in the scheduler or shared package. **The scheduler's ClickHouse access lives in one module, `releases/store.py`,** added to `SCHEDULER_MODULES_ALLOWED_THE_RAW_CLIENT` (`test_tenant_chokepoint_structure.py:110`), and `PROJECT_BOUND_PACKAGE` (`:116`), a single string today, becomes a tuple with `releases`. F3 plans the same edit, and whichever ships first makes it. `releases/facts.py` reads ClickHouse through the store.

**The scheduler Postgres tenancy test exists:** `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` came with F6 (corrected 2026-11-02; this document and the foundation had said otherwise). F28's upserts run with no request identity, so they must pass it from Step 1b, and any statement that reads every tenant is named there with its reason (D48). The ClickHouse statements are proved with `tenant_statements`, as `test_detection_store.py` does.

The one exception stays `live_webhook_secrets`, which is reached before anything has proved itself and reads the org off the matching secret (`deploys/service.py:92-94`).

## Performance and scale

Nothing here is measured. Arithmetic is marked.

| Thing | Expectation | Basis |
|---|---|---|
| Rollup rows | Near `service_stats_1m`, more during rollouts | **Guess:** a service mostly runs one version |
| Index memory | About 2.1 MiB per 78 million rows | `000035`'s measured figure for the same shape |
| Worker memory | Worst case in section 1: about 4.9 MB per hostile tenant, plus the ordinary map | **Arithmetic on a guess.** The earlier "about double" did not follow from the design |
| Sync queries | One per active project per minute, at most | Arithmetic |
| Postgres `releases` | 39,500 rows a year for 100 services with one release a day (100 × 395) | Arithmetic, and the frequency is a guess |
| Worst-case webhook storage | 1,000 an hour × 1 KiB metadata = 1 MB an hour, bounded by retention | Arithmetic, on a stolen secret |
| Audit rows | Normally one per request. Worst case single-item requests, 1,000 an hour, about 8.76 million a year, and `audit_log` does not expire (`jobs/expiry.py:27-44`) | Arithmetic. A stated risk |
| Unsigned request | Up to 5 secret openings and 5 HMACs over 512 KiB, bounded by 60 a minute per address | Measured in Step 2 |
| Comparison read | Over 24 hours at most, one service, plus a stretch before | Bounded by key |
| Errors-by-version read | Scans `spans` and `logs` for one service over up to 24 hours, for two versions | **Unknown.** Measured in Step 4 |
| Per-endpoint option (D18) | Rows of `endpoint_stats_1m` × versions | **Arithmetic estimate only,** in the cost report |

**The gate (D27), in plan Step 1a.** Run by `tools/release-demo/release_measure.py`. The baseline is the commit before T1.2. Same traffic script, 30 minutes per run (*guess*), at least 3 repeats. Memory is sampled from the worker every 10 seconds, and the report gives spread as well as peak. Disk and index size at volume reuse `tools/detection-measure/scale.py`.

| Measure | Pass (*guesses, the founder sees the numbers*) |
|---|---|
| Rows in steady traffic | At most 1.5× `service_stats_1m`. **A floor only:** `otel-demo` runs one version per service, so this is near 1.0 by construction |
| Rows with one rollout an hour on every service | At most 3×. `release-demo` produces this |
| Worker peak memory | At most 15% more than the baseline |
| Bytes on disk | At most 2× `service_stats_1m` |
| Many services with many versions on every partition, at the arrival caps | Worker stays under `GOMEMLIMIT`, totals exact |
| Alert evaluation start delay, with N projects and one on a long first read | Within 10 seconds of baseline |

The report must also give absolute disk and memory per million requests, for sizing; how often `side_by_side` is reached at small-team traffic; and it must carry a demo label throughout.

**If it fails.** Tighten the caps once. If still failing, bring the numbers to the founder before anything else proceeds (spec, *Founder decisions needed*, the conditional one, whose recommendation splits cost checks from correctness checks). The recorded fallback is time comparison with F6's readings. **Its true cost:** UR2, FR15 to FR19's seen path, FR26, FR47, FR48, AC22, coverage `versions_only` and `deploys_and_versions`, the rollback with no CI, and the "no webhook needed" route, which buyers expect as table stakes (spec, *Table stakes*). All that remains is signed deploys, markers, time comparison, and errors by version.

### Capacity and AI cost

F28 makes no model call, so its AI cost is zero. No new container. `sizing.md` is updated if the gate shows meaningful growth in worker or ClickHouse memory.

## Failure handling

| Failure | What happens | Retry | What people see |
|---|---|---|---|
| Postgres down at webhook | 503. Nothing stored | Recipe retries, then warns, exit 0. Same id is safe | CI's output, as a warning |
| ClickHouse down at rollup | Worker retries, offsets do not move, re-reads later | At-least-once with dedup token | Delay |
| Only the version insert fails, transient | 30-second budget, then counted and stepped over, skip for 5 minutes | Next drain after skip | "Incomplete" label |
| Rollup refused permanently | Counted, stepped over, no F6 hold | None | "Incomplete" label |
| Worker restart | Re-derives same offsets, same block, dedup | Automatic | Nothing |
| Rebalance | `Disown` drops the share, new owner re-counts | Automatic | Nothing |
| Sync job fails or times out | Job retries. `synced_through` unmoved, so nothing is lost | Next run | Late seen releases |
| A row cannot fit Postgres | Skipped, counted, logged. Others proceed | None | Operator metric |
| Two jobs for one project | Lock refused, second ends | Next sweep | Nothing |
| Upsert race | `ON CONFLICT`, in sorted order, by the start rule | Deadlock retried once as a last resort | Nothing |
| ClickHouse down at read | Facts show from Postgres; comparison section shows `InlineFailure` | Retry button | Partial page |
| Marker read fails | The chart's failure line | Retry | Never an unmarked chart |
| Release-line read fails at notice | Line left out | None | Alert on time, without it |
| Settled minute unknown | Reads to now minus 3 minutes, "as of" | Automatic | Label |
| Sealed value cannot open | Treated as no match. Logged for the operator, not shown to the caller | Re-seal | Refusal, same words |
| F6 not active on project | No settled-minute issue, as the minute is install-wide | | Normal |
| Secret withdrawn mid-rollout | Later deliveries refused | Rotate in order | CI's warning |
| Eviction cap reached | Oldest inactive removed | Automatic | Sentence in Deploys section |

## Security, tenancy, and privacy

**Thinking as an attacker.**

| Attacker holds | Can do | Answer |
|---|---|---|
| Nothing | Hit the public route | Body cap first, signature before parse. At most 5 secrets to open per request. 60 a minute per address. All refusals alike |
| A captured signed request | Replay | **Fixed:** id is inside the signed item (D6). A changed header has no effect (AC7). A repeat answers with the stored row, and uses no allowance. Old ones are refused by time |
| The deploy secret | Forge a deploy before an incident, to shift blame or steer an AI; fill the release cap | Bounded time; arrival time shown; a webhook never moves a counted start; source shown; 1,000 an hour; withdraw and remove (D37); eviction of unknown services first (FR56); audit trail. **It can still plant a believable false marker on a version not seen, and this cannot be fully prevented,** so F3 labels the basis, and the page says "Reported by your CI" |
| A browser key | Name any version in a span; send an exception naming a server version; push made-up counts | **Excluded from the version rollup** by door (D12), and from a server release's errors by the new door column (D38). Reading shows nothing filters span kind by door, so a SERVER span from a page is **expected** to reach `service_stats_1m` today. **`time_before_after` and FR28's check read that table, so they inherit this until the separate F6 change lands,** which is a precondition of Step 4. See *status.md* |
| A secret ingest key | Make any "seen" version, up to 20 per service per drain | Caps, eviction, `telemetry` class for F3, and it already has full write to telemetry |
| Database read access | Read signing secrets | Sealed (D7) |
| Stolen admin session | Mint a secret | Audited, shown in the list with last use, and old values cannot be read |
| A hostile sender's text | Script or link injection | React escaping everywhere, and the hover box is a React portal, since it is driven from uPlot's cursor hook outside normal rendering. Its render function has its own test (AC43). No `dangerouslySetInnerHTML`. `url` is `https` only, no query or fragment, `rel="noopener noreferrer nofollow"`, and labelled |

**One name check at both doors.** `nexovue_platform` holds the name rule: C0 and C1 controls, bidirectional overrides, Unicode tag characters and `__other__`. The webhook refuses. The worker folds, against shared fixtures, since Go cannot import Python. Today's schema only strips whitespace and limits length (`deploys/schemas.py:20-22`).

**Masking at the webhook.** The telemetry path masks credentials (`stages.go:250-251`, `mask/rules.go:25-38`), and the masker exists only in the worker. So `nexovue_platform` gains a Python port of the default rules applied to `metadata` before storing, tested against the Go rules' fixtures. `url` holds no query or fragment, so a signature parameter cannot land there.

**`url` and SSRF:** nexovue never fetches it. It only renders it for a person.

**Audit.** `deploy.batch_recorded` per accepted request, with secret id, count and first deploy id, replacing `deploy.recorded` per deploy. The row today holds no detail (`audit/service.py:763-769`). `deploy.webhook_secret_created` and `_revoked` (exist). New: `deploy.removed`, with the secret and time range where one applies. `deploy.webhook_secret_viewed` is removed, since nothing can be viewed now. Per-request rows reduce normal volume. Single-item requests at the limit do not improve (arithmetic above), and that stays a risk to the log's readability.

**Permissions.** No new action. `MANAGE_CREDENTIALS` is already owners and admins (`auth/permissions.py:58`). Reads are for anyone who can read the project; a viewer may read. `metadata` needs `_EVERYONE_WHO_WORKS`, by a small role check in the route. Support view is read-only by method, so it cannot mint or remove.

**Privacy.** `version` and service names are not personal data in normal use. `metadata` could be anything, so it is short, masked, and shown only through `sender-details`, to members and above, and not to F3, F4 or MCP. Reports can name people only if a sender puts them there, and a test proves our recipes do not (AC49). A record holding names lasts only as long as the project's derived retention (D20), so it does not outlive the telemetry it names. It is stored in clear text, and the setup page says so.

**Rate limits.** `/api/v1/releases` joins `TELEMETRY_PATHS` (`ratelimit/policy.py:234`), or `test_ratelimit_coverage.py` fails. The comment at `policy.py:123-125`, "no CI system pushes sixty a minute", is corrected. It stays true per request now that batches exist.

## Observability and operations

- **Metrics** (no tenant label, per F6's C8): versions folded, refused rollup rows by the existing count, version inserts stepped over, sync job duration, rows skipped by the sync, new releases, evictions, unsigned webhook refusals, `releases` queue age. Tenants are named in logs only.
- **Logs:** a tenant over a version cap (once per drain, as `reportServiceCap` does at `pipeline.go:759`), sealing failures, caps hit.
- **For operators:** a stopped sync shows as late releases, with no customer harm to findings or alerts.
- **Setup docs:** `docs/product/setup/deploys.md` new, an entry in `setup/index.md`, `setup/api.md`'s list, `troubleshooting.md`, and corrections to `platforms/kubernetes.md`, `platforms/aws-ec2-and-bare-metal.md`, the language pages and onboarding.
- **Architecture docs:** `docs/architecture/database.md`, plus `docs/features/integrations-deploy-events/spec.md`, which will point here.

## Alternatives and tradeoffs

| Rejected | Why |
|---|---|
| **Compare by time only** (foundation) | Cheap and wrong during rolling deploys (section 5). It stays as the labelled fallback |
| **Version split from stored spans** | Skewed by the sampler (`sample/policy.go:39-46`), and an earlier design built that way measured wrong by an order of magnitude. Fine only for "which errors" and "which labels" |
| **Add `version` to `service_stats_1m`** | Would make detection's table wider and its reads sum more rows, and a cap's folding could affect F6. F6 is built and measured against its table. Costs a second table |
| **Add `version` to `endpoint_stats_1m`** | Multiplies endpoints × versions. Not proven wanted (D18) |
| **Statistical canary judge** (Kayenta's Mann-Whitney) | For gating a rollout, which we do not run, and needs per-instance samples |
| **Derived view for releases** | D2 |
| **One row with a moving start** | D15 |
| **A second table for first-seen, in ClickHouse** | Solves drift but not ids or reported-only rows, and adds a store for what Postgres does simply |
| **Worker writes releases to Postgres directly** | Puts a control-plane write in the hot path. A slow Postgres would hold back telemetry |
| **Hold F6's progress on a failed version rollup, or end the worker** | D13 |
| **A counter for environments in the worker** | D39 |
| **Timestamp in the signature, like Standard Webhooks** | Closes replay too, but every CI recipe must compute it. A signed id plus a signed time achieves the same with no second header. Our alert webhooks do use it, and native senders may later need it |
| **Native GitHub `deployment_status` now** | D5 |
| **Webhook counting a per-project limit before the signature** | Lets an outsider use up a victim's limit |
| **Kubernetes watcher** | New component, cluster permissions, one platform |
| **Reading F6's `detection_readings` for a normal line on the page** | Only exists for judged services, and F6's finding already gives that opinion. A second reading of "normal" invites a contradiction |
| **A verdict badge** | Would be a second detector (D17) |
| **A summary sent only when a reading is "higher"** | That is an alert from a second detector (D40) |

**What this costs, plainly.** One more table written on every drain; two jobs; more states on a page; a new column on two raw tables. A customer with neither version nor deploys still gets nothing, and that may be many customers: how many set `service.version` is not established, and our setup pages have not told them how.

## Proposed ADRs

Written here rather than in `docs/decisions/`. Numbers are assigned on approval.

### Proposed ADR: Version numbers are counted before sampling, in their own rollup

**Status:** Proposed. **Context:** Sampling is skewed, and rollups hold no version. Fair comparison needs both versions at the same moments. **Decision:** The worker writes `service_versions_1m` from the same inbound spans as `service_stats_1m`, with folding caps, and detection's table is untouched. A failing write to it never holds detection or ends the worker. **Consequences:** Exact comparison and rollout share with no webhook. One more table per drain, with costs measured at the plan's first step. A failed write labels comparisons and leaves a hole. Caps are per drain and first come, so a version's count can be short, and the label says so. **Rejected:** comparison by time alone, widening `service_stats_1m`, reading sampled spans.

### Proposed ADR: A release is a stored record with periods, fed by two sources, with measured beating told

**Status:** Proposed. **Context:** Facts about a change arrive from a signed sender and from telemetry. They can disagree, and rollups age out. Versions come back. **Decision:** `releases` holds one row per project, service and version, updated from both. `release_periods` holds one row per stretch in service, and a past period only ever gains an end. A start prefers what was counted. Reported and seen are shown as different. A release never opens or moves a finding (ADR-022 stands). **Consequences:** stable ids, history that does not change, coverage that can be stated, and a record outliving its numbers. Two jobs are needed. **Rejected:** a derived view; one row with a moving start.

### Proposed ADR: The deploy envelope signs its id and time, and secrets are shown once

**Status:** Proposed. **Context:** GitHub's scheme has no timestamp, and the id sat outside the signature, so a captured body could be stored again under a new id. Secrets were re-readable. **Decision:** `delivery_id` and `deployed_at` are inside the signed body, with bounds. Secrets are sealed under the API-only key and shown once. **Consequences:** Native GitHub delivery, if added later, must use GitHub's own header id on its own path, and is weaker on replay, which must be stated then. F22's webhook must do the same, with a compensating dedupe on the signed payload's event id. A lost secret means minting another. **Rejected:** a separate signed timestamp header; sealed and re-readable.

## Specification impact

- Spec follows this design. D1 no longer differs from the foundation, whose section 9 was changed to match (`phase-2-foundation.md:547`).
- `phase-2-foundation.md`, applied 2026-11-02: section 9's F28 row says "by version first", and `deploy_impact` becomes `release_comparison`; section 8's route becomes `/releases/{id}/comparison`; line 495's row says the test exists; section 12's reason at line 613 changes, keeping "seen is not a deploy"; line 527's GitHub sentence goes; section 3's rule U6 (lines 204-205) gains the mapping under *The fact F3 receives* (D54).
- `feature-catalog.md:104,371-372` should point here. They change at approval, not in this run.
- **F3's documents** must change: T1.4 calls F28's `release_before` and `releases_near` rather than adding them (D56); `deploys_in_window` is signed deploy events only, with the base rate from it; `releases_seen_in_window` is `telemetry_text`, its rule 5 needs it, and it applies D54's U6 mapping; add the ClickHouse read for `release_comparison` (spec line 427 still says `deploy_impact`) to its table; note the release lag and read `basis`; `PROJECT_BOUND_PACKAGE` may already be a tuple. Its citations of this file's line numbers have moved. F3 says the finding page says "deploy" while this design says "release": settle it when F3's section folds in this one.
- **F20's design and D14** must change from `deploys_in_window` to `markers_in_window`, still inside its context route, with cap 500 and F28's states. Its "deploy in 30 days" read should become `coverage_of`, since coverage needs no ClickHouse here and F28 has no 30-day flip. F20's note that this file should say "calls `deploys_in_window`" is answered by this line: that read would now lose seen releases.
- **F22's design** used the savepoint at `deploys/service.py:99-107` as its pattern, and F28 rewrites that block, so it now says it keeps its own. `signatures.py` must stay a plain verifier taking a list of secrets, since F22's `signature_matches` reuses it. F22 dropped its delivery table (a replay only asks for a re-read), so no dedupe is owed.
- **F4's design:** `CurrentReader` routes carry no sender text, and `sender-details` refuses its ticket. MCP gains `partial`.
- **F21:** a new line in `_compose_finding`, and a new frozen kind.
- `console-redesign/design.md`'s IA3 is overtaken (D25). `000035`'s comment goes stale. The database document gets the correction, and the migration stays as applied.
- **A required F6 change, before Step 4,** recorded in `status.md`: filter browser-door SERVER and CONSUMER spans out of `service_stats_1m`, and out of `service_versions_1m` at the same point. Its test must send such a span and assert it is in neither table, that AC15's sum holds, and that FR28's check reads complete (D55).

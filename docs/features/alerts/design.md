# Design: Alerts & notification connectors (F21)

## Status

**Approved** — by the founder, 2026-09-19. Two decisions here are expensive to reverse and have their own ADRs:
[ADR-012](../../decisions/ADR-012-the-scheduler-evaluates-alerts.md) (where alerts are evaluated) and
[ADR-013](../../decisions/ADR-013-notifications-are-an-outbox.md) (how a notification survives a crash). A
third amends [ADR-010](../../decisions/ADR-010-check-secrets.md) (channel secrets).

**Amended 2026-10-31** by anomaly detection (F6,
[ADR-022](../../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md)). A fifth
condition kind, `anomaly_finding`, routes findings, which are their own record, not incidents. The outbox's
subject is an incident, a finding or the project itself, the webhook body is version 2, and every new project
gets a second default rule. The sections below say where.

## Context and goal

The specification (`spec.md`, approved 2026-09-18) asks for four conditions, six channel kinds, one
incident per breach, noise controls, a visible delivery log, and zero-configuration alerting for a new
project. This document says where each part runs, what it stores, and what happens when something fails.

What already exists, read from the code rather than the documents:

| Need | Exists | Where |
|---|---|---|
| A confirmed check state change | yes, written in one transaction | `probes/state_store.py`, `_apply` |
| Periodic evaluation under a single leader | yes | `beat.py` `_sweep_entries`, `leader.py` |
| Outbound HTTP that refuses private addresses, DNS-rebinding safe | yes, scheduler only | `probes/addresses.py`, `ConnectGuard` (pycurl) |
| Sealed secrets opened in the worker | yes | `nexovue_platform.checks.sealing`, ADR-010 |
| Email | yes, API only, three fixed messages | `apps/api/.../notifications/` |
| Per-service error rate and p95 per minute | yes, ClickHouse | `endpoint_stats_1m` (request_count, error_count, latency_quantiles) |
| Which services sent telemetry recently | membership per day only | `usage_daily_services` (`first_seen_at`, no last-seen) |
| A stable identity for an error group | computed at read time | `errorgroups/queries.py` — no stored groups |
| ClickHouse access from the scheduler | **no** | the scheduler has no ClickHouse setting or client |

## User experience

**The Indie's path is zero steps.** They sign up, create a check, and are emailed when it goes down and
when it recovers. They first meet the Alerts screen from the link in that email.

**The Dev's path starts from the thing they care about.** On a check's page or a service's page, *Alert
me about this* opens the rule editor prefilled with that subject. The first time, the editor offers to
connect a channel inline — Slack, Teams, Discord, PagerDuty, webhook or email — with *send test* before
saving. Nobody is sent to a settings screen to come back later.

**When something fires**, the message links to the evidence: the failing run for a check (the F18 evidence
view), the error group, or the service over the incident's window. The Alerts screen lists what is open
now, above everything else.

**When the alerting itself is broken**, the Channels screen shows it before anyone asks: a failing channel
is red, its log names the reason, and the owners have been emailed.

## UI structure

**Alerts** — a main navigation entry under *Home*, beside Findings.

- **Open incidents**: subject, rule, severity, since when, which channels were told, and *flapping* or
  *muted* when true. Empty state: *Nothing is open. The last incident resolved <when>.*, or *No rules yet*
  with the path to create one.
- **Rules**: name, condition in words ("*Checkout API* is down"), channels, state (active, muted until,
  *not notifying anyone*, *covers nothing*). The default rule is labelled as the default.
- **Recent incidents**: the last 30 days, resolved and open, with duration.
- A banner when evaluation is more than 3 minutes behind (spec, *Error cases*).

**Rule editor** — one screen, sections in the order a person decides them: *what* (condition and its
subject), *how bad* (severity, thresholds, waits — with the defaults already in place), *who* (channels,
with an inline *add channel*), *noise* (re-notify interval, off by default). A threshold rule shows the
last hour of the chosen reading with the threshold drawn on it, so the number is chosen against real data.

**Channels** — under organization settings (`shared/settings/settings-nav.ts`, *This organization*):
list with health, kind and last delivery; detail with *send test*, quiet hours, mute, the secret shown as
*set* with *replace*, and the delivery log.

**On existing screens**: a check's page and a service's page gain a small *Alerts* strip — the rules that
cover it, any open incident, and *Alert me about this*.

**Components reused**: `EditableRows` (email recipients, quiet-hour windows), `Tabs`, `SelectField`,
`TextField`, `EmptyState`, `InlineFailure`, `SkeletonRows`, `UnsavedChangesPrompt` (rule editor and channel
forms), the synthetics sparkline for the threshold preview.

**States** (`docs/design/principles.md` rule 3), owned per screen:

| State | Alerts | Rule editor | Channels |
|---|---|---|---|
| Loading | skeleton rows per section | skeleton form | skeleton rows |
| Empty | nothing open / no rules yet | — | only *Organization owners* exists |
| Error | inline failure with retry, per section | save refused with field messages | inline failure |
| Partial | evaluation-behind banner | threshold preview unavailable → the form still works | a failing channel among healthy ones |
| Stale | "evaluated N min ago" when late | — | last delivery time |
| Forbidden | read-only for non-admins (writes are `MayAdminister`) | read-only | read-only |

## Proposed design

### Where evaluation runs — the scheduler ([ADR-012](../../decisions/ADR-012-the-scheduler-evaluates-alerts.md))

The scheduler already owns the two moments alerts start from — a check's state change and a periodic
sweep under a single leader — and the only outbound HTTP client that refuses private addresses safely.
It gains a **read-only ClickHouse client** for the two conditions that need telemetry, reading through
statements shared with the API from `packages/py-platform`, each bound to a tenant as the API's are.

| Condition | Evaluated | From |
|---|---|---|
| Check state | **inline**, in the transaction that records the state change | `check_states`, `check_state_changes` |
| Service went silent | sweep, every 60 s | `service_last_seen` (new, Postgres) |
| Service error rate, p95 | sweep, every 60 s | `endpoint_stats_1m` (ClickHouse) |
| New error group | sweep, every 60 s | spans/logs exception columns (ClickHouse) + `error_groups_seen` (new, Postgres) |

A sweep evaluates only projects that have a rule of that kind, so its cost grows with rules, not with
telemetry (NFR4).

### Check state — inline, in the same transaction

`state_store._apply` already writes `check_states` and `check_state_changes` in one transaction. After it
inserts the change, it calls `alerts.on_check_state_change(connection, change)`, which in the **same
connection**:

1. finds the project's enabled check rules that cover this check;
2. for each, opens, escalates or resolves the incident for `(rule, check)`;
3. writes the notification rows the channels need (ADR-013).

The state change and the alert commit together or not at all. A worker killed after the commit has already
recorded the notification; one killed before it has recorded neither, and the run is retried by the
existing `record_outcome` retry.

**Mapping states to incidents.** The rule selects which of *down*, *degraded*, *misconfigured* it cares
about. A transition *into* a selected state opens an incident if none is open; a transition *out of every*
selected state resolves it. A transition *between* selected states keeps the same incident and, if the
severity rises (degraded → down), sends an **escalated** notification — see *Specification impact*.

**Maintenance and pause** (spec BR2, BR3) need no code here: F18 records no state change during a window
or while paused. A check that went down inside a window records its first change on the first confirmed
run after it, which opens the incident then.

**Closing without recovering** (spec D8, added after implementation review, 2026-09-19). Deleting, disabling
or narrowing a rule, or deleting its check or project, closes the open incidents it no longer watches — nothing
would ever evaluate them again. The API closes them in the same transaction as the change and writes a
`closed` notification for each PagerDuty or webhook channel that was sent the opening (a *resolve* to
PagerDuty, `incident.closed` to a webhook); people are not told. A recovery during a rule's mute likewise
still reaches those machine channels as *resolved*.

### Service went silent — from the ingest meter

The Go usage meter (`apps/workers/internal/pipeline/usage`) flushes every 10 s, and each flush already
carries the exact set of services that sent anything since the last one — it writes them to
`usage_daily_services`. The same transaction now also upserts `service_last_seen (org_id, project_id,
service_name, last_seen_at)`, with `last_seen_at = greatest(existing, flush time)`.

The sweep then asks Postgres, not ClickHouse: services under a silence rule whose `last_seen_at` is older
than the rule's silence and newer than 24 h (spec FR5: *a service that sent some*). Every signal counts —
spans, logs and metrics — because the meter counts all three.

**A stalled meter is not a silent service** (decided at the plan gate, 2026-09-19, Q2). Silence is judged
against the clock, but the sweep first reads the meter's own progress (`usage_ingest_offsets.updated_at`,
newest across partitions): if the meter has not advanced for 5 minutes, the sweep opens nothing, logs
*"usage meter stalled"* at error level, and records the skip for the *evaluation behind* banner. Without this
guard, a meter that falls behind would report every service in every organization silent at once.

### Service thresholds — from the per-minute rollup

One statement per project with threshold rules, over `endpoint_stats_1m`:

- `sum(error_count) / sum(request_count)` and `quantilesMerge(0.5, 0.95, 0.99)(latency_quantiles)[2]`,
  grouped by service, restricted to server spans — the same definition the Services screen reads, taken
  from the same shared statement so the alert and the screen cannot disagree.
- The window ends **2 minutes ago**. The rollup is by span time and late spans still arrive; judging the
  last minute would fire on a half-counted minute and resolve when it filled in.
- A window with fewer requests than the rule's minimum produces *not enough traffic*, which neither fires
  nor resolves (spec FR6).

The per-rule, per-service evaluation state — `ok`, `pending` since, `firing`, `resolving` since — lives in
`alert_evaluations`. The fire and resolve waits are counted from those timestamps, so a restart of the
scheduler resumes the waits rather than restarting them.

### New error groups — a baseline, then a watermark

A group's identity is its fingerprint, computed in SQL today by the API (`errorgroups/queries.py`). That
expression moves to `packages/py-platform` so the API and the scheduler compute the identical fingerprint;
a contract test holds them to the same constant.

- **Baseline.** When a project's first new-error rule is created, every fingerprint seen in the retention
  window is written to `error_groups_seen` without notifying. Without the baseline, a rule on an existing
  project would announce every error it has ever had as new.
- **Each minute**, the sweep reads fingerprints with an occurrence received since the project's watermark,
  inserts them into `error_groups_seen` with `ON CONFLICT DO NOTHING RETURNING`, and treats only the
  inserted rows as new. The insert *is* the decision, so a sweep that runs twice cannot announce one group
  twice (spec AC8).
- A new-error incident is an **event**: opened and closed at once, notified once, never resolved.

### Incidents

`incidents` holds one row per breach: rule, subject, severity (the highest reached), opened and resolved
times, and whether it is flapping or was muted when it opened. A partial unique index — one row per
`(rule_id, subject_key)` where `resolved_at IS NULL` — makes a second open incident for the same breach
impossible at the database (spec BR1), whichever path tries.

**Flap guard** (spec FR11): when an incident opens, the incidents opened for the same rule and subject in
the last hour are counted. From the fifth, the incident is marked *flapping*, one *flapping* notification
is written, and open/resolve notifications for that subject stop until it has stayed resolved for 30
minutes.

### Channels and the organization-owners recipient

`notification_channels` belongs to the organization (spec D4). Kinds:

| Kind | Stored | Delivered as |
|---|---|---|
| `email` | addresses (plain) | plain-text email, subject prefixed with severity |
| `owners` | nothing — resolved at send time to the organization's owners' verified addresses | as `email` |
| `slack` | webhook URL (sealed) | incoming-webhook JSON, colour by severity |
| `teams` | Workflows webhook URL (sealed) | Adaptive Card |
| `discord` | webhook URL (sealed) | Discord webhook JSON, embed colour by severity |
| `pagerduty` | routing key (sealed) | Events API v2 `trigger` / `resolve`, `dedup_key` = `incident:<id>` or `finding:<id>`, shared by a linked pair (F6, ADR-022) |
| `webhook` | URL (plain), signing secret (sealed, generated by us, shown once) | JSON, Standard Webhooks headers; body version 2 since F6: `type` is `<subject kind>.<event>` (`incident.opened`, `finding.joined`), the subject under `data.subject` ([webhooks](../../product/setup/alerts/webhooks.md)) |

**`owners` is a channel kind** so the default rule has something to point at that stays right when the
owner changes their address, and so it can be muted and logged like any other. Every organization gets one
at signup; it cannot be deleted, only muted. See *Specification impact*.

### Rules and the default rule

`alert_rules` (org, project, name, condition kind and its configuration, severity per state or a single
severity, waits, re-notify interval, enabled, muted until and by whom, `is_default`) and `alert_rule_channels`.

The default rule (spec FR8, D3) is written by one function, `alerts.defaults.provision_project`, called from
both paths that create a project — `signup/service.py` (the first project) and `projects/service.py` —
inside their transactions. A migration backfills it for existing projects. Deleting it deletes the row; no
path recreates it (spec AC22).

*Since F6:* the condition kinds include `anomaly_finding`, a rule that routes detection's findings: services
(all, or a list) and a minimum severity. Only `CONFIGURE_DETECTION` (owners and admins) may create, change,
mute or delete one, which the alerts service checks. `provision_project` also adds a second default rule,
*Critical problems*: every critical finding to the owners channel (F6 FR29).

### Notifications — an outbox ([ADR-013](../../decisions/ADR-013-notifications-are-an-outbox.md))

Every decision to notify is a row in `notifications`, written in the transaction that made the decision:
incident id, channel id, kind (`opened`, `escalated`, `resolved`, `repeated`, `flapping`, `digest`,
`summary`, `test`), a sequence number, `deliver_after`, state and attempts. A unique key on `(incident_id,
channel_id, kind, sequence)` means no path can create a second *opened* for one incident on one channel
(spec NFR3).

*Since F6 (ADR-022, migration 0041):* a row's subject is an incident, a finding (`finding_id`), or, for
*detection stopped* and *running again*, the project itself. A check holds every row to exactly one, and each
subject has its own unique key. Findings add the kinds `joined` and `adopted`; the project's are `stopped` and
`running_again`. Delivery reads every subject through one `Subject` (`alerts/outbox.py`). PagerDuty's
`dedup_key` is `incident:<id>` or `finding:<id>`, recorded on the row (`notifications.dedup_key`), so a linked
finding and incident share one alert.

**Delivery**: after a commit that wrote notifications, a `deliver` task is dispatched at once (the fast
path); a sweep every 15 s claims anything due that the fast path missed (the safety net). Claiming is
`FOR UPDATE SKIP LOCKED` with a lease: a worker that dies mid-delivery leaves the row to be reclaimed when
its lease ends, so nothing is lost (spec NFR2).

**Shaping at delivery time**, where all due rows for one channel are visible together:

- **Grouping** (spec FR13): an *opened* for a check incident waits 30 s (`deliver_after`); every such row
  due for the same channel and project is sent as one message listing the checks.
- **Burst cap** (spec FR12): a channel that has delivered 10 messages in the last minute sends the rest of
  what is due as one summary.
- **Quiet hours** (spec FR15, BR4): a *warning* created inside a channel's quiet hours is written with
  `deliver_after` at their end; everything held is delivered as one digest. A *critical* is never delayed.
- **Mute** (spec FR14, BR5): a muted rule writes the incident and no notification rows; a muted channel's
  rows are written as `dropped` with the reason, so the log still shows them.

**Retries** (spec FR19): network error, timeout, 429 (honouring `Retry-After`) and 5xx retry with
exponential backoff and jitter for up to an hour; any other 4xx is final. Every attempt is a row in
`notification_attempts` — the delivery log, kept 30 days.

**A failing channel** (spec FR21): a channel with no successful delivery for an hour, and at least one
failure, is marked `failing` and the owners are emailed once, through email, never through the failing
channel. The next success marks it healthy.

### Email from the scheduler

The account-email port (`notifications/sender.py`, the Resend and console adapters, the templates) moves to
`packages/py-platform/.../email/` so the API and the scheduler send through one implementation, per
`docs/engineering/code-structure.md`. The API's behaviour is unchanged; the scheduler reads the same
`EMAIL_*` settings. `test_provider_ports.py` follows the port to its new module.

### Outbound HTTP

Slack, Teams, Discord, PagerDuty and webhook deliveries use the scheduler's guarded pycurl path — the same
`ConnectGuard` that refuses a private, loopback, link-local or metadata address at connect time, after DNS,
so a hostname that resolves somewhere private is refused however it was written (spec BR7). The API
additionally refuses a literal private address when the channel is saved, for a faster answer.

**Webhook signing** (spec FR4): Standard Webhooks — `webhook-id` (the notification id, stable across its
retries), `webhook-timestamp`, and `webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">` with
the channel's secret.

### Send test

`POST /channels/{id}/test` dispatches a delivery task to the scheduler over the broker ADR-011 already set
up for Run now, and stores the outcome in Redis under a request id, and the API waits up to 10 s for it — the pattern `GET
/check-runs/{id}` uses (ADR-011); there is no Celery result backend. It
has its own rate tier: it makes our infrastructure contact an address the customer chose
(`ratelimit/policy.py`, beside `RUN_NOW_TIER`).

## Data flow and interfaces

### API

| Method and path | Role | Purpose |
|---|---|---|
| `GET /alerts/incidents?project_id&state` | member | open and recent incidents |
| `GET /alerts/incidents/{id}` | member | one incident, its notifications and their attempts |
| `GET/POST /alerts/rules?project_id` | member / admin | list, create |
| `GET/PATCH/DELETE /alerts/rules/{id}` | member / admin | read, change, mute, delete |
| `GET /alerts/evaluation` | member | when each sweep last completed (the *behind* banner) |
| `GET/POST /channels` | member / admin | list, create |
| `GET/PATCH/DELETE /channels/{id}` | member / admin | read (secret as *set*), change, replace secret, quiet hours, mute, delete |
| `POST /channels/{id}/test` | admin | send test; own rate tier |
| `GET /channels/{id}/deliveries` | member | the delivery log |

Every write takes `MayAdminister` and writes an audit row (spec FR26); no response carries a secret.

### Scheduler

| Task | Queue | Trigger |
|---|---|---|
| `alerts.evaluate_silence` | `light` | beat, 60 s |
| `alerts.evaluate_thresholds` | `light` | beat, 60 s |
| `alerts.evaluate_new_errors` | `light` | beat, 60 s |
| `alerts.renotify` | `light` | beat, 60 s |
| `alerts.deliver` | `light.deliveries` (new) | after a commit that wrote notifications; beat, 15 s |
| `alerts.send_test` | `light.deliveries` | the API |
| `alerts.prune_attempts` | `light` | beat, hourly |

Deliveries have their own queue so a slow receiver cannot hold up evaluation (spec NFR5); each attempt has
a 10 s timeout. `light.deliveries` joins `task_queues` in `celery_app.py` and `task_routes`, and is consumed by
**its own worker service, `scheduler-deliveries`**; `scheduler-light` excludes it (`-X`), so a slow receiver can
never take a probe's slot (decided at the plan gate, 2026-09-19, Q1).

## Data and migration impact

**Postgres** (one Alembic revision, 0028):

| Table | Holds |
|---|---|
| `notification_channels` | org, kind, name, plain config, sealed secret, quiet hours, mute, health |
| `alert_rules` | org, project, condition, severities, waits, re-notify, enabled, mute, `is_default` |
| `alert_rule_channels` | rule ↔ channel |
| `alert_evaluations` | per rule and subject: phase and since, last value |
| `incidents` | org, project, rule, subject, severity, opened/resolved, flapping, muted |
| `notifications` | the outbox; since F6 its subject is an incident, a finding or the project |
| `notification_attempts` | the delivery log |
| `error_groups_seen` | org, project, fingerprint, first seen; watermark per project |
| `service_last_seen` | org, project, service, last seen — written by the Go meter |

Every table carries `org_id` (and `project_id` where it belongs to one), a UUID primary key, and joins
`organizations/tables.py` for tenant deletion. The revision backfills the `owners` channel for every
organization and the default rule for every project.

**ClickHouse**: no change. The scheduler reads `endpoint_stats_1m`, `spans` and `logs`.

**Go worker**: the meter's flush gains one upsert into `service_last_seen`, in its existing transaction.

**Deployment**: the scheduler gains `CLICKHOUSE_DSN` and the `EMAIL_*` settings in all three compose
files — which reverses one of the ten deletions made by `docs/changes/compose-dependencies/` the same day.
That deletion was right for the code as it stood; this design is what makes the scheduler read ClickHouse,
and `test_compose_dependencies.py` will require the scheduler to wait for ClickHouse once it names it.

## Performance and scale

- **Check state**: one indexed rule lookup per state change, inside a transaction that already exists.
- **Sweeps**: one ClickHouse statement per project with threshold rules and one per project with new-error
  rules, each a minute; one Postgres statement for all silence rules. At 1,000 projects with rules that is
  up to 2,000 small ClickHouse reads a minute, each pruned to one tenant and project by the tables' sort
  keys and to a few minutes of `received_at` partitions. Measured before shipping (*Observability*).
- **Delivery**: one row claim per notification; the lease and `SKIP LOCKED` let several workers deliver
  without coordination.
- **Storage**: attempts pruned at 30 days; incidents kept with the organization.

## Failure handling

| Failure | Behaviour |
|---|---|
| Worker dies after a state change commits | The notification row is committed with it; the delivery sweep sends it (NFR2) |
| Worker dies mid-delivery, before recording the send | The lease expires and the row is delivered again. **The one duplicate this design allows**: a crash in the moment between a receiver accepting a message and our recording it. PagerDuty collapses it by `dedup_key`; webhook receivers by `webhook-id`; Slack, Teams, Discord and email would show it twice |
| ClickHouse unreachable | The threshold and new-error sweeps skip that minute and record the skip; waits are not advanced by a minute nobody saw; the Alerts screen shows *evaluation behind* after 3 minutes |
| Postgres unreachable | Nothing is evaluated or delivered; the state change itself is retried by `record_outcome`'s retry |
| A receiver is down | Retried for an hour, logged every attempt, then *failing* and the owners emailed |
| A receiver is slow | 10 s timeout; its own queue; other organizations unaffected |
| The email provider is not configured | Email and owners channels cannot be created or delivered; the owners-alert for a failing channel is logged instead |
| Beat leader changes | The new leader rebuilds its schedule; evaluation state is in Postgres, so waits continue |

## Security, tenancy, and privacy

- **Channel secrets** are sealed with `SealedBox` under a new purpose, `notification-channel`, bound to the
  channel id, with the key `CHECK_SECRET_KEY` already shared by the API and the scheduler — see the ADR-010
  amendment. Write-only; replaced, never read back.
- **No private destinations**: the connect-time guard in the scheduler, plus a literal-address refusal in the
  API at save.
- **Messages carry no telemetry payloads** (spec BR9): subject names, the state, the reading and the threshold,
  the time, and a console link that requires signing in. Never a request or response body, a header, a stack
  trace or an attribute value.
- **Tenancy**: every new Postgres query binds `org_id` (`test_postgres_tenancy_structure.py`). The scheduler's
  ClickHouse statements carry `tenant_id = {tenant_id:String}` as a real predicate, enforced by a structural
  test mirroring the API's chokepoint test.
- **Logs**: channel URLs are logged through `reportable_url` with the path's secret part removed; routing keys
  and signing secrets are never logged.

## Observability and operations

- The scheduler logs each sweep's duration, projects evaluated and incidents opened, and each delivery's
  outcome with the channel kind — never the destination's secret.
- `GET /alerts/evaluation` exposes when each sweep last finished; the console warns after 3 minutes.
- **Before shipping**: time the threshold and new-error sweeps against the development data at 1, 10 and 100
  projects with rules, and record the numbers in `status.md`.

## Alternatives and tradeoffs

- **Evaluate in the API** — rejected. The API is request-driven and runs several replicas with no leader; a
  periodic evaluator there needs its own leader election, and the API has no guarded outbound client.
- **A separate alerting service** — rejected for now. It would duplicate the scheduler's leader, queue and
  outbound guard to run four sweeps. The alerts module is shaped so it can move out if it grows (ADR-012).
- **Silence from ClickHouse** (`max(received_at)` per service across three tables) — rejected: three scans a
  minute per project for a fact the meter already knows every 10 seconds.
- **Check alerts from a sweep over `check_state_changes`** — rejected: it adds up to a minute of latency and a
  cursor to maintain, where the inline path is in the very transaction that decides the state.
- **Deliver inside the evaluating transaction** — rejected: a slow Slack would hold a database transaction
  and a lock on the check's state, and a failed send could roll back a recorded state change.
- **Store owners' addresses on the default rule** — rejected for the `owners` kind, which stays correct when
  an owner changes their address or, after F26, when a second owner is added.
- **A per-deployment Slack app** — rejected for the first version (spec D2).

## Specification impact

Two additions, found while designing; both are consequences of approved requirements rather than new scope.
**Applied to `spec.md` on approval, 2026-09-19:**

1. **Escalation within an incident.** A rule may select *degraded* (warning) and *down* (critical) for one check.
   A check going degraded and then down stays one incident (BR1), but must not stay a warning: the spec as
   approved has no notification for a rising severity. Added: an *escalated* notification, sent once when an
   open incident's severity rises; PagerDuty receives a new `trigger` with the same `dedup_key`, which updates
   the alert's severity. FR10 amended, AC27 added.
2. **The organization's owners are a channel.** FR8's default rule emails "the organization's owners"; the spec
   does not say how that recipient is stored. Designed as a channel kind, `owners`, that cannot be deleted,
   only muted. FR1 and FR2 amended.

**F18 design drift, corrected here.** `synthetic-checks/design.md` says transitions to `paused` and
`maintenance` write `check_state_changes` rows. The code (`probes/state_store.py`) records only `up`, `degraded`,
`down` and `misconfigured`; a paused check is not run and a maintenance run changes no state. The code is right —
an alert for "paused" would be noise the customer caused — and F21 depends on exactly that behaviour, so F18's
design is corrected to match.

## ADRs

- [ADR-012 — The scheduler evaluates alerts, reading ClickHouse through shared, tenant-bound statements](../../decisions/ADR-012-the-scheduler-evaluates-alerts.md)
- [ADR-013 — Notifications are an outbox, written in the transaction that decided them](../../decisions/ADR-013-notifications-are-an-outbox.md)
- [ADR-010, amended — channel secrets are sealed with the check-secret key, under their own purpose](../../decisions/ADR-010-check-secrets.md)

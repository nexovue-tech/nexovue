# Design: Anomaly detection (F6)

## Status

**Approved** by the founder, 2026-10-31, as revision 2, against [`spec.md`](spec.md) as amended through
D21. Its *Specification impact* is applied: this feature's FR28, FR29 and AC19, F21's BR10, F1's AC1.8,
and `phase-2-plan.md`'s Stage 3.

Revision 1, of 2026-10-30, was reviewed by five independent agents: statistics, architecture, operations,
security and tenancy, and product and QA. They found it not ready. Every finding they made about the code
was checked against the source before this revision. Each section below says which finding it answers,
under **Review**.

With it, the founder accepted [ADR-022](../../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md)
and [ADR-023](../../decisions/ADR-023-the-scheduler-writes-detection-state-to-clickhouse-through-its-own-account.md).
Both are accepted with this design.

## Revision 2.1, 2026-11-01

Six persona reviews read the spec, this design and the plan: a solo developer, an on-call lead, the
implementing engineer, security, QA and a startup advisor. The founder's decisions D22 to D32, and eight
defects the reviews found, change this design as follows. Where a section below disagrees with this list, this
list wins. The sections are corrected in place too.

**Defects fixed**

1. **Nobody outside nexovue can switch detection off** (security B1).
   - *Recording refusals.* The receiver records a refusal minute only for a broker-level failure while the
     request's context is still live. It never records a cancelled request or a per-record error, and it
     needs at least 5 such refusals in the minute.
   - *The install-wide guard.* It counts **organizations**, not projects, and needs at least 5 of them. A
     pipeline signal must agree with it: the receiver's accepted count falling in the same minutes.
   - *Limits.* Each hold lasts at most 30 minutes. After that, the minute is judged, a `detection_events` row
     is written and the operator is alerted. A refused derived insert holds every partition in that settlement,
     because one insert covers them all. `stepOverRefusal` reports the refusal to the caller, not just a log
     line. That hold lasts at most 15 minutes, the longest a missing minute can sit inside a window.
2. **"Detection not running" is measured against the job, not the clock** (QA B1). A project is *not running*
   when its last completed project job is more than 3 minutes old. Freshness is never measured from the
   `last_judged_minute`, which is always at least 3 minutes behind by design.
3. **A new signal or endpoint joining an open finding sends one message** (on-call B1, D29). A finding linked to
   an incident that already went to the same PagerDuty channel adds a note to that incident through the Events
   API, never a second trigger.
4. **A stop is judged at any expected traffic, from day 3** (solo developer B1, D30). A drop to zero for the
   whole sustain window opens a critical finding, whatever the floor.
5. **Readings are keyed by tenant** (security M1). `detection_readings` is `ORDER BY (tenant_id, project_id,
   service_name, minute)`. A test writes the same service name in two tenants, runs `OPTIMIZE … FINAL`, and
   keeps both rows.
6. **A member's false alarm cannot widen the normal** (security M3, D28). It closes and counts the finding.
   Only an admin's mark returns its minutes to history. False-alarm minutes never enter the burst profile
   (D26).
7. **The anomaly-rule check covers every route** (security M4): a change to or from the kind, enabled, channels,
   mute and delete. Each has its own refusal test.
8. **Smaller security fixes:**
   - **The writer account** has its own profile with read-only settings, a quota on rows and bytes written,
     and the internal network only. Its password uses `${…:?}` in the deploy compose, and an upgrade adds only
     the keys that are missing (security M2, m5).
   - **Caps per organization.** One organization has at most 2,000 service names in `service_stats_1m`, and
     past that cap the rest are not counted, with a *too many services* state. Detection jobs share the queue
     fairly between organizations, and at most 2 run at once (security M5).
   - **Endpoint names in titles** come only from endpoints already in the normal pool. A read token's view of
     a finding leaves out `acknowledged_by` and `feedback_reason`. Every read route checks the token's scope
     (security m1, m2).
   - **Install-wide tables.** `intake_refusals` and `detection_events` carry no `org_id`, each with a reasoned
     exemption in the new scheduler structure test (security m3).
   - **Findings keep their telemetry text** only as long as the derived retention. After that they are purged
     (security m4).
   - **Small hardening** (security m6):
     - the per-call override can only lower the limits;
     - the project job reads `org_id` from the `projects` row;
     - the progress table's exemption applies to `detection.settle` only.

**Founder decisions**

- **D22:** a critical finding, or a rise in failures, is never adopted. Adoption emails the owners directly,
  outside any rule.
- **D23:** signup records the browser's zone on the user.
- **D24:** a 15-minute *detection stopped* email goes to the owners channel, then one when detection runs
  again. Both are kinds in the existing outbox, with a project subject.
- **D25:** members can snooze a service for up to 4 hours (`POST /services/{name}/snooze`). Snoozed minutes
  stay out of the normal.
- **D26:** burst tolerance never applies to a window at or above critical.
- **D27:** an endpoint at the critical share of its own requests makes its finding critical.
- **D31:** health-check exclusion and per-signal switches are deferred.
- **D32 trims version 1:**
  - **No new container.** `scheduler-light` also takes the `detection` queue, at most 2 jobs at once of its 8
    slots. The writer's DSN is given to that container. That costs the least-privilege separation the
    security review preferred (M2); the gain is 0.4 to 0.8 GB of memory on the 16 GB install. It is revisited
    if the measurement in Plan Step 8 shows probes starved.
  - **Deferred:** the normal bands on the service charts, so `TrendChart` is unchanged; per-service
    sensitivity; and the services-list column.
  - **The cost target:** 200 projects and 2,000 services (NFR2, AC29).

**Other corrections**

- **Closing.** A signal is clear when, for *recovery*, its window is either normal or no longer passes the
  current sensitivity's minimum change (AC30).
- **Closing messages** go to the channels told at opening **or at escalation** (AC22).
- **`closed_reason`** also has *could no longer be judged* (D20).
- **Burst height and length** are the maximum over 28 days, as the simulation chose, capped below critical
  (D26). They are not "a high quantile".

## As built

Recorded as each plan step lands. It says where the built code differs from the text above.

- **Step 2a, 2026-11-01:**
  - **Three migrations, not one:** 000035 is `service_stats_1m`, 000036 is `detection_readings` and 000037 is
    `span_derived_progress`. The convention is one statement per file.
  - **The latency buckets** are `AggregateFunction(sumForEach, Array(UInt32))`, filled with
    `arrayReduce('sumForEachState', …)`. ClickHouse 26.3 refuses the simpler forms.
  - **`detection_readings`** keeps a baseline for each signal, and its per-endpoint failures as a `Map`.
  - **Progress does not go through `WriteGuard` (C1).** It is written only for the settlement's own partitions.
    It never moves backwards, and a revoked partition's hold is forgotten.
  - **What KeeperMap can't stop.** It has no compare-and-set, so a stalled worker that has lost a partition
    can still write a stale mark. That is accepted, because the scheduler compares every partition with
    Kafka's high watermark.
  - **A refused derived insert holds that settlement's partitions for at most 15 minutes,** counted from the
    refusal (`MaxProgressHold`). Any of the four settlement inserts counts.
  - **The 2,000-service cap is applied per drain on one worker.** With several workers, or a minute reopened
    by late spans, an organization can pass it for that minute.
- **Step 2b, 2026-11-01:**
  - **A partition that has never held a record is caught up,** though it has no progress row. Otherwise a
    quiet install with one unused partition would never settle a minute. A partition holding records with no
    row is still behind.
  - **Progress is read before Kafka,** and "now" in the same statement as the progress. A record landing in
    between is then seen as unread, never as caught up.
  - **The writer's quota counts inserts and bytes, not rows.** ClickHouse 26.3 has no written-rows quota
    (`system.quota_limits`). It allows 60,000 inserts and 1 GiB written an hour, an estimate until T8.2.
  - **The writer's networks** are the private ranges and loopback, which cover the compose network and
    Docker's host-side gateway.
  - **`add_missing_secrets` adds only ClickHouse account passwords.** They are read from the environment at
    each start, so one added later works. Any other secret is fixed into a store, a key or the Kafka log at
    install, and a fresh value would lock that data away, so a missing one is left for Compose's `${…:?}`
    to name. It runs on upgrade, and on an install or quickstart that reuses existing settings.

- **Step 2c, 2026-11-01:**
  - **The queue (C23 corrected).** `detection` joins the worker's default queues (`__main__.py`), so
    `scheduler-light` takes it with no `--queues` flag. The plan's `--queues light,detection` would have
    dropped the per-region probe queues (`light.<region>`).
  - **The cap of 2 detection jobs** is set in the worker (`jobs/worker.py`, `QUEUE_CAPS`), not by a Postgres
    semaphore. It applies per worker process.
  - **Fair order:** the coordinator hands each project the settled minute. It orders projects one per
    organization in turn.
  - **Freshness** is `detection_project_runs.last_completed_at`. `stopped_reported_at` sends D24's notices
    once each.
  - **`findings.signals`** is a list, leading signal first, as defined by `FindingSignal` in
    `nexovue_platform.detection`.
  - **`detection_baselines`** is keyed by the absolute minute.
  - **Upsert keys** begin with `org_id`.
  - **The beta-binomial tail** uses an exact sum with `math.lgamma`. scipy's deep tail was measured wrong
    (6.9e-13, where the exact value is 6.06e-32). At the cut-off it never changes a verdict.
  - **The shipped method matches the simulation** on all 624 frozen windows
    (`tests/fixtures/detection_parity.json`).
  - **`/findings` is on the API's telemetry rate tier,** because its trend reads ClickHouse.
  - **AC8 versus the 80% rule:** a finding can open after 4 of 5 slow minutes at normal sensitivity, as the
    simulation assumed, where AC8 said 5 of 5. The founder chose 4 of 5 (2026-11-01). AC8 and FR12 now
    say so, and the tests assert it.

- **Step 3, 2026-11-01:**
  - **Traffic's spread** is the level pool's MAD on the square-root scale, with Poisson's floor of 1. The
    dispersion pool is not used for traffic.
  - **Endpoints are judged for *failing more* only** (FR3), keyed by `span_name`. *Slower* names no
    endpoint.
  - **Endpoints get D18's switch too.** Readings keep no per-endpoint normal, so the project job records each
    endpoint's excursions in the service's state as it judges them, and forgets them after 28 days. History
    judged before this was built carries none, and learning days give no heights.
  - **Kept-out minutes** (false alarms, snoozes, outages) are stored as ranges in
    `detection_state.signals["excluded"]`. The burst profile is cut at them, so they never widen tolerance.
  - **The project finding (FR15)** counts each service's clear traffic in judged minutes in a row
    (`clear_minutes`). It closes as recovered once fewer than half its services are still unusual for the
    recovery time. It closes as *service gone* once none of its services has sent anything for 24 hours.
  - **A stop's limit:** an empty window is told apart from chance only from about 6.5 expected requests in
    the window (spec, FR32, *Limit*). At high sensitivity, with its 3-minute window, that is about 2.2 a
    minute.
  - **"Data delayed" needs no new column.** The API derives it when the judged minute is more than 6
    minutes behind; up to 3 minutes is ordinary settling.
  - **Times name the project's zone.** `shared/format/zoned.ts` prints the abbreviation, or the zone's full
    name where the abbreviation is ambiguous, as with IST.
  - **A job that has stopped** reads "Detection has not run since 14:02" when its last run is known, and
    "Detection is not running" when it is not.
  - **The parity fixture** now holds failing, traffic and endpoint-burst windows, 3.3 MB. Turning endpoint
    tolerance off fails 11 of its windows.
- **Step 4, 2026-11-01:**
  - **The outbox's subject** is an incident, a finding, or the project itself, for *detection stopped*. The
    in-order rule, that *resolved* never overtakes *opened*, holds for each kind.
  - **PagerDuty's `dedup_key`** is `incident:<id>` or `finding:<id>`.
  - **Webhook version 2:** `type` is `<kind>.<event>`, and the subject sits under `data.subject`. *Send
    test* sends `test.opened`, with subject kind `test`; before, it posed as an incident.
  - **Migration 0042, `findings.flapping`:** the flap guard must remember across runs which finding is
    flapping. A project finding has no service state to hold that, and the outbox has no row when no rule
    has a channel. F21 keeps the same fact on the incident.
  - **Migration 0043** widens `notifications.sequence` to `integer`. Each stop takes the next number, and
    `smallint` would run out after 32,767 stops.
  - **The incident side of `finding_incidents`** is written in `alerts/incidents.py`, not `sweeps.py`,
    because both sweeps open incidents through `incidents.fire`. The project job also links each open
    finding to its service's open incidents on every run, which covers either order and a race.
  - **Closing goes to every channel told** at opening, escalation or joining, even if the rule changed
    since. A service muted or snoozed mid-finding still sends its closing message to PagerDuty and
    webhooks, so no page stays open.
  - **Delivery reads the rule's name** at send time. A notification row doesn't record its rule, so a
    rule deleted since sends no "Rule:" line.
  - **The rule editor's *anomaly finding* kind** is built here, not in T5.6, because every new project
    carries *Critical problems* and the console must show it. Members see it read-only. Findings are never
    repeated, so the editor offers no repeat.
  - **The founder's answers:** *detection stopped* is critical (D33), a project whose detection never ran
    is reported (D34), D29 works in both orders (D35), and a muted finding's adoption sends nothing (D36).
  - **When detection is expected (D34):** migration 0044 adds `detection_project_runs.expected_since`.
    The stop sweep sets it the first time it sees a project with traffic in the last day or an open finding,
    and clears it after a quiet day. A stop counts from the later of the last completed run and
    `expected_since`. A healthy new project gets its first job in that same sweep, so it is never reported,
    and a project returning after a quiet day is not reported before it has had its chance. The status API
    uses the same clock, so its *not running* and the email agree.
  - **One page per problem (D35):** each PagerDuty notification records the `dedup_key` it was sent under
    (0044). The key is fixed when a finding or incident first reaches that channel, and a linked finding and
    incident share the alert of whichever was sent first. Only the last of the linked records to close sends
    *resolve*. An earlier close sends nothing, and the delivery log records why. PagerDuty's Events API v2
    drops an acknowledge or resolve on a resolved alert, and a later trigger would page again, so the alert
    must stay open until the problem has ended.
- **Step 5, 2026-11-01:**
  - **Migration 0046, `findings.services`:** a closed project finding must still name its services.
  - **`MayConfigureDetection`** gates every detection setting, mute and new normal. A change to a finding
    that moved meanwhile is a `409`, and each change writes an audit row with its reason.
  - **Closing from the API** writes one *resolved* message, through a statement shared with the scheduler
    (`nexovue_platform.detection`). A new normal, an admin's false alarm or a settings change queues an
    immediate rebuild of the normal. The baseline job's request, with its 330-second limit, is defined once
    in the platform.
  - **A false alarm** holds its signals until they read normal for 10 minutes. A member's hold keeps its
    minutes out of the normal too (D28).
  - **Shifts** are applied per past day in the hourly baseline job, keeping the daily shape and never
    shifting a day twice. A normal built before a shift is never judged by.
  - **A new normal** needs 15 readings with a value, three windows at normal sensitivity, and a ratio for
    every signal of the finding. Moving only some would reopen it at once. It starts from the minute the
    finding started. Endpoint normals don't move, and a project finding has none, so its new normal is a
    `422`.
  - **Adoption** emails the owners and sends *resolved* to every other channel told, so a PagerDuty alert
    closes. *The same signals for 7 days* counts from the last signal or endpoint to join.
  - **A false alarm on a recovered finding** changes its closed reason, keeps its close time and sends
    nothing. Acknowledging twice is a `409`.
  - **Snooze and mute** need a service detection has seen, or the answer is a `404`. A snooze is 30 minutes
    to 4 hours, and a mute 1 hour to 30 days. The audit target is the project, naming the service.
  - **Limits:** a false-alarm reason is at most 1,000 characters, and the finding page reads at most 24
    hours of readings.
  - **Signup** sends the browser's zone. A bad one falls back to UTC rather than refusing the signup.
  - **The web:** `DetectionBadge` lives in `shared/detection/`, because two features use it and a feature
    may not import another. The finding page draws the reading, the normal and the band's edge as lines.
    `TrendChart` gains shaded spans and series with gaps, and a fix for a redraw that never fired, which
    left late deploy markers undrawn.
- **Step 6, 2026-11-01:**
  - **The receiver** counts a refusal under `broker_unavailable` only when the broker failed while the
    sender waited. `internal/intake/refusals.go` writes a finished minute to `intake_refusals` once it has
    at least 5, and drops a minute it can't write after 30 minutes.
  - **Holds:** a refused minute, or one the guard covers, is judged for no project. A hold ends once the
    pipeline has delivered past it, so retries are counted, and lasts at most 30 minutes, counted from its
    first minute; held minutes up to 3 minutes apart are one hold. Reaching the limit writes a
    `detection_events` row and logs an ERROR. The logic is in `detection/holds.py`.
  - **The pipeline signal** is the span topic's record count per minute, read from Kafka's record
    timestamps. A minute has fallen at half the reference rate or below, the reference being the 10 minutes
    before the 30-minute window. Under 1 record a minute, nothing can fall. If Kafka can't be read, the guard
    can't engage.
  - **The guard** needs 5 organizations, not projects, losing traffic in a fall, counting a drop that began
    up to 5 minutes before it. Migration 0047 holds each project's vote, `traffic_lost_since`. A drop that
    began during a fall waits FR15's 3 minutes for the votes, so traffic findings can be up to 3 minutes
    slower then. One `intake_guard` event is written per guard, its span is enforced for 30 minutes, and
    its minutes stay out of the normal.
  - **With fewer than 5 organizations,** on most self-hosted installs, the guard can't engage. A project
    finding then names nexovue's intake as a possible cause, as item 3 above says.
  - **Pruning** is in the expiry sweep (`detection/retention.py`). A closed finding is purged at its
    plan's derived retention, and an open one never is. `intake_refusals`, `detection_events` and
    `baseline_shifts` are kept 30 days, as far back as the baseline job reads. Migration 0048 indexes
    closed findings by when they closed, built concurrently.
  - **Alerts:** `NexovueReceiverKafkaUnavailable` now pages on `broker_unavailable` only, so a sender
    cancelling no longer pages. A new `NexovueReceiverDependencyUnavailable` pages when over half the exports
    are refused as `unavailable` for 5 minutes, the control plane being down. `NexovueDetectionHeldByIntake`
    pages on a guard or a hold reaching its limit.
- **Step 7, 2026-11-01:**
  - **The sweep records itself** in a one-row table, `detection_sweep` (migration 0049): when it ran, and
    the newest minute it handed out. The design's "the coordinator records its own finish" had not been
    built. A worker's metric would reset on restart, and processes that never ran the sweep would read 0.
  - **Three install-wide gauges**, published by beat's leader with no project label: the settled minute's
    age (+Inf with no record), projects behind, and the oldest lag. "Behind" is the API's *not running*
    rule, no completed run for 3 minutes, now one constant in `nexovue_platform.detection`.
  - **Two paging alerts:** `NexovueDetectionStalled`, the settled minute over 10 minutes old, which fires
    when the sweep itself stops; and `NexovueProjectDetectionFailing`, any project behind for 2 minutes.
  - **Two Step 6 defects, fixed:**
    - *Held by intake* could never fire: the sweep runs in a worker's child process, so its counter never
      reached the scraped `/metrics`. It is now a leader gauge, `detection_events_recent{kind}`, read from
      `detection_events`.
    - A failing project job never named its project. Each failure now logs `project_id` (C8).
  - **`docs/operations/detection.md`** is the runbook, walked on the dev stack (`status.md`).

## Words used here

- **Pool:** the past readings that a minute is compared with, such as the same weekday at about the same
  time over the last 4 weeks.
- **Median and MAD:** the middle value of the pool, and the typical distance from it (median absolute
  deviation). Neither moves much if a few past values are odd.
- **Tail probability:** how likely a reading this extreme would be if nothing were wrong. "Below 3 in
  100,000" means the reading is very unlikely to be noise.
- **Beta-binomial:** a model of failures out of requests that allows the failure rate itself to wander, as
  real rates do.
- **Leave-one-out:** the window must still be unusual with its single worst minute removed, so one minute
  can never open a finding.
- **Settled minute:** a minute every part of the pipeline has finished writing.
- **KeeperMap:** a ClickHouse table kept in its coordination service, which the workers already use for
  progress records that must survive a restart.
- **Outbox:** the `notifications` table. A decision to notify writes a row there in the same transaction
  as the decision (ADR-013).

## Context and goal

The spec says what detection must do. This design says:

- where it runs, and what it stores;
- the test that decides "unusual";
- how a finding reaches people;
- how the product knows a minute is complete before judging it.

FR9 and D17 require the test to stay within the false-alarm budget across a range of failure-burst rates.
*Simulation* is that evidence.

What exists, verified in code:

- **The per-minute rollup.** `endpoint_stats_1m` holds exact counts, taken before sampling. Its latency is
  a sample of at most 8,192 values per endpoint per minute (`latencysample/reservoir.go:26`).
- **The minute sweeps.** These run in the scheduler with 50 s soft and 58 s hard limits. Beat's leadership
  is a Postgres advisory lock (ADR-012).
- **F21's alerting:** rules, channels, the outbox, the burst cap and quiet hours. The outbox is bound to
  incidents: delivery joins `incidents` (`outbox.py:162-170`), and the PagerDuty `dedup_key` and the
  message link come from `incident_id` (`delivery.py:84`, `messages.py:115`).
- **The workers' per-partition progress records**, in KeeperMap (`ingeststate/`), written only after
  ClickHouse acknowledges, behind `WriteGuard`.
- **The silence sweep's guard.** It compares stored progress with Kafka's high watermarks, so a partition
  with nothing unread counts as caught up (`alerts/silence.py`, `alerts/sweeps.py:44`).
- **The scheduler's ClickHouse account** can only read: `nexovue_reader`, `readonly = 2`, `GRANT SELECT`
  (`deploy/clickhouse/users.d/reader.xml:20,46`).

## User experience

- **A new project (P2 Indie, no setup).** The feed reads "Learning your services' normal. Judging starts on
  3 Nov" (FR25, AC42). It never says "all systems normal" while it is learning. On the service screen,
  each service carries a *Learning until 3 Nov* badge.
- **A problem appears.** A card appears: "Checkout is 4× slower than normal since 15:04 IST". Its times
  are in the project's time zone, which is named. A critical card also emails the owners through the
  second default rule.
- **The card opens the finding's page** (D12). It shows:
  - each signal's readings drawn against its normal band;
  - the normal, current and worst values;
  - the comparison sentence ("Tuesdays around 15:00, last 3 weeks");
  - the evidence endpoint, and any linked threshold or *went silent* incident.

  Its actions are *Acknowledge*, *False alarm*, *This is the new normal* and *Mute this service*.
  - A viewer sees no actions.
  - A member sees *Acknowledge* and *False alarm*.
  - An admin sees all four.
- **On a service's screen:**
  - ~~the charts draw the normal band~~ *deferred to after version 1 (D32)*;
  - findings appear as markers on the timeline;
  - a badge gives the detection state;
  - mute (admins) and snooze (members) sit beside the badge. Per-service sensitivity is deferred (D32).
- ~~**The services list** gains a *Detection* column~~ *deferred to after version 1 (D32)*. Each service's state
  is on its own screen (UR3).
- **An admin's settings** are in *Settings → Projects*, in a new *Detection* section of
  `ProjectSettings.tsx`: sensitivity, the time zone, and the false-alarm counts.
- **All new screens** work at phone width (F1 AC1.10).

## UI structure

- **The findings feed** (`features/findings/`, route `/findings`) reads `GET /findings`.
  - `FindingState` becomes `open`, `acknowledged` or `closed`. A closed card names its reason:
    *recovered*, *false alarm*, *new normal*, *adopted* or *service gone*. It never just says "resolved".
  - Muted findings stay out of the list. A "Muted (2)" link under the list opens them. The built feed has
    no filters, so none is invented here.
  - The headline (`headline.ts`) reads `GET /projects/{id}/detection/status` and says one of these:
    - all learning;
    - some still learning;
    - data delayed;
    - detection not running;
    - all judged services normal.

    If that read fails, it shows the error state, never the healthy one.
  - The card's *probable cause* stays empty until Stage 2.
- **The finding page**, `/findings/$findingId`, is new. Its states:
  - *loading*, *not found* and *forbidden*, from the shared components;
  - *partial*: the readings have expired after 35 days, so the stored values show without the chart, with
    a note saying so (AC40);
  - *closed*: only *False alarm* remains, and only if the finding closed as recovered;
  - *muted*;
  - *project finding*: it has no service, and lists the services affected;
  - *dangerous action*: *This is the new normal* names its effect in real numbers ("Checkout's normal
    becomes about 330 ms") and asks for confirmation;
  - *action failed*: if the finding changed meanwhile (`409`), it says so and reloads;
  - *success*: "Closed as false alarm. Resolved message sent to #ops".
- **The service screen:**
  - ~~**The band**~~ *deferred to after version 1 (D32). The finding page draws its own band from its readings.*
  - **States:**
    - *Learning*, with a date for each signal;
    - *Too little traffic* for one signal;
    - *Data delayed*;
    - *Detection not running*;
    - *No detection yet*: a service seen for the first time, with no detection state;
    - *Detection unavailable*: if the detection read fails, the badge shows an error, never *Judged*.
- **Settings, *Detection* section:**
  - shown with every control disabled to anyone without the detection permission; editable by admins;
  - validation, unsaved-changes and success states;
  - a time zone change warns that the normal bands move to the new local clock.
- **The rule editor** (`RuleEditor.tsx`) gains the *anomaly finding* condition: services (all, or a list)
  and the minimum severity. It is admin only (D11).

## Proposed design

### Where it runs (ADR-012) — *Review: operations 5, 6, 8; security 6; architecture 6*

*Revision 2.1:* there is no new container. `scheduler-light` takes the `detection` queue, at most 2 jobs at once
(D32).

The design splits the work into three kinds of job, each on a `detection` queue of its own so check probes
are never starved:

- **The coordinator.** `sweep:detection` runs each minute. It does no judging. It reads which minute is
  settled (next section), then queues one **project job** for each project that has had traffic in the
  last 24 hours or has an open finding. It records its own finish. A skip is recorded separately from a
  finish, because `results.py` sets `finished_at` even on a skip.
- **A project job** judges one project. It has its own time budget (20 s) and short query limits (5 s,
  256 MiB).
  - It takes a per-project transaction advisory lock.
  - It moves each service's `last_judged_minute` with a compare-and-set, so two overlapping jobs can never
    judge the same minute twice.
  - It judges at most 2 minutes of catch-up per run. If a project is more than 10 minutes behind, it
    restarts at the latest settled minute and judges nothing older (spec, *Backfill*).
- **The baseline job** runs hourly for each project. It computes the next hour's normal for every service
  and signal (*The normal*), and stores them. The project job then reads one small row per service, not
  four weeks of history. A finding opening or closing, a false alarm or a new normal queues an immediate
  recompute for that service.

Projects never wait on each other, and a large project can only overrun its own budget. Each service's
state is fresh when its `last_judged_minute` is recent; it no longer depends on when a whole pass ended.

### When a minute is settled (BR5) — *Review: architecture 1, 2; operations 1, 2, 3*

A minute M is settled when all three of these hold:

1. **Every partition of the trace topic has delivered past M plus the export allowance.**
   - The worker writes `span_derived_progress` after ClickHouse acknowledges each drain's rollup insert.
     This is a KeeperMap table, one row per partition, holding the partition's counted-through offset and
     that record's `received_at`. It goes through the existing `WriteGuard`, so a worker that has lost a
     partition cannot write for it.
   - The scheduler compares each row with Kafka's high watermark, as the silence sweep does. A partition
     with nothing unread is caught up through now. Otherwise it is through its `received_at`.
   - M is settled when every partition is through **M + 1 minute + 2 minutes**. Two minutes is the
     export allowance F21's thresholds already use (`thresholds.py:3-5`). It lets an SDK's batch, or a
     request lasting up to about 2 minutes, arrive before the minute is judged.
   - The partition count comes from Kafka's metadata, and freshness from ClickHouse's own `now()`, so one
     clock decides.
2. **nexovue's receiver refused nothing in M for lack of a store.** When it refuses a request because its
   store is unavailable, the receiver records the minute in Postgres, in `intake_refusals`: one upsert per
   minute per receiver, rate-limited in memory. Any such minute is unsettled for every project.
3. **Most projects did not lose traffic in the same minutes.** If at least half of the projects expecting
   traffic become unusual for *losing traffic* together, the cause is upstream of all of them: nexovue's
   edge or network.
   - The pass opens no customer findings for it.
   - It records one `detection_events` row, which operators see in the metrics.
   - Every project shows *data delayed*.
   - With a single project there is nothing to compare with, so the project finding (FR15) appears, worded
     to include "or nexovue's intake".

A span arriving after its minute was judged still lands in the rollup, so history is complete. It never
changes a judgement already made, because the judged reading is frozen (*Readings, frozen*).

### Inputs — *Review: architecture 5, 8, 9, 10; statistics 4*

- **`service_stats_1m`** (new ClickHouse rollup), written by the worker's derive stage beside
  `endpoint_stats_1m`, from the same inbound SERVER and CONSUMER spans.
  - It holds `request_count`, `error_count` and `latency_buckets`, an `Array(UInt32)` of 64 fixed
    log-spaced buckets from 1 ms to 60 s.
  - The buckets count **every** span, not a sample. So they are exact and they merge by addition, with no
    weights, rounding or randomness. A service's window p95, and its count of requests above any bucket
    boundary, can therefore be reproduced exactly.
  - It carries the insert-dedup token and `non_replicated_deduplication_window`, as migration 000021 gives
    `endpoint_stats_1m`, so a replayed batch is not counted twice.
  - Its retention is the lesser of the plan's derived retention and 35 days.
  - A service with only client or internal spans has no inbound requests, so it is never judged. Its
    service screen says *Not judged: no incoming requests*.
- **`endpoint_stats_1m`**, counts only, for each endpoint's *failing more* (FR2). Its per-endpoint p95 is
  also read for the *slower* evidence endpoint (FR3). It is compared with the same window last week, and it
  is evidence only, never a judgement.

### The normal (FR4 to FR8) — *Review: statistics 1, 6, 8, 9; product 1*

The hourly baseline job builds, for each service, signal and minute of the coming hour, a **level pool**
and a **dispersion pool**.

- **The level pool:** readings at the same local minute, ±15 minutes.
  - **Weekly mode:** the same weekday in the last 4 weeks, when at least 3 distinct days of it remain
    after exclusions.
  - **Daily mode:** otherwise, earlier days of the same class, weekday or weekend, when at least 3 distinct
    days remain.
  - **Learning:** fewer than 3 distinct days in either mode. *Learning until* is the first date on which
    the signal's class reaches 3 days. A weekend signal can therefore learn later than a weekday one.
  - **Losing traffic** waits for 21 days of history (FR6). After that, if its weekly days are excluded at
    some minute, it uses daily mode for that minute (FR4's fallback, AC13).
- **The dispersion pool:** the same days at ±180 minutes. Only the spread is taken from it. The ±15-minute
  pool holds about 4 independent samples of a drift with 1-hour memory, so its spread is too narrow; the
  statistics review traced the slower signal's false findings to it.
- **Exclusions:**
  - minutes inside a finding, unless the finding was marked a false alarm (BR3);
  - minutes the service read *data delayed*;
  - before 21 days, minutes when the shadow traffic test found traffic far below its daily normal (FR6).

  Short failure bursts that never opened a finding **stay in** the history, so the normal learns them (D17).
- **Like with like (FR8):** pooled values use the same window length as the value judged.
- **Readings are written for every settled minute,** learning minutes included, so a service always leaves
  learning.
- **Time zone (FR7, FR24):** local minutes come from the project's IANA zone. In the autumn fall-back hour,
  two UTC minutes map to one local minute and both are pooled. Traffic that follows another country's clock
  is out of scope.

### The statistical method (FR9 to FR15) — *Review: statistics 2, 3, 4, 5, 6, 7*

Each signal is judged on a window as long as the sustain time: 3, 5 or 10 minutes (*Product values*). The
window is unusual only if every listed condition holds for it, and **also with its single worst minute
removed**. At least 80% of its minutes must each lean the same way. The bar is `α = 3.2 × 10⁻⁵`, the
one-sided tail of 4 spreads, at every sensitivity. Sensitivity changes the window and the minimum change.

- **Losing traffic:**
  - the count is judged on the square-root (Anscombe) scale, where Poisson noise has a spread of 1 at any
    level;
  - it must be at least 4 spreads below the level pool's median, with the dispersion pool's spread
    floored at 1;
  - it must be at most the minimum ratio of the normal;
  - it is judged only when the **expected** count is at least the sensitivity's floor: about 20, 47 or 150
    in the window (*Product values*). Below that floor, a drop to the minimum cannot be told from noise.
- **Failing more (D17):**
  - **The base test.** A beta-binomial tail is used. The normal rate is the level pool's failures plus 0.5,
    divided by its requests plus 250, so a service with no failures in its history is not alarmed by one.
    The wander comes from the dispersion pool, by method of moments. The window passes when its tail
    probability is below α.
  - **Plus the minimum change:** +2 points and at least 5 failures at normal sensitivity, with at least 20
    requests in the window.
  - **And the service's own burstiness.** From the dispersion pool, the baseline job takes:
    - a **burst height**, the highest window failure rate in 28 days, capped below critical (D26);
    - a **burst length**, the longest excursion above the base test in 28 days.

    The window is unusual only if it is **higher** than the burst height, **or** has been above the base
    test for **longer** than the burst length. A naturally bursty service then tolerates its usual bursts.
    A fault that is higher than them, or outlasts them, is still caught.
  - **Only for a service whose own history is bursty (D18, D21).** That means at least one excursion above
    the base test per week of history, on average. A calm service skips the burst profile and keeps full
    sensitivity. The burst profile comes from the service's last 28 days. Its burst height is the highest
    window rate in that time, compared only with windows of similar request counts, and taken without the
    window's worst minute. Its burst length is the longest burst. The simulation showed every lower quantile
    sitting inside the bursts themselves.
  - **Per endpoint (FR2):** the same test, with α divided by the number of endpoints over the 20-request
    floor, and at least 10 failures.
- **Slower** is judged as a count, not a quantile.
  - **L** is the bucket boundary nearest the level pool's p95. The count is the requests above L in the
    window.
  - The count is tested by beta-binomial against the pool's own share above L, with its wander from the
    dispersion pool.
  - Separately, the window's p95, from the buckets, must be at least the minimum ratio of the normal p95.
  - At least 200 requests in the window are needed.
  - Counting slow requests is the review's remedy for latency with two speeds, such as cache hits and
    misses. There, a p95 jumps while the slow share barely moves.
- **Opening (FR12):** the first unusual window opens the finding. Leave-one-out and the 80% rule mean one
  minute never can.
- **Closing (FR13):** a signal is clear after 10 consecutive judged minutes whose window is **normal**: its
  tail probability is above 0.05, which is a stricter test than merely "not unusual". Minutes with too
  little traffic, or with delayed data, pause the count without resetting it. A finding closes when all of
  its signals are clear. After 6 hours in which none of its signals could be judged, it closes as *could
  no longer be judged*, with one message (D20).
- **Severity (FR14):** a finding is critical when its window crosses the critical value. Severity never
  falls.
- **Many services at once (FR15):**
  - A project finding opens when, within any 5 minutes, at least 3 judged services, and at least 75% of
    those judged for traffic, become unusual for *losing traffic*.
  - While 2 or more of a project's services are pending on traffic, a service's own traffic finding waits up
    to 3 extra minutes, so the project rule can fire first. That is AC21's one finding, not 9.
  - A project finding is critical. It closes when fewer than half of those services remain unusual for the
    recovery time.
  - Its title reads: "Traffic stopped arriving from 9 of 11 services at 14:02 IST. Check your collector,
    your network or nexovue's intake".
- **A service that stops (FR32):**
  - a fall to zero requests is critical *losing traffic*;
  - after 24 hours of silence, the finding closes as *service gone*, with one message, and is never
    adopted;
  - its history is kept, so the service is judged against its old normal if it returns.

### Level shifts: new normal and adoption (FR21) — *Review: statistics 9*

A `baseline_shifts` row moves the normal of one signal. Its ratio is the median of the last 60 readings
over the normal.

- **Traffic:** expected counts are multiplied by the ratio. The spread is scaled by the ratio's square root
  on the Anscombe scale.
- **Failing:** the normal rate is multiplied by the ratio, capped below 1. A normal rate of zero is replaced
  by the observed rate.
- **Slower:** L, and the pool's latency, are shifted by the ratio on the log scale.

Either way, the daily and weekly shape is kept, so the signal is judged straight away with no learning gap
(AC15). Adoption happens after 7 days open with the same signals (AC16), except for a service that has
stopped (FR32).

### Readings, frozen (BR5, NFR4) — *Review: architecture 3; operations 10*

`detection_readings` (ClickHouse, ADR-023) holds one row for each service and each judged minute:

- the window's counts;
- the window's p95 and its count above L;
- the per-endpoint failure counts over the floor;
- the baseline used: its mode, number of days, median, spread, burst height and length, and L;
- the verdict for each signal.

Other details:

- It is a `ReplacingMergeTree` with a version column. Reads use `argMax`, so a minute rewritten after a
  crash is counted once.
- Its retention is the lesser of the derived retention and 35 days.
- A finding copies its own normal, current and worst values and its comparison into its `signals` column.
  It stays explainable after its readings expire (AC40).

### Findings and their state — *Review: operations 9; product 5, 7, 10; architecture 7*

- **`findings`** (Postgres) has these columns:
  - `org_id` (not null), `project_id`;
  - `scope` (service or project) and `service_name`;
  - `severity` and `state`;
  - `closed_reason`: recovered, false alarm, new normal, adopted, service gone or could no longer be judged;
  - `muted`;
  - `acknowledged_by` and `acknowledged_at`;
  - `feedback_reason`;
  - `signals` (JSON);
  - `started_at` and `closed_at`.

  Two partial unique indexes enforce one open finding per scope, because Postgres treats nulls as
  distinct:
  - `(project_id, service_name) WHERE scope = 'service' AND state = 'open'`;
  - `(project_id) WHERE scope = 'project' AND state = 'open'`.

  `acknowledged_by` is a foreign key to `users` with `ON DELETE SET NULL`, because deleting an
  organization deletes its users before its projects (`organizations/tables.py`).
- **`finding_incidents`** links a finding to a threshold or *went silent* incident on the same service. The
  threshold and silence sweeps, and the project job, write it in whichever direction comes second (BR2).
- **`detection_state`** holds one row per service:
  - `last_judged_minute`, moved by compare-and-set;
  - for each signal: its phase, its clear-minute count, its false-alarm hold (FR20) and its learning date.
- **`detection_baselines`** holds the hourly baseline rows, one per service, signal and local minute of the
  coming hour.
- **`baseline_shifts`** is described under *Level shifts*.
- **`service_detection_settings`** holds a service's sensitivity, `muted_until` and `muted_by`. `muted_by`
  is set null when its user is deleted.
- **`projects`** gains `timezone`, which is not null. It is the creating user's zone, or UTC (FR24).
  It also gains `detection_sensitivity`.
- **Every new table** carries `org_id` (not null), and every upsert's conflict key includes it.

### Notifications (ADR-022) — *Review: architecture 3; operations 11; product 11*

- **The outbox gains a second subject.**
  - `notifications` gains `finding_id`. `incident_id` becomes nullable, with a check that exactly one of
    the two is set, and a unique key for each.
  - Delivery's claim (`_TAKEN`) reads either subject through a shared `Subject`. That covers its kind, id,
    key, label, opening and closing times, rule and detail.
  - `Notice.incident_id` becomes `subject_kind` and `subject_id`.
  - The in-order rule (`_IN_ORDER`) matches on both.
  - The PagerDuty `dedup_key` becomes `finding:<id>` or `incident:<id>`.
  - The message link is `/findings/{id}` for a finding.
  - The webhook schema moves to **version 2**: events are `incident.*` and `finding.*`, each with
    `data.subject`. nexovue is pre-deployment, so version 1 is replaced, not kept beside it.
- **What a finding sends.** In the job's transaction, a finding that opens, turns critical or closes writes
  one row per channel, for each enabled *anomaly finding* rule that matches it. A closing message goes only
  to the channels told at opening (F21's `_TOLD_CHANNELS`). Findings have no repeats, and no grouping wait,
  because FR15's project finding is the grouping. The burst cap and quiet hours apply unchanged.
- **The flap guard.** F21's guard (`incidents.py:41`) flags a fifth opening within an hour, for one project
  and service. It sends one *flapping* message, then nothing until the service has been quiet for 30
  minutes. This adds one message to what FR28 lists. See *Specification impact*.
- **Mute (BR9, D8).** A finding that opens while its service is muted is marked muted, and nothing is sent
  for it: not when it opens, escalates or closes. Muting an open finding, or its service, stops everything
  after that, except the recovery a PagerDuty or webhook channel needs to close what it was sent (F21 D8).
  When a mute ends, nothing is sent for findings it covered.
- **Rendering.** The finding's message uses `one_line` and each channel's escaping, from
  [nexovue-tech/nexovue#20](https://github.com/nexovue-tech/nexovue/pull/20), which is already merged.
- **Titles (FR17).** Each signal has its own template, with the project's zone named:
  - "Checkout is 4× slower than normal since 15:04 IST";
  - "Checkout is failing 3.2% of requests, against 0.4% normally, since 15:04 IST". A failure rate is given
    as two rates, because "8× more" from a 0.1% normal informs nobody;
  - "Checkout's traffic is 30% of normal since 15:04 IST";
  - "Checkout's POST /pay is failing 41% of requests…", when only an endpoint fails;
  - the project finding's title, given under FR15.

  The title is rewritten in place as the finding's worst value grows.
- **The default rule (FR29).** `provision_project` adds a second default rule, *Critical problems*. It is
  *anomaly finding* at critical or above, to the owners channel.

### Roles (FR23, D11) — *Review: security 4, 7*

- **`CONFIGURE_DETECTION`**, a new action for admins, gates:
  - sensitivity;
  - mute;
  - new normal;
  - creating, changing, muting or deleting any *anomaly finding* rule. This check is in the alerts service,
    because the router's `MayOperateAlerts` admits members.
- **`OPERATE_ALERTS`**, which excludes viewers, gates acknowledge and false alarm.
- **The authorization coverage table**, which `test_authorization_coverage.py` checks, is updated, and the
  router's stale "changes are an admin's" comment is corrected.
- **The read API (ADR-017).** `GET /findings`, `/findings/{id}` and `/findings/stats` are on the read
  surface, through `CurrentReader` and the checked-in list. `/findings/{id}` checks the row's project
  against the token's scope, as `get_incident` does. Every mutating route uses `CurrentIdentity`.

## Data flow and interfaces

```
receiver ── refuses for lack of a store ─► intake_refusals (Postgres)
spans ─► worker derive ─┬─ endpoint_stats_1m                     (as today)
                        ├─ service_stats_1m                      (new: exact buckets)
                        └─ span_derived_progress (KeeperMap)     (after the insert is acknowledged)

each minute   sweep:detection: settled minute ← progress + Kafka high watermarks + intake_refusals
                  └─ one project job per active project, on the `detection` queue
each hour     baseline job: dispersion and level pools ← service_stats_1m ─► detection_baselines
project job   this window ← service_stats_1m, endpoint_stats_1m counts   (tenant_statements, project-bound)
              baseline    ← detection_baselines
              write detection_readings                                   (writer account, ADR-023)
              one Postgres transaction: detection_state (compare-and-set), findings, finding_incidents,
                                        notifications
deliver (subject-aware) ─► channels
```

### API

| Route | Who | What |
|---|---|---|
| `GET /findings?project_id&state&muted` | read surface | The feed |
| `GET /findings/stats?project_id` | read surface | FR26. Declared before `/{id}` |
| `GET /findings/{id}` | read surface | One finding, with its readings while they exist |
| `POST /findings/{id}/acknowledge` | `OPERATE_ALERTS` | FR19 |
| `POST /findings/{id}/false-alarm` `{reason?}` | `OPERATE_ALERTS` | FR20. Also allowed on a finding closed as recovered |
| `POST /findings/{id}/new-normal` | `CONFIGURE_DETECTION` | FR21. `409` if the finding is no longer open |
| `GET /projects/{id}/detection/status` | read surface | The counts of learning, judged, delayed and not running, and the earliest judging date |
| `GET /services/{name}/detection?project_id&from&to` | read surface | State, bands and markers for a chart range |
| `PUT /services/{name}/detection` `{sensitivity: low\|normal\|high\|null, muted_until?}` | `CONFIGURE_DETECTION` | FR18, FR22. `null` returns the service to its project's sensitivity. A mute is limited to 30 days |
| `GET`, `PUT /projects/{id}/detection` `{sensitivity, timezone}` | read; `CONFIGURE_DETECTION` | FR18, FR24 |

## Data and migration impact

- **Postgres** (one migration, additive):
  - new tables `findings`, `finding_incidents`, `detection_state`, `detection_baselines`,
    `baseline_shifts`, `service_detection_settings`, `intake_refusals` and `detection_events`;
  - `projects.timezone` and `projects.detection_sensitivity`;
  - `notifications.finding_id`, its check and its unique key;
  - the `CONFIGURE_DETECTION` action;
  - the `anomaly_finding` condition kind;
  - the second default rule, for new projects.

  The deletion inventory moves `notifications` under `projects`, and lists the new tables in
  `REMOVED_BY_CASCADE`.
- **ClickHouse** (one migration, additive): `service_stats_1m`, `detection_readings` and
  `span_derived_progress`. The migration file states each table's reasoning.
  - `span_derived_progress` holds no tenant data. It gets a stated exception in the tenant-structure test,
    and an entry in the erasure exclusions.
  - It joins `KEEPERMAP_TABLES` in `deploy/backup/common.sh`, so a restore drops its stale rows as it does
    for the three already listed.
- **ClickHouse users:** a new `nexovue_detection_writer`, with `GRANT INSERT ON nexovue.detection_readings`
  and nothing else. It needs its own DSN, its install step and its secret (ADR-023).
- **Rollback.** Both migrations add and never change, but `docs/operations/rollback.md` refuses a rollback
  across a schema change, so its restore path applies, as for every migration.

## Performance and scale

- **A project job reads:**
  - one row per service and signal from `detection_baselines`;
  - the current window from `service_stats_1m`, at most 10 rows per service, each 64 small integers;
  - endpoint counts for the window.
- **The hourly baseline job** reads up to 4 weeks × 361 minutes per service from `service_stats_1m`, once an
  hour, not once a minute.
- **The readings insert** is one batched insert per project job, with an async insert, so ClickHouse is not
  given many small parts.
- **Storage:** about 300 bytes before compression per service-minute in each of `service_stats_1m` and
  `detection_readings`, for 35 days. That is unmeasured.
- **Plan Step 1 measures** pass time, rows read, memory and storage at named counts of projects and
  services, against NFR2 and NFR5 (AC29).

## Failure handling

| Failure | Behaviour |
|---|---|
| A worker is behind, dead, or has lost a partition | That partition's progress stops, or the `WriteGuard` refuses it. The minute is not settled. *Data delayed* (AC23) |
| A partition is idle | Nothing is unread, so it is caught up. It never blocks settling |
| nexovue's intake refuses data, or its edge fails | `intake_refusals`, or the install-wide guard. No customer findings. *Data delayed* |
| Spans arrive after the minute was judged | They are in the rollup and in later history. The frozen reading and every finding are unchanged (AC24) |
| A project job overruns its budget | It stops where it is. The next run continues, catching up at most 2 minutes. Other projects are unaffected |
| Two project jobs overlap | The advisory lock and the compare-and-set mean one judges and the other finds nothing to do (AC25) |
| A crash between the readings write and the Postgres commit | The minute is judged again. `argMax` counts the rewritten row once. Postgres moves once |
| ClickHouse is slow or down | The job fails, writing nothing. Its last completed run ages, so the project shows *detection not running* after 3 minutes (AC26). After 15 minutes the owners are emailed (D24) |
| The scheduler is down for more than 10 minutes | Judging restarts at the latest settled minute. Nothing old is sent |

## Security, tenancy, and privacy — *Review: security 1, 2, 3, 5, 8, 12*

- **ClickHouse reads** go through `tenant_statements`, bound to one project. Every detection statement
  carries both `tenant_id = {tenant_id:String}` and `project_id = {project_id:String}`. The shared check and
  the structure test enforce the second predicate for the scheduler's detection modules (AC28).
- **ClickHouse writes** (ADR-023): the writer lives in the chokepoint module and is built for one project
  by `for_project`. Its row type has no tenant or project field; the writer fills both in. The structure
  test widens to `INSERT INTO` and `.insert(`, and allows them only there.
  - A live test writes as project A and reads as project B, and finds nothing.
  - Another proves the API's account is refused an insert.
- **Postgres:** a new scheduler structure test parses its `text()` statements for `org_id`.
  `test_postgres_tenancy_structure.py` scans only `apps/api/src`, and only statements built with
  SQLAlchemy, so it cannot see the scheduler's.
- **Personal data:**
  - acknowledge, false alarm, mute and new normal each write an audit row with their reason. The
    personal-data export already includes a person's audit activity, and a test proves these appear in it;
  - adoption audits as the system actor, as erasure already does (`erasure/owed.py`);
  - foreign keys are set null when their user is deleted.
- **Telemetry text is untrusted:**
  - service and endpoint names reach messages only through #20's escaping;
  - the web shows them as text only;
  - Stage 2's AI will receive names as structured fields marked untrusted, never as a composed title, and
    gets its own injection tests (`phase-2-plan.md`, *AI safety*).
- **Fairness between tenants:** each project job has its own budget and lock, so a noisy project cannot
  starve another. A project with more than 500 judged services judges the 500 busiest, and shows *too many
  services to judge all* on the rest. This is a starting cap, measured in Plan Step 1.

## Observability and operations — *Review: operations 4*

- **Gauges:**
  - the age of the settled minute;
  - the oldest `last_judged_minute` per project;
  - project jobs failed and overrun;
  - install-wide intake events;
  - rows read per job.
- **Prometheus alerts:**
  - *detection stalled*: the settled minute is more than 10 minutes old;
  - *project detection failing*: 3 failed jobs in a row for one project.

  The rules go into `deploy/monitoring/rules/nexovue.yml`, with tests.
- **A runbook**, `docs/operations/detection.md`. It covers what *data delayed* means, how to see which
  partition holds it back, and what to do.
- **Logs:** one structured line per project job, and one per finding change.

## Simulation — *Review: statistics 1 to 9; D17*

[`tools/detection-sim/`](../../../tools/detection-sim/) runs the method above on synthetic services.
[`RESULTS.md`](../../../tools/detection-sim/RESULTS.md), *Revision 2*, has every table, command and seed.

**The traffic:**
- 1 to 20 endpoints per service, with drifting failure rates;
- multi-minute failure bursts;
- latency with a cache-miss tail;
- a jittering daily batch spike;
- weekday and weekend patterns;
- 2 to 3,000 requests a minute at peak.

Each run is 4 seeds × 100 services, 4 weeks of history, then 4 judged weeks.

**The final method (D18 to D21), at normal sensitivity:**

| Failure bursts | False findings per service per week (budget 1) | Days 3 to 20 | Services classed bursty |
|---|---|---|---|
| None | 0.25 ± 0.06 | 0.16 | 2% |
| 1 in 5,000 min | 0.39 ± 0.09 | 0.27 | 11% |
| 1 in 2,000 min | 0.53 ± 0.15 | 0.43 | 25% |
| 1 in 500, 4 min | 0.80 ± 0.22 | 0.85 | 40% |
| 1 in 500, 8 min (harshest) | **1.11 ± 0.28** | **1.23** | 46% |

At the harshest setting, high sensitivity gives 1.04 and low 0.57. The 6-hour close (D20) fires about once
in 1,000 service-weeks with no fault.

**Faults caught within *time to catch*,** at normal sensitivity, counting only faults clearly past the
minimum (D19):

| Fault | Harshest bursts | No bursts |
|---|---|---|
| 5× slower, 30% failing, traffic to 5%, cache misses 30% | 99 to 100% | 99 to 100% |
| 2× slower | 84% | 83% |
| One endpoint failing | 86% | 91% |
| Failing +5 points | **61%** | 90% |
| One-minute spike opening a finding | 1 in 1,196 | 0 |

**AC13** opened within *time to catch* for traffic and failing at both outage times, and for slower in 98 to
100% of cases. D20 fixed the earlier miss: a finding that never closed.

**What this means (D21):**
- **The budget and AC36 conflict only at the harshest guess.** At 1 in 500 with 4-minute bursts, and
  everything milder, the budget holds. The one tried fix, capping the burst length, raised the +5-point
  catch rate to 75% but false findings to 1.64. The founder chose the budget.
- **Real burst rates are measured in the plan's demo run** (D14), which settles where real services fall.
- **The cost of tolerance falls only on bursty services.** There, faults shaped like their own bursts are
  caught 9 to 42% of the time. Calm services lose nothing.
- **AC2's single case** was a spike right after a natural near-burst on a quiet service (the spec, AC2).
- **Traffic to 50% was injected at exactly the minimum,** so only 46 of 1,200 injections were clearly past
  it. The demo run injects it at 40%.

**The D17 run, 2026-10-31,** applied burst tolerance to every service. Its full tables are in
[`RESULTS.md`](../../../tools/detection-sim/RESULTS.md).

| Bursts | False findings per service per week, normal | Fixed rule (D15) |
|---|---|---|
| None | 0.23 | 0.25 |
| 1 in 2,000 min | 0.42 | 0.65 |
| 1 in 500, 4 min | 0.61 | 1.67 |
| 1 in 500, 8 min | 0.85 | 2.84 |

- **Within budget at normal across the sweep,** except daily mode under the harshest bursts: 1.08.
- **The one-minute spike opened nothing:** 0 of 1,196.
- **AC13 passed for traffic** at both outage times.
- **AC36 failed in two places:**
  - *failing +5 points* under harsh bursts was caught in time only 56% of the time;
  - *traffic to 50%* was caught in time only 58% of the time, at exactly the minimum.

  D18 and D19 answer these.

What earlier runs established, and this design keeps:

- **First simulation, 2026-10-30:** small-count tails need exact or square-root tests, not log-counts or a
  bell curve. One-minute spikes need leave-one-out.
- **The harsher simulation (`sim2`), 2026-10-30:**
  - the ±180-minute dispersion pool cut false *slower* findings by 6× without losing detection;
  - with bursts at 1 in 500 minutes, a fixed rule gave 1.65 false findings per service per week at normal
    sensitivity, against a budget of 1. That is why D17 replaced it;
  - faults beyond critical were caught in 98 to 100% of cases, within 12 minutes;
  - outage minutes were kept out of history in 96 to 100% of cases.

## Alternatives and tradeoffs

- **Findings as F21 incidents, in one table.** Rejected (ADR-022). Incidents are keyed by rule; findings
  exist with no rule and with several. The outbox gains a second subject instead.
- **One sequential pass over every project.** Rejected. It overruns at about 1,000 small projects, starves
  the projects at the end of its order, and lets a noisy tenant delay everyone.
- **Reading four weeks of history every minute.** Rejected: per-minute cost grows with history. The hourly
  baseline job reads history once an hour.
- **A t-digest, or reservoir samples, for service latency.** Rejected:
  - t-digest weights must be integers, so rounding errors of up to 2× were measured;
  - reservoir merges are not deterministic;
  - and both lean toward quiet endpoints.

  Exact buckets are smaller, exact and reproducible.
- **A fixed rule for failure bursts** (D15, superseded). Rejected: it rests on a guessed burst rate. The
  per-service burst profile adapts to each service's real one (D17).
- **Detection in the Go workers, or a new `apps/insight` service.** Rejected: ADR-012 and ADR-019. The
  workers are partitioned by trace, and hold no rules or findings.
- **EWMA, Holt-Winters or STL decomposition.** Rejected: a weighted mean follows an ongoing outage (BR3), and
  0013 leaves models for later.

## Specification impact

- **FR28, AC19:** findings also send F21's single *flapping* message after five openings in an hour. To be
  amended on approval.
- **FR29:** built as a *second* default rule, *Critical problems*. Amended.
- **F21 is amended on approval:**
  - BR10, for *anomaly finding*;
  - its incidents are not used for findings;
  - the webhook schema becomes version 2;
  - the PagerDuty `dedup_key` gains a kind prefix.
- **F1 is amended on approval:**
  - AC1.8, as the spec says;
  - a closed card names how it closed;
  - muted findings are reached through a link, not a filter.
- **`phase-2-plan.md`, Stage 3,** amended from "grows the existing incident" to "grows the finding"
  (D1, ADR-022).
- **Design-level values, chosen here, and tuned in the demo run (D14):**
  - k = 4, so α is about 3.2 × 10⁻⁵;
  - level pool ±15 minutes, dispersion pool ±180 minutes;
  - 3 distinct days to leave learning;
  - the 80% rule and leave-one-out;
  - the burst quantiles (*Simulation*);
  - the 2-minute export allowance;
  - the 3-minute wait for the project rule;
  - the cap of 500 services;
  - the 20-second budget for a project job.

## ADRs

- [ADR-022](../../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md): a finding is the
  one record of a detected problem, and the outbox addresses findings or incidents.
- [ADR-023](../../decisions/ADR-023-the-scheduler-writes-detection-state-to-clickhouse-through-its-own-account.md):
  the scheduler writes detection readings to ClickHouse, through an account that can insert into that one
  table only.

## Open questions

None. D18 to D21 answered what the simulation raised.

# Feature: Alerts & notification connectors (F21)

## Status

**Approved** — by the founder, 2026-09-18, as written: the seven decisions below were accepted as
recommended and are recorded under *Decisions taken*. **Amended 2026-09-19** with the approved design:
FR1, FR2 and FR10 amended and AC27 added (see the design's *Specification impact*). **Amended again
2026-09-19** after implementation review, by the founder's decision: BR5 and FR11 corrected, D8 added.
**Amended 2026-10-31** with the approved F6 design (`docs/features/anomaly-detection/design.md`,
ADR-022):

- BR10 has an exception for *anomaly finding* rules;
- findings are not incidents;
- the webhook schema becomes version 2;
- PagerDuty's `dedup_key` names its subject's kind.

## Problem

nexovue watches everything and tells nobody.

- **F18 records every confirmed state change and sends it nowhere.** `check_state_changes` already calls
  itself "what an alert will be raised from (F21)" (`data/postgres/models.py`), and the scheduler task
  that writes it (`probes/tasks.py`, `record_outcome`) ends a state change with a log line. A check that
  goes down at 3 a.m. is visible at 9 a.m., to whoever happens to open the Synthetics screen.
- **Nothing else can raise anything.** There are no alert rules, no notification channels, no delivery
  table, no route, no screen and no scheduler task. "New" error groups are a read-time filter
  (`errorgroups/queries.py`, `STARTED_IN_WINDOW`) that nothing remembers, so nothing can say "this error
  is new *since you last looked*".
- **The only outbound message the product can send is an account email** — verification, recovery, email
  change (`notifications/`). Its port has three members and no room for anything a customer asked for.

The catalogue calls this "the largest gap in the product", and the vision's whole pipeline ends in it:
*detection → insight → case → **alert sent via connectors***. Everything to the left of that arrow is
worth little until something reaches a person.

### What a customer compares us against

Researched 2026-09-18 from vendor documentation, pricing pages and changelogs; user evidence from Hacker
News, GitHub issues, vendor forums and Capterra (Reddit could not be fetched and is cited only
second-hand). **✓** means the vendor's own documentation states it; **✗** the opposite; **?** not verified.

| Expectation | Better Stack | Sentry | Checkly | UptimeRobot | Grafana | Datadog |
|---|---|---|---|---|---|---|
| Email, Slack and a generic webhook | ✓ | ✓ | ✓ | ✓ (Slack, webhook paid) | ✓ | ✓ |
| PagerDuty | ✓ | ✓ | ✓ | ✓ paid | ✓ | ✓ |
| Microsoft Teams, Discord | Teams ✓, Discord ✗ | ✓ ✓ | ✓ ✓ | ✓ ✓ | ✓ ✓ | Teams ✓ |
| Wait before firing and before resolving | ✓ confirmation + recovery periods | ✓ failure + recovery tolerance | ✓ run/time/location based | ✓ re-checks, "notify after" paid | ✓ pending + keep-firing | ✓ |
| One incident per downtime, resolved on recovery | ✓ | ✓ | ✓ | ✓ | n/a | ✓ |
| Mute, and one-off and recurring maintenance | ✓ | ? | ✓ | ✓ paid | ✓ | ✓ |
| A default alert with no setup | ✓ email the signup user | ✓ (and it over-fires, getsentry#84155) | ? | ✗ contacts attached per monitor | ✗ rules start empty | ? |
| Signed webhooks | ✗ not documented | ? | ✓ HMAC-SHA256 | ? | ✓ HMAC-SHA256 | ✗ not documented |
| A delivery log the user can see | ? | ? | ✓ filterable | ✗ "does not retry … does not tell you" | ✓ per contact point | ? |
| Resolve posted into the original Slack thread | ? | ✓ since 2024 | ? | ? | ✗ closed "not planned" (grafana#93047) | ✓ via notification rules |

**What users complain about, in their words:** "There are days I can't keep up with Sentry notifications
because we get so many of them" (Sentry, quoting customers); "Daily digests frequently report dozens of
triggered alerts—many of which are repetitive or low-priority" (Datadog review, Capterra, June 2025); a
Grafana user asking to update the original Slack message on resolve instead of posting a new one; "140
emails" about a service cycling up and down (UptimeRobot, secondary). The pattern: **noise, repetition, and
silence about the alerting itself** — Grafana users build meta-alerts to find out a contact point is broken.

**Two platform facts that change the channel list:**

- **Opsgenie ends support on 5 April 2027** — sales stopped 4 June 2025, and unmigrated data is deleted
  (atlassian.com/software/opsgenie/migration). A native Opsgenie connector built now has a six-month life.
- **Microsoft Teams "Office 365 Connector" webhooks stopped working 18–22 May 2026.** Posting to Teams now
  requires a Workflows webhook (devblogs.microsoft.com, *Retirement of Office 365 connectors*).

## Goal

When something a customer cares about breaks — a check goes down, a new error appears, a service's error
rate or latency crosses a line, a service stops sending telemetry — the right people hear about it once,
in the place they already work, with a link to the evidence; they hear again when it recovers; and they
are not woken by the same thing twice, by something that fixed itself in a minute, or by planned work.

## Target users

- **P1 Dev** — owns the service; wants Slack in the day, PagerDuty at night, and the link to the failing
  request in both.
- **P2 Indie** — one person, no on-call rotation; wants "email me when my site is down" to work the moment a
  check exists, and does not want to configure routing to get it.

**Not served:** P3 Owner. A non-technical owner learns about an outage from a status page (F7, F30) or from
the developer, not from alert routing. **Not served either: an on-call team.** Schedules, escalation and
acknowledgement belong to PagerDuty and its peers; this feature hands them the alert and steps back
(*Deliberately NOT built*, `feature-catalog.md`).

## Scope

**What can raise an alert** (the *conditions*):

1. **A synthetic check changes state** (F18): goes down, degraded or misconfigured, and recovers. Already
   confirmed from a second location by F18 before it is recorded.
2. **A new error group appears** in a project — the first occurrence of an exception nobody has seen
   before.
3. **A service's error rate or p95 latency crosses a threshold** for a sustained period.
4. **A service stops sending telemetry** it was sending — silence, which F18 already treats as a failure for
   heartbeats and which is the one failure no other condition can see.

**Where it can go** (the *channels*): email, Slack, Microsoft Teams (Workflows), Discord, PagerDuty (Events
API v2), and a generic signed webhook — which also reaches incident.io, Jira Service Management and
anything else that accepts one.

**How it avoids noise:** a wait before firing and a wait before resolving; one *incident* per breach,
opened once and resolved once; a re-notification interval that is off by default; a flap guard; a per-channel
burst cap; mute; quiet hours; and maintenance windows, which already exist for checks.

**How it stays trustworthy:** every delivery is logged and visible, retried with backoff, and a channel that
keeps failing tells the organization's owners by email. A *send test* button on every channel.

**Out of the box:** every new project gets one rule — *any check goes down or recovers → email the
organization's owners* — so a single-person account is alerted with zero configuration.

## Out of scope

- **On-call schedules, escalation chains and acknowledgement.** PagerDuty's and its peers' job, decided
  2026-09-07 (`feature-catalog.md`, *Deliberately NOT built*). An alert reaching PagerDuty carries the
  deduplication key PagerDuty needs to acknowledge and resolve it; everything after that is theirs.
- **SMS, phone calls and WhatsApp.** Paging, and metered by every vendor that offers them. They reach a
  person today through PagerDuty. A direct connector needs a paid provider account and a cost model this
  pricing (per service monitored) does not have. **This removes three channels the catalogue line promises**
  — see *Decisions taken*, D6.
- **Opsgenie as a native connector.** Support ends 5 April 2027. Its successor, Jira Service Management,
  accepts the generic webhook.
- **A Slack app** with buttons, threads and message updates. Incoming webhooks first — see *Decisions taken*, D2.
- **Alerts on arbitrary metric queries and log searches.** Conditions 1–4 cover the signals every customer
  has; a query builder for alert conditions is a second feature with its own evaluation cost. Phase 2,
  alongside anomaly detection (F6).
- **Anomaly detection, AI root cause in the message, findings and cases** — F6, F3, F1, F22.
- **Composite conditions** ("A and B"), **label-matched routing trees**, **per-user notification
  preferences.** Until F26 there is one person per organization; routing by label solves a problem a
  small team does not have yet (research: Grafana's policy tree is where users report wrong routing).
- **Mobile push.** No mobile app.
- **Status pages** — F7, F30.

## User requirements

- **US21.1** — As an Indie (P2), when I create my first check I am emailed if it goes down and when it comes
  back, without having configured anything, so that the first alert I need is one I already have.
- **US21.2** — As a Dev (P1), I can connect Slack, Teams, Discord, PagerDuty, a webhook or another email
  address in the console, send a test message, and see that it arrived, so that I trust the channel before
  I rely on it.
- **US21.3** — As a Dev (P1), I can write a rule — *when this happens in this project, tell these channels* —
  for any of the four conditions, so that the right people hear about the right things.
- **US21.4** — As a Dev (P1), I am told once when something breaks and once when it recovers, with how long
  it lasted, so that a two-hour outage is two messages, not forty.
- **US21.5** — As a Dev (P1), a problem that fixes itself within a minute, or that flips on and off, does
  not page me, so that a page means something.
- **US21.6** — As a Dev (P1), every message says what broke, how badly, since when, what the value was
  against the threshold, and links to the evidence — the failing run, the error group, the service — so
  that I can start diagnosing from the notification.
- **US21.7** — As a Dev (P1), I can mute a rule or a channel for a while, set quiet hours on a channel, and
  rely on maintenance windows, so that planned work and my evenings are quiet — without missing something
  critical.
- **US21.8** — As a Dev (P1), I can see every notification sent, to where, whether it was delivered, and why
  one failed, and I am told when a channel keeps failing, so that silence means *nothing broke* rather than
  *the alert did not arrive*.
- **US21.9** — As a Dev (P1), an alert sent to PagerDuty resolves itself there when the problem recovers, so
  that my on-call tool and nexovue agree.
- **US21.10** — As a Dev (P1), I can see which incidents are open now and which were open recently, so that I
  can tell whether an alert I received is still true.

## Business rules

- **BR1 — An incident is opened once and resolved once.** A condition that stays breached produces one
  *incident*; every notification about it — opened, repeated, resolved — belongs to that incident and
  carries its identity. Two incidents for one continuous breach is a defect.
- **BR2 — Nothing fires during planned work.** A check in a maintenance window changes no state (F18, AC23)
  and therefore raises nothing. A check that went down during a window alerts on the first confirmed run
  after the window ends — planned work suppresses the alert, it does not hide a failure that outlasts it.
- **BR3 — A paused check or rule raises nothing**, and resuming it does not replay what happened while it
  was paused.
- **BR4 — Quiet hours hold, they do not drop.** A *warning* held by quiet hours is delivered when they end,
  collected into one message. A *critical* alert is never held.
- **BR5 — Mute drops.** A muted rule or channel sends nothing and delivers nothing afterwards — that is
  the difference from quiet hours. A mute always has an end time. One exception, for machines (D8): a
  PagerDuty or webhook channel that was sent an incident's opening before a rule was muted is still sent its
  recovery, or the alert would stay open there.
- **BR6 — A channel's secret is a secret.** A Slack, Teams or Discord webhook URL, a PagerDuty routing key
  and a webhook signing secret are write-only: stored sealed, never returned by the API, never logged,
  never in an audit row or an error message.
- **BR7 — We never deliver to a private address.** A webhook whose host resolves to a private, loopback,
  link-local or metadata address is refused at delivery — the same guard the synthetic probes use.
- **BR8 — Delivery failures are visible.** No notification fails silently: each attempt is recorded with its
  outcome and reason, and the console shows it.
- **BR9 — An alert names only what the recipient may already see.** A message carries the organization's
  own data and a link into the console; it carries no telemetry payloads, no request bodies from check
  evidence, and no secret — redacted or not.
- **BR10 — Severity is part of the rule, not a guess.** Every rule states whether it is *critical* or
  *warning*; defaults are given per condition (FR5) and can be changed. *Exception (F6, 2026-10-31):* an
  *anomaly finding* rule states only a minimum severity, and each message carries the finding's own
  severity, which the detector sets. Findings are their own record, not incidents (ADR-022). The outbox
  addresses either kind of subject. The webhook schema is version 2, with `incident.*` and `finding.*`
  events. PagerDuty's `dedup_key` is `incident:<id>` or `finding:<id>`.

## Functional requirements

### Channels

- **FR1** — Channel kinds: **email** (one or more addresses), **Slack** (incoming webhook URL), **Microsoft
  Teams** (Workflows webhook URL), **Discord** (webhook URL), **PagerDuty** (Events API v2 routing key), and
  **webhook** (URL, with a signing secret generated by nexovue and shown once). Plus **organization owners**:
  email to the organization's owners at their current verified addresses, resolved at send time — the
  recipient of the default rule (FR8).
- **FR2** — A channel belongs to the organization and is available to every project's rules (D4). Every
  organization has exactly one *organization owners* channel, created with it; it can be muted but not deleted.
- **FR3** — **Send test** delivers a clearly labelled test message through the real path and shows the
  outcome and, on failure, the reason — within the request, up to a time limit.
- **FR4** — A generic webhook follows the **Standard Webhooks** conventions: `webhook-id`,
  `webhook-timestamp` and `webhook-signature` (HMAC-SHA256) headers, a JSON body with a documented schema
  and a version field, and the same `webhook-id` on every retry of one delivery so a receiver can
  deduplicate.

### Conditions and rules

- **FR5** — A rule is: a project, a condition, a severity, one or more channels, and noise settings.
  Conditions and their defaults:

  | Condition | Scope | Fires when | Default severity | Default waits |
  |---|---|---|---|---|
  | Check state | all checks in the project, or chosen checks | a check becomes *down*, *degraded* or *misconfigured* (each selectable) | down: critical; degraded, misconfigured: warning | none — F18 already confirmed it |
  | New error group | all services, or chosen services | an error group's first occurrence | warning | none |
  | Service error rate | a service, or all services | error rate ≥ threshold % over the evaluation window | critical | fire after 5 min breached; resolve after 5 min clear |
  | Service p95 latency | a service, or all services | p95 ≥ threshold ms over the evaluation window | warning | fire after 5 min; resolve after 5 min |
  | Service went silent | a service, or all services | no telemetry received from a service that sent some in the previous 24 h | critical | fire after 10 min of silence |

- **FR6** — A service-threshold rule states a **minimum request count** in its window, below which it
  neither fires nor resolves — five failures out of eight requests is not a 62 % outage worth a page.
- **FR7** — *Misconfigured* is a separate choice on a check rule, not implied by *down*: it is the author's
  mistake, and waking PagerDuty for a typo in a check's own configuration is the noise this feature exists
  to prevent.
- **FR8** — A new project is created with the default rule of *Scope* (any check down or recovered → email
  the organization's owners, critical). It can be edited, muted or deleted like any other rule, and is
  **not** recreated once deleted.

### Incidents and noise

- **FR9** — An **incident** opens when a rule's condition fires for a subject — one check, one error group,
  one service — and resolves when the condition clears after the resolve wait. Its identity is the rule and
  the subject; it is the deduplication key sent to PagerDuty and the thread anchor in the delivery log.
- **FR10** — Notifications: **opened** (always), **resolved** (always, with the duration), **repeated**
  (only if the rule sets a re-notification interval; off by default, never more often than every 30
  minutes, and at most 5 repeats per incident), and **escalated** — once, when an open incident's severity
  rises (a check selected for both *degraded* and *down* going from one to the other stays one incident, BR1,
  but is no longer a warning). PagerDuty receives a new `trigger` with the same `dedup_key`.
- **FR11** — **Flap guard.** An incident for the same rule and subject that opens more than **4 times in an
  hour** is marked *flapping*: one notification says so, further open/resolve notifications for that
  subject are suppressed until it has stayed resolved for 30 minutes, and the console shows the flapping
  state. A check flipping up and down all hour produces nine messages — four openings, four recoveries and
  one *flapping* — not forty.
- **FR12** — **Burst cap per channel.** A channel sends at most **10 notifications a minute**; the rest in
  that minute are combined into one summary message ("7 more incidents opened — see nexovue") with a link to
  the open incidents. A misconfigured rule on a large project cannot flood a Slack channel.
- **FR13** — **Grouping of simultaneous check failures.** When several checks in one project go down within
  30 seconds of each other, a channel receives one message listing them rather than one per check — the
  shape of a shared outage (DNS, a load balancer, the region). Each check still has its own incident.
- **FR14** — **Mute**: a rule or a channel, for a chosen duration (1 h, 4 h, until tomorrow 09:00, a custom
  time). Muting shows who muted it and until when, and ends by itself.
- **FR15** — **Quiet hours** on a channel: days and hours in a time zone. Warnings raised during them are
  held and delivered as one digest when they end (BR4); critical alerts are delivered immediately.

### Messages

- **FR16** — Every message states: the rule's name; severity; what fired, in words ("*Checkout API* is down
  from 2 locations: status 503, expected < 400"); the value against the threshold where there is one; when
  it started; for *resolved*, how long it lasted; and one link — to the **incident's page**, which links
  on to the evidence (the check and its runs, the errors, the service for a threshold or silence) while
  that evidence exists (D9). A message about several incidents links to the Alerts screen.
- **FR17** — Each channel gets its native format: Slack and Discord messages with a colour by severity,
  Teams an Adaptive Card, email plain text with a subject that starts with the severity, PagerDuty a
  `trigger`/`resolve` event with `severity`, `summary`, `source` and `dedup_key`.
- **FR18** — A message never includes check request or response bodies, headers, secrets, telemetry
  attribute values or stack-trace contents (BR9). The error-group message names the exception type and
  the service; the stack is behind the link.

### Delivery

- **FR19** — Delivery is retried on a network failure, a timeout, 429 (honouring `Retry-After`) and 5xx,
  with backoff, for up to 1 hour; a 4xx other than 429 is final and recorded as the reason.
- **FR20** — **Delivery log**: every attempt of every notification — incident, channel, time, outcome, HTTP
  status, and for a failure the reason in words — visible on the channel and on the incident, kept 30 days.
- **FR21** — A channel whose deliveries have failed for **1 hour** is marked *failing* in the console, and the
  organization's owners are emailed once — through the account email, not through the failing channel.
  It recovers to *healthy* on the next successful delivery.

### Console

- **FR22** — **Alerts** in the main navigation: open incidents first (subject, rule, severity, since when,
  channels notified), then rules, then recent incidents.
- **FR23** — **Channels** under organization settings: add, edit (replacing a secret, never reading it), send
  test, mute, delete, health, delivery log.
- **FR24** — Every check's page and every service's page shows the rules that cover it and any open
  incident, and offers **Alert me about this** — a rule scoped to that subject, prefilled.
- **FR25** — Deleting a channel used by a rule says which rules will lose it; a rule left with no channel is
  shown as *not notifying anyone*, never silently kept.

### Audit

- **FR26** — Creating, changing, muting or deleting a rule or a channel writes an audit row naming the actor
  and the rule or channel — never the secret.

## Non-functional requirements

- **NFR1 — Latency.** A check's state change reaches an immediate channel (not held, not muted) within **60
  seconds** of being recorded. A threshold or silence condition fires within **2 minutes** of its wait
  elapsing.
- **NFR2 — No lost incident.** A notification for an incident that opened is never lost to a restart of any
  service between the condition firing and the first delivery attempt. It may be sent late; it may not be
  skipped.
- **NFR3 — At most one of each.** A process retry or restart does not produce a second *opened* or
  *resolved* notification for the same incident on the same channel (NFR2's counterpart).
- **NFR4 — Evaluation cost.** Threshold and silence conditions evaluate from the per-minute rollups the
  service screens already read, not from raw spans, so evaluation cost grows with the number of rules, not
  with telemetry volume.
- **NFR5 — Isolation.** One organization's slow or failing webhook cannot delay another organization's
  notifications.
- **NFR6 — Tenancy.** Every rule, channel, incident and delivery belongs to one organization; nothing
  crosses organizations — the same rules as every other store (`test_postgres_tenancy_structure.py`,
  `organizations/tables.py`).

## Error cases

| What goes wrong | What the user sees | Can they recover? | Distinguishable from "nothing happened"? |
|---|---|---|---|
| A Slack/Teams/Discord webhook URL is wrong or revoked | *Send test* fails with the platform's reason; later deliveries show *failed* in the log; after 1 h the channel is *failing* and owners are emailed | Yes: replace the URL | Yes — the log and the channel state |
| PagerDuty routing key invalid | *Send test* fails with PagerDuty's reason ("invalid routing key") | Yes | Yes |
| A webhook URL resolves to a private address | Refused at save when the host is a literal private address; refused at delivery otherwise, recorded as "the destination is not a public address" (no address shown) | Yes: use a public endpoint | Yes |
| The receiving endpoint is down or rate-limits us | Deliveries retried for up to an hour; each attempt in the log | Automatically, if it comes back within the hour | Yes |
| Email provider not configured (self-hosted without Resend) | Email channels cannot be created; the form says why and what to set | Yes: configure the provider | Yes |
| A rule covers nothing (its only check was deleted) | The rule is shown as *covers nothing* | Yes: edit or delete it | Yes |
| A rule's channels were all deleted | The rule is shown as *not notifying anyone* (FR25) | Yes | Yes |
| A service has too little traffic for its threshold rule | The rule shows *not enough traffic to judge* for that window rather than silently passing (FR6) | n/a | Yes |
| A condition fires while its rule is muted | Nothing is sent; the incident is still recorded and visible, marked *muted* | Unmute to be notified of what is still open | Yes |
| Flapping | One *flapping* notification; the incident shows the flip count | Fix the cause or adjust the rule | Yes |
| More than 10 notifications a minute to one channel | One summary message for the overflow (FR12) | n/a | Yes — the summary says how many |
| nexovue's own alert evaluation is behind | The Alerts screen states when conditions were last evaluated when that is more than 3 minutes ago | n/a | Yes |

## Acceptance criteria

**Channels**

- **AC1** — Each channel kind must be creatable in the console and its *send test* must reach a real
  endpoint of that kind (email to the console provider; Slack, Teams, Discord, PagerDuty and webhook to a
  recording endpoint) with the kind's native format (FR17).
- **AC2** — No API response, console page, log line, audit row or error message may contain a channel's
  webhook URL, routing key or signing secret after it is saved (BR6).
- **AC3** — A webhook delivery must carry `webhook-id`, `webhook-timestamp` and a `webhook-signature` that
  verifies with the channel's secret; a retried delivery must carry the same `webhook-id` (FR4).
- **AC4** — A webhook whose hostname resolves to a private or metadata address must not be delivered to, and
  the refusal must be recorded without the address (BR7).

**Conditions**

- **AC5** — A check confirmed down must produce one *opened* notification on each of the rule's channels
  within 60 s, and its recovery one *resolved* notification with the duration (NFR1, FR10).
- **AC6** — A check that goes down during a maintenance window must produce no notification during the
  window, and exactly one *opened* notification after the first confirmed run once the window ends, if it is
  still down (BR2).
- **AC7** — A *misconfigured* check must not notify a rule that selected only *down* (FR7).
- **AC8** — A new error group must produce one notification the first time it occurs, and none for later
  occurrences of the same group.
- **AC9** — A service error-rate rule with a 5-minute fire wait must not fire for a 3-minute breach, must fire
  for a 6-minute one, and must not fire at all while the window's request count is below the rule's minimum
  (FR6).
- **AC10** — A service that stops sending telemetry must open a *went silent* incident after the configured
  silence, and resolve it when telemetry resumes.

**Noise**

- **AC11** — A condition that stays breached for 2 hours with no re-notification interval must produce
  exactly two notifications per channel: opened and resolved (BR1).
- **AC12** — A check alternating down and up more than 4 times in an hour must produce one *flapping*
  notification and no further open/resolve notifications until it has stayed up for 30 minutes (FR11).
- **AC13** — Five checks in one project going down within 30 s must produce one grouped message per channel,
  and five incidents (FR13).
- **AC14** — Fifteen incidents opening within one minute for one channel must produce at most 10 messages and
  one summary naming the other 5 (FR12).
- **AC15** — A warning raised during a channel's quiet hours must be delivered, in a digest, when they end;
  a critical alert raised during them must be delivered immediately (BR4).
- **AC16** — A muted rule must send nothing while muted and nothing afterwards for what fired during the mute
  (BR5); the incident must still appear in the console.

**Delivery**

- **AC17** — A receiver answering 503 twice and then 200 must receive the notification once, and the log must
  show three attempts.
- **AC18** — A receiver failing for more than an hour must leave the channel *failing* and the organization's
  owners must receive one email saying so; the first later success must return it to *healthy* (FR21).
- **AC19** — Restarting the scheduler, worker or API between a condition firing and its delivery must not
  lose the notification (NFR2) and must not produce a second *opened* for it (NFR3).
- **AC20** — An alert sent to PagerDuty must carry the incident's `dedup_key`, and the incident's recovery
  must send a `resolve` event with the same key (US21.9).

**Defaults and console**

- **AC21** — A new organization's first project must have the default rule, and its first check going down
  must email the owner without any further configuration (US21.1, FR8).
- **AC22** — Deleting the default rule must not recreate it (FR8).
- **AC23** — Every message must link to its evidence, and the link must open that evidence for a signed-in
  member of the organization (FR16).
- **AC24** — A message must contain no request or response body, header value, secret or stack-trace content
  from the evidence it links to (BR9, FR18).
- **AC25** — Every state in `docs/design/principles.md` rule 3 must be handled on the Alerts screen, the
  channel screens and the rule editor, including a channel that is failing and a rule notifying nobody.
- **AC26** — Every rule and channel change must write an audit row with no secret in it (FR26).
- **AC27** — A check selected for *degraded* and *down* going degraded then down must stay one incident and
  produce one *opened* (warning) and one *escalated* (critical) notification per channel; PagerDuty must
  receive both events with the same `dedup_key` (FR10).

## Dependencies and constraints

- **F18 — synthetic checks** is the first input: `check_state_changes` and the `record_outcome` task.
  **Drift found while specifying:** F18's `design.md` lists *paused* and *maintenance* as recorded state
  changes, but the code records only up, degraded, down and misconfigured — *paused* and *maintenance* are
  computed for display (`checks/service.py`). BR2 and BR3 are written against what the code does; F18's
  design is corrected in the same change as F21's design.
- **Email** reuses the account-email port (`notifications/`). It lives only in the API process today, with
  a closed `Message` enum and plain-text templates, and sends one recipient per call — the design must
  decide where alert email is sent from.
- **The SSRF guard** (`probes/addresses.py`, `ConnectGuard`) is the scheduler's; the API only validates
  literal addresses (`checks/targets.py`). BR7 holds wherever delivery runs.
- **Sealed secrets** (`nexovue_platform.checks.sealing.SealedBox`, ADR-010) are reusable with their own
  *purpose*. ADR-010 rejected sharing a key across unrelated kinds of secret; whether channel secrets get
  their own key is a design decision, and the missing key-rotation procedure (production readiness,
  blocker 8) applies to it equally.
- **Roles:** writes take `MayAdminister`, reads `CurrentIdentity`, as every other settings surface. Until F26
  there is one person per organization, so "the organization's owners" is usually one address.
- **Rate limits:** *send test* makes our infrastructure contact an address the customer chose, so it gets its
  own tier (`ratelimit/policy.py`, beside `RUN_NOW_TIER`).
- **Contradicts the catalogue line for F21**, which promises WhatsApp and SMS and names Opsgenie among the
  connectors. Recorded as D6; the catalogue is updated when this spec is approved.
- **The vision's onboarding promise** — "auto-create … alert presets" — is met by FR8 for checks only.

## Decisions taken

Accepted by the founder with the specification, 2026-09-18.

- **D1 — The first version's conditions** are the four in *Scope*: check state, new error group, service
  error rate and p95, service went silent. Metric-query and log-search alerts wait for Phase 2 with anomaly
  detection (F6).
- **D2 — Slack through incoming webhooks**, not a Slack app: identical hosted and self-hosted, no per-deployment
  app registration. Threads, buttons and message updates wait for a Slack app, which needs no change to rules
  or incidents.
- **D3 — Every new project gets one default rule**: any check down or recovered → email the owners. None for
  errors.
- **D4 — Channels belong to the organization; rules to a project.**
- **D5 — Every channel on every plan.** No channel is behind a paywall; alerting is part of what a monitored
  service gets.
- **D6 — WhatsApp, SMS and a native Opsgenie connector are dropped** from the F21 promise. SMS and phone reach
  a person through PagerDuty; Opsgenie's successor takes the generic webhook. `feature-catalog.md` is
  updated to match.
- **D7 — Quiet hours hold warnings, never criticals** (BR4).
- **D8 — An incident that ends without recovering tells the machines, not the people** (2026-09-19). When a
  rule is deleted, disabled or narrowed, or its check or project is deleted, its open incidents close, since
  nothing will evaluate them again. Every PagerDuty and webhook channel that was sent the opening is sent a
  *closed* notification — a *resolve* to PagerDuty, `incident.closed` to a webhook — so the alert does not
  stay open there for ever. People are not told: nothing recovered, and a person made the change.
- **D9 — A message links to its incident, not to its evidence** (2026-09-19, amending FR16). The link went
  to the check; a check deleted after the alert turned every message about it — email, Slack, PagerDuty,
  webhook — into a link to "No such check". An incident is never deleted with its subject, so its page
  (`/alerts/incidents/{id}`) is what a message links to: what broke, when, for how long, who was told,
  and the evidence one link on — or that the check has since been deleted
  (`docs/changes/alert-links-to-incident/`).

## Open decisions

None.

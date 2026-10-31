# Feature: Anomaly detection (F6)

## Status

**Approved** by the founder, 2026-10-30, as revision 2, including D5 to D10. Stage 1 of
[`phase-2-plan.md`](../../product/phase-2-plan.md). The design is approved (revision 2), and no code exists
yet. Amended through D32: the latest amendments come first below.

**Amended 2026-11-01** after six persona reviews of the spec, design and plan, with the founder's decisions
D22 to D32:

- adoption limits (FR21);
- the signup time zone (FR24);
- telling the owners when detection stops (FR25);
- member snooze (FR22, FR23);
- burst tolerance capped below critical;
- endpoint criticals;
- the member false-alarm rule (BR3, FR20);
- a new signal joining a finding notifies (FR28, AC19);
- a stop caught at any traffic (FR6, FR32);
- deferrals (UR3, UR4, FR18, FR30);
- AC29's scale.

**Revision 2, 2026-10-30.** Rewritten after four independent reviews (on-call, statistics, architecture,
product and QA), and with the founder's answers to nine decisions (*Decisions taken*). The first draft
fixed a formula that the reviews showed to be unsound. This revision states the behaviour the product owes
the user, as acceptance criteria. The exact statistics move to the design, where they must be simulated
against the false-alarm budget before approval (FR9).

**Amended 2026-10-31** with the approved design: FR28, FR29 and AC19 (the design's *Specification
impact*).

**Amended 2026-10-30** after five independent reviews of the design, with the founder's decisions D11 to
D14. The changes are:

- FR23 and AC34: who may change detection;
- the finding page in scope (FR31, AC38 to AC40);
- a service that stops entirely (FR32, AC41);
- learning counted in days (FR4, FR6, AC13, AC14);
- the traffic floor set per sensitivity (*Product values*);
- the feed's healthy state (FR25, AC42);
- calibrating the method on the demo (FR9).

Then D15 and D16, after the harsher simulation: measure the demo before the design is approved, and give
quiet services 20 minutes. On 2026-10-31, D17 replaced D15: the method adapts to each service's
burstiness, and the measurement moves to the plan's demo run.

## Words used here

- **Signal**: one thing measured about a service. There are three:
  - *slower*: its slowest 5% of requests are slower than usual (in engineering terms, the 95th percentile
    of latency, or *p95*);
  - *failing more*: a larger share of its requests fail;
  - *losing traffic*: fewer requests arrive than usual.
- **Normal (baseline)**: what a signal usually reads at this time of day on this day of the week, learned
  from the service's own past.
- **Finding**: the record that says a service is not normal. It is the one record for one problem.
- **Rollup**: the per-minute totals the worker writes for every service and endpoint
  (`endpoint_stats_1m`). The worker counts every request before it throws most traces away (sampling),
  so the counts are exact.
- **Settled minute**: a minute the pipeline has finished writing (BR5).

## Problem

nexovue cannot say "this is not normal for this service". It can only say "this crossed a number
someone typed".

- **Every judgement today is a fixed number.** The scheduler's threshold rules fire when a service's
  error rate is at or above a typed percentage, or its p95 at or above a typed number of milliseconds
  (`apps/scheduler/src/nexovue_scheduler/alerts/thresholds.py`, `judge()`). The service list colours a
  service by fixed 1% and 5% error-rate bands "rather than a service's own baseline"
  (`apps/web/src/features/services/severity.ts`). A checkout that normally runs at 80 ms and now runs at
  400 ms is invisible unless someone guessed 400.
- **The findings screen has nothing behind it.**
  - `apps/web/src/features/findings/api.ts` opens with "Nothing produces findings" and answers with an
    empty feed by design.
  - The vision's whole chain starts with detection (`vision.md` §4).
  - So every later link, from explanation and cases to the chatbot and the fix, has no starting point.
- **A traffic drop has no signal at all.** `service_silent` (F21) sees a service that stops entirely.
  Nothing sees one whose traffic halves at an hour when it is normally busy.

Customers compare this with Datadog, New Relic and Grafana, which all sell detection against a seasonal
normal. Datadog sells it on its enterprise plan only. Datadog documents two weaknesses this specification
has to answer:

- a signal without history gives poor results;
- a short trigger window gives false alarms.

New Relic says a stable baseline needs one to four weeks of history (*Research*).

## Goal

When a service gets slower, fails more often, or loses traffic compared with its own normal for that hour
of that day, one finding says so in plain language within minutes. Nobody has to set a threshold.

- **It is explained.** The finding states what normal was and how far off the service is.
- **It looks after itself.** It updates while the problem continues and closes itself when the problem
  ends.
- **It never guesses.** A new or quiet service stays silent and says why.
- **False alarms stay rare.** They stay under a measured budget (the plan's starting bar, at most 1 per
  service per week), and every one a user reports is counted.

## Target users

- **P1 Dev**: owns services and gets paged. Wants to hear about a slow checkout without having guessed its
  threshold, and to say "false alarm" or "be less sensitive" without filing a ticket.
- **P2 Indie**: one person who never configures anything. Detection has to be correct with no settings
  touched, and must never leave them with a finding that stays open forever because they did not press a
  button.

**Not served:** P3 Owner, who learns of problems through F7's status page. **Not served either: a data
scientist** who wants to choose algorithms or see model parameters. Sensitivity is a three-step choice.

## Scope

1. **Three signals per service**: *slower*, *failing more* and *losing traffic*, read from
   `endpoint_stats_1m`. *Failing more* is also judged for each endpoint busy enough to judge (FR2).
2. **A normal for each signal**, learned from the service's own history for the same time of day and day
   of the week, in the project's time zone.
3. **A detector** that opens a finding only on a sustained deviation that is both statistically unusual
   and large enough to matter.
4. **Findings opened, updated and closed** by the detector. A finding is the one record for one problem.
   F1 shows it, F21 routes its notifications, and Stage 3 grows it into a case.
5. **The user's controls**: sensitivity, mute, acknowledge, false alarm, and "this is the new normal".
6. **Visible baselines and states**: each signal's normal band on the service screen, and each service's
   detection state.
7. **A detection condition for alert rules**, and critical findings in each new project's default rule.
8. **A project time zone.**
9. **The test bed run** from the plan: injected faults caught and scored, and a no-fault run counting
   false findings.

## Out of scope

- **Machine learning.** Decision 0013 settles that version 1 is statistical. The test bed's gaps are
  recorded as the requirements for what replaces it (FR27).
- **Explanation, probable cause and AI wording**: F28 and F3, Stage 2. A finding says *what* changed,
  never *why*. The plan's "stated cause" score in the test bed is Stage 2's.
- **The feed screen**, its ordering, filters and live updates: F1. This feature produces the findings it
  shows.
- **The finding page beyond version 1.** Traces, logs, deploys and the AI cause belong to F2 and Stage 2.
  Version 1, the detector's own evidence, is in scope (FR31, D12).
- **Cases**: Stage 3, which grows a finding into a case.
- **Other signals**: logs, customer metrics, CPU, memory, browser vitals, dependency edges. Synthetic
  checks already alert through F18 and F21, and 0013's absolute rules exist as F21's threshold
  conditions.
- **Traffic spikes.** More traffic is not a problem by itself. If it hurts, *slower* or *failing more*
  shows it.
- **Holidays and calendars.** A public holiday can look like a traffic drop. False alarm and mute are the
  version 1 answers.
- **Customer-typed detection numbers.** F21's threshold rules stay for anyone who wants a number.
- **Backfill.** Detection judges minutes from when it starts, never history.

## User requirements

- **UR1**: A developer whose service gets slower, fails more or loses traffic sees one finding saying so,
  with no setup.
- **UR2**: The finding says what normal is and how far off the service is, in plain words: "Checkout is 4×
  slower than normal since 15:04 (usually about 80 ms on a Tuesday afternoon, now 330 ms)."
- **UR3**: A developer can see, for every service, whether nexovue is judging it, and if not, why and until
  when. In version 1 this is on each service's screen. A column on the services list is deferred (D32).
- **UR4**: A developer can see a service's normal band on its charts and when it left it. *Deferred to after
  version 1 (D32): the finding page states the normal and draws its band.*
- **UR5**: A developer can acknowledge a finding, so the team sees someone is on it.
- **UR6**: A developer can mark a finding "false alarm", with an optional reason.
- **UR7**: A developer can say "this is the new normal" after an intended change.
- **UR8**: A developer can make detection less or more sensitive, for a project or one service.
- **UR9**: A developer can mute detection for a service until a time they choose.
- **UR10**: A solo developer who sets nothing up is emailed when a service has a critical problem.

## Business rules

- **BR1: Detection never waits for AI and never depends on it.** With AI off or failing, findings open,
  update, close and notify exactly as with it on (`phase-2-plan.md`, *Order*).
- **BR2: One problem is one record.** At most one open finding per service. It lists every signal that
  moved and every endpoint that was judged failing. Alert rules route its notifications. They never
  create a second record for it. A threshold or *went silent* incident on the same service is linked to
  the finding, not merged into it.
- **BR3: An outage cannot teach a false normal.** Minutes inside a finding never enter the baseline,
  with one exception: a finding an admin marked false alarm, whose minutes the admin has said were normal
  (FR20, D28). Snoozed minutes also stay out (FR22).
  The baseline moves to a new level on purpose only through "new normal" or adoption (FR21).
- **BR4: Not judged is said, never guessed.** A signal without enough history or traffic is not judged.
  The product says which reason applies and when judging can start. It never shows such a signal, or a
  project whose detection is not running, as healthy.
- **BR5: Only settled minutes are judged, and a judged minute stays judged.**
  - A minute is judged only once the trace pipeline has written past it on every partition.
  - Its reading is frozen when it is judged. Data that arrives later never changes a judgement already
    made.
  - So nexovue's own delay is never reported as the customer's traffic drop.
- **BR6: Baselines and findings belong to one project.** Every read and write carries the tenant
  predicate. Another project's traffic never enters them.
- **BR7: User actions never change stored telemetry.** They change judgements from then on.
- **BR8: Feedback is counted, never applied automatically.** A false-alarm mark is counted and shown, and
  may suggest "lower sensitivity for checkout". It never changes sensitivity or a baseline by itself.
- **BR9: Mute means what it means in F21: nothing is sent, during the mute or after it.** A muted
  service's finding is still recorded, marked muted, and shown under *muted*. Muted minutes outside a
  finding are ordinary history.

## Functional requirements

### What is judged

- **FR1**: A service's three signals are judged on all of its inbound requests together.
- **FR2**: *Failing more* is also judged for each endpoint of the service that carries enough traffic to
  judge. This way a payment endpoint with 1% of the service's traffic cannot fail completely unseen. A
  failing endpoint joins the service's one finding (BR2). It never opens a finding of its own.
- **FR3**: Each finding names its evidence endpoint:
  - for *failing more*, the endpoint with the largest share of the extra failures;
  - for *slower*, the endpoint with the largest increase in slow requests;
  - each against the same period last week.

### The normal

- **FR4**: A signal's normal comes from the service's past at the same time of day. It pools enough
  neighbouring minutes that one past value never decides it.
  - Enough history is counted in **distinct past days**, not in values: at least 3 days of the matching
    kind remain after BR3's exclusions.
  - If the weekly pattern loses its days to exclusions, the signal falls back to the daily pattern for that
    minute, rather than stopping.
  - With fewer than 3 days, the signal is *learning*.
- **FR5**: Days are matched by pattern.
  - Until a service has 3 weeks of history, weekdays are compared only with weekdays, and weekends only
    with weekends.
  - From 3 weeks, the same weekday is used.
  - At most the last 4 weeks are used. That fits every plan's rollup retention: Free keeps 30 days
    (`apps/api/migrations/versions/0005_plans_table.py`).
- **FR6**: *Losing traffic* is judged only once the weekly pattern exists (3 weeks). *Slower* and *failing
  more* can be judged from 3 days of history. A fall to zero is the exception: it is judged from day 3, at
  any expected traffic (FR32, D30). Before then, minutes when traffic was clearly far below its
  daily normal still stay out of the baseline (BR3), even though no finding opens for them.
- **FR7**: Time of day and day of week are taken in the **project's time zone** (FR24). A daylight-saving
  change therefore never shifts the normal.
- **FR8**: The current value and the normal are measured the same way: the same window length and the
  same aggregation.

### Judging

- **FR9**: A reading is **unusual** only when **both** of these hold:
  - it is statistically unusual against its normal;
  - its change is at least the sensitivity's minimum size (*Product values*).

  The design states the statistical test and its parameters. Before the design is approved, it must show
  by simulation that a service following its own normal produces no more false findings than the budget
  (*Product values*). The simulation includes per-endpoint tests, drifting failure rates, failures in
  multi-minute bursts at a range of rates (D17), and latency with a slow tail. Before the cut-offs are
  frozen, the method is checked against the demo's measured variability (D14).
- **FR10**: **Failing more is judged on counts.** An increase counts only with at least the minimum number
  of failures, and only if that many failures is unlikely at the service's normal rate. Two failures in
  four requests is not a 50% failure. A service whose normal failure rate is zero is not alarmed by a
  single failure.
- **FR11**: **Traffic floors are on what is expected, not on what arrived.**
  - *Losing traffic* is judged whenever the normal expects enough traffic, however little actually
    arrives. A fall to near zero is its strongest case, not a gap.
  - *Slower* and *failing more* are judged only when enough requests actually arrived (*Product values*).
    Below that, the signal reads *too little traffic*, and an open finding is neither closed nor extended.
- **FR12**: **The wait before opening is counted in minutes of evidence.** One unusual minute, however
  extreme, never opens a finding by itself. A finding opens only when at least 80% of the sensitivity's
  sustain window has been unusual: 4 of 5 minutes at normal, 8 of 10 at low, and 3 of 3 at high. One
  ordinary minute inside a real slowdown therefore does not restart the wait (founder, 2026-11-01).
- **FR13**: A finding **closes** once all of its signals have been normal for the recovery time. A
  deviation that returns before then continues the same finding. A finding none of whose signals can be
  judged for 6 hours in a row closes as **could no longer be judged**, with one message. Its minutes stay
  out of the normal (D20).
  - A signal with no judgeable minute for 6 hours stops holding its finding open. The finding closes when
    each of its signals has either recovered or gone unjudged that long.
- **FR14**: A finding's severity is **warning**, or **critical** at the critical size (*Product values*).
  It can rise while the finding is open, and never falls.
- **FR15**: **Many services at once is one problem.** When most of a project's services lose traffic in
  the same minutes, the product opens one project finding instead of one per service. It says: "Traffic
  stopped arriving from 9 of 11 services at 14:02. Check your collector or network."

### Product values

These are the values a user can feel. Each is a starting value, tuned from the test bed under the founder's
decision. Acceptance criteria name a value, never its number, so tuning changes only this table. The
statistical parameters behind FR9 are the design's.

| Value | Low | Normal (default) | High |
|---|---|---|---|
| Sustain before opening | 10 min | 5 min | 3 min (0013's floor) |
| Recovery before closing | 10 min | 10 min | 10 min |
| Minimum change, *slower* | 2× normal | 1.5× | 1.25× |
| Minimum change, *failing more* | +5 points and ≥ 10 failures | +2 points and ≥ 5 failures | +1 point and ≥ 5 failures |
| Minimum change, *losing traffic* | ≤ 30% of normal | ≤ 50% | ≤ 70% |

| Value | All sensitivities |
|---|---|
| Critical | *slower* ≥ 4× normal; *failing more* ≥ 20% of requests; *losing traffic* ≤ 10% of normal |
| Requests needed to judge *slower* | 200 in the window. Fewer gives an unreliable p95: ±30% at 20 requests |
| Requests needed to judge *failing more* | 20 in the window |
| Expected requests needed to judge *losing traffic* | The least traffic at which the sensitivity's minimum drop can be seen at all: about 20 (low), 47 (normal) and 150 (high) in the window. Below it, *losing traffic* reads *too little traffic* |
| Adoption of a lasting change | 7 days |
| False-alarm budget | 1 false finding per service per week, over a run: false findings ÷ (services × weeks) |
| Time to a finding, beyond critical | 12 minutes at normal sensitivity |
| Time to catch a fault past the minimum, at normal sensitivity | 12 minutes for a service at 200 requests a minute or more; 20 minutes below that, where too few failures arrive to be sure sooner (D16) |

### Findings

- **FR16**: A finding carries the following, which F1 and F2 display:
  - the project, the service, and the signals and endpoints involved;
  - when it started, its severity, and its state: open, acknowledged, closed (with how it closed), or
    muted;
  - for each signal, the normal value and band, the current value and the worst value;
  - the comparison used ("Tuesdays around 15:00, last 3 weeks");
  - its links to any threshold or *went silent* incident on the same service;
  - feedback.
- **FR17**: The title follows F1's AC1.3: subject, then what changed, then magnitude, then since when. It
  uses no metric names, IDs or the term p95. A failure rate is given as two rates, never a multiple. With
  several signals, the worst one leads: "Checkout is failing 3.2% of requests, against 0.4% normally, since
  14:10 IST, and is slower."

### The user's controls

- **FR18**: **Sensitivity**: low, normal or high, per project. A per-service override is deferred to after
  version 1 (D32). A change applies from the next judged minute. An open finding is judged on the new level from then on, and can
  close under it.
- **FR19**: **Acknowledge** marks an open finding as being handled, by whom and when (F1 AC1.8). It does
  not close it. Findings close themselves (FR13), or by false alarm, new normal or adoption.
- **FR20**: **False alarm** closes the finding, with an optional reason, and counts it. Its minutes return
  to normal history (below) only when an admin marks it. A member's mark closes and counts the finding,
  but leaves its minutes out, so a member cannot widen the normal (D28).
  - On an open finding, the same signals cannot reopen a finding until they have been normal for the
    recovery time.
  - Its minutes are treated as normal history. The user has said they were normal.
  - One *resolved* message is sent.
- **FR21**: **This is the new normal** closes an open finding.
  - Its signals' baselines move to the new level and keep their daily and weekly shape, so judging
    continues with no learning gap.
  - The same thing happens by itself after the adoption time if nobody acts. The exceptions are a service that
    has stopped entirely (FR32), a critical finding, and any rise in failures. These are never adopted
    (D22). Adoption always emails the owners, whatever the rules, and its message says so:
    "Checkout's new normal is 1.6× slower. Adopted after 7 days."
  - Both are written to the audit log.
- **FR22**: **Mute** a service until a time no more than 30 days ahead (BR9). **Snooze** a service for at
  most 4 hours: nothing is sent, and the snoozed minutes stay out of the normal (D25).
- **FR23**: Sensitivity, mute and new normal need an admin. Snooze is open to every role that may operate
  alerts (D25). So does creating, changing, muting or deleting an
  *anomaly finding* alert rule, the default one included. Otherwise anyone refused a mute could silence
  detection through its rule. Acknowledge and false alarm are open to every role that may operate alerts.
  A read-only viewer may do neither.
  - *Corrected 2026-10-30:* revision 2 said "as alert rules do". It does not hold: today every working
    member may change and mute alert rules (`apps/api/src/nexovue_api/auth/permissions.py`, `OPERATE_ALERTS`),
    and the router's "changes are an admin's" comment is stale (D11).
- **FR24**: Each project has a **time zone** (an IANA name). It defaults to the creating user's
  `users.timezone`. A user's zone is taken from the browser at signup (D23). UTC applies only when neither is
  known. An admin can change it. A change re-reads history in the new
  zone and starts no learning period.

### The finding page

- **FR31**: Each finding has a page of its own (version 1):
  - each signal's readings drawn against its normal band;
  - the normal, current and worst values, and the comparison used;
  - the evidence endpoint and the linked incidents;
  - its feedback;
  - the actions FR19 to FR22 allow the viewer.

  F2 later widens it with traces, logs, deploys and the AI cause. A closed finding stays readable after its
  readings have expired, showing its stored values without the chart (D12).

### A service that stops

- **FR32**: A service whose traffic stops entirely opens a critical *losing traffic* finding, like any other
  drop. If it stays silent for 24 hours, the finding closes as **service gone**, with one message saying so.
  It is never adopted as a new normal. When it sends again, it is judged against its old normal. A renamed
  service is therefore one finding closed as gone, while the new name is *learning* (D13). A stop is judged
  from day 3, at any expected traffic, so a small service's outage at night is caught (D30).
  - *Limit:* an empty window is told apart from chance only when its normal expects about 6.5 requests in
    the window, or more when the service's traffic varies. That is about 0.7 a minute at low, 1.3 at
    normal and 2.2 at high, whose window is shortest. Below it, no test can tell a stop from a quiet
    spell.

### States, measurement and notification

- **FR25**: When detection has not judged a project for 15 minutes, its owners are emailed once, and once
  more when it runs again (D24). Every service shows its detection state: *learning* (until a date), *judged*, *too little
  traffic* (per signal), *data delayed*, *muted*, or *detection not running*. When detection's last pass is
  older than 3 minutes, the project shows *detection not running*, and F1's healthy state must not say
  "all systems normal". The feed says what is true instead:
  - while every service is learning: "Learning your services' normal. Judging starts on 3 Nov";
  - while some are: "All judged services are normal. 2 are still learning";
  - while data is delayed or detection is not running: which of the two, and since when.
- **FR26**: The product counts, per project and per service, findings opened, false-alarm marks, and the
  false-finding rate against the budget, readable through the API.
- **FR27**: The test bed records every fault that is a gap (AC36), with the flag, and the service and
  signal it was expected to move. This list is the gap record 0013 asks for.
- **FR28**: F21 gains a condition kind, **anomaly finding**, which selects all services or a list, and a
  minimum severity.
  - A matching finding sends one message when it opens, one when it turns critical, one when a new signal or
    endpoint joins it (D29), and one when it closes. Nothing is sent for other updates, except F21's single
    *flapping* message after a fifth opening within an hour.
  - A finding linked to an incident that already reached the same PagerDuty channel adds a note to that
    PagerDuty incident instead of opening a second one (D29). The same holds when the finding was sent
    first and the incident second (D35).
  - It uses F21's channels, outbox, burst cap and quiet hours.
  - The finding's severity is the detector's, not the rule's. This amends F21's BR10 for this condition
    kind.
- **FR29**: Each new project gets a **second default rule**, *Critical problems*. It sends critical findings
  to the owners. A rule has one condition kind, so the existing default rule for checks going down cannot
  carry it. Warnings appear in the feed only, until a rule asks for them (amended with the approved
  design).
- **FR30**: Each service screen shows its detection state and its findings on the timeline. The normal band
  behind its charts is deferred to after version 1 (D32).

## Non-functional requirements

- **NFR1: Time to a finding.** For a deviation beyond critical, the finding must exist within the
  *time to a finding* value of the fault's start. That time covers settling, the window filling and the
  sustain time.
- **NFR2: The minute pass keeps up.** At 200 projects and 2,000 judged services on the 16 GB install, every
  service must be judged within 90 s of its minute settling. That leaves 30 s of margin in each minute's
  work, inside the scheduler's container ceiling (D32). A pass that overruns is
  skipped and recorded (`apps/scheduler/src/nexovue_scheduler/sweeps.py`). It is never queued behind
  itself.
- **NFR3: Restart-safe.** A scheduler restart or change of leader must never duplicate a finding or a
  message. It must not lose a sustain wait by more than the restart's own duration.
- **NFR4: Explainable from stored data.** Every number in a finding can be reproduced from the frozen
  readings and baselines stored when it was judged (BR5), including which minutes BR3 left out.
- **NFR5: Read cost.** A pass must not re-read four weeks of minute rows per service. The design states and
  measures what one pass reads.

## Error cases

| What happens | What the user sees | Can they recover? | Distinguishable from normal? |
|---|---|---|---|
| New service, under 3 days of history | *Learning*, with the date judging starts. Traffic drops wait for 3 weeks | Wait | Yes: never shown as healthy |
| Too little traffic for *slower* or *failing more* | *Too little traffic* on that signal | Nothing to do | Yes: shown apart from *normal* |
| nexovue's pipeline is behind, or one partition is | No finding from the delayed minutes. *Data delayed* | Automatic, on catch-up | Yes (BR5) |
| Spans arrive late, after their minute was judged | Nothing changes: the judgement stands | n/a | n/a (BR5) |
| The scheduler is down | *Detection not running*. No "all normal". On return, judging resumes from now and nothing old is sent | Automatic | Yes (FR25) |
| The customer's collector dies | One project finding: traffic stopped from most services (FR15) | Fix the collector | Yes |
| A service hangs and stops answering | It reads as *losing traffic*, not *slower*, because unfinished requests are not yet reported | Fix the service | Partly: the finding says traffic fell |
| A deploy changes the service on purpose | A finding, until "new normal" or adoption | New normal (FR21) | n/a |
| A holiday looks like a traffic drop | A *losing traffic* finding | False alarm, or mute | No: version 1's stated limit |
| A finding is wrong | The finding | False alarm (FR20) | n/a |
| A mute ends while the problem continues | Nothing is sent, as in F21. The finding shows in the feed as muted until it closes | Read the feed | Yes (BR9) |
| An admin changes the time zone | Normal bands shift to the new local clock. No learning period | n/a | n/a |
| A service is renamed or retired | The old name's finding closes as *service gone* after 24 hours (FR32). The new name is *learning* | Wait | Yes |
| A plan downgrades to Free | History beyond 30 days goes. Four weeks still fit | n/a | n/a |
| AI is off or failing | The same finding, without explanation | n/a | Yes (BR1) |

## Acceptance criteria

Each criterion uses synthetic per-minute series written to `endpoint_stats_1m`, unless it names the test
bed. *Sustain*, *recovery*, *critical* and the other values are those in *Product values*.

**Quiet when it should be**

- **AC1**: A service with a steady four-week daily and weekly pattern, with ordinary minute-to-minute noise,
  must produce no finding over a simulated week. The test runs at its busiest and quietest hours.
- **AC2**: A single minute of any size (100% failures, or 10× slower), with normal minutes around it, must
  open no finding at any sensitivity (FR12).
  - A spike right after a natural small burst is not "with normal minutes around it". The two together
    can open a finding. The simulation saw this once in 1,196 spikes (D21).
- **AC3**: A business service whose weekend traffic is 15% of its weekday traffic must produce no finding
  on day 10 (a Saturday) or day 17. It must show *learning* for *losing traffic* until day 21 (FR5, FR6).
- **AC4**: A window with 4 requests and 2 failures on a service whose normal failure rate is 1% must open
  nothing. A service whose normal failure rate is 0 must not open a finding for one failure (FR10).
- **AC5**: A service below *requests needed to judge slower* must show *too little traffic* for *slower*,
  and open no *slower* finding. A finding open before such a gap must still be open after it, with no
  message in between (FR11).
- **AC6**: A very steady service whose p95 moves from 80 ms to 84 ms must open nothing (FR9's minimum
  change).
- **AC7**: A batch job whose minute-long spike starts up to 3 minutes earlier or later each day must open
  nothing (FR4).

**Catching what it should**

- **AC8**: A service whose p95 rises past the normal-sensitivity *slower* minimum for 80% of the sustain
  window (4 of 5 minutes) must open exactly one finding. For one minute less (3 of 5), it must open none.
  The same holds at low (8 of 10) and high (3 of 3) (FR12).
- **AC9**: A service whose traffic falls from its normal to 5% of normal must open a critical *losing
  traffic* finding within *time to a finding* (FR11, NFR1).
- **AC10**: A service with 3 weeks of history at its normal Monday 09:00 traffic must open nothing. The
  same service at its Sunday 09:00 level on a Monday must open a *losing traffic* finding.
- **AC11**: An endpoint carrying 1% of a busy service's traffic that fails completely must appear in the
  service's finding as failing, with the endpoint named (FR2, FR3).
- **AC12**: Each of the three signals, at a deviation beyond critical, must have its finding within *time to
  a finding* (NFR1).

**Baselines**

- **AC13**: A service with the same 6-hour outage on two of three Tuesdays must, on the fourth Tuesday,
  produce a finding for the same values, falling back to the daily pattern for those hours (FR4). The
  baseline used must equal the one computed without the outage minutes (BR3).
- **AC14**: A service with under 3 distinct days of history must open nothing, whatever its values, and
  show *learning* with its start date (BR4, FR4).
- **AC15**: After "new normal" on a *slower* finding, the same latency must open nothing. Judging must
  continue at once, and the baseline must keep its daily shape at the new level (FR21).
- **AC16**: A service that stays 1.6× slower for 7 days with nobody acting must:
  - close its finding as adopted, with one message saying so;
  - open a new finding when it then reaches 3× its new normal (FR21).
- **AC17**: A project in a time zone with daylight saving, whose traffic follows local time, must produce
  no finding in the three weeks after a clock change (FR7).

**One record, one message**

- **AC18**: A deviation lasting 2 hours, on two signals and three endpoints, must be one finding whose
  duration and worst values update (BR2, F1 AC1.9).
- **AC19**: An *anomaly finding* rule on a Slack channel must send:
  - one message when AC18's finding opens;
  - one when it turns critical;
  - one when a new signal or endpoint joins it;
  - one when it closes;
  - and nothing else, apart from F21's flapping message if the finding opens a fifth time within an hour
    (FR28).
- **AC20**: A threshold rule and detection that both fire on the same service must produce one finding
  linked to one threshold incident, not two findings (BR2).
- **AC21**: Traffic stopping from 9 of 11 services in the same minute must open one project finding, not 9
  (FR15).
- **AC22**: A new project with no rules set up must email its owners one *opened* message and one *resolved*
  message for a critical finding. It must send nothing for a warning. A warning that later turns critical
  sends its critical message and then its *resolved* message (FR29).

**Safe under failure**

- **AC23**: The things below must open no finding, and must show *data delayed* while they last (BR5):
  - a worker stopped for 10 minutes;
  - a single partition held 3 minutes behind.
- **AC24**: Spans arriving 30 minutes late for an already judged minute must not change that minute's
  stored reading or any finding (BR5).
- **AC25**: Killing the scheduler leader while a sustain wait is running, then starting another, must
  produce exactly one finding and one message per channel (NFR3).
- **AC26**: With the scheduler stopped for 5 minutes, the project must show *detection not running*. F1's
  healthy state must not say "all systems normal" (FR25).
- **AC27**: With AI disabled, AC8's finding must open, notify and close identically (BR1).
- **AC28**: Project A's traffic must never appear in project B's baselines, findings or counts. The
  tenancy structure tests must pass for every new query and table (BR6).
- **AC29**: At 200 projects and 2,000 judged services, every service must be judged within 90 s of its minute
  settling, inside the container's ceiling. The run must be recorded with the rows read, the memory used and
  the storage used (NFR2, NFR5).

**Controls**

- **AC30**: Changing a service from normal to low sensitivity must stop AC8's rise from opening a finding,
  from the next judged minute. A finding already open must close under low, after *recovery*, once its window
  no longer passes low's minimum change (FR18).
- **AC31**: A muted service with AC8's deviation must record a muted finding. It must send nothing during
  the mute or after it ends, and show the finding under *muted* (BR9).
- **AC32**: Marking an open finding "false alarm" must:
  - close it, with one *resolved* message;
  - keep the same signals from reopening a finding until they have been normal for *recovery*;
  - count one false alarm;
  - change no sensitivity (FR20, BR8).
- **AC33**: Acknowledging a finding must record who and when, and leave it open (FR19).
- **AC34**: A member who is not an admin must be refused sensitivity, mute and new-normal changes. They
  must also be refused creating, changing, muting or deleting an *anomaly finding* rule, and changing a rule
  to or from that kind. They must be allowed to acknowledge, snooze and mark a false alarm; their false alarm
  must leave the minutes out of the normal. A viewer must be refused all of these (FR20, FR22, FR23).
- **AC35**: A project created by a user with no time zone must get UTC. Changing it must start no
  learning period (FR24).

**Test bed** (the OpenTelemetry Demo, with traffic shaped over the day and week through
`loadGeneratorVUs` or `tools/loadgen`, after 3 weeks of warm-up)

- **AC36**: A no-fault reference run must first establish, for each fault flag, which services and
  signals the flag moves past the normal-sensitivity minimum. This is done by comparing an injection with
  the same period of the reference run.
  - A flag that moves none of them is a gap (FR27).
  - Every other flag must be injected at least 5 times. Each injection is scored as caught or not, by
    time to catch, and by service and signal. It must be caught on at least 4 of the 5, within *time to
    catch* for that service's traffic (D16).
  - For a service whose own history is bursty (D18), a failure increase no larger, and no longer, than its
    usual bursts is not required to be caught. The false-alarm budget comes first (D21).
  - A fault counts here only when it is clearly past the minimum: its first full window exceeds the
    normal-sensitivity minimum change by at least a tenth of that minimum. For example, traffic at 45% of
    normal or less, where the minimum is 50%. A fault exactly at the minimum is a coin toss by
    construction (D19).
- **AC37**: A no-fault run of at least 2 weeks must stay within the *false-alarm budget*. This criterion
  waits on the test server (`phase-2-plan.md`, Open 2). Two weeks of zero false findings on one service
  bounds its rate only loosely, so the budget is measured across all services.

**The finding page, a stopped service, and the feed**

- **AC38**: A finding's page must show each signal's readings against its normal band, its normal, current
  and worst values, and its comparison sentence (FR31).
- **AC39**: A viewer must see the page with no actions. A member must see *Acknowledge* and *False alarm*
  but not *New normal* (FR23, FR31).
- **AC40**: A closed finding whose readings have expired must still show its stored values, with a note
  that the chart is no longer available (FR31).
- **AC41**: A service whose traffic stops entirely must open a critical finding within *time to a finding*.
  This must hold on a service expecting only 2 requests a minute at low and normal sensitivity, and
  from day 3; at high it needs about 2.2 a minute (FR32, *Limit*). After 24 hours of silence,
  the finding must close as *service gone* with one message, and must never be adopted (FR32, D30).
- **AC42**: A new project whose every service is learning must not say "all systems normal". It must say
  that it is learning, and when judging starts (FR25).
- **AC43**: With detection stopped for 15 minutes, the owners must be emailed once, and once more when it
  runs again. This must hold for a project with traffic whose detection has never run (D34). A healthy project, judged on time, must never show *detection not running* (FR25, D24).
- **AC44**: A finding never adopts when it is critical or a rise in failures. Adoption of any other finding
  must email the owners, even with no matching rule (FR21, D22).
- **AC45**: A new signal joining an open finding must send one message. A finding linked to an incident already
  sent to the same PagerDuty channel must not open a second PagerDuty incident, whichever of the two was
  sent first (FR28, D29, D35).
- **AC46**: An endpoint failing at the critical share of its own requests must make its finding critical
  (D27).

## Dependencies and constraints

- **Decision 0013** (statistics, not ML) stands and is followed:
  - median-style robust baselines over the same hour and weekday, and a sustained deviation of at least 3
    minutes;
  - EWMA, which 0013 also allowed, is not used, because a weighted mean follows an ongoing outage, which
    BR3 forbids;
  - 0013's absolute rules are F21's threshold conditions.
- **`endpoint_stats_1m`** (`deploy/clickhouse/migrations/000008_endpoint_stats_1m.up.sql`) is the only
  source.
  - It is derived before sampling (`apps/workers/internal/pipeline/traceassembly/pipeline.go`).
  - It holds inbound spans only (`derive/endpoints.go`).
  - It has no status-code column: *failing more* uses the span error flag the rollup counts.
  - Its latency is a sample of at most 8,192 values per endpoint per minute
    (`apps/workers/internal/pipeline/latencysample/reservoir.go`). Combining endpoints without weighting
    leans toward quiet ones, so the design must weight by request count.
- **The pipeline has no "written through" mark.**
  - The derive stage drains a minute by the clock, 10 s after it ends
    (`apps/workers/internal/pipeline/derive/counters.go`).
  - Spans are accepted up to 7 days late (`clocktrust.go`, `PastSkew`).
  - BR5 needs the worker to record, per partition, how far it has written. This is new work on the
    workers.
- **Retention**: rollups live 30 days on Free and 395 on paid plans. FR5's four weeks fits Free.
- **Stored readings and baselines are new tables.** They need retention, a place in the erasure inventory
  (`apps/scheduler/src/nexovue_scheduler/erasure/tables.py`) and the tenancy checks:
  - ClickHouse reads go through `nexovue_platform.tenant_statements` (ADR-012);
  - Postgres tables go under `test_postgres_tenancy_structure.py`.
- **F21 is amended in two places**, recorded in its spec when the design is approved:
  - BR10: for *anomaly finding*, severity comes from the finding;
  - its incidents are not used for findings, which are their own record (BR2).

  Its BR5 mute meaning is adopted unchanged. Its channels, outbox (ADR-013, the message written in the
  same transaction as the decision), burst cap and quiet hours are reused.
- **F1 is amended:** AC1.8's "resolve" on a card becomes false alarm or new normal, because findings close
  themselves.
- **One record per problem (BR2) is expensive to reverse.** The design records it as an ADR, together with
  how Stage 3 grows a finding into a case.
- **Fault presets and traffic shaping** do not exist in `tools/otel-demo` or `tools/loadgen`. AC36 needs
  a scripted injector.

## Research

Searched 2026-10-30:

- **Datadog**, anomaly monitors ([docs](https://docs.datadoghq.com/monitors/types/anomaly/)):
  - *Basic* has no seasonality. *Agile* follows level shifts quickly. *Robust* holds steady through long
    anomalies but responds slowly to intended change.
  - Seasonal modes need three periods of history and use up to six weeks.
  - A trigger window that is too short "might get false alarms due to spurious noise".
  - Anomaly monitors require its enterprise plan.
  - BR3 is *Robust*'s shape. FR21 answers its weakness.
- **Grafana Labs**, a PromQL framework:
  - mean ± 2 standard deviations over 1 hour, a daily band offset by 23 h 30 min, and a margin band
    against flat series;
  - [blog](https://grafana.com/blog/2024/10/03/how-to-use-prometheus-to-efficiently-detect-anomalies-at-scale/),
    read through a search summary, because the page could not be fetched from this session.
  - The margin band is FR9's minimum change.
- **New Relic** says 1 to 4 weeks of history stabilise a baseline
  ([docs](https://docs.newrelic.com/docs/alerts/create-alert/set-thresholds/anomaly-detection/)).
- **Failures at low volume:**
  - Datadog gates error-rate alerts on a minimum request count
    ([guide](https://docs.datadoghq.com/monitors/guide/add-a-minimum-request-threshold-for-error-rate-alerts)).
  - Baidu's SRE talk treats requests as binomial trials, because a ratio's spread grows as volume falls
    ([slides](https://www.usenix.net/sites/default/files/conference/protected-files/sre19apac_slides_chen_golden_signals.pdf)).
  - These two are FR10 and FR11.
- **Robust statistics:** Twitter's Seasonal Hybrid ESD ([paper](https://arxiv.org/pdf/1704.07706)) and
  eBay's MEDIFF ([paper](https://ar5iv.labs.arxiv.org/html/2008.09245)) back median-based methods for
  minute-level service metrics.
- **ClickHouse** documents `quantiles` as reservoir sampling with a non-deterministic result
  ([docs](https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/quantiles)). This is why
  NFR4 stores frozen readings.
- **Not found:** any source on keeping incident periods out of a baseline. BR3 is this specification's own
  rule.
- **Review, 2026-10-30.** The statistics reviewer simulated the first draft.
  - A median and spread taken from 3 values flagged about 12% of normal windows. This is why FR4 pools
    neighbouring minutes.
  - Its error settings gave about 3.4 false chances in 1,000 windows at 100 requests, against a budget of
    about 1.7 in 10,000. This is why FR9 requires simulation before the design is approved.

*Product values* are judgement drawn from these sources, not measurement. Until AC36 and AC37 run, nothing
shows they suit this product's traffic.

## Decisions taken

By the founder, 2026-10-30:

- **D1: One record per problem.** The finding is the record. Alert rules only route its notifications, and
  Stage 3 grows it into a case (BR2).
- **D2: A lasting change nobody marks is adopted after 7 days, with a message saying so (FR21).**
- **D3: Faults the three signals cannot see are recorded as gaps**, established from a no-fault reference
  run (AC36). This narrows the plan's "the injected faults are caught" to faults these signals can show.
- **D4: Critical findings are in each new project's default rule.** Warnings stay in the feed (FR29).

Proposed by the agent, 2026-10-30, and approved by the founder with this specification:

- **D5: Services, with each busy endpoint judged for *failing more* (FR1, FR2).** Judging every endpoint
  on every signal multiplies the chances of a false alarm by the endpoint count. Judging only the service
  hides a small endpoint that fails completely. *Failing more* is the signal where a small endpoint
  matters most and counts are the most reliable.
- **D6: A project time zone**, defaulting to the creator's, then UTC (FR24).
- **D7: Feedback never tunes automatically (BR8).** A few impatient clicks could otherwise blind detection
  to a real fault, and nobody could say why it went quiet.
- **D8: Mute is F21's mute (BR9).** One meaning across the product, recorded but never sent.
- **D9: Acknowledge, and findings close themselves (FR19).** A person's "resolve" is false alarm or new
  normal. A finding can therefore never say "resolved" while the problem is still visible.
- **D10: One finding per service, listing every signal that moved (BR2).** One outage on three signals is
  one message, not three.

By the founder, 2026-10-30, after the design reviews, as the agent recommended:

- **D11: Admin only** for sensitivity, mute, new normal and every *anomaly finding* rule. Acknowledge and
  false alarm are for every role that operates alerts (FR23).
- **D12: The finding page, version 1, is built in this feature**, with its own acceptance criteria. F2
  widens it later (FR31).
- **D13: A service that stops entirely** opens a critical finding, and closes as *service gone* after 24
  hours of silence, never adopted (FR32).
- **D14: The method is calibrated on real variability.** The design switches to the sturdier tests the
  statistics review proposed, and simulates them under harsher traffic. The plan's first step then measures
  the demo's real variability, as a gate before the method's cut-offs are frozen (FR9).

By the founder, 2026-10-30, after the harsher simulation:

- **D15: Measure before the design is approved, not after.** *Superseded by D17 on 2026-10-31.* The
  harsher simulation broke the budget: 1.65 false findings per service per week at normal sensitivity.
  The cause was short failure bursts, at a rate that was a guess. D15 asked for the demo's real bursts to
  be measured over at least 3 days before the design.
- **D16: Quiet services get longer.** A service under 200 requests a minute has *time to catch* of 20
  minutes, not 12, for a fault past the minimum (*Product values*, AC36). Below that volume, too few
  failures arrive to tell a real fault from noise sooner without breaking the budget.

By the founder, 2026-10-31:

- **D17: The method adapts to each service's own burstiness, instead of waiting on a measurement.** This
  supersedes D15.
  - *Failing more* learns, from each service's history, how high and how long its ordinary failure bursts
    run. A fault opens a finding when it is higher than those bursts, or lasts longer than them.
  - The design must show by simulation that the budget holds across a range of burst rates, from none to
    the harsh guess. One value is not enough.
  - The demo's real bursts are measured during the plan's demo run, which tunes the cut-offs (D14).
  - The cost is accepted: a fault that looks exactly like a service's usual bursts, no higher and no
    longer, is not reported.

By the founder, 2026-10-31, after the simulation of D17:

- **D18: Burst tolerance is per service.** A service tolerates its usual bursts only when its own history
  shows them often: at least one a week on average (amended by D21; first decided as about one a day). A calm service keeps the plain test and full sensitivity. The
  simulation showed tolerance for every service costing too much: a +5-point failure on a bursty service
  was caught in time only 56% of the time.
- **D19: AC36 scores only faults clearly past the minimum** (AC36). The 58% for traffic down to exactly
  50% measured the scoring rule, not the detector.
- **D20: A finding that can no longer be judged closes after 6 hours** (FR13), so it cannot stay open for
  days.

By the founder, 2026-10-31, after the simulation of D18 to D20:

- **D21: The false-alarm budget comes first.**
  - **The trade-off.** Under the harshest burst rate simulated, 8-minute bursts at 1 in 500 minutes, no
    setting meets both the budget and AC36 for a +5-point failure. Meeting AC36 cost 1.64 false findings
    per service per week.
  - **What is accepted.** On very bursty services, a small failure increase within the service's usual
    burst envelope is caught later or not at all (AC36).
  - **The bursty threshold.** Burst tolerance starts at one burst a week, not one a day. At one a day,
    almost no service qualified, and false findings reached 2.1.
  - **AC2's edge case.** A spike right after a natural small burst is recorded as a known case, not
    closed with more rules.
  - **Settled by the demo.** The demo run (D14) measures real burst rates. Whether real services come near
    the harshest case is settled there.

By the founder, 2026-11-01, after six persona reviews (a solo developer, an on-call lead, the implementing
engineer, security, QA and a startup advisor):

- **D22: Some findings are never adopted.** A critical finding, or any rise in failures, is never adopted.
  Adoption always emails the owners.
- **D23: A user's time zone comes from the browser at signup,** so a project's default is local, not UTC.
- **D24: The owners are told when detection stops.** One email after 15 minutes, and one when it runs again.
- **D25: Members can snooze a service for up to 4 hours.** It is the on-call lever at 3 a.m. Mute stays
  admin only.
- **D26: Burst tolerance never applies at or above critical.** False-alarm minutes stay out of the burst
  profile, so feedback cannot widen it.
- **D27: An endpoint failing at the critical share of its own requests is critical,** whatever its share of
  the service.
- **D28: A member's false alarm closes and counts a finding,** but only an admin's returns its minutes to the
  normal. This upholds D11.
- **D29: A new signal or endpoint joining an open finding notifies.** A finding linked to an incident already
  sent to the same PagerDuty channel adds a note there, not a second page.
- **D30: A stop is caught at any traffic, from day 3.** This closes the gap where a small service's outage at
  night was never judged.
- **D31: Deferred to after version 1:** health-check exclusion, and switching single signals or endpoints
  off. These are known gaps.
- **D32: Version 1 is smaller and named.**
  - Detection runs in the existing scheduler container, not a new one.
  - Deferred: the normal bands on service charts, per-service sensitivity, and the services-list column.
  - The cost target is 200 projects and 2,000 services on the 16 GB install (NFR2, AC29).
  - Stage 1 ships on its own, ahead of the rest of Phase 2 (`phase-2-plan.md`).
- **D33: *Detection stopped* is critical** (founder, 2026-11-01). It reaches the owners in quiet hours,
  because no finding can open while detection is stopped.
- **D34: A project whose detection has never run is reported too** (founder, 2026-11-01). Once it has
  traffic, and detection has had its chance to judge it, a project with no completed run counts as
  stopped. A fresh install that cannot judge anything is caught on day one.
- **D35: One problem pages once, whichever fires first** (founder, 2026-11-01). D29's PagerDuty note works
  in both orders: a threshold incident linked to a finding already sent to the same PagerDuty channel is a
  note on that page too.
- **D36: A muted finding's adoption sends nothing.** BR9 governs D22: "always" means even with no matching
  rule, never despite a mute (AC44).

## Open decisions

None in this specification. The statistical method and its parameters are the design's. They must be
shown by simulation to meet the false-alarm budget, and calibrated on the demo's measured variability
before they are frozen (FR9, D14).

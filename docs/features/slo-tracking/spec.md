# Feature: SLO tracking (F19)

## Status

**Proposed**, 2026-11-02, revised the same day after eight persona reviews, and again after those personas reviewed the full set of documents ([`status.md`](status.md), *Reviews*). Nothing here is approved until the founder approves it, and no code is written from it.
Stage 6 of [`phase-2-plan.md`](../../product/phase-2-plan.md), built after the chain. Input:
[`research.md`](research.md) and [`phase-2-foundation.md`](../../product/phase-2-foundation.md).

**Where this differs from those two documents**, as foundation asks a feature to say:

| Document says | This spec says | Why |
|---|---|---|
| Research R2: history in a new `service_stats_1h` table | Daily counts in Postgres (D2) | Reading the code showed a self-hosted install keeps rollups only 30 days, so an hourly table could never show last month there. The table also served only 2 of the 4 kinds |
| Research R12: a project's maintenance window may apply if ticked | One-time exclusions only; recurring windows later (D13) | Keeps the first version small. An exclusion can be entered ahead of time |
| Foundation section 10: "Free's 30 days of rollups limit SLO windows" | Windows fit inside 30 days, and monthly history does not depend on rollups | Same trace as D2 |
| Research: "the view's duplicate behaviour must be tested" | No view and no ClickHouse table at all | Same decision |
| Catalog: targets on "services/endpoints"; burns "into findings feed" | Four kinds, no endpoint latency. A burn is an incident shown on the finding it joins | Research; foundation FD16 |
| Foundation section 9: "endpoints are approximate" | Endpoint **counts** are exact; only endpoint latency is sampled | `observeEndpoint` adds to `requests` for every inbound span (`derive/endpoints.go:107-130`), and only the latency sample is capped (`:30-33`). So `endpoint_availability` meets foundation's "what the rollups count exactly" rule |
| Foundation: "`/slos`, `/slos/{id}/budget`" | Those two, plus `/months`, `/history`, `/exclusions`, `/restore`, and `/slo-readings/{id}/burn` and `/slo-readings/suggestion` (D59) | Foundation lets a feature add names. `/budget` reads Postgres only, so an F20 panel adds no ClickHouse read |
| Foundation: "a burn adds no second alarm" | True only on a channel told within the last hour, within limits (FR21 to FR24). Some second messages remain, on purpose | A dead service and a failing one are different news. See *Must not be claimed* |
| Foundation section 12: "request and error counts are exact", as true today | True only once P2 lands, and only for what reached a service and was reported. Foundation still licenses the wording this spec forbids, so launch text must be written from this spec | `spec.md`, *Must not be claimed* |
| Research: `scheduler-light`; first draft: no new container | A new role, `scheduler-slo`, on the same image (D39) | Under F3's floor, capped queues share 4 of `scheduler-light`'s 8 slots, and F6, F3 and F28 already ask for 6 |

## Words used here

| Term | Meaning on this page |
|---|---|
| **SLO** (service level objective) | A reliability target: "99.9% of `checkout`'s requests succeed over 28 days". On screen this is a **target** |
| **SLI** (service level indicator) | The measured number behind it: good events divided by all events |
| **SLA** (service level agreement) | A contract promise, usually with money attached. **Nexovue does not do this** |
| **Error budget** | The failure a target allows. At 99.9% over 1,000,000 requests, 1,000 failed requests. On screen, by kind: **failures still allowed**, **slow requests still allowed**, **failed runs still allowed** (FR12) |
| **Burn rate** | How fast that budget is being spent. 1 means it runs out exactly at the end of the window. 14.4 means 14.4 times that fast. On screen: "14 times faster than allowed". Inside these documents a burn is a tier holding, and on screen the state is **Running out** |
| **Window** | The stretch of time a target is judged over: the last 7 or 28 days |
| **Settled minute** | A minute the pipeline has finished writing (`detection/settle.py:1-18`) |
| **Coverage** | The share of a window nexovue actually measured |
| **Quiet minute** | A minute with no requests from a service. Not a failure, and not a success |
| **Lost minute** | A minute nexovue's own intake failed. The customer's traffic may have been fine |
| **Excluded time** | A period a person left out, with a reason, such as planned work |

## Problem

A small company cannot answer two questions from nexovue today.

- **"How reliable were we last month?"** A board or a manager asks. Nexovue has request counts taken before its own sampling
  (`000035_service_stats_1m.up.sql:1-11`), and nothing turns them into an answer. A search of `apps`, `packages` and
  `tools` found no SLO code.
- **"Is this small problem worth waking up for?"** A threshold rule says a number was crossed
  (`alerts/thresholds.py:118-127`). F6 says a service is not normal. Neither says how much failure the team has agreed to
  accept, or how soon it will run out at this rate. A low steady failure rate can stay under every threshold and still
  use up a month's allowance in a week.

**Be honest about the size of this problem.** Nothing found shows that SLOs win a purchase (research). Our guess is that a full
self-hosted product is expected to have them and that their absence would be noticed in an evaluation; there is no evidence for
more. SigNoz's request is open as of 2026-11-02, and that note will go stale. If SigNoz ships SLOs, F19 has no rival-gap claim, and its case rests on the monthly figure and honesty. So this feature must stay small, must not add alert
noise, must not pretend to do what only an agreement between people can do, and must pass a demand check before its alert work
starts ([`plan.md`](plan.md), *Demand gate*).

## Goal

- A person sets a target in under a minute, from the service's own history, with no query language.
- Anyone in the organization reads, in plain words, how much failure is still allowed, how fast it is going, and what
  the number did **not** measure.
- A burn that matters reaches people as part of a problem they already know about, or as its own message when it is new news. A
  channel told about the same news within the last hour, at the same or a higher severity, gets no second message about it. **Setting a target does add burn alerts,** and the screen says so.
- A buyer gets one answer per target for last month, and one line across a project's targets ("5 of 7 targets met in October"), labelled for what it covers. Figures build up from the day a target is set. Nexovue never blends services into one percentage, because that would count one request at every hop.
- All of this works with AI off, because it contains no AI.

## Target users

| Persona | What they do here | What they need most |
|---|---|---|
| **Buyer**: CTO or founder of a small company | Reads last month's result. Rarely opens the screen | One answer per target with an honest label that survives being copied. No SRE hire. Two or three targets, not twenty |
| **On-call engineer** | Receives a burn alert, usually at a bad time | How bad, how fast, since when, what else happened. No extra message for a problem already reported. A slow burn waits for morning |
| **New developer** (the catalog's P2) | Creates a first target | No unexplained jargon. A suggestion rather than an empty form. The definition of "failure" on the screen |
| **Admin** | Owns routing and planned work | A target that does not page anyone by surprise, and never silently pages nobody. Planned work that does not spend budget |
| **Viewer** | Reads | Everything a member sees, including every change and exclusion |

**Not served:**

- **A large SRE team** with per-service tuning, journey SLOs and a policy engine. Datadog, Grafana and Honeycomb serve them.
- **A lawyer or customer-contract owner.** This is never contract evidence (BR10). Default audience is internal and board use: founder decision 2.
- **A customer's end users.** No public status page (foundation).

**The attacker is a persona too.** See BR11 to BR13 and the design's *Security*.

## What nexovue brings to the table

**Positioning line, future tense until shipped, naming no rival:** "Reliability targets that say what they did not measure. A silent service will never be shown as healthy, and last month's figure will carry its gaps wherever it is copied."

Every clause of this can be tested (BR5, AC8, AC78). The message rule is only ever said with its limit: "A burn adds no second message about the same problem to a channel told within the last hour."


Stated for what is true once built. **Say what nexovue does, never what others lack.** Rivals' facts are from [`research.md`](research.md), with a URL and read date there. T6.8 re-reads each before anything is said in public.

| Claim | Basis | How strong |
|---|---|---|
| Counts requests before nexovue's own sampling, among those that reached a service and were reported | `000035:5-6`, `derive/services.go:141-168`; limits in `docs/product/setup/already-instrumented.md:63-82` | **True once P2 lands.** Table stakes, not a reason to choose us. If the customer samples upstream, these are counts of a sample |
| Missing data is never counted as good, and the screen tells apart "nexovue may have lost it" from "nothing came" | `settle.py`, `holds.py` | **Parity with OpenObserve's published rule.** Datadog publishes a rule that differs by SLO type. What nexovue will do: a named split between quiet and lost time, a dead service never reading healthy, and a link from a quiet period to the traffic finding. Unverified against rivals until T6.8 |
| When a channel was told about the same problem in the last hour, at the same or a higher severity, a burn sends no second message, and each withheld message is in the delivery log with the reason | FR20 to FR24 | True only for messages about burns, within FR22's limits. The older two-email case between a finding and a threshold rule stays (AC29). A burn with no finding still sends its own alert |
| Monthly figures that build up from the day you set a target, up to 13 months, on every install including self-hosted. The first month may be partial | D2, FR27 | Depends on this design. **Per project,** not across a company (D47) |
| A suggestion you can check, because the arithmetic is on screen, needing no AI and working self-hosted | FR7 | Only this narrow claim. Grafana Cloud and New Relic also propose targets from recent data |
| Free on self-hosted with no small count limit | FD20, D15 | **Not unique.** Coroot's and OpenObserve's own pages include SLOs in their free self-hosted products |
| Works with AI off | No AI in it, held by a structure test (FR33). The test catches imports, not a provider reached by plain HTTP, so review must check that no F19 module calls out | True by construction |

**Where rivals are plainly ahead:** Datadog's and Grafana's maturity; free-form SLI queries; journey SLOs; calendar views;
Datadog's correction types; recurring exclusions (Datadog, New Relic); one-time maintenance windows (Grafana, public preview); leaving out known failures or
traffic (Honeycomb); 90-day windows (Datadog, OpenObserve); **targets working right after install (Coroot's presets); targets proposed from history (Grafana Cloud, New Relic); targets as code or by API.** Multi-window burn alerts earn no credit, because they are now standard.

**What a seller can answer.** Future tense until shipped. Each is honest about when the answer is "you may not need this".

| Buyer asks | Answer |
|---|---|
| "My uptime monitor already gives me this" | "A monitor says whether one URL answered. This will count real requests, and show what was not measured, beside an outside check on the same screen. If you only need URL uptime, your monitor may be enough." To be tested in the customer conversations |
| "Coroot or OpenObserve do this free" | "They do, and Coroot starts with presets. Here it sits with findings and alerts you already run, and the month's figure carries its gaps. If that is not what you need, they are good tools" |
| "I run Prometheus, and Sloth or Pyrra are free" | "Then you can keep them. Nexovue's figures come from its own rollups and do not read your Prometheus" |
| "Can I define targets in code?" | "Not in this version. API tokens only read. Tell us if this is what decides it" |
| "How fast am I paged if everything fails?" | "On a busy service, in minutes. On thin night traffic with everything failing and at least 20 requests in the hour, within the hour. Below that, up to 6 hours. A service that stops completely is not a burn: add a silence rule or a check" |
| "Can an engineer tidy this before my board meeting?" | "They can exclude or delete, and it will be visible: everyone gets a notice, the reason shows, and a deleted target still counts in past months. It is visible, not preventable" |
| "Can I send this to my customer?" | "If you share it, share it with its label. It is a measurement, not a contract figure. Ask your lawyer before promising anything on it." Depends on founder decision 2 |

## Must not be claimed

The one list. T6.9 checks launch text against it, and AC13's test enforces the words it can.

| Do not say | Why |
|---|---|
| "One problem, one message" | False. A finding and a threshold rule still send two (AC29), and different news goes separately (FR21). Say the qualified sentence under *What nexovue brings to the table* |
| "Adds no extra alert" or "no extra message" with no qualifier | A burn with no finding alerts. An exhausted warning and an exclusion notice are never withheld. The limit is 60 minutes. Setting a target adds alerts |
| "Tells you what it could not see" with no qualifier | Upstream sampling cannot be detected, and a service over the cap reads as quiet until P2 |
| "13 months of history" with no qualifier | A service target reaches back about a month, and the rest builds up forward |
| "Measures your uptime" with no qualifier | It measures requests that reached a service and were reported |
| "Exact request counts" or "exact failure counts" with no qualifier | Upstream sampling and the service cap change them, and P2 has not landed |
| "Others don't have SLOs" | Only SigNoz's lack is supported, and only as of its date |
| "Tamper-proof" or "audit-grade" | A secret-key holder can backdate 7 days. A member can exclude time, visibly |
| "SLA reporting" | Telemetry cannot prove a contract |
| "Exact latency" for any endpoint | Quantile state from a sample |
| A latency target of exactly 500 ms | It is 487.2 ms, and the screen says so |
| "A target in under a minute" or "figures within a minute" in customer text | Unproved until AC38 and AC86 run with an outside tester |
| "Nobody can move the number" | Real traffic, including an attacker's real requests, moves it (BR11) |
| "Specific to nexovue" or "only nexovue" for any feature here | Datadog's monitor-based SLOs support Synthetic tests, for one. Say "what nexovue will do" |

## Scope

- Four kinds of target: service availability, service latency, endpoint availability, check availability.
- Windows of 28 days (default) and 7 days, rolling.
- Status with failures still allowed, coverage, and what was not measured.
- Burn alerts, through F21's rules, channels, quiet hours, flap guard and one-incident rule.
- A suggested target from plain arithmetic.
- One-time exclusions, with a required reason.
- A change history anyone in the organization can read, including deletions.
- Monthly figures for up to 13 months, a *Last month* view across a project's targets, and a copy action and CSV that carry the label.
- Linking a burn to a finding, and the rule for what a burn may withhold.
- Leaving named endpoints, such as health checks, out of a service availability target.
- The `slo_budget` panel contract for F20.
- `/slos`, `/slos/new`, `/slos/{id}` and `/slos/{id}/edit` screens, the rule editor's new kind, and lines on the incident and finding pages.

## Out of scope

| Left out | Why |
|---|---|
| **Endpoint latency targets** | `endpoint_stats_1m` holds a quantile state built from a sample of at most 8,192 durations (`derive/endpoints.go:30-33`, `latencysample/reservoir.go:26`). It cannot answer "how many were under X". Adding buckets would cost storage that nobody has measured. T6.2 reports a figure, and adding the kind later is a new request through feature-work (D65). Until then the form says what a service latency target mixes (FR5) |
| **A custom definition of "good"** (status codes, attributes) | Rollups hold one error flag (`traceassembly/stages.go:19`). `http_status` exists only on sampled spans |
| **Leaving named endpoints out of a service latency target** | The service's buckets have no endpoint, so it cannot be counted. The screen says health checks are included there. Availability targets can do this (FR34) |
| **Telling by itself whether a customer samples upstream** | The form asks (FR5). A warning triggered by a high failure share would be false too often, and cannot be tested without real traffic. T1.9 reads whether the worker can see a sampling marker, and the decision is recorded either way |
| **A monthly digest message** | Copy and CSV come first (D31). Add it when a buyer asks |
| **Free-form SLI queries** (SQL, PromQL) | Statements must be constants that bind the tenant (`alerts/telemetry.py:3-7`) |
| **Multi-service or user-journey targets** | Adding services counts one request again at every hop |
| **Automatic deploy freezes or a policy engine** | The failure is between people. A wrong freeze is expensive, and nexovue must not act on this number |
| **SLA or contract reports, credits or legal wording** | Telemetry cannot prove a contract. It misses requests that never arrived. Claiming otherwise is legal exposure |
| **Public status pages** | Foundation: not planned |
| **Per-target burn tuning** | Google strongly advises one set. Each knob is a way to silence an alert by accident |
| **AI that writes targets or reads this feature** | Arithmetic does it, with AI off |
| **OpenSLO import or export, targets as code, copying a target between projects** | No one has asked. API tokens read only today (foundation section 5). A customer conversation asks (D54) |
| **Web Vitals targets** | Comes through a browser key, and so raises the forging problem directly |
| **Recurring exclusions and calendar-month windows** | Later. A month is offered as a report (FR27), not as a window |
| **90-day windows** | Possible with D2, but alerts and suggestions would need their own rates. Add when someone asks |
| **A company-wide *Last month* line** | A read takes one project, because a project is an environment. The view says so (D47) |
| **Load balancer or gateway metrics as a kind** | Requests rejected before a service write no span, and this is the kind to evaluate next, as a fixed statement rather than a free-form query. Today the form says so, and FR27c catches a sharp fall |

## User requirements

- **UR1.** A developer can create a target for a service by picking the service, accepting or changing a suggestion, and
  saving, in under a minute.
- **UR2.** Anyone can see which targets are on track and which are in trouble, in one list, without reading a chart.
- **UR3.** Anyone can open a target and read one sentence saying where it stands, then what was and was not measured, then who is told.
- **UR4.** An on-call engineer told about a burn can see how fast, how long what is left will last, and whether a finding
  or deploy goes with it, and can act without opening a second page.
- **UR5.** A team already told about a problem is not told again because a target is burning, and is told when the news is new.
- **UR6.** A buyer can read "99.95% against 99.9% in October" and see what that figure leaves out, including when copied elsewhere.
- **UR7.** An admin can see, while creating a target and afterwards, where its alerts will go, that they go nowhere, or that they are snoozed.
- **UR8.** A person doing planned work can leave that period out, and everyone can see that they did, when and why.
- **UR9.** A viewer can see every change made to a target, and that a target was deleted. Change history and past months' results stay for 400 days after a deletion; daily figures go at 30 days (FR31).
- **UR10.** A person whose service is dead, or whose traffic is too thin to judge, is told so and offered a check-based
  target. Someone creating a target is told plainly that a burn alert does not cover a service stopping completely.

## Business rules

- **BR1. A target is one thing measured over one window.** A person may keep two targets on one service, for example
  availability and latency.
- **BR2. A failure is what the rollups call a failure:** a server or consumer span with status `ERROR`, and nothing else
  (`traceassembly/stages.go:19`, `derive/endpoints.go:100-102`). The screen says so.
- **BR3. A latency target is on a real bucket boundary.** The person sees the boundary they will actually get. Nexovue
  never interpolates a figure it did not count.
- **BR4. Nothing missing is ever counted as good.** A quiet minute or a lost minute adds no good requests. A target whose
  coverage is too low says so and shows no plain healthy state (BR5).
- **BR5. A service with no recent requests is never shown as healthy,** however good its figure and however long the silence.
- **BR6. Excluded time is left out,** neither good nor bad, does not lower coverage, and is always visible with its reason and who entered it.
- **BR7. A change to a target is never hidden.** Every change, exclusion and deletion is recorded in the audit log and
  in a history anyone in the organization can read. A deleted target leaves a line on `/slos` for 30 days, and still counts in months that ended before its deletion.
- **BR8. A burn is an F21 incident, not a finding** (FD16). Findings keep their three signals (`detection.py:38`).
- **BR9. A channel told about the same news within FR22's limits gets no second message about it, and new news is never hidden** (FR20 to FR24). An alert never waits
  to see whether another arrives. A withheld subject still open when its reason ends is told then.
- **BR10. Nexovue never calls this an SLA, a contract figure, tamper-proof or audit-grade.** It measures requests that reached
  a service and were reported.
- **BR11. A browser key cannot add to these counts** (precondition P2). Real traffic, including an attacker's real requests to a
  service, still moves the number and can page someone (foundation section 3).
- **BR12. Nothing here is held back by plan,** and the limit of 200 targets per project is stated as protecting the system,
  never sold as a tier (FD20).
- **BR13. Figures for the last 7 days may still change,** because a span's own time is trusted up to 7 days back
  (`clocktrust/clocktrust.go:44`). The screen says so. After that a day is final unless a person changes it, by an exclusion or a
  definition change, and that shows on the day and its month.
- **BR14. A target is an organization's data.** It is deleted with its project or organization.
- **BR15. An SLO's name, description and an exclusion's reason are free text** under foundation section 3. A service,
  endpoint or check name is a name. Burn rates and counts are facts.

## Functional requirements

### Kinds and targets

- **FR1.** Each kind reads exactly one source:

  | Kind | Good | All | Source | Exact? |
  |---|---|---|---|---|
  | `service_availability` | requests minus errors, less any left-out endpoints (FR34) | requests, likewise | `service_stats_1m`, and `endpoint_stats_1m` when endpoints are left out | Yes, for what reaches nexovue |
  | `service_latency` | requests at or under the boundary | requests | `service_stats_1m` buckets | Yes, for the boundary shown |
  | `endpoint_availability` | requests minus errors | requests | `endpoint_stats_1m` counts | Yes, for what reaches nexovue |
  | `check_availability` | confirmed runs that were up | confirmed counted runs | `metrics_gauge`, by the Synthetics screen's own rule (`synthetics/queries.py:53-60`) | Yes, for runs that count |

- **FR2.** A target's objective is between 90% and 99.99%, to at most two decimals. Anything finer is refused with the
  reason: at 99.999% over 28 days a whole outage of about 24 seconds spends it, and the rollups count by the minute.
- **FR3.** A latency target's threshold is one of the 63 bucket boundaries, from about 1.2 ms to about 50.5 s (my arithmetic
  from `derive/services.go:32-39`). A person types any number. The screen answers "this counts requests at or under
  487.2 ms".
- **FR4.** A fast failure counts as good on a latency target, because the buckets do not separate failures. The screen says:
  "A fast failure counts here. Pair this with an availability target."
- **FR5.** The screen states each kind's definition, for example: "A failure is a request whose span status is ERROR. A 4xx
  usually does not count." A queue consumer and a web route are counted together in one service. Request kinds also say: "Needs
  server spans, not sampled before they reach nexovue. If you sample first, these figures count the sample." and "Requests rejected before they reach this service are not counted. Add a check for this." (`docs/product/setup/collector.md:96-99`). A service availability target says health checks count as good unless left out (FR34). A service latency target says: "This includes health checks and background jobs, so it may not reflect your users' experience."
  **The form asks request kinds one question:** "Do you sample before sending to nexovue?", Yes, No or Not sure. **Yes** says the figures will be wrong, offers a check target, and creates the target with alerts off until the answer changes. **Not sure** links to how to check, and leaves alerts on. The answer is stored, and shows on the page and in the copied label.
- **FR6.** Targets per project are limited to 200, a guess until AC50 measures one project at the limit, and the refusal says it protects the system.

### Suggesting a target (D10)

- **FR7.** For availability, the suggestion reads the last four 7-day windows and shows worst, typical (the median) and best. It takes the worst
  and rounds **down** to the next of 90, 95, 97, 98, 99, 99.5, 99.9, 99.95. When worst and typical differ by more than one of those steps, it
  suggests from the typical week instead and says: "Your worst week would have missed this." For latency, it suggests the boundary that held at
  least 95% of requests in those same windows. The screen shows the arithmetic and lets the person change it. A worst and typical week both below 90% offers no suggestion and says why.
- **FR8.** With fewer than 7 days of data, the screen says so and offers a starter of 99.5%, labelled "a starting guess, not from your data" (the
  99.5 is a guess). With too few requests to measure a suggestion (FR12) it offers none, and offers a check-based target where a check exists. A check
  target's form shows what its interval allows: "At every 5 minutes, 99.9% allows 8 failed runs over 28 days. Run it every minute or choose 99.5%."

### Status

- **FR9.** Figures for a window are the last 27 complete days plus today so far, by the project's time zone, or 6 plus today
  for a 7-day window, and never reach before the source's first event. So a full window is between 27 and 28 days long, and the screen says "by whole days".
- **FR10.** Each target has exactly one state, the first that applies. **Any other fact that also holds is shown as a qualifier line under the
  sentence,** so nothing true is hidden by the order. List order is separate (FR10a).

  | State | When | Sentence shape | Next step |
  |---|---|---|---|
  | **Disabled** | The target is switched off | "Alerts are off for this target. It keeps counting. 312 of 672 failures still allowed." With a tier holding, a qualifier: "Failing too fast, alerts are off" | "Turn alerts back on", for those allowed |
  | **Not up to date** | `slo_status` is older than 15 minutes, worked out in the browser too | "Not up to date since 02:14. These figures may be out of date." With a burn: "Was using allowed failures 14 times faster than allowed when last worked out at 02:14 (IST). Not up to date since then." No "what is left lasts" line. With the reason if known (FR13) | "Nexovue is not judging this target. See system health" |
  | **Source gone** | Its check was deleted (`check_id` null), or its service or endpoint has sent nothing inside the window | "This check no longer exists." / "`checkout` has sent nothing in the last 28 days." | "Point the target elsewhere, or delete it" |
  | **Running out** | Any tier holds in `slo_status`, or an incident on it is still open. **Needs no rule** | "Using allowed failures 14 times faster than allowed, since 02:41. What is left lasts about 2 days." With nothing left: "…since 02:41, and nothing is left." Resolving: "Back within its allowance for 4 minutes. Will close at 03:12 if it stays there." When no tier can judge: "Was using allowed failures too fast until 02:41. Not judged since." The number is the long-window rate of the highest tier holding, rounded down | Critical: "Open the incident". Warning: "Can wait for working hours" |
  | **Counting** | History still being read | "Reading the past 28 days." After 30 minutes (a guess): "Reading history is taking longer than expected", with the reason if known | None |
  | **Not measured** | Coverage below 90% | "Nexovue measured only 71% of this window. It will not judge it." | "Nothing to do; see system health" |
  | **No recent requests** (**No recent runs** for a check) | No requests in the last 15 minutes and at least one earlier in the window, with **no upper limit**. A check: no run for 3 times its interval | Below the attention limit (FR10a): "No requests for 40 m. Its longest quiet stretch in the last 7 days was 2 h." Past it: "No requests for 3 h 10 m. The figure below does not count that silence. Check the service is running." | Past the limit: "Check the service is running" |
  | **Building history** | Holds fewer than a quarter of the window's days since its first event (under 7 of 28, or under 2 of 7; a guess) | "Holds 2 of 28 days. Too early to judge." | "Nothing to do yet" |
  | **Nothing left** | No failures still allowed | Beyond the allowance: "Target missed: 99.85% against 99.9%." Exactly at it: "No failures left. 99.90% against 99.9%, exactly on the target. One more misses it." A figure is rounded towards failing, so a missed target never prints at or above its objective | "It recovers as old failures leave the window" |
  | **Too little traffic** | Fewer than 10 failures allowed in the window. A check: the same count of failed runs, and the form states what the interval allows | "At this traffic 99.9% allows 2 failures. One failure moves it a lot." A check: "At this interval 99.9% allows 8 failed runs." | Request kinds: "Add a check-based target" |
  | **On track** | Otherwise | "Failures still allowed: 312 of 672." | None |

  Qualifier lines include: "Traffic is thin: one failure moves this a lot"; "Nexovue measured only 71%"; "No requests for 3 h 10 m"; "Holds 12 of 28 days. This figure will still move"; "Messages are snoozed until 09:00"; "Target missed". They come in this order, at most two showing, with "N more notes" opening the rest; "not up to date" and "no alerts" notes never collapse.
  The 15 minutes, the 3 intervals and the 10 failures are guesses, unproved until real traffic.
- **FR10a. What needs attention.** The list headline counts Running out, Nothing left, Not measured, Not up to date, Source gone, and No recent requests only once the silence
  passes the larger of 15 minutes and twice the longest quiet stretch of the last 7 days, capped at 24 hours (guesses), or once a traffic finding or silence incident is open. So a thin service's normal night does not raise the headline, and a dead one always does. Nothing else is called fine: Disabled, Building history, Too little traffic and Counting are named in the headline's second clause ("0 of 7 need attention. 2 have too little traffic to judge."). A list with any **Not up to date** target carries a banner saying how many, and the headline never says all is well: "These figures are not up to date for 3 of 7 targets."
  **Order:** the counted rows first, so the top N rows are the N in the headline; among them critical, then warning, then shortest time left; then the other unjudged states; Disabled last. Tones are in the design.
- **FR11.** Coverage is measured minutes divided by the window's minutes less excluded minutes, counted from the source's first event. So only lost time lowers it. Excluded time is shown on its own line. Quiet minutes do not lower it,
  because a quiet minute is real, but they are always stated, with the longest stretch: "No requests for 3 h 10 m in this window, the longest 2 h 05 m", with a link to any
  traffic finding or silence incident from those hours. A check target has no quiet minutes. Its coverage is the runs seen against the runs its interval expected while it was enabled, so missed runs lower it, and they are stated: "12 runs missed."
- **FR12.** The allowance is `(1 - objective) x all events - failures`, rounded down, and never shown below zero. Words by kind: **Failures still allowed** (availability), **Slow requests still allowed** (latency), **Failed runs still allowed** (check). Every sentence and message uses the kind's word: "allowed slow requests", "allowed failed runs".
- **FR13.** The status shows when it was last worked out. **Every time in a sentence, qualifier, message and the incident page shows the project's time zone, and the date when not today:** "since 23:10 yesterday (IST)". A figure older than 15 minutes becomes **Not up to date**, with the reason
  if nexovue knows it, using F6's detection status.
- **FR13a.** A service left out by the cap is not quiet. Once P2's record exists, those minutes read: "`checkout` was not counted for 40 minutes, because the project went over 2,000 services." Until P2, the error table says what happens.
- **FR13b. When nexovue goes blind.** When a project's `slo_status` is older than 15 minutes, or every tier of a target has been unable to judge for 15 minutes because of coverage, one message goes to the channels its `slo_burn` rules name, or else to the owners, and one "judging again" when it returns. It is critical, because while it lasts no burn can page. A process other than the one judging raises it (`scheduler-deliveries`). If the whole install is down nothing here can speak, so the setup page documents an outside heartbeat. Too little traffic never starts it.

### Burn alerts

- **FR14.** Burn is judged each settled minute on three fixed tiers. A tier holds only when both its windows exceed its rate **and** failures in its long window are at least half of the budget it would nominally spend (a guess), **unless at least half of the requests in both windows are failing (a guess), when the floor does not apply:**

  | Tier | Long / short window | Rate over 28 days | Rate over 7 days | Budget spent when it fires, 28 / 7 days (arithmetic, **assuming flat traffic**) | Severity |
  |---|---|---|---|---|---|
  | Fast | 1 h / 5 m | 14.4 | 16.8 | 2.1% / 10% | Critical |
  | Medium | 6 h / 30 m | 6 | 5.6 | 5.4% / 20% | Critical |
  | Slow | 24 h / 2 h | 3 | 2.8 | 10.7% / 40% | Warning |

**Why the budget floor.** Traffic is not flat. With 1,000 failures allowed, 2 failures in a 3 a.m. hour of 20 requests is a burn rate of 100, yet only 0.2% of the budget. Without a floor that pages. With it, the fast tier needs about 11 failures here. A small budget falls back on FR16's 2. **Why the exception.** By arithmetic, at 99% with night traffic at 5% of average, a service failing every request could never reach the fast tier's floor within its hour, and the medium tier would take 3.6 hours. While history is still being read, the floor uses what is counted so far, or FR16's 2 with none.
- **FR15.** The largest possible burn rate is `1 / (1 - objective)`. A tier whose rate is above 90% of that is capped there, so
  a 90% target can still page, and the screen does not need to refuse low objectives.
- **FR16.** A tier needs a minimum number of events in its long window to judge: 20 for request kinds (the
  existing rule's figure, `alerts/model.py:179`) and 10 for checks. Below that the tier judges nothing. So below 20 requests an hour the fast tier cannot judge, and the medium tier can take up to 6 hours. A tier also needs at least 2 failures to hold; fewer means it does not hold, not that it cannot judge. A check's short window is at least twice its interval, so it can hold a run. A check needing more than 2.4 hours per run can reach no tier's floor, and the form and target page say: "Alerts cannot judge a check this infrequent. It needs at least 10 runs a day." All of these are guesses, unproved until real traffic.
- **FR17.** Below 90% coverage in its long window, a tier judges nothing. Excluded time inside a window is left out of its events and its coverage. When no tier can judge, an open incident stays open
  and nothing opens (`thresholds.py:86-90`). **But a burn has an end:** when the target's source has sent nothing for 24 hours (the longest long window), with coverage of those hours at 90% or more, the incident closes and every channel told is told: "No longer judged: `checkout` has sent nothing for 24 hours. The burn may not be over." If failures resume, a new incident opens and pages. So a stopped service cannot hold a PagerDuty alert open for longer than that. Nexovue's own lost minutes never close a burn.
- **FR18.** A burn fires with no wait, because the windows are the wait. It resolves 10 minutes after no tier holds. The 10 is a
  guess, unproved until real traffic. Severity only rises within one incident (`alerts/model.py:36`), and while firing it is judged every minute, so a slow burn that becomes fast escalates. Figures on a repeat, a recovery and the incident page are current, not the opening's. "What is left lasts" comes from what is left and the failures per hour actually seen.
- **FR19.** When the allowance reaches zero, one warning goes to the channels the rules name, even if failures have
  stopped, and is never withheld by FR22, once per channel however many rules name it. It waits while a target is **Building history**. It is said again only after the allowance has come back above 5% and run out again, and at most once in 7 days (both guesses).

### What a burn may withhold

- **FR20.** A burn on a service joins any open finding on that service in the same project, in either order, through `finding_incidents`.
- **FR21. Same news.** On one channel, two subjects are the same news only when they are in the same organization **and project**, and one of these holds:

  | Pair | Same news when |
  |---|---|
  | A burn and a finding | Linked, and the finding holds a signal of the burn's type: `failing` for availability, `slower` for latency |
  | A burn and a threshold incident | Same service and same type: `service_error_rate` for availability, `service_latency_p95` for latency |
  | Two burns | Same service and same type, unless each names a different endpoint |
  | One target under two rules | Always |
  | A check burn and its check's `check_state` incident | Always, and only the burn can be the one withheld |

  **Never the same news, so never withheld and never withholding:** `service_silent`, because "stopped" is different from "failing"; a `check_state` opening; a finding's `traffic` signal alone; an exhausted-budget warning; an exclusion notice.
- **FR22. Withholding.** On a channel a person reads (email, owners, Slack, Teams, Discord), a news message of X is recorded and not sent only when a burn is involved as X or as the other subject Y, and Y is the same news, still open, told there at the same or a higher severity within the last 60 minutes (a guess). A message still being retried counts as told. A burn only counts as Y if a tier could judge it within the last 15 minutes, so a frozen burn silences nothing. A minute when no tier can judge does not count, though the job still ran (D56). It stays in the delivery log with the reason. A row already in a batch is never withheld. **With no burn involved, F6's and F21's behaviour is unchanged.** The 60 minutes stop new messages being withheld; they do not release one already withheld.
- **FR23. Release.** When a withheld subject is still open and nothing left withholds it, because Y ended, Y's message failed for good, Y's burn froze, or a service's snooze ended, one new message goes to that channel at the current severity, in the same message as Y's ending where there is one. So an all-clear never stands alone while the problem is still open, and no news is lost by being withheld.
- **FR24. Severity and endings.** A higher severity always goes through. An ending is withheld on a channel only if **no** news message of that subject (opened, flapping, escalated, joined, repeated) reached that channel. So a channel told "critical" is always told it ended. F3's `explained` line goes only where a news message of that finding reached the channel. Webhooks still receive every event.
- **FR24a. PagerDuty.** One alert per problem, by the existing key (`outbox.py:220-258`), extended: a burn shares the alert of an open subject on the same `about_key` or `subject_key`, with or without a finding, as long as that alert is at the same or a higher severity. `service_silent` and `check_state` openings always make their own alert. The last to end resolves it.

### Routing

- **FR25.** Every project has a default rule, *Reliability targets*, sending warnings and criticals to the organization's owners.
  It stays silent until a target exists. Like the other default rules, it is not recreated if deleted (`alerts/defaults.py:1-8`). A `slo_burn` rule does not count as watching a service on the service coverage route (`alerts/service.py:325-329`).
- **FR25a.** The create form and the target page show who is told: "Warnings and criticals go to Organization owners, through *Reliability targets*"; "No alerts will be sent", with a link to the rule editor; or "Messages are snoozed until 09:00 by [person]. Failures still count." **The form lists the organization's channels, and ticking one writes a rule for this target in the same save,** with a PagerDuty channel pre-ticked for criticals when one exists, so the first target can reach the on-call engineer and not just the owners. That rule's name is fixed and composed by nexovue, never the target's name (D57). A list row shows a *No alerts* or *Muted* chip. Both also say, where no silence rule or check covers the service: "This target does not alert if the service stops completely", with **a link to a prefilled form**: a silence rule through `/alerts/rules/new`, a check through `/synthetics/new`. Those links show only to people allowed to create those things; a viewer reads "Ask an owner, admin or member to add one." Default rules cover check-down and critical findings only (`alerts/defaults.py:48-70`).
- **FR26.** A service's F6 **snooze** also stops burn messages for targets on that service, at the moment of sending, whenever it was set: to people's channels, and news to PagerDuty, because a page wakes a person. The budget still spends and the state is still true. Endings a machine needs still go, and webhooks get everything. **When the snooze ends and the burn is still open, one message goes** (FR23), and an ending dropped during it is sent to a channel that had been told. A service's F6 **mute stops findings only:** burn messages continue, because a mute may last 30 days and a burn is the alert a person agreed to. To silence a burn, a person mutes the rule or disables the target, which is visible on the target. The snooze control says it also holds back reliability target messages, and the mute control says they continue.

### History and change

- **FR27.** Monthly figures, by the project's time zone, for up to 13 months building up from creation. Each month leads with a sentence from its data. At 90% coverage or more: "October: 99.95% against 99.9%. Measured 98% of the month. 3 h 10 m with no requests, the longest 2 h 05 m. 40 m may have been lost by nexovue. 2 h left out for planned work." Below 90%: "October: not judged. Nexovue measured only 71%." A longest stretch crossing midnight is one stretch. The raw counts sit in a table beneath. "May still change" shows while any day is under 7 days old, "Changed on 3 Nov by an exclusion" on a month changed after it ended, and a month with less than a full month of counts says how many days it holds. A check target's month also states its maintenance time: "Runs in maintenance windows are not counted (6 h this month)."
  The form says how far back each kind reaches at creation: "Figures start about a month before this target was created" for a service target, because `service_stats_1m` holds at most 35 days, on any plan; an endpoint or check target reaches the plan's derived retention (30 or 395 days). Where a month's figure and the current rolling window disagree, the page says: "The last 28 days and October are different periods."
- **FR27a.** A month or window holding a traffic finding or a silence incident says so beside the figure, while those records last: "This figure does not count the outage on 14 Oct (3 h 10 m). Requests that never arrived are not failures."
- **FR27b. Last month across targets.** `/slos` has a *Last month* view: one row per target with figure, objective, met, missed or not judged, and what it leaves out, headed "5 of 7 targets met in October. 1 missed. 1 not judged. 2 of those met have time not counted." A target deleted after that month ended still counts, and says so. It is per project, and says why it gives no single blended percentage. Each month has a copy action, and months download as CSV, with every customer-text cell made safe for a spreadsheet. Both always carry the period, what it covers, quiet and excluded time, the sampling answer where it is Yes, and a **fixed label by kind:** request kinds, "Measured from requests that reached the service and were reported. Not a contract figure."; a check, "Measured from nexovue's own check runs, one view from outside. Runs in maintenance windows are not counted. Not a contract figure."
- **FR27c. A sharp fall in requests.** A day whose total is at least 50% below the same weekday in the earlier weeks held (a guess) adds a line to the day and its month, with or without a finding: "On 14 Oct requests were 60% below the same weekday in earlier weeks. Requests rejected before reaching this service are not failures." It cannot catch a short partial outage, and says so in the docs.
- **FR28.** Changing the objective never needs a recount. Changing what is counted (kind cannot change; threshold, service, endpoint
  or check can) recounts back to the recount limit, and older days stay as counted and are marked with the old definition. Re-pointing a target closes its open burn incident, because it is a different thing now. **The recount limit** is the later of (today minus the project's retention, plus one whole day) and the 29 days that hold records last. An existing day beyond it is never rewritten. A day first read beyond 29 days is marked "loss not known", and that carries onto its month.
- **FR29.** An exclusion needs a start, an end, and a reason of 10 to 500 characters. It may be up to 7 days long, and total excluded time in any window may be at most 7 days of 28, or 2 of 7 (guesses). It may start in the future, or in the past back to the recount limit. A target holds at most 50 exclusions whose time lies within kept figures (a guess); removed and expired ones do not count. Creating one sends one warning-level notice to the channels its rules name, whether or not those rules are muted, or to the owners when no rule covers it, never withheld. The notice says who left out how long from which target, and holds no reason. Overlapping an open burn is also recorded on that incident, by its times and id. It can be removed, which is itself recorded. A removal reaching past the recount limit stays visible as "removed, figure not recounted". One exclusion is for one target (D52).
- **FR30.** History records each change: what, from what, to what, when, and by whom. A read token sees everything except the person.
- **FR31.** Deleting takes a target off the list and closes its open burn incident and alert position, after a confirmation stating how many days of figures will go in 30 days (a guess). For those 30 days `/slos` shows anyone: "Checkout availability was deleted by [person] on [date]. Its daily figures go on [date]." Anyone with `CONFIGURE_SLOS` can restore it until then, under a new name if its name was taken, and then a recount fills the gap the source still holds. After 30 days, its daily figures go, and each complete month's result and its change history stay for 400 days, so a past report stays true (D46). Project and organization deletion still remove everything at once. The audit log keeps the fact.
- **FR32.** A disabled target stops alerting, closes any open burn incident, keeps counting, and says so.

### AI

- **FR33.** No F19 module, in the scheduler, the platform or the API, may import an AI module, held by the structure test detection uses (`test_detection_has_no_ai.py`), from Step 1. Any API route's authorization imports `nexovue_api.lifespan`, which today imports the MCP SDK to type one argument. That import goes, by a small Protocol, so the walk needs no exception (D55). A real AI import in an F19 module must still fail it (D55). Status,
  alerts, messages and suggestions work the same whatever the AI setting is. No `slo_burn` incident or notification may hold a target's name, description or an exclusion's reason, because incidents already reach AI (design, *AI*). `rule_name` is existing customer text on every incident, handled as a name (D57). `subject_label` is composed by nexovue from source name, kind, objective and window, such as "checkout availability, 99.9% over 28 days", so two targets on one service read differently and no free text enters it. A target with the same definition as a live one is refused. A target's own name shows only on console pages. F4 gets no `/slos` tool in this version, and its ticket is refused by every `/slos` and `/slo-readings` route.

### Leaving endpoints out

- **FR34.** A `service_availability` target may name up to 20 endpoints (a guess) to leave out, such as health checks. The form pre-ticks names that look like health checks and shows them, so nothing is left out unseen. Left-out requests also do not count as activity for **No recent requests**, so probes cannot make a dead service look busy. A latency target says plainly that health checks are included. Left-out requests are subtracted minute by minute, and a minute with no service row subtracts nothing, so a figure never goes negative.

## Non-functional requirements

- **NFR1. Cost.** At the F6 cost target (200 projects and 2,000 services), with a mix of all four kinds and one project at the 200-target limit, every burn job in a minute must finish
  inside that minute, the largest single project's job is reported alongside the average, and no read may exceed 5 seconds or 256 MiB, the bounds detection uses (`detection/store.py:50-57`). F6's AC29 must still pass beside it. These are
  targets, and the reads are not measured yet.
- **NFR2. No starvation.** SLO work runs in its own role, `scheduler-slo`, on the same image: 4 slots, with `slo` capped at 2, `slo_counts` 1 and `slo_history` 1, and a memory ceiling of 384M (a guess until AC50). So a slow SLO read cannot hold a probe or an alert sweep, and a history read cannot hold a burn. `scheduler-light` keeps its 8 slots and its 768M. A test fails if `scheduler-slo`'s caps exceed its slots, or if its queues leave it. F3's floor keeps `scheduler-light`'s probes and sweeps 4 of 8 whatever the capped queues do (`ai-insights/design.md:271,829`). On that role, F19's queues would share the other 4 with F6, F3 and F28, which is why it has its own.
- **NFR3. Speed.** The list must load with no ClickHouse read, in under 1 second for 200 targets. A guess until AC90 measures it.
- **NFR4. No double counting.** A retried or repeated job must leave the same counts, and write one incident and one notification.
- **NFR5. Tenancy.** Every ClickHouse statement must bind tenant and project, and every Postgres statement `org_id`.
- **NFR6. Accessibility.** State is never carried by colour alone (`docs/design/principles.md`, rule 6). Screens use the window's width. All screens and the form must work at 375px and by keyboard (AC81).
- **NFR7. An open page does not lie.** The list and status refetch every 60 seconds (a guess). The age is worked out in the browser, so a page whose refreshes fail still turns **Not up to date**, and says "Could not refresh, last read at 03:04".

## Cannot be proved before a customer

Named here so nobody reads a test pass as more than it is. Seeded and demo traffic is steady and artificial (`plan.md`, *Validation plan*). **This is the one register of guesses.** Each is unproved until real traffic, and T6.10 reads the delivery log's sent and withheld rows per target, and asks whether any withheld message was needed.

| Guess | Where | What would settle it |
|---|---|---|
| How often burn pages falsely, and the tier rates' fit to real traffic | FR14, FR15 | Pages per target against what people did about them |
| The 20 and 10 events, and 2 failures | FR16 | Pages on thin targets |
| The budget floor, and the 50% that skips it | FR14 | Night pages sent and missed |
| The 10-minute resolve | FR18 | Burns reopening within the hour |
| The 24-hour close of a stopped burn | FR17 | How often it closes a burn that resumed |
| The 15-minute silence and the attention limit | FR10, FR10a | Headline counts against real outages |
| The 90% coverage line | FR10, FR17 | Coverage on real installs |
| The quarter-window Building history line | FR10 | When figures stop moving much |
| The 10-failure Too little traffic line | FR10 | Targets at that line |
| The 30-minute Counting limit, and AC64's 2 minutes | FR10, AC64 | AC50 and real installs |
| The suggestion's quality, and the 99.5% starter | FR7, FR8 | Targets kept or changed |
| The 60-minute limit on withholding, and whether a withheld message was ever needed | FR22 | The delivery log's withheld rows |
| The 15-minute freshness on a burn | FR22 | Withheld rows beside frozen burns |
| The 5% and 7-day repeat limit on the exhausted warning | FR19 | How often it repeats |
| The 15 minutes before "cannot judge" | FR13b | False notices |
| The 50% fall | FR27c | Lines against known outages |
| 30-day restore, 7-day exclusion, 7-of-28 cap, 50 exclusions, 20 left-out endpoints, 200 targets, 60-second refresh, `scheduler-slo`'s 384M | FR29, FR31, FR34, FR6, NFR7, NFR2 | AC50 for cost; use for the rest |

## Error cases

| What goes wrong | What the user sees | Can they recover? | Told apart from a normal result? |
|---|---|---|---|
| Service stops completely | **No recent requests**, never healthy, however long. Linked traffic finding if any. A check-based target is offered. **A burn alert does not fire** (FR25a), and a burn already open is frozen and says so, and closes after 24 hours (FR17) | Fix the service, or add a silence rule or check target | Yes (BR5) |
| A load balancer or gateway rejects part of the traffic | Those requests write no span. The service shows fewer requests, all successful, with no silence, and a figure near 100%. FR5's line says so. FR27c adds a line for a long enough fall | Add a check. Watch the gateway | **Partly.** A short partial outage is not told apart |
| Nexovue's intake fails | Those minutes count as lost, and coverage falls. Below 90%: **Not measured**. No alert fires or resolves. **One message says so (FR13b)** | Nothing to do; it says so | Yes: "Nexovue may have lost these minutes" against "no requests". A hold is install-wide, so it may read low for a tenant whose traffic got through |
| The SLO worker, the sweep or the job queue stops | **Not up to date** on every target, and one message (FR13b). No burn can fire until it returns | Restart it | Yes |
| Pipeline is behind | **Not up to date**. Burn is judged only up to the settled minute | Waits | Yes |
| ClickHouse cannot be read | **Not up to date** with last known figures and their time. The chart shows a failure line with a retry | Retry | Yes (FR13) |
| Screen cannot load | "The targets could not be loaded", with a retry. No headline and no empty state. It never says there are no targets | Retry | Yes |
| Instrumentation never sets a status | A permanent 100%. If a service has no failures in 28 days the screen adds: "No failure was ever recorded. Check that your instrumentation sets span status." | Fix instrumentation | Partly. Nexovue cannot know for sure, and says so |
| Customer samples upstream | Counts are of the sample. Tail sampling that keeps errors makes the failure share look far too high, so false pages from day one. The form asks (FR5), and on Yes alerts start off | Turn off upstream sampling | **Only by asking.** Nothing else tells nexovue (`docs/product/setup/troubleshooting.md:245-249`) |
| Health checks in the traffic | They count as good and raise the figure, unless left out (FR34) | Leave them out, or filter them in the Collector | Yes, on the form |
| Very low traffic | **Too little traffic**, and a check-based target is offered, unless it already is one | Choose a check target or a lower objective | Yes |
| Too-fine objective | Refused beside the field, with the reason | Change it | Yes |
| Latency above 60 s | Durations above 60 s fold into the last bucket, so the highest boundary is about 50.5 s and nothing higher is offered | Choose within range | Yes |
| Check deleted, or service renamed | **Source gone**, once nothing arrived inside the window; **No recent requests** before that. Past figures stay. An open burn on a deleted check closes at once; on a renamed service, after 24 hours (FR17) | Point the target elsewhere, or delete it | Yes |
| Prober or metrics worker stopped | Check targets read **No recent runs**, and missed runs lower coverage. Spans keep moving, so the settled minute alone would not show it | Restart it | Yes |
| Late spans change a past day | The figure changes, and "may still change" was already on it | None needed | Yes (BR13) |
| No rule covers a target | "No alerts will be sent" on the form, the target and its list row. A burn still reads **Running out** | Add a rule | Yes (FR25a) |
| Service pushed past the 2,000-a-minute cap | **Reads as quiet.** Today nothing records which service was left out (`traceassembly/pipeline.go:763` logs only). P2 fixes who can cause it and adds the record, and then FR13a applies | Depends on P2 | **Not until P2** |
| Budget is gone and failures stopped | **Nothing left** on the screen; one warning; no page | Wait for old failures to leave the window | Yes |
| Project's plan shortens retention | Figures already counted stay; a recount reaches only the recount limit, and says so | None needed | Yes |

## Acceptance criteria

**Counting**

- **AC1.** A service with 1,000,000 seeded requests and 400 errors at 99.9% over 28 days must show 600 failures still allowed of 1,000. A 7-day target with one day missing must add only the days it holds.
- **AC2.** Each of the four kinds must give the seeded count exactly. For checks, an overruled attempt, a maintenance run and a `prober_error` must not count, and repeated reading rows must not count twice.
- **AC3.** A latency target asked for 500 ms must store and show 487.2 ms, and its good count must equal the count of durations at or under that boundary. A parity test must hold Python's boundaries equal to `derive/services.go`, and one live span set at and on either side of a boundary must pass through the real worker.
- **AC4.** An endpoint latency target must not be creatable by any route.
- **AC5.** An objective of 99.999 or 89.9 must be refused with a reason beside the field.
- **AC6.** Replaying the same job must change no count, and a day recomputed twice must give one row.
- **AC7.** A day older than 7 days must be marked final and not be rewritten, except by a definition change or an exclusion reaching it within the recount limit.

**Honesty**

- **AC8.** A stopped service must never show as **On track**, and must show the length of its silence, whatever its figure. This must be tested end to end through the job, at figures of 100% and 99.99%, for service and endpoint kinds, after silences of 16 minutes, 25 hours, 3 days, 10 days and 27 days. A target's check kind must read **No recent runs** at 60 s, 1 h and 3 h intervals only after 3 intervals.
- **AC9.** A lost minute must lower coverage and add no good events. At 89% coverage a target must read **Not measured**, and at 91% it must not.
- **AC10.** With lost minutes, quiet minutes and excluded minutes in one window, the screen must name each separately.
- **AC11.** A request target allowing fewer than 10 failures must read **Too little traffic** and offer a check-based target. A check target at that count must say what its interval allows, and must never offer a check target.
- **AC12.** A service with no failure in 28 days must show the instrumentation line.
- **AC13.** A word-scan test, matching whole words and ignoring case, over `features/slos`, the SLO lines on the incident, finding and service pages, `slo_burn` message wording and the new docs page must find none of these, each with a named allow-list: "SLA" (allowed: the line saying nexovue does not do SLAs); "uptime" (none); "tamper-proof", "audit-grade", "exact" (none), "exactly" (allowed: "exactly on the target", which FR10 and AC111 require); "budget" and "burning" ("error budget" allowed only on the *Definition* tab, the docs page and page metadata; "SLO" allowed in the page title and nav). The test must also find "SLO" and "error budget" on the docs page. Existing Synthetics and onboarding screens are outside it.

**Burn alerts**

- **AC14.** A pure test must hold every tier at its boundary: just over and just under each rate, for long and short windows, at 28 and 7 days.
- **AC15.** At a 90% objective the fast tier must be able to fire (FR15).
- **AC16.** With one failure in 10 requests in an hour at 99.9%, nothing must fire. On a target allowing 8 failures, 2 failures in 20 requests in the hour with a failing short window must fire the fast tier. **On a target allowing 1,000 failures, 2 failures in a 20-request hour must not fire, and 10 of 20 must, because half are failing.** Seed data must hold day and night traffic.
- **AC17.** A long window over the rate with a clean short window must not fire, and an open incident must resolve 10 minutes after no tier holds.
- **AC18.** Two targets on one service under one rule must hold two incidents at once.
- **AC19.** A slow burn must open as a warning and wait out quiet hours. When the fast tier then holds, exactly one `escalated` row must be written, with no second incident, tested through the real job and not only through `step()`. A critical must never wait for quiet hours. A repeat and a recovery message must show current figures, not the opening's.
- **AC20.** A burn must not move while intake is held (F6's holds), and must judge only settled minutes.
- **AC21.** A scheduler restart in the middle of a resolving wait must resume it, not start it again.
- **AC22.** The allowance reaching zero must send one warning and open no page, even with a burn open at that moment and with two rules naming one channel. It must not send it again until the allowance has returned above 5% and run out again, and not within 7 days, over a target hovering at zero across several midnights.
- **AC23.** A restart or a repeated job must write no second incident or notification.

**One message**

- **AC24.** Burn then finding, and finding then burn, of the same type on one service and one email channel, at equal severity, must each deliver exactly one message, and the second must appear in the delivery log as `dropped` with a reason. Each order must be tested with the two due in separate claims and in the same claim. **Three subjects:** threshold, then finding, then burn must deliver two and drop the burn. Burn, then finding, then threshold must deliver one and drop two.
- **AC25.** A warning burn followed by a critical finding, and a warning finding followed by a critical burn, must each deliver both.
- **AC26.** An ending must be withheld only if no news message of that subject reached the channel, and sent otherwise. **Opening withheld, escalation delivered, then the end: the ending must arrive,** for a burn and for a finding. A dropped opening must not by itself count as told on a repeat.
- **AC27.** On PagerDuty, a linked burn and finding must make one alert, resolved by the last to end, in both orders. So must two burns on one service with no finding, and a burn and a threshold rule with no finding. A `service_silent` opening must make its own alert. A critical burn after a warning alert must page.
- **AC28.** A webhook channel must receive every event in each case.
- **AC29.** A threshold incident and a finding on one service with no target must deliver exactly what they deliver today. The existing outbox tests must pass unchanged.
- **AC30.** A check-kind burn with an open `check_state` incident on that check told to the same channel must not send a second message or page. A `check_state` opening after a burn must always be sent.
- **AC31.** Delivery must never be delayed by this rule: a burn with nothing to join must be claimed as early as any other row.
- **AC52.** **The told one ends first.** Finding told, burn withheld, finding closes, burn still holds: the channel must receive the finding's ending and exactly one burn message, in one message. The same must hold with burn told, finding withheld, burn resolves. A person closing the finding, with no ending sent to people, must release too, by the next delivery sweep.
- **AC53.** **Errors, then silence.** A critical burn is open, then the service stops. The test must run the real `judge_slos` through the silence, with time moved forward. The went-silent message must be delivered. `alert_evaluations.evaluated_at` must stop moving once no tier can judge, at most 2 hours after the last event by FR14's short windows, while the job goes on running. Any finding after that, once the burn has gone 15 minutes unjudged, must be delivered, and one withheld earlier must be released then.
- **AC54.** **Age.** A finding told 3 days ago and still open must not withhold a new burn. Told 59 minutes ago it must, and 61 minutes ago it must not.
- **AC55.** **Different news.** A latency burn then an availability burn on one service must deliver both. So must two availability burns on different endpoints, and a burn with a finding holding only `slower` when the burn is availability.
- **AC56.** A burn in project A and a finding on the same service name in project B, on one channel, must each deliver.
- **AC57.** Two burns due in one claim on one service must deliver exactly one message and drop one, by the stated order. A retrying first row must still count, with no second message. A first row that fails for good must release the withheld one. A row already in a batch must never be withheld.
- **AC58.** An `explained` row must go only to a channel a news message of that finding reached.
- **AC59.** A snooze set before opening: incident recorded, the budget spends, no message to people or page; webhooks still get every event. A snooze set after opening: no escalation, repeat or ending to people's channels; a machine still gets the ending it needs. A snooze ending while the burn holds: exactly one message. A burn that ended during the snooze: its ending, once, to a channel told before it. **A service mute set before or after opening must stop no burn message.** `incidents.muted` must stay false in every case.

**People and permissions**

- **AC32.** A viewer must read every route's result and be refused every write. A member, admin and owner must each be able to write through `CONFIGURE_SLOS`. A viewer may read `/slo-readings/suggestion`. `test_authorization_coverage.py` must pass.
- **AC33.** A read token must read targets, status and history, must be refused writes and `/slo-readings/suggestion`, and no response may hold `created_by`, `removed_by`, `deleted_by` or `actor_user_id`. A token scoped to project A must be refused each `/slos/{id}` route of project B in the same organization, and an exclusion id from another target must be refused. No response may hold a hold's reason, a guard's vote or an organization count. Each route must be tested, and `test_read_surface_structure.py` must pass.
- **AC34.** Each create, change, exclusion, deletion and restore must write its audit entry from the step that adds the route, and all must appear in what a viewer can read.
- **AC35.** A target on one project must not be readable, writable or usable in a rule from another organization's or another project's context. `test_tenant_isolation_live.py` must cover each route.
- **AC36.** A service name or endpoint name holding a line break, a Slack mention or a Markdown link must arrive as one line of plain words in every channel.
- **AC37.** A `SERVER` span sent through a browser key must not change any figure, and a forged `synthetic.check.status` reading sent through a browser key must not change a check target (P2). Proved end to end through the real receiver and worker, beside `test_system.py`, as a condition of starting Step 4 and of releasing F19.
- **AC71.** A deleted target must leave the list and close its incident. A viewer must see its line for 30 days, a member must be able to restore it, and its daily rows must be gone afterwards, with its month results and change history remaining. Deleting a project must not wait the 30 days.
- **AC72.** A burn whose target name, description and an exclusion's reason hold an injection payload must leave no such text in any `incidents` or `notifications` row, and `subject_label` must hold no free text. A rule written by the form (FR25a) must not hold the target's name. A test must feed that incident, one with a hostile service and endpoint name, and one whose rule has a hostile name, through MCP's `list_incidents`, `get_finding` and `list_findings`, F4's `get_incident` and F3's linked-incident evidence, and find no target name, description or reason, with service, endpoint and rule names arriving as aliases at F3 and labelled untrusted at MCP and F4. A test must hold that no assistant tool reads `/slos`.
- **AC88.** Removing a person must replace their email and full name inside `name`, `description`, `reason` and at any depth in `before`/`after`, across all the organization's rows, whoever wrote them. No `slo.*` audit detail may hold free text. Their export must hold the exclusions and changes they made. Removed exclusions and changes older than 400 days must lapse.

**Screens**

- **AC38.** Someone who has not seen the feature (the conversation round's mock of the month line alone is allowed, D64), with no seed-specific help and 7 days of seeded data, must create a target from the suggestion and choose where it alerts in under a minute, in a timed walkthrough of the finished form at Step 6, timed by someone other than the author, and must then say what **Running out** means. The tester comes from the customer conversations. With no outside tester, this reads "not proved", and the Goal's wording stays out of any claim.
- **AC39.** Each state in FR10 must render with its sentence, its next step and its tone from the design's table, for each kind's wording, in a render test. A critical and a warning burn must read differently. No recent requests must render both sides of the attention limit.
- **AC40.** The list must render with no ClickHouse available, and must say how old its figures are.
- **AC41.** With no targets, the list must explain what a target is and offer a first one, making no suggestion call. With no services or checks it must say "Nothing is reporting yet" and link to setup. A viewer must be told who can add one.
- **AC42.** Deleting must state the number of days of figures that will go, and when, before it can be confirmed.
- **AC43.** The form must warn on leaving with unsaved changes, as `UnsavedChanges` does elsewhere.
- **AC51.** While a tier holds or an incident is open, an enabled target with fresh status must read **Running out** and sort first, however little traffic there is and whatever else holds, and any other fact must show as a qualifier. Each combination must be tested: 20 requests in an hour; allowance at zero; coverage below 90%; a stopped service; a target with no rule, which adds "No alerts will be sent". **A burn with status older than 15 minutes must read **Not up to date** with "Was using…", no "what is left" line, and still sort among the counted rows. A disabled target with a tier holding must read **Disabled** with the qualifier, and sort last. A resolving burn must read "Back within its allowance".**
- **AC62.** With 2 days of data, a 28-day target must read **Building history**, count coverage and quiet time only from its first event, and hold back the exhausted warning; burn alerts must still run. A no-seed first run must be walked through.
- **AC63.** With status older than 15 minutes, no target may show the words **On track** or a live **Running out** sentence, the headline must say so, and a banner must say how many.
- **AC78.** The *Last month* view must head "N of M targets met" with missed and not judged named, and hold no blended percentage. Copied text and CSV must each contain the period, what it covers, quiet and excluded time, and the fixed label for the kind.
- **AC79.** On a service with no silence rule and no check, the form and target page must say it does not alert if the service stops completely, and link to a prefilled form; a viewer must read who can add one. Request kinds must show the sampling line. A check slower than 2.4 hours a run must show that alerts cannot judge it.
- **AC80.** With seeded probe traffic, a service availability target leaving out `/health` must count no probe requests, must still read **No recent requests** when only probes arrive, and the form must show what it pre-ticked.
- **AC81.** At 375px, all screens and the form, in each FR10 state, must have no page-level sideways scroll, and the name, state and sentence must be visible with no sideways scroll even inside a container. Sentence and next step must come before any table or chart, with qualifiers in FR10's order. The whole create flow must work by keyboard. Validation errors must be tied to fields and to a summary. The chart must have a description with current and peak values. A change from **Counting** to figures, and into or out of **Running out**, must be announced through a polite live region. Checked at Step 1, and again at Step 6.
- **AC82.** When a load fails, `/slos` must show "The targets could not be loaded" with a retry and no headline or empty state, and the target page and each tab must show their own failure line.
- **AC83.** A viewer must see no *New target*, *Edit*, *Delete*, *Restore*, *Disable*, *Add exclusion* or *Remove exclusion*, and following an edit link must show `Refused`.
- **AC84.** The target page must state who is told in each of the three cases, and a list row must show its chip.
- **AC85.** On an `slo_burn` incident, a check-kind one included, at 375px, the page must show since when with zone, the target link, the linked finding, the latest deploy before the start in words, telling "No deploy recorded" from "Deploys are not connected", and links to act from: the finding, the service or check page, the service's errors from the burn's start, and the target. With another project selected the link must switch project. A deleted target must read "This target has since been deleted. What it recorded is above." A finding whose burn was withheld must show the burn's current rate and what is left beside its line.
- **AC89.** Allowance and message wording must be tested per kind: a latency target must never say "failures", and a check target must never say "requests".

**History**

- **AC44.** On a project whose rollups have expired, last month's figure must still show, counted from stored days.
- **AC45.** A definition change must leave older days marked with the old definition, and the month that holds both must say so.
- **AC46.** Deleting a project or organization must delete all F19 rows. `test_deletion_live.py` must pass.
- **AC69.** At the recount limit: an existing day beyond it must not be rewritten, a day first read beyond 29 days must read "loss not known", and removing an exclusion reaching past it must show "removed, figure not recounted".
- **AC77.** A month at 89% coverage must read "not judged" and at 91% must give its figure. The cap at 13 months and "may still change" must hold. A month holding a traffic finding must carry the outage line. A nightly lull and a real outage in one month must be told apart by the longest stretch and the link. A month changed after it ended must say so.

**Exclusions**

- **AC60.** A 3-day exclusion, and a 20% one, must not produce **Not measured**. Failures inside one must leave good, all and failures, no burn may fire inside it, and removing it must restore them. A reason of 9 or 501 characters, a span over 7 days, excluded time over the cap and a 51st live exclusion must be refused. Each creation must send one notice, with no reason in it, under a muted rule too, and to the owners with no rule, and one overlapping an open burn must also show on the incident. An exhausted warning and an exclusion notice must never withhold or be withheld.

**Targets, rules and suggestions**

- **AC70.** A rule narrowed by `slo_ids`, a disabled target, a deleted target, a deleted check and a re-pointed target must each close its open burn incident and remove its position. The service coverage route must work in a project holding a `slo_burn` rule, and a check target must allow a null `check_id` after its check is deleted.
- **AC73.** In Asia/Kolkata and in a zone on its clock-change day, a seeded minute at each side of local midnight must land in the right day, and day minutes must total 1,410 to 1,500 as the zone requires.
- **AC74.** The 201st target must be refused with the protective wording.
- **AC75.** A service at 99.95% with one week at 98.7% must be offered a suggestion from its typical week, with the warning line. Worst and typical below 90% must offer none and say why. Under 7 days must offer the labelled starter.
- **AC76.** A new and an existing project must each hold the default rule, and with it deleted or disabled the form must say "No alerts will be sent".

**Off and failure**

- **AC47.** The structure test must fail when an F19 module imports an AI module, including a planted `mcp` import under `nexovue_api.slos`. It must pass with Step 1's API routes in place, with no exception in the walk, because `lifespan.py` no longer imports `mcp`.
- **AC48.** With the `slo` queue stopped, detection, alerts and every other screen must be unaffected, and the SLO screens must say **Not up to date**, with no **On track**.
- **AC49.** On the fault bed (`tools/fault-bed`), at 99.9% over 28 days, with a target on `payment`, over 5 `paymentFailure` injections in the **full 14-fault order**, so about 21 hours apart and a burn can clear between them (by arithmetic, the default 90-minute slot on this fault alone leaves one incident open throughout), a burn must fire in at least 4 of 5. A burn already open during an injection counts as caught only if it escalates, and the report must say how many. Where a finding also opens, they must be linked with one email delivered, read from the delivery log, and the report must state how many injections had both. A 7-day no-fault run must report every burn, read against the demo's baseline failure rate: **a reported number, not a fixed zero, until a first run shows that baseline. These are demo results, and must say so.** Without a machine to run it, this reads "not run".
- **AC50.** NFR1 must be measured and written into `status.md`, as F6's AC29 was: with all four kinds, failing if one is absent from the measured mix; one project at 200 targets; each statement's time and memory apart; the largest project's job beside the average; at least one full round of day rewrites; claim time with and without the withholding statement, at 0 open burns, at 200 open across the install (a guess at a bad night), and with one project at 200 targets all burning; and F6's AC29 re-measured beside it, which must still pass (judged within 90 s, no OOM kills, children under the limit), with SLO and detection lag side by side. `scheduler-slo` must show no OOM kills at its ceiling, and probes and alert sweeps no added lag.
- **AC64.** With 20 history jobs queued, burn must still be judged every minute. With one organization queuing history for 200 targets, another organization's burn must fire within 2 minutes (a guess). A definition change during a running history read, and a crash between commit and queueing, must still end with every day recounted under the new version.
- **AC65.** If a counts run is skipped, the next must do the work. With a job budget smaller than the number of projects, at 2 times overload, a project at the tail of the order must still be judged within 5 minutes of its last judgment (a guess; oldest-first at 2 times overload should be about 2 by arithmetic), and `nexovue_scheduler_slo_oldest_status_age_seconds` must show it.
- **AC66.** Two overlapping `judge_slos` runs must not both judge a project, and an older minute must not overwrite a newer `slo_status` or move a position back.
- **AC67.** A stopped prober and a stopped metrics worker must each show **No recent runs**, lower coverage through missed runs, and judge no tier, at 60 s, 300 s and 3,600 s intervals.
- **AC68.** A structure test must hold `QUEUE_CAPS` for `scheduler-slo`'s queues within its slots, that no other role takes them, and that `scheduler-light`'s queues gain none of them. `test_compose_contract.py` must hold the role in every layout.
- **AC86.** Time from save to first figure at 30 days of history must be measured and written into `status.md`. It is a reported measure, with no pass mark.
- **AC87.** A service left out by the cap must show as not counted for N minutes, not as quiet. This is a condition of F19's release, because P2 is.
- **AC90.** The list must be measured at 200 targets against NFR3, and the result written into `status.md`.
- **AC91.** With `scheduler-slo` stopped, a delivered message must arrive from `scheduler-deliveries` within 16 minutes of the last judgment (FR13b's 15 minutes, one 15-second sweep, and the rest of that minute for delivery), once, however many sweeps run and even if every rule is deleted while it is open, and one "judging again" after restart. So too with every tier below 90% coverage for 15 minutes. A thin target must not cause it.
- **AC92.** At 99% and at 95%, with day traffic and a night at 5% of average of at least 20 requests an hour, a service failing every request at night must page within 1 hour. A service failing 2% at the same night traffic must not pass the floor.
- **AC93.** A burn open, then 20 hours with no traffic, then healthy traffic must resolve 10 minutes after judging resumes.
- **AC94.** A burn and a finding sharing one PagerDuty alert, the service stops, the finding closes: the alert must stay open, then resolve when the burn closes 24 hours after the last event, and the people told must get the "No longer judged" message. With the same silence caused by lost minutes, the burn must not close.
- **AC95.** The regression gate, because existing tests hold no burn: (a) with an open burn on service A, a finding and a threshold on service B in the same project must deliver exactly today's messages; (b) with an open latency burn on A, an availability-only finding and a threshold on A must deliver both; (c) a withheld finding opening, then a joined, escalated, flapping and resolved sequence, must match FR24 row by row; (d) a forced exception in `slos.queue_jobs` must leave detection jobs queued and the sweep recorded; (e) the existing outbox suite must pass a second time with a fixture holding an unrelated open burn.
- **AC96.** With the AI role stopped and AI off, and again with AI on, one seeded burn must produce identical `slo_status`, incident, notification rows and message text. A structure rule must fail if a `slos/` module imports a network client.
- **AC97.** An F4 ticket on every `/slos` and `/slo-readings` route must get 403.
- **AC98.** With a longest quiet stretch of 40 minutes, the headline must not count a silence at 79 minutes and must at 81. With 8 hours, a silence past 16 hours must count. A silence of 25 hours must always count, and an open traffic finding must count at once.
- **AC99.** Each row of FR21 must be tested both ways, including one target under two rules, and a latency burn with a `service_latency_p95` threshold.
- **AC100.** A check target's floors must hold: below 10 runs nothing judges, and a short window must be at least twice its interval.
- **AC101.** The form must show FR4's fast-failure line and the highest offered boundary, and refuse above it. FR7's latency suggestion must pick the boundary holding 95% of requests. FR8's check wording must state what the interval allows.
- **AC102.** A future-dated exclusion must change nothing until it starts. After a restore, a recount must fill the gap the source still holds, and mark what it cannot.
- **AC103.** While Counting, the floor must use the counts so far, and FR16's 2 with none. The floor must use the capped rate at low objectives.
- **AC104.** A burn falling from 14 to 4 times without a severity change must show the lower rate on the incident page, with "As of".
- **AC105.** Two targets on one service must give different labels in a message and on the incident page. A target with the same definition as a live one must be refused beside the form.
- **AC106.** With ClickHouse down, the form must say the suggestion could not be read, offer a retry, and still save.
- **AC107.** With a fake clock, an open page whose refreshes all fail must stop showing **On track** after 15 minutes and say when it last read.
- **AC108.** With a project zone different from the browser's, the target page and the incident page must show the same start time, and a time before today must carry its date.
- **AC109.** In a live API test, the point `/slo-readings/{id}/burn` returns at `slo_status.evaluated_minute` must equal the rate `slo_status` stored for that minute and the same tier, with a burn holding, with no tier holding (On track), and after the holding tier changes, when the response must name the new tier. With no tier able to judge there is no such point and none must be shown. A minute with no events must be a gap, not 0. The render test checks display only: the sentence's number, and the gap, as given.
- **AC110.** The top N rows of the list must be the N in the headline, with critical before warning and shorter time left first.
- **AC111.** Failures exactly equal to the allowance must read "exactly on the target", and one more "Target missed". A missed target must never print a figure at or above its objective. A target allowing 2 failures with 5 must read **Nothing left**, not **Too little traffic**, and count in the headline.
- **AC112.** A month with a deleted target, a not-judged target and a met target with an outage must give the right N, X, Y and M, and name how many met with time not counted. After the purge, the month's result must still show.
- **AC113.** A day 60% below its weekday, with no finding, must carry FR27c's line onto its month, and a 40% one must not.
- **AC114.** A request-kind target answered Yes on sampling must start with alerts off, say why, and carry the answer in its copied label. Not sure must link to how to check.
- **AC115.** A service or endpoint name starting `=`, `+`, `-` or `@` must be safe in the CSV.
- **AC116.** A deleted target's name must be free to reuse, and restoring into a taken name must ask for a new one.
- **AC117.** Where a month reads met and the rolling window reads missed, the page must carry "different periods".

AC61 is not used: the number was retired in review.

## Dependencies and constraints

**Preconditions**

- **P1.** F6 is merged. Findings, `finding_incidents`, the outbox's finding subject, and migrations 0041 to 0050 exist only on this branch.
- **P2.** **A separate change must land first:** spans from the browser door must not enter `service_stats_1m` or `endpoint_stats_1m`;
  readings named `synthetic.*`, or carrying a `synthetic.check.*` resource attribute, are accepted only from the prober's door; and the
  2,000-service cap must stop favouring names early in the alphabet and record who went over. See the design's *Forged spans*. It also affects F6, which reads these rollups today. It has no change record yet. It blocks Step 4, F19's release, and any public claim.
- **P3.** F21's rules, channels, outbox and quiet hours, as built.

**Takes from the chain, but needs none of it.** F28's deploy markers appear beside a burn when they exist, and F22 records a burn on the timeline when it exists. F3's explanation lives on the finding, and F19 screens only link to the finding, so with AI off or no finding they show nothing extra. F19 builds with or without them, and holds no AI link.

**Gives.** To F20: the `slo_budget` panel, stored by name (D60), reading the Postgres-only function behind `GET /slos/{id}/budget`. To F21: the `slo_burn` kind and the withholding rule, which F21 may later widen. To F4: nothing in this version. F4's spec and design do not mention `/slos`, so adding a tool later needs its own suite covering name, description and reason.

**Constraints.** ADR-022 (a finding is the one record), ADR-012 (scheduler reads), ADR-023's account pattern (F19 needs no new account, because it writes no ClickHouse table). `vision.md`: never per seat.

**Recorded decisions this contradicts:** none. Catalog wording is narrowed as above.

## Decisions taken

*Recommended by the authors, founder may overrule.* Research R1 to R18 stand unless changed here.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | Default window | 7 / 28 / 30 / 90 / calendar | **28, with 7 also offered** (R1). Google's default. It fits Free's rollups. Because the window is by whole days (D4), it holds the same mix of weekdays only approximately, and the figure can step at midnight. Buyers used to "30 days" need one line |
| D2 | History | (a) Raise the TTL on `service_stats_1m`. (b) New `service_stats_1h`. (c) Daily counts in Postgres | **(c), changing R2.** A self-hosted organization is on Free's row, so rollups last 30 days, and (a) or (b) could never show a whole last month there. (b) would also leave endpoint and check targets without history. (c)'s weaknesses are handled: days are rewritten until final, and definitions are stamped. See the design's Proposed ADR |
| D3 | Kinds | Four, plus endpoint latency | **Four** (R3, R4) |
| D4 | Rolling window edge | By the minute / by whole days | **By whole days in the project's zone.** It reads stored days, so the list costs no ClickHouse read. The figure moves in a small step at midnight, and it says "by whole days". Burn alerts are by the minute regardless |
| D5 | Latency threshold | Snap and show / interpolate | **Snap and show** (R5) |
| D6 | Burn rates | Google's three / 24-hour variant / customer-set | **14.4, 6 and 3 over 24 h, with 16.8, 5.6 and 2.8 for 7 days, fixed** (R6). The 7-day rates spend much more of a 7-day budget before firing, as the table shows; they are what Datadog and OpenObserve publish, and this is accepted so a 7-day target is not noisy. A budget floor is added (D28) |
| D7 | Low objectives | Refuse / cap the rate | **Cap at 90% of the largest possible rate.** Datadog blocks the alert; capping keeps one simple rule |
| D8 | Low traffic | Half-window / minimum failures / both | **At least 2 failures and at least 20 events (10 for checks), plus D28's budget floor** (R7). Guesses |
| D9 | Missing data | Good / bad / gap | **Gap** (R8). Below 90% coverage alerts neither fire nor resolve. The 90 is OpenObserve's number and a starting guess |
| D10 | Suggestion | None / model / arithmetic | **Arithmetic** (R15). It has never been tried on real traffic |
| D11 | Message rule | Both / first only / hold and merge / first only with limits | **First only per channel where a burn is involved, limited to the same news, the same project and the last 60 minutes, and released when its reason ends** (R9, R10, changed by review). Holding breaks "an alert never waits". Widening it to all findings and thresholds is left to F21. Rival grouping was not compared (research), so no noise claim rests on this until T6.8 |
| D12 | Routing | New rule required / default / built into the SLO | **A default rule to owners** (R11) |
| D13 | Planned work | Ignore / reuse maintenance windows / one-time exclusions | **One-time, visible, audited, and may be entered ahead of time. Recurring ones later,** a small change from R12. Check targets already skip maintenance runs |
| D14 | Changing a target | Reset / recompute | **Recount what the source still holds; stamp the rest** (R13) |
| D15 | Count limit | None / small tier / protective | **200 per project, stated as protective** (R17). Guess |
| D16 | Words on screen | "Error budget" / plain | **The kind's plain words (FR12), with states named Running out and Nothing left.** "SLO" and "error budget" appear in the page title and on the *Definition* tab, so they can be found, and AC13 allows exactly those: nav *Targets (SLOs)*, page title *Reliability targets (SLOs)* (R16) |
| D17 | Forged browser spans | Ignore / inside F19 / separate change | **Separate change, first** (R18). Tracing the code confirms the path. See the design |
| D18 | A snooze or mute on a service | Applies to burns / does not; at opening / at sending | **A snooze applies to messages, decided at sending, so it starts and ends when the person meant. One message if the burn outlasts it. A mute does not apply,** changed by review: a mute lasts up to 30 days (`detection.py:115`), and noise is why a person mutes a statistical detector, while a burn is an alert they agreed to. The budget still spends. A critical is held back during a snooze: whoever snoozed has said they know, the control says so, and it lasts at most 4 hours (`detection.py:116`) |
| D19 | A check target and a service | No link / optional service | **An optional service,** used only to join findings. A check has no service of its own (`models.py`, `Check`) |
| D20 | Who may change a target | Admin / anyone who works | **Owner, admin, member,** as foundation section 5 says. Accepted because every change is visible to all, and a deletion can be undone for 30 days (D29) |
| D21 | Disabling | Stops counting / keeps counting | **Keeps counting,** so re-enabling leaves no hole |
| D22 | Where it sits in the sidebar | Home / Signals | **Home, after Alerts.** It answers "are we meeting our target", as those two answer "what is wrong" |
| D23 | Time zone for days | UTC / project's | **Project's** (`projects.timezone`), so "October" means the customer's October. Changing a zone leaves final days alone |
| D24 | Exhausted budget | Nothing / page / one warning | **One warning,** said as an event, as a new error is (`new_errors.py:170-200`). No new notification kind |
| D25 | Instrumentation that never fails | Say nothing / warn | **Warn, plainly and with doubt,** because nexovue cannot know for sure |
| D26 | What a screen's state reads from | The incident / `slo_status` | **`slo_status`,** which the job writes whatever the rules are. An incident needs a rule, and the list reads no incidents. So a target with no rule still reads Running out |
| D27 | Order of states | Status first / burn first / two parts | **Burn first among live facts, and every other fact as a qualifier line,** so a page and its screen agree. **Changed by review:** Disabled and Not up to date come before it, because a burn sentence on stale status says something false in the present tense, and FR32 says a disabled target does not alert. AC48 and AC51 could not both pass otherwise |
| D28 | Night traffic and pages | No floor / a budget floor / a floor with an exception | **A tier needs at least half its nominal spend in failures,** so 2 failures in a quiet hour on a large budget does not page. **At least half failing in both windows skips it,** because by arithmetic a complete night failure might otherwise never page on a 99% target. Below 20 requests an hour, it takes up to 6 hours. Guess |
| D29 | Deleting | Hard delete by member / admin only / soft delete | **Soft delete, 30 days, undoable by anyone with `CONFIGURE_SLOS`.** Admin-only restore was proposed. Rejected: restoring cannot hurt, and needs no new action across C14's five copies |
| D30 | Who a figure may be quoted to | Internal and board / customers too | **Internal and board by default.** Founder decision 2. Research's buyer often gives it to a customer, so the demand gate has a branch for that |
| D31 | Taking the number off the screen | Nothing / copy and CSV / monthly digest | **Copy and CSV, each carrying the label.** A digest later: it needs its own schedule and a new notification kind |
| D32 | Night quiet | 15 minutes for all / by usual gap / state and attention apart | **State and attention apart.** The state always tells the truth at 15 minutes. The headline waits for twice the usual gap, capped at 24 hours, so a weekend-idle service cannot hide a dead one for days |
| D33 | Health checks | Out of scope / offer on availability | **Offer on service availability** (research said worth offering). Arithmetic: three pods probed every 10 seconds is about 725,000 good requests in 28 days |
| D34 | First-week suggestion | None / labelled starter | **A labelled starter of 99.5%,** since the first week is the evaluation |
| D35 | AI path through incidents | Mark it untrusted / keep the name out | **Keep the name out.** `subject_label` is composed by nexovue from kind, source name, objective and window, so nothing needs a new label, and F3 sends no free text. A source name is still a name, aliased or labelled by those features |
| D36 | Exclusion notices | None / only when overlapping a burn / every one | **Every one, once.** Exclusions are rare, and only telling at creation closes the loophole of an exclusion entered ahead of a planned outage |
| D37 | Tenants seeing nexovue's lost minutes | Hide / show when, never why | **Show that nexovue may have lost minutes, and when, never why or how many organizations.** This changes `holds.py`'s stated rule, recorded in the design |
| D38 | Demand | Stop at Step 1 / gate before alert work | **A gate before alert work** (*Demand gate* in the plan). The one round (foundation item 8) is held before Step 1, so only a mock of the month line can be shown; Steps 1 to 3 do not exist yet (changed in review, 2026-11-02). The bar is D62. If half or more name a customer as the asker, the founder takes legal advice before the month's wording is fixed |
| D39 | Where SLO work runs | `scheduler-light`, 8 slots / `scheduler-light`, 12 or more / a new role | **A new role, `scheduler-slo`.** F3's floor keeps probes and sweeps 4 of `scheduler-light`'s 8 slots, so all capped queues share the other 4, and F6, F3 and F28 already ask for 6. F19's 4 more would make 10 asking for 4. 12 slots at the 64 MiB child limit is 768 MiB, its whole ceiling, so the memory saved is nothing, and a failure would be shared with probes. See the design's ADR. The earlier draft rejected a new role on memory, by a comparison that no longer holds |
| D40 | A burn on a source that stopped | Stays open / closes at 7 days / closes at 24 hours | **24 hours,** the longest long window: nothing can hold without new events, so it cannot be misleading, and if failures resume a new page is right. People told are told. A reviewer proposed machines only and 7 days. Rejected: no person chose this, so FR24 owes an ending, and 7 days holds a PagerDuty alert open too long. Guess |
| D41 | The worst state's name | Too fast / At risk / Running out | **Running out,** changed by review. "Too fast" reads as good news in a monitoring tool, worst on a latency target. "Running out" names the harm for every kind and pairs with Nothing left. AC38 asks the outside tester what it means |
| D42 | The copied sentence | One sentence / one per kind | **One per kind.** One sentence said "requests" on a check, against AC89 |
| D43 | A target's name in a message | Read at sending / composed label only | **Composed label only.** A name may hold a customer's name, and a message needs no join to F19. A reviewer proposed reading it at send time. Rejected: no gain over the label |
| D44 | Which comes first | Burns, then months / months, then burns | **Months first, then the gate, then burns.** The month's figure is the stated buying reason, and a reporting-only product can stop before the riskiest change |
| D45 | Nexovue going blind | Screen only / a message | **A message, critical, from another process.** An alert system that fails silently is worse than none. F6's own stop notice sets the level (`notify.py`, `STOPPED_SEVERITY`). A reviewer proposed warning. Rejected: it would wait out quiet hours, while no burn can page |
| D46 | A deleted target and past months | Gone at 30 days / stays in past months | **Its past months stay for 400 days,** so "5 of 7" cannot become "5 of 6" by deleting a miss. One small table |
| D47 | A company-wide line | Now / later | **Later.** A project is an environment and a read takes one (foundation section 5). The view says so, and a buyer will ask |
| D48 | Upstream sampling | A line / a required question | **A required question on request kinds,** Yes starting alerts off, because tail sampling means false pages from day one. Costs a click against the minute's walkthrough |
| D49 | The 60 minutes passing | Releases what it held / stops new withholding only | **Stops new withholding only.** Otherwise every problem over an hour old sends a second message |
| D50 | A snooze and PagerDuty | People only / also pages | **Also pages' news.** A page wakes a person. An ending still goes, and a webhook gets all. A reviewer's fix named people's channels only |
| D51 | Leaving endpoints out | Subtract / `NOT IN` on the endpoint table | **Subtract, minute by minute, with a guard.** A reviewer proposed `NOT IN` as simpler. By arithmetic it costs about 10 times the named read, because it reads every endpoint |
| D52 | One exclusion across many targets | Now / later | **Later.** It is real pain for one planned upgrade over 7 targets, and it complicates the notice. Exclusions are expected to be rare, a guess. Fix if a customer says so |
| D53 | Backfilling a service target from `endpoint_stats_1m` past 35 days | Do it / do not | **Do not.** It would give paid plans a long start, with a second source that may not add up to the first, since only service rows are capped. Figures build up forward |
| D54 | Copying a target between projects, or targets as code | Now / later | **Later.** The conversations ask. Listed where rivals are ahead |
| D55 | The no-AI walk from the API root | Fail on `lifespan` and leave Step 1 red / leave the API root out / stop at `lifespan` only / move `mcp`'s import out of `lifespan.py` | **Move the import, recommended by the authors, founder may overrule.** `lifespan.py:21` imports the SDK only to type one argument, and F20's D38 reached the same answer, so an exception here would be undone a stage later. One shared task: whichever of F5, F19 or F20 lands first does it, with the walker in `nexovue_platform/import_walk.py` |
| D56 | What "judged recently" reads | Skip `evaluated_at` when `breached` is None / a new "last judged" column / `slo_status` | **F19's save leaves `evaluated_at` alone when `breached` is None.** It needs no new column, and the outbox reads only F21's table. No other code reads that column |
| D57 | `rule_name` and AI | Remove it from burn incidents / treat it as a name | **Treat it as a name, and test it hostile.** It is on every incident kind and F3 already handles it so. Removing it for one kind gains nothing. The rule FR25a writes is named `Reliability target rule <first 8 characters of the target id>`, so a target's name never enters by that road |
| D58 | How the blind notice stays single | A project-subject notification kind, as F6's stop notice / its own path on an incident with a null `rule_id` | **Its own path, with a partial unique index limited to `slo-project:` keys.** F6's kind reaches owners only, and FR13b must reach the rules' channels. Without the key limit, deleting two rules would fail on the index |
| D59 | Rate limit for `/slos` | All on the telemetry tier / a second router with its own prefix | **A second router, `/slo-readings`.** The tier follows a static prefix, and the list that must answer with ClickHouse down must not share a concurrency cap with reads hanging on it |
| D60 | How F20's panel names a target | By id / by name | **By name.** One dashboard then works across environments, and names are unique per project. Its budget function reads Postgres only, and the burn line is a separate function |
| D61 | Which tier the chart plots when several or none hold | The sentence's tier always / a fixed tier | **The holding tier, the fastest if several; with none holding, the slowest able to judge,** named in the response. It is what lets AC109 compare one stored value with one point |
| D62 | The demand gate's bar (plan Q6), decided 2026-11-02 | Opinions count / past events only; 1, 2 or 3 of the round | **Pass when at least 2 people in the round describe a real time in the last 12 months when someone asked them for a reliability number, name who asked (role is enough), and say what they sent was a monitor's uptime figure that did not answer it, or a number they built by hand.** "That would be nice" or "I would use it" does not count. Why: the round is 5 to 8 people (foundation item 8), so 2 is a quarter or more, and one story alone could be a one-off. Past events, not opinions, is the standard rule for interviews (Rob Fitzpatrick, *The Mom Test*; summary at [sachinrekhi.com](https://www.sachinrekhi.com/the-mom-test-rob-fitzpatrick)). The bar is a way of measuring, so it is decided here. **What to do if it fails stays the founder's at the time**, because stopping the alert work changes what is sold (plan, *Demand gate*) |
| D63 | Can a real browser span be `SERVER`? (plan Q4), decided 2026-11-02 | Filter the browser door by span kind / by door | **No real browser span is `SERVER`, so P2 filters by door, not by kind, and loses nothing.** nexovue's own browser SDK creates one span, the page view, with no kind set, so it is `INTERNAL` (`packages/browser-sdk/src/rum.ts:68-71`, sent as kind + 1 by `otlp/spans.ts:38`). OpenTelemetry's fetch instrumentation sets `SpanKind.CLIENT` ([fetch.ts](https://github.com/open-telemetry/opentelemetry-js/blob/main/experimental/packages/opentelemetry-instrumentation-fetch/src/fetch.ts)), and document-load sets none, so `INTERNAL` ([instrumentation.ts](https://github.com/open-telemetry/opentelemetry-js-contrib/blob/main/packages/instrumentation-document-load/src/instrumentation.ts)), both read 2026-11-02. A `SERVER` span on the browser door is therefore forged or misconfigured. P2's record still owns the change itself |
| D64 | Finding AC38's outside tester | Wait until Step 6 and look then / ask in the conversation round | **Ask in the round.** At the end of each conversation in foundation item 8, ask whether the person will do a 15-minute screen test later. The first who agrees is AC38's tester. Everyone in the round sees the mock of the month line, so "has not seen the feature" means has not seen the form or the state names: **the round's mock shows a month figure only, and never the words Running out or Nothing left** (changed in review, 2026-11-02; otherwise no one in the round could qualify, or AC38's question would be answered in advance). Why: the round happens long before Step 6, and asking for a small later commitment is also a sign of real interest. Naming no person is needed. Without a yes, AC38 still reads "not proved" |
| D65 | Endpoint latency targets after T6.2's cost figure | Founder decides on the figure / a normal new request | **Left out of this version. If T6.2's figure looks affordable and customers ask, adding it is a new request through feature-work,** with its own spec, which the founder approves like any spec. Why: research R4 and foundation leave it out, the form already explains what a service latency target mixes (FR5), and a storage figure alone decides nothing |

## Founder decisions needed

**Both approved by the founder on 2026-11-02, as recommended** (foundation, *Founder decisions*, 20 and 16). The text is kept so references still point to it.

1. **Approved 2026-11-02: 13 months on every plan, Free included.** **How long the monthly figures stay on the hosted Free plan.** In plain words: F19 keeps one small row per target per day, so it can show "last month" and the months before. Our plans differ by how long they keep data (foundation section 10), and Free keeps rollups only 30 days. Keeping these daily rows 13 months on Free gives Free customers something the plan table does not list. **Recommendation: 13 months on every plan.** The month's figure is the main reason to buy this. The rows hold numbers only, no telemetry text, and cost little (a guess of 80 MB at the F6 cost target). FD20 already says no Phase 2 feature is gated by plan. **What it goes against, checked in review:** foundation section 10 assumes "Free's 30 days of rollups limit SLO windows", and F6's readings already expire at the plan's derived retention (`apps/scheduler/src/nexovue_scheduler/detection/store.py:296-300`). Rivals sell long history: Grafana Cloud's Free plan keeps 14 days, and 13 months of metrics is on its paid Pro plan ([pricing](https://grafana.com/pricing/), [free tier](https://grafana.com/products/cloud/free-tier), read 2026-11-02 through search results). So 13 months on Free gives away something rivals charge for. **What 30 days would cost:** a calendar month has up to 31 days, so Free would never show a whole last month, only the 28-day rolling figure. And a self-hosted install sits on Free's row (D2), so the prune rule would have to tell hosted Free from self-hosted (`deployment.py`), or self-hosted loses its months too. **Why it is yours:** what each plan includes is pricing and packaging. If you choose otherwise, that one prune rule changes, nothing else.
2. **Approved 2026-11-02: internal and board use only,** with the label below, and customer quoting added to the lawyer's reading (foundation item 16). **Who a figure may be quoted to.** In plain words: may a customer of ours send this number to *their* customers? A figure can read 99.95% across an outage that sent no data, and a member can add exclusions, so handing it to a customer is close to contract (SLA) reporting, which this feature leaves out. **Recommendation: internal and board use only.** Screens and docs say: "This figure is for your own team and board. It is not for customer promises. Ask your lawyer what to use for those." They name no feature as suitable, because pointing to one would itself steer people to quote it. The copied label always says it is not a contract figure. If half or more of the conversations name a customer as the asker, take legal advice before the month's wording is fixed. **Why it is yours:** it is legal exposure and a promise about what the product is for. If you want to encourage customer use, take legal advice first.

**Settled by the foundation, not asked again** (approved 2026-11-02, [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions)):

- **Self-hosted retention** was this list's item 2. It is foundation item 6: an operator setting for raw and derived retention, today's values until the bed measures disk use. F19 does not depend on it.
- **P2, the browser-door defect.** Foundation item 7: one change, taken before Stage 1 reaches a customer. Its owner is F28 (foundation item 22, approved 2026-11-02). By reading the code, anyone holding a page's public site key can send spans under any service name, and they count in the rollups F6 judges. It was not run. D63 settles the one technical question P2 had from F19.
- **The customer conversations.** Foundation item 8: one round of five to eight, before F28's Step 1b. F19's questions go into that round: "When did someone last ask you for a reliability number, who asked, and what did you send them?", "Would this change a purchase?", "We already get an uptime figure from our monitor: is that enough?", "Would you need to define targets in code?", and, at the end, D64's request for a later screen test. D62 reads the answers.
- **The test server and bed.** Foundation items 4 and 9, changed 2026-11-02: no rented server. AC49's weeks-long run waits for Oracle after deployment, and reads "not run" until then.

## Open decisions

None. Both founder decisions were approved on 2026-11-02. Decisions made in review are D26 to D61. Own questions answered on 2026-11-02 are D62 to D65 (status, *Own questions answered, 2026-11-02*). Every review finding's outcome is in [`status.md`](status.md), *Reviews*.

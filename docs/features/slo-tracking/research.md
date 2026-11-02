# Research: SLO tracking (F19)

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Input to F19's specification and design. Nothing here is approved, and no code
> is written from it.
>
> **How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present, so
> line numbers will drift. Nothing was run: no test, no query, no measurement. Every limit and time below labelled
> "guess" is a guess. Competitor pages were read on 2026-11-02 through a summarizing tool. Check a page again before
> quoting it in public.
>
> **[`spec.md`](spec.md)'s *Must not be claimed* list and its claims table override any claim below.** This document records what was found, and some of its early conclusions were later narrowed. It was corrected on 2026-11-02 after the persona reviews (`status.md`, *Reviews*), and rival rows were re-read that day, partly through a summarizing tool. Where that tool and the page disagreed, the page's HTML was read: the tool's Datadog table was wrong.

## The short version

1. **What it is.** A customer sets a reliability target, such as "99.9% of `checkout`'s requests succeed over 28 days".
   Nexovue shows how much failure that target still allows, and warns when it is being used up too fast.
2. **Nexovue is well placed for it.** It already counts every request before sampling. The counting method is the
   easy part. The hard parts are honesty about what was not measured, and not adding alert noise.
3. **The first version should offer three kinds of target:** service availability, service latency, and endpoint
   availability, all counted exactly. A fourth, availability from a synthetic check, covers the services the
   other three cannot judge. Endpoint latency cannot be counted exactly, so it must not be offered.
4. **Use 28 days, not 30.** It is Google's recommended default, it always holds the same mix of weekdays, and it fits
   Free's 30 days of rollups where 30 sits on the edge.
5. **Five things in the code change the plan:**
   - A service that stops completely looks perfect, because zero requests contain zero failures.
   - "No second alarm" exists today only for PagerDuty. Email and Slack would get both messages.
   - The existing link between an incident and a finding cannot work for an SLO without changing it.
   - A customer can see outage history change for up to 7 days, because late spans are still counted.
   - The cap of 2,000 services per minute keeps the first names in sort order, which lets anyone able to invent
     service names push real services out of the counts.
6. **Be honest about demand.** Nothing found shows that SLOs win a purchase. Their clearest commercial value is
   filling a gap against SigNoz, whose request has been open since 2022. Keep the feature small.

## Words used here

| Term | Meaning |
|---|---|
| **SLI** (service level indicator) | The measured number: good events divided by all events |
| **SLO** (service level objective) | A target for that number over a period |
| **SLA** (service level agreement) | A contract promise, usually with money attached. Nexovue does not do this |
| **Error budget** | The failure the target allows. At 99.9% over 1,000,000 requests, that is 1,000 failed requests |
| **Burn rate** | How fast the budget is being spent. 1 means it runs out exactly at the end of the period; 14.4 means 14.4 times that fast |
| **Count-based** | Each request is judged good or bad, and the requests are counted |
| **Time-slice** | Each minute is judged wholly good or bad, and the minutes are counted |
| **Rolling window** | The last N days, moving forward every minute |
| **Settled minute** | A minute the pipeline has finished writing (`detection/settle.py:1-18`) |
| **Coverage** | The share of a window that was actually measured |

## What exists (read from the code)

No SLO code exists. A search of `apps`, `packages` and `tools` for `slo`, `error budget` and `burn rate` matched only
a stylesheet in the vendored demo (`tools/otel-demo/upstream/src/flagd-ui/assets/css/app.css`).

### The numbers F19 can read

| Source | What it holds | Exact? | Kept for | Evidence |
|---|---|---|---|---|
| `service_stats_1m` | `request_count`, `error_count`, 64 latency buckets, per service and minute | **Yes, all three.** Every inbound span, before sampling | **The lesser of derived retention and 35 days** | `000035_service_stats_1m.up.sql:1-11,31-32,50`; `derive/services.go:14-55` |
| `endpoint_stats_1m` | `request_count`, `error_count`, a quantile state, per service, span kind, span name and minute | **Counts yes. Latency no**: the state is fed from a sample of at most 8,192 durations, and it cannot answer "how many were under X" | Derived retention, with no 35-day cap | `000008_endpoint_stats_1m.up.sql`; `derive/endpoints.go:30-33,130-134`; `latencysample/reservoir.go:26` |
| Synthetic check readings | One reading per run, with up or down, whether it was the confirmed one, and whether it fell in maintenance | Yes, for runs that count | The metric's own `retention_days`; the value that applies was not traced | `synthetics/queries.py:50-60`; `000004_metrics_gauge.up.sql:38` |
| `check_state_changes` | Each confirmed state change | Yes | Not traced | `models.py:573-599` |

**Plan values.** Free keeps rollups 30 days, and Pro, Scale and Enterprise 395 (`0005_plans_table.py:13-18`). A search of the
migrations found no `UPDATE plans`, so the seeds stand, as far as migrations go. What a self-hosted organization's plan row
resolves to was not traced, and F19's design must trace it, because self-hosting is the main route.

### What each reading really means

- **A request is an inbound span:** kind `SERVER` or `CONSUMER` (`derive/endpoints.go:100-102`). So a queue consumer and a
  web route are mixed in one service's numbers.
- **A failure is a span with status `ERROR`,** and nothing else (`traceassembly/stages.go:19`). OpenTelemetry's rule is that a
  server span's status "MUST be left unset" for a 4xx, and "SHOULD" be `Error` for a 5xx
  ([spec](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)). That suits an availability target, with two limits:
  instrumentation that never sets a status reports 100% forever; and a customer cannot redefine "good" from the rollups.
  `http_status` lives on `spans` only (`000001_spans.up.sql:30`), which are sampled.
- **Latency buckets are about 19% wide** (`derive/services.go:16-17`; my arithmetic gives 18.8%). A latency target can only sit on a
  boundary. By my arithmetic from `services.go:32-39`, the nearest boundaries are:

  | A customer asks for | Nearest boundary | Off by |
  |---|---|---|
  | 100 ms | 103.7 ms | +3.7% |
  | 300 ms | 290.9 ms | -3.0% |
  | 500 ms | 487.2 ms | -2.6% |
  | 750 ms | 687.1 ms | -8.4% |
  | 1,000 ms | 969.0 ms | -3.1% |

  A duration above 60 s folds into the last bucket (`services.go:16-19`), so targets above that are meaningless.
- **Past minutes change.** A span's own time is trusted up to 7 days back (`clocktrust/clocktrust.go:44`), so a replayed outage
  reaches its real minutes (`derive/clockskew_test.go`). That is right for the pipeline. It also means a budget figure for a past
  minute is not final for 7 days.
- **Services over the cap are not counted.** Each tenant keeps 2,000 services per minute (`derive/services.go:55`), and
  `capServices` keeps the first in sorted order (`:205-222`). The rest are logged (`traceassembly/pipeline.go:707`). A service's
  name decides whether it survives, so `aaa-1`, `aaa-2` and so on displace `checkout`.

### Parts to reuse

| Part | What it gives | Evidence |
|---|---|---|
| Position per rule and subject, which survives a restart | `step()`, a pure function; `alert_evaluations` | `alerts/thresholds.py:74-107`; `models.py:1317-1341` |
| Too little traffic moves nothing, and an open incident stays open | Same function | `thresholds.py:86-90` |
| Opening, escalating and resolving; one open incident per breach; a flap guard; a severity that only rises | `incidents.fire`, `clear` | `alerts/incidents.py:101,206-284,315-386`; `alerts/model.py:36-37` |
| One failed judgment costs only that subject | `judged_alone` | `incidents.py:427-443` |
| Routing to all six channels; warnings wait out quiet hours, criticals never do | The outbox | `alerts/outbox.py:15-21` |
| A tenant-bound telemetry reader, which alone may hold the raw driver | `TenantScopedReader` | `alerts/telemetry.py:1-12,125-149` |
| The read-only account, in the worker that runs alert sweeps. Query time is capped | `nexovue_reader` | `deploy/clickhouse/users.d/reader.xml:23-46`; `deploy/compose/nexovue.yaml:349-359` |
| Which minutes are settled, and which were lost by nexovue's own intake | `settle.py`, `holds.py`, `intake_refusals` | `detection/settle.py:1-35`; `detection/holds.py:1-13` |
| "Only a confirmed run counts, and not in maintenance" | `_COUNTED` | `synthetics/queries.py:50-60` |
| Exact failure share | `readings.failure_ratio` | `readings.py:32-34` |

### What must change

| Change | Why | Evidence |
|---|---|---|
| New condition kind `slo_burn`, with a migration | The vocabulary is frozen in a migration and compared by a test | `alerts/model.py:24-31,234-262`; `0041_anomaly_detection.py:113` |
| New message text | `_said` chooses words by kind | `alerts/messages.py:136-175` |
| New link path to findings | See *The second alarm* below | `incidents.py:107-118,357-366`; `detection/project_job.py:344-355,1405-1418` |
| New action `CONFIGURE_SLOS` | Every write needs one, or the build fails. Foundation names it | `auth/permissions.py:16-42`; foundation section 5 |
| `/slos` under the telemetry rate limit | It reads ClickHouse, and a test compares the list with the routers | `ratelimit/policy.py:234-249` |
| `slo_targets` in the deletion table | `test_deletion_live.py` requires it | `organizations/tables.py:69-84`; foundation section 8 |
| `/slos` in the closed nav list | A test holds the list to the router | `web/src/app/routes/route-manifest.ts:18-34` |
| The comment "nothing else reads this table" | F19 makes it false, and F6's tuning assumed short reads | `000035_service_stats_1m.up.sql:25-32` |

### Corrections to earlier documents

| Document says | The code or page says | What to do |
|---|---|---|
| Catalog: targets on "services/endpoints" | Endpoint availability yes, endpoint latency no | Narrow it in the spec |
| Catalog: "into findings feed" | Foundation already changed this (FD16). Confirmed: a finding allows three signals only (`detection.py:38`) | Follow FD16 |
| Foundation R7, FD16: "adds no second alarm" | True only for PagerDuty today | F19 must build this, not assume it |
| Foundation section 9: "a 30-day window sits at the edge" | Confirmed, and avoidable | Use 28 days |
| Competitors research: Datadog "burn-rate status over 2 hours" | Datadog's own page allows a long window of 1 to 48 hours | Use the page's figure |
| Competitors research: New Relic, Dynatrace, Sentry "not checked" | Now checked, in part, below | Use the table below |

## The five problems that shape this feature

### 1. A dead service looks perfect

Count-based SLOs measure failures among the requests that arrived. If the service, its load balancer or its network dies,
no span is written, so nothing is counted as a failure. **The worst outage leaves the budget untouched.** Google says server-side
measurement "will miss requests that fail to reach the backend"
([Implementing SLOs](https://sre.google/workbook/implementing-slos/)).

Nexovue can do better than most here, because it has three other sources:

- F6's `traffic` signal says a service lost traffic, and the `service_silent` rule says it stopped sending.
- A synthetic check measures from outside.
- `intake_refusals` and the settling rules know when *nexovue* lost the data.

**Recommendation.** Each window must show its coverage. The screen must say "no requests for 3 h 10 m in this window" and link any
traffic finding from those hours. A dead service's status must never be shown as plain green. A minute nexovue itself failed to
receive must be marked unmeasured, and not counted as healthy. The screen should offer a check-based target for a service whose
availability really matters. OpenObserve has the clearest rule found: "A slice that was never measured is a gap, not a zero", and
its alerts freeze below 90% coverage ([docs](https://openobserve.ai/docs/user-guide/analytics/slos/index.md)).

### 2. The second alarm

Foundation decides that a burn joins an open finding and adds no second alarm. What the code does today:

- `incidents._open` links the incident, **and then still writes notifications** to the rule's channels
  (`incidents.py:357-386`).
- The sharing in the outbox is for PagerDuty alone (`outbox.py:27-33`, `_PIN_KEY` at `:224`). Everything else gets its own message.
- So a threshold incident and a finding on one service already send two emails. An SLO burn would add a third.

**The link itself cannot be reused.** It works by matching the subject key to `service:<name>` (`incidents.py:364`,
`project_job.py:1415`). Two SLOs on one service under one rule would share one key, while the unique index allows one open incident
for each rule and key (`incidents.py:6-7`). So the subject must be `slo:<id>`, and both link statements need another way of knowing the service.

**Recommendation.**

- Subject `slo:<id>`, with the service name held in a way both link statements can read. The design chooses how.
- A rule, for channels people read: **on any one channel, the first message about a problem is sent, and later ones are recorded
  and not sent.** The `dropped` state with a reason already exists for this kind of decision (`alerts/model.py:78-82`,
  `_WITHHOLD_ENDING` at `outbox.py:262`).
- Both orders must be tested: burn then finding, and finding then burn. An alert must not wait to see whether the other arrives.
- Whether this rule should also cover threshold incidents and findings is F21's concern. It would fix an existing noise problem,
  and it changes behaviour F6 has already tested. **F19 should apply it to `slo_burn` first.**

### 3. Too little traffic

At 99.9% and 10 requests an hour, the budget over 30 days is about seven failed requests, so one failure spends 13.9% of it (Google's
[Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)). By my arithmetic that one failure is a burn rate of 100 in the
hour (10% failing, against 0.1% allowed), so it pages. The page as summarized says 1,000; do not quote either until it has been read directly.

| Product | Its rule |
|---|---|
| Coroot | Computes burn only "if at least half of the window contains valid data" |
| Grafana | A *Minimum Failures* setting on every alert window; "The ideal solution for low-traffic is to supplement your traffic flow with synthetics" |
| OpenObserve | Freezes on no events. A window with only a few events is not described as freezing |
| Google | Artificial traffic, combining services, lowering the target, or longer windows |

**Recommendation.** A burn must fire only with at least 2 failures *and* at least a minimum number of requests in its long window.
The existing rule's 20 requests (`alerts/model.py:179`) is the starting guess; the design must test it against F6's seed data. The screen must
say when a target is finer than the traffic can measure: at 2,000 requests in 28 days, 99.9% allows 2 failures. It should then offer a check-based target.

### 4. Windows and retention

| Window | Fits `service_stats_1m` (35 days)? | Fits Free (30 days)? | Who offers it |
|---|---|---|---|
| 7 days rolling | Yes | Yes | Datadog, OpenObserve |
| **28 days rolling** | **Yes** | **Yes, with 2 days spare** | Google's default; Sloth; New Relic's example |
| 30 days rolling | Yes | **On the edge.** Rows expire at `minute + 30 days` (`000035:50`), so the oldest minutes of a window disappear at a time set by background merges | Datadog, OpenObserve, Honeycomb's example |
| 90 days rolling | **No** | No | Datadog, OpenObserve |
| Calendar month | Up to 31 days: yes, but last month's figure is gone soon after | No | OpenObserve says it does not support this |

Google: "We have found a four-week rolling window to be a good general-purpose interval", and rolling windows should be "an integral number of
weeks". A whole number of weeks means a quiet weekend never enters or leaves the window alone.

**But a buyer asks "what was our uptime last month?"** Two rolling windows cannot answer that, and that question is probably the commonest reason a small company
wants this at all (my judgment, not a finding). Options for keeping longer history:

| Option | For | Against |
|---|---|---|
| (a) Raise the TTL on `service_stats_1m` to the derived retention | One table, one change | 395 days of minutes for each service, on a table whose granules were tuned for short reads (`000035:25-30`). Storage is unmeasured |
| (b) **A new `service_stats_1h`, hourly, kept for the derived retention** | Small; long reads are cheap; also serves F20. A monthly figure comes from whole hours | A second write path. The view's duplicate behaviour must be tested, as 000021 found the hard way. Whether a view folds these state columns cleanly is **untested** |
| (c) Monthly figures stored in Postgres | Very small; simple | A stored number can go stale, the past 7 days can still change, and a definition change leaves old figures meaning something else |

**Recommendation: (b).** The rolling windows read minutes, and history reads hours. Latest-month figures must be labelled "may still change" for 7 days.
History is bound by the plan's derived retention, so Free shows what it holds and says so.

**Cost of reading (a guess until measured).** A 28-day read covers 40,320 minutes per service. Fast windows need reading every minute. The whole-window status does not; every 5
minutes is a guess. F6's AC29 measured its own short reads, and nothing has measured these long ones.

### 5. People who can move the number

The SLO's result can matter to someone: a manager, or a customer. So a false number is worth attacking, or quietly fixing.

| Attacker | What they could do | Defence |
|---|---|---|
| Anyone with a browser key | Send spans of any kind under any service name. **A search of `apps/receiver/internal/otlp` found no filter on span kind for the browser door.** The envelope records the door (`ingest/ingest.go:47-54`), and a search of `traceassembly` found no use of it. **This was not traced to the end.** If true, forged `SERVER` spans add good requests to hide failures, or bad ones to fire a page | The design must trace this and test it. If true, spans from that door must not enter these rollups, which is also a defect in F6 today and should be raised as its own change |
| The same person, minting names | Push real services past the 2,000 cap, because it keeps the first names in sort order (`services.go:205-222`) | The same fix. Also show "not counted for N minutes" on the SLO. F6 has the same exposure |
| A member with `CONFIGURE_SLOS` | Lower a target, or exclude an outage, to make a bad month look good | Audit every change (`slo.target_changed`, `slo.excluded`). The screen must show that a target changed and when, and must show each excluded period with its reason. A viewer can see all of it |
| Anyone with a secret key | Backdate spans up to 7 days | Accepted. A secret key can already send anything. Noted so nobody calls these figures tamper-proof |
| Someone writing a malicious service name | Text in messages or an AI's input | Names reach alerts through existing escaping (`alerts/payloads.py:204`). An SLO's own name and description are free text under foundation section 3 |

## What users need

### The buyer (CTO or founder of a small company)

- Needs one number when a customer or a board asks: "99.95% last month". They care about the *reporting* more than the method. **The spec recommends internal and board use only** (founder decision 2), and the demand gate tests how often the asker is a customer.
- Needs to not hire an SRE to get it. Keep it few: one guide suggests two or three SLOs for a three-person team (C-grade evidence, customers research).
- Has probably heard of SLOs and not run them. In Grafana's 2025 survey, 29% of SREs and 18% of developers reported production use (scope research).
- **Will not** pay more for it alone. Nothing found shows that it decides a purchase.

### The on-call engineer

- Is afraid of more alerts. Needs a burn to arrive as part of the problem already reported, not as a new one.
- At 3 a.m., needs: how bad, how fast, since when, what else happened. So the message says "at this rate the budget lasts 2 days", with links to the finding and any deploy.
- Needs a slow burn to wait for morning. The outbox already holds warnings through quiet hours (`outbox.py:19-20`), so slow burns should be warnings.
- Does not want to tune four numbers. Google "strongly" advises one set of parameters for all services.

### A new developer

- Does not know the terms, and the catalog says findings must not assume them (`feature-catalog.md:220`). So the screen says "Failures still allowed: 312 of 672" and puts "error budget" in the help text.
- Needs the definition on the screen: "A failure is a request whose span status is ERROR. 4xx usually does not count."
- Needs a suggestion to start from, not an empty form.

### The admin

- Needs a target that does not page anyone until they choose it. A new SLO that never alerts because no rule routes it is also a trap, so the create flow must show where its alerts go.
- Needs planned maintenance to not spend budget. Today's windows cover checks only (`models.py:684-709`), and do not touch span rollups.
- Needs to see the audit trail for changes.

### The attacker

See *People who can move the number*, above.

### The failure that matters most

"If budget exhaustion doesn't change next sprint's work, you don't have an SLO program" (DevOpsDays Rockies 2026, C-grade); "I'm yet to see an error budget effectively inform eng decision making" ([HN](https://news.ycombinator.com/item?id=38051172), C-grade). Software cannot supply the
agreement between people that Google says is required: a written policy that all stakeholders approve. **F19 must not pretend to.** It can make the number visible and the conversation easier. Other reports say it works at Google and Uber, which are much larger companies.

## How competitors do it

Read 2026-11-02. "Not stated" means the page read did not say, not that the thing is absent. Items marked [S] come from a search result's summary, not a page read in full.

| Product | Kinds | Windows | Burn alerts | Missing data | Exclusions | Price or plan | Self-hosted? |
|---|---|---|---|---|---|---|---|
| **Datadog** | Metric (count), monitor (time, including Synthetic tests), time slice | Rolling 7, 30, 90 days; 12-month calendar view; up to 15 months of history in the side panel | Long window 1 to 48 h; short = 1/12 of long. 14.4, 6, 3 for 30 days | **Published, by type:** ignored for metric-based, by the monitor's setting for monitor-based, treated as uptime for time slice ([comparison](https://docs.datadoghq.com/service_level_objectives/guide/slo_types_comparison/)) | Corrections, one-time and recurring, with a separate permission and limits | Price not found | No |
| **Honeycomb** | Per-event | Rolling | Exhaustion time and budget rate. An exhaustion alert "will not alert again until after" it resolves, and its docs recommend one at 0 to learn the budget has gone | Not stated | Filter out known failures or traffic in the SLI ([docs](https://docs.honeycomb.io/notify/slos)). No budget reset is described there | **Free none; Pro 2 (from $150 a month); Enterprise from 100** | No |
| **Grafana SLO** | SLI queries; aggregated or by dimension | Not read | Fast and slow; *Minimum Failures* | Not read | Maintenance windows, **in public preview: one fixed range, "Recurring schedules are not supported", none in the past, no backfill** ([docs](https://grafana.com/docs/grafana-cloud/observe-and-act/alert-and-measure-reliability/slo/maintenance-windows/)) | Pro from $19 + usage; Enterprise from $25,000 a year. **Plan for SLO not confirmed** | Documented under Cloud. Whether self-managed has it is **unknown until read on a pricing or licensing page** (T6.8). A search summary marks one 2026 SLO feature as having no self-managed release |
| **SigNoz** | **None native** | | | | | | Issue [#658](https://github.com/SigNoz/signoz/issues/658) open since 2022, no assignee or milestone. "customer demand" and "ICP" labels added 2025-08-05. **The last comment, 2026-10-23, is an outside engineer offering to write it,** not a sign of a plan. A reviewer reports a community SLO pack on dev.to; not read here |
| **OpenObserve** | Count, time slice, alert-based (disabled in this release, per one page) | Rolling 7, 30, 90. **No calendar** | 1h/5m, 6h/30m, 24h/2h. Thresholds by window | **Gap, not zero.** Below 90% coverage shows No data, and alerts freeze | Not found | Cloud usage-based; one page says $0.50/GB [S]. **Its own page says SLOs are "part of the open-source platform" and work self-hosted** ([page](https://openobserve.ai/slo.md)) | Yes. Released 2026-08-19 [S] |
| **Coroot** | Availability, latency from eBPF or Prometheus. **"Preset SLOs" you can adjust, so monitoring starts "right after installation";** whether every service gets them, and their values, the page does not say | Not stated | 14.4 and 6, both critical, and they **trigger an incident.** Short window differs by page: 30 m or 15 m | Half the window must hold valid data | Not stated | **Free, Apache-2.0, Community Edition lists "SLO tracking"; AI root cause is Enterprise at $1 per core a month** ([editions](https://coroot.com/editions)) | Yes |
| **New Relic** | Good or bad over valid, on any event. Can create common SLIs for an entity, using "the latest data as a baseline" ([docs](https://docs.newrelic.com/docs/service-level-management/create-slm/)) | Rolling N days | Supported; detail not read | Not stated | **One-time or recurring maintenance windows, left out of compliance** ([docs](https://docs.newrelic.com/docs/service-level-management/maintenance-windows-slm/intro/)) | Plan not stated. 500 SLIs per account; 13-month retention | No |
| **Dynatrace** | Two generations, classic and DQL [S] | Set per SLO [S] | Suggests 10 to 14 over 1 h [S] | Not read | Not read | Not read | Mostly SaaS; not read |
| **Sentry** | **No SLO page found** [S]; uptime monitors exist | | | | | | Not confirmed absent |
| **Pyrra** | Prometheus | Set per SLO; example 2 weeks | 4 multi-burn alerts | Not stated | Not stated | Free, Apache-2.0 | Yes; needs Prometheus |
| **Sloth** | Prometheus rule generator | 28 and 30 days by default | Page and warning | Not stated | Not stated | Free, Apache-2.0 | Yes; needs Prometheus |
| **Better Stack** (uptime monitor) | Monitors' availability | Reports; ranges over 6 months default to monthly | Not read | Not read | Not read | Not read | No |
| **Elastic** | SLOs with burn-rate rules on a long and a short window, 1/12 of it, four windows by default ([docs](https://www.elastic.co/docs/solutions/observability/incident-management/create-an-slo-burn-rate-rule)) | Not read | Yes | Not read | Not read | Needs "an appropriate license"; tiers only from an old search summary, so re-read | Elastic Stack |

**Who already suggests a target.** Grafana Cloud "suggests availability and latency targets from recent request, error and latency metrics" ([2026-09-10](https://grafana.com/whats-new/2026-09-10-let-grafana-propose-slos-for-your-service/)); how far back is not stated there, and a separate page says it uses Grafana Assistant [S]. New Relic uses recent data as a baseline (above). So nexovue's only claim is the narrow one: arithmetic on screen, no AI, self-hosted.

**Who already groups alerts.** Not compared for most rivals. Datadog's Event Correlation promises "one single work item and one notification" [S], with no confirmation that it applies to SLO burn alerts. Coroot and OpenObserve were not read on this. **Any claim about noise is unverified until T6.8.**

**Coroot, head to head.** It starts faster for someone with nothing set up: presets from install, using eBPF. Nexovue needs an OpenTelemetry install and a target created by hand. What nexovue may offer instead, each to be re-read before use: a calendar-month figure with its gaps attached (OpenObserve's docs say calendar windows are not supported, and Coroot's page states no window), visible one-time exclusions (OpenObserve's page describes none, Coroot's states none), and the link to findings.

**The real alternative for "how reliable were we last month?" may be an uptime monitor.** Better Stack's reports give "Compound SLA" and "Average SLA" over selected monitors, by month, with data export ([docs](https://betterstack.com/docs/uptime/reporting)). UptimeRobot's was not confirmed. A monitor sees outside availability only. Nexovue adds request-level failure and latency beside it, and an outside check on the same screen. **If a buyer only needs URL uptime, their monitor may be enough.** That answer is to be tested in the customer conversations.

**What the table shows.**

- **Multi-window burn alerts are now standard.** Offering them earns no credit. Omitting them would look amateur.
- **Several products publish how they treat missing data:** OpenObserve and Coroot have rules, and Datadog's differs by type. Honeycomb's, Grafana's and New Relic's were not found. Nexovue's position is the named split between quiet and lost time, and a dead service never reading healthy, not that others are silent. **Say what Nexovue does, never what others lack.**
- **Honeycomb's limit of 2 on Pro** is what foundation chose to differ from.
- **Coroot and OpenObserve already give this away on self-hosted products, per their own pages.** So "free SLOs" is not a unique claim. The true gap is only against SigNoz, and only as of this date. **If SigNoz ships SLOs, F19 has no rival-gap claim, and its case rests on the monthly figure and honesty.** T6.8 and the Step 1 entry check re-read issue 658.

## Methods compared

### How to measure

| Method | How it works | For | Against | Verdict |
|---|---|---|---|---|
| **Count-based** | Good requests ÷ all requests | Exact from rollups; matches user experience; a brief failure costs in proportion | Blind to no traffic; traffic-weighted, so a failure at peak costs more | **Use for services and endpoints** |
| **Time-slice** | Good minutes ÷ measured minutes | Works on aggregates such as p95; easy to read; quiet hours count equally | One slow minute at 3 a.m. counts as much as one at peak; a threshold decides each minute; needs minimum-traffic rules | **Do not use for spans.** Use the idea only for checks, below |
| **Check-based** | Confirmed good runs ÷ counted runs | Sees a dead service; works at no traffic | One outside view; interval limits precision; a check on `/health` proves little | **Use, as the fourth kind** |
| **Percentile target** ("p95 under 300 ms") | Judge a percentile over the period | Familiar | Percentiles do not add, so there is no budget to spend | **No.** Offer "95% of requests within 300 ms", which *is* countable and reads the same |
| **Alert-based** | Time a rule was not firing | Reuses rules | Inherits the rule's waits and mutes | **No** |

### How to alert

From [Google's chapter](https://sre.google/workbook/alerting-on-slos/), which ranks six approaches. Summary of the worse five: a target-breach alert fires on trivial blips; a long window resolves very late; a wait resets on a brief recovery; one burn rate misses anything just under it; several without a short window take long to clear.

**Multi-window, multi-burn-rate is the recommended one.** Both a long and a short window must exceed the rate: the long says it is real, and the short says it is still happening, so alerts stop soon after failures end.

Budget spent when each fires, by my arithmetic from rate × long window ÷ period:

| Purpose | Burn rate | Long / short window | 30-day budget | **28-day budget** | Severity proposed |
|---|---|---|---|---|---|
| Page | 14.4 | 1 h / 5 m | 2% | 2.1% | Critical |
| Page | 6 | 6 h / 30 m | 5% | 5.4% | Critical |
| Next working hours | 3 | 24 h / 2 h | 10% | 10.7% | Warning |

The third row follows Datadog, OpenObserve and Coroot's documented 24-hour options, rather than Google's 1× over 3 days/6 h, which also spends about 10%. It is cheaper to read, and it clears faster. This is a choice, not a finding.

**Traps found in the sources.**

- The largest possible burn rate is 1 ÷ (1 - target) (Datadog). At 99%, it is 100. **At 93% or below, 14.4 cannot happen,** and Datadog blocks such an alert. So the rule must cap the rate or tell the customer.
- For a 7-day window these rates spend more of the budget. Datadog and OpenObserve publish different rates by window (16.8, 5.6, 2.8 for 7 days). **Each window needs its own fixed set.**
- Honeycomb warns that an exhaustion-time alert does not fire twice once the budget has reached zero, which is why it also offers budget-rate alerts [S]. A rate-based alert does not have this problem.

**What fires at the end of the budget?** A rate alert does not fire on "budget gone". When the budget reaches zero and failures have stopped, nothing should page. The screen should show it, with one warning-level notice to the channels the rule names.

### Fitting into F21

`step()` already does what is needed, with `breached` meaning "some row above holds":

- Fire with no wait, because the windows are the wait. Resolve after the short window clears, with a small wait to be set by test.
- Escalate from warning to critical through the existing path (`incidents.py:221-245`). Severity never falls within one incident (`model.py:36`).
- `alert_evaluations.subject_key` takes `slo:<id>` without change (`models.py:1332`).
- Do not widen `Window` (`model.py:163`). `SloBurnCondition` carries no window at all, because the windows are fixed.

**Use the settled minute, not the fixed clock.** Threshold rules end two minutes ago by clock (`thresholds.py:44,140`). F6 uses the pipeline's real progress. A 5-minute short window is very sensitive to half-counted minutes, and a backlog there would falsely resolve a burn, so F19 must use what `settle.py` knows.

### Suggesting a target

"Propose a target from the service's own history" is the research's recommendation. Google adds a caution: current performance "can be a good place to start", and "we advise against picking an SLO based upon current performance" when that locks in an unnecessary strictness.

**Proposed method (plain arithmetic, no model):** take the worst of the last four 7-day windows, then round *down* to the next of 90, 95, 99, 99.5, 99.9, 99.95. The screen must show the arithmetic and let the person change it. It needs 7 days of data and enough requests for that target to be measurable; otherwise it says so. For latency: the bucket boundary that held at least 95% of requests in those same windows.

**This is a proposal. It has never been tried on real traffic.**

## What Nexovue should bring

1. **A target in under a minute, from the service's own history,** in plain words. No query language.
2. **Counts before nexovue's own sampling.** True once P2 lands, with the limits above (`000035`, `000024`). See the spec's must-not list for how this may be said.
3. **Honest coverage.** Says when it was not measured, and separates "nexovue lost it" from "nothing came". Nexovue is well placed for this because of `settle.py`.
4. **A burn does not repeat news.** A burn joins the finding and shows on it, and within the limits of FR22 a channel told in the last hour is not told again. Noise is the top fear, but no rival's grouping was compared, so this is not yet shown to be a difference.
5. **Budget and finding on one page.** A burn links to its finding, deploys (F28) and explanation (F3), with what the budget cost.
6. **A monthly number a buyer can quote,** labelled for what it covers.
7. **All free on self-hosted, with a protective limit stated as protective.** Per project, 200 is a guess; it exists to bound the sweep, and must not be sold as a tier.
8. **It works with AI off, and contains none.** No model chooses a target or wording. Add F19's module to the structural check that detection uses (`test_detection_has_no_ai.py`), since the alert path shares its reasoning.

**AI safety.** F19 adds no AI link, so it needs no injection suite of its own. If F3 or F4 later read SLO data: a burn rate and a budget are **facts**; a service name is a **name**, to be aliased; an SLO's name, description and an exclusion's reason are **free text** (foundation section 3). **Superseded by the spec's FR33:** F4 gets no `/slos` tool in this version, and a later tool needs its own suite covering these fields.

## What Nexovue should not attempt

| Do not build | Why |
|---|---|
| **Endpoint latency targets** in this version | Not countable from the quantile state. Adding buckets to `endpoint_stats_1m` multiplies storage, and rebuilds a table F6 already flags (`anomaly-detection/status.md:245`). Revisit if customers ask |
| **A custom definition of "good"** (status codes, attributes) | Rollups cannot express it. It needs a new derive stage and a cardinality decision. Excluding named endpoints from availability **is** possible and worth offering, because endpoint counts are exact |
| **Free-form SLI queries** (SQL, PromQL) | Statements must be constants that bind the tenant (`alerts/telemetry.py:3-7`) |
| **Multi-service or user-journey SLOs** | Adding services double-counts one request across hops. Honeycomb allows up to 10 services, but that is a larger product decision. Later |
| **Automated deploy freezes or a policy engine** | The failure is between people. A wrong freeze is expensive. Nexovue must not act on this number |
| **SLA or contract reports, credits, or legal wording** | Telemetry cannot prove a contract. It misses requests that never arrived. Claiming otherwise is legal exposure |
| **Public status pages** | Foundation: not planned |
| **Per-SLO tuning of burn rates** | Google strongly advises one set. Each knob is a way to silence an alert by accident |
| **AI that writes targets** | Arithmetic does it, with AI off |
| **OpenSLO import or export now** | A vendor-neutral YAML format ([repository](https://github.com/OpenSLO/OpenSLO)). Useful later for "as code"; no one has asked. API tokens read only today (foundation section 5), so writes need their own decision |
| **Web Vitals SLOs now** | `web_vitals_1m` exists (`000031`), and a browser-key source raises the forging problem above. Later |

## Must not be claimed

| Do not say | Why |
|---|---|
| "Measures your uptime" with no qualifier | It measures requests that reached a service and were reported |
| "Others don't have SLOs" | Only SigNoz's lack is supported, and only as of this date |
| "Tamper-proof" or "audit-grade" | Secret-key holders can backdate; browser-door forging is untraced |
| "SLA reporting" | See above |
| "Exact latency" for any endpoint | Quantile state from a sample |
| A latency target of exactly 500 ms | It is 487.2 ms, and the screen must say so |

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| R1 | Default window | 7 / 28 / 30 / 90 / calendar | **28 rolling, with 7 also offered.** Google's default, whole weeks, fits Free. 30 sits on Free's edge. Buyers used to "30 days" need one line of explanation. Accepted |
| R2 | History for monthly figures | TTL raise / hourly table / Postgres | **`service_stats_1h`,** subject to a test that the view adds correctly. If that fails, the design returns to this decision |
| R3 | Kinds in the first version | Service availability and latency, endpoint availability, check availability, endpoint latency | **The first four.** The fifth cannot be counted exactly. Check-based is included because it is the only cover for a dead or quiet service |
| R4 | Endpoint latency | Time-slice on the quantile / add buckets / leave out | **Leave out,** as foundation asks |
| R5 | Latency threshold | Snap and show / interpolate / pretend | **Snap to the boundary and show it.** Interpolating would invent numbers, and F6 chose boundaries too (`detection/method.py:833-850`) |
| R6 | Burn rates | Google's three / the 24-hour variant / customer-set | **14.4, 6, then 3 over 24 h/2 h, fixed per window.** Slow burn as a warning, so it waits for morning |
| R7 | Low traffic | Half-window rule / minimum failures / both | **A minimum of 2 failures and a minimum number of requests.** Both are guesses until tested |
| R8 | Missing data | Treat as good / as bad / as a gap | **As a gap, with coverage shown.** Below a coverage floor the alert neither fires nor resolves. 90% is OpenObserve's number and a starting guess here. Reuses `step()`'s `None` |
| R9 | Message rule | Both messages / first only, per channel / hold and merge | **First only, per channel, for `slo_burn`.** Holding breaks "an alert never waits" |
| R10 | Subject | `service:<name>` / `slo:<id>` | **`slo:<id>`.** The other allows one open incident across every SLO on a service |
| R11 | Routing | New rule required / default rule per project / built into the SLO | **A default rule per project, critical and warning, to the owners channel,** following F6's precedent (`model.py:225-229`). It stays silent until an SLO exists, and the create flow shows where alerts go |
| R12 | Planned work | Ignore / reuse windows / one-time exclusions | **One-time exclusions with a required reason, visible and audited; a project's maintenance window may also apply if the customer ticks it.** Excluded time is left out, not counted as good, as Datadog does for count-based SLOs. Recurring ones later |
| R13 | Changing a target or definition | Reset history / recompute | **Recompute from the rollups, and show the change on the screen.** Nothing is stored that would go stale. Costs nothing, since numbers are computed when read |
| R14 | Where it runs | New role / `scheduler-light` | **`scheduler-light`, in the alert sweep's family.** It already holds the reader (`nexovue.yaml:358-359`) and alert sweeps run there alone. **No AI nearby.** Whether long reads need their own slot limit, as detection has, is for the design |
| R15 | Target suggestion | None / model / arithmetic | **Arithmetic,** per the section above |
| R16 | Words on screen | "Error budget" / plain | **"Failures still allowed", with the term in help text.** Follows `feature-catalog.md:220`. Experienced users may find it odd; the term stays findable |
| R17 | Count limit | None / a small tier / protective | **Protective, per project, 200 as a guess,** said to be for the system. Honeycomb's 2 is what to differ from |
| R18 | Forged spans from the browser door | Ignore / handle inside F19 / separate change | **Trace it first; if real, a separate change, ahead of F19,** because it also affects F6 and every screen reading these rollups. Recorded here because F19 raises the stakes |

## Founder decisions needed

**None new.** F19's packaging follows the foundation's assumption of no plan gating (FD20) and no small count limit. The outcome of R18 might come to you as its own change, since it could affect what is already built.

One thing to know: **the evidence that SLOs win a purchase is still absent** (customers research). You placed this after the chain. That order looks right, and the five to eight customer conversations foundation already asks for should include one question on it.

## Not checked

- Anything running. No query cost, no storage, no test. All timings and limits are guesses.
- Whether the browser door's spans really reach the rollups, end to end (R18).
- Which plan row a self-hosted organization uses, and so its derived retention.
- Which retention applies to synthetic check readings.
- Whether a materialized view can fold `service_stats_1m`'s state columns into an hourly table, and how it behaves with duplicates and late spans.
- How the demo's services actually set span status. A real check must confirm failures show up as expected.
- Competitors: Grafana's plan for SLO and self-managed availability; Datadog and New Relic prices; Dynatrace in depth; how Honeycomb treats missing data; how rivals group SLO alerts; Elastic's tiers; UptimeRobot's reports; the SigNoz community pack. Sentry's absence rests on a search only.
- Google's low-traffic burn figure, as noted above. The 13.9% and "seven failures" are consistent with arithmetic; the burn figure is not.

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md), [customers](../../research/phase-2-customers-2026-11.md), [scope](../../research/phase-2-scope-2026-10-30.md), [F6 design](../anomaly-detection/design.md), [F6 status](../anomaly-detection/status.md), [`feature-catalog.md`](../../product/feature-catalog.md).

**Method and standards, read 2026-11-02:**

- [Google SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- [Google SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/)
- [OpenTelemetry: HTTP span conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
- [OpenSLO](https://github.com/OpenSLO/OpenSLO)

**Competitors, read 2026-11-02:**

- Datadog: [SLOs](https://docs.datadoghq.com/service_management/service_level_objectives/), [burn rate alerts](https://docs.datadoghq.com/service_management/service_level_objectives/burn_rate/)
- Honeycomb: [SLOs](https://docs.honeycomb.io/notify/alert/slos), [pricing](https://www.honeycomb.io/pricing), [monitoring SLOs](https://docs.honeycomb.io/notify/alert/slos/monitor/) (search summary), [alert best practices](https://docs.honeycomb.io/get-started/best-practices/alerts) (search summary)
- Grafana: [SLO](https://grafana.com/docs/grafana-cloud/alerting-and-irm/slo/), [best practices](https://grafana.com/docs/grafana-cloud/alerting-and-irm/slo/best-practices/), [pricing](https://grafana.com/pricing/)
- OpenObserve: [SLOs](https://openobserve.ai/docs/user-guide/analytics/slos/index.md), [SLO alerts](https://openobserve.ai/docs/user-guide/analytics/slos/slo-alerts.md), [count SLOs](https://openobserve.ai/docs/user-guide/analytics/slos/count-slos.md) (search summary), [release post](https://openobserve.ai/blog/slos-and-alerts-release.md) (search summary), [pricing](https://openobserve.ai/pricing/) (search summary), [downloads](https://openobserve.ai/downloads/) (search summary)
- Coroot: [SLO monitoring](https://docs.coroot.com/alerting/slo-monitoring), [editions](https://coroot.com/editions), [SLO inspection](https://docs.coroot.com/inspections/slo) (search summary), [repository](https://github.com/coroot/coroot) (search summary)
- New Relic: [introduction](https://docs.newrelic.com/docs/service-level-management/intro-slm/), [create SLIs and SLOs](https://docs.newrelic.com/docs/service-level-management/create-slm/)
- Dynatrace: [SLOs](https://docs.dynatrace.com/docs/deliver/service-level-objectives) (search summary), [classic basics](https://docs.dynatrace.com/docs/deliver/service-level-objectives-classic/slo-basics) (search summary)
- Sentry: [uptime monitoring](https://docs.sentry.io/product/monitors-and-alerts/monitors/uptime-monitoring/) (search summary)
- SigNoz: [issue 658](https://github.com/SigNoz/signoz/issues/658)
- [Pyrra](https://github.com/pyrra-dev/pyrra), [Sloth](https://github.com/slok/sloth)

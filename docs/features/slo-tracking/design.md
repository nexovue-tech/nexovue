# Design: SLO tracking (F19)

## Status

**Approved** by the founder, 2026-11-02, with D66 (SLO work on `scheduler-light`). Written 2026-11-02 and rewritten the same day against the revised [`spec.md`](spec.md), after eight
persona reviews ([`status.md`](status.md), *Reviews*). The feature's lifecycle status lives in [`status.md`](status.md).

**How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present, so line numbers will
drift. **Nothing was run,** apart from one read-only import walk (see *AI*). Every time, size and limit below is arithmetic or a guess, and says which. Paths are short: `alerts/` and
`detection/` mean `apps/scheduler/src/nexovue_scheduler/…` unless they name `apps/api`, and `derive/` means
`apps/workers/internal/pipeline/derive/`.

## Context and goal

F19 lets a customer set a reliability target and be warned when it is being used up too fast, without adding noise. The counting is the
easy part. What this design has to get right is:

1. **Honesty about what was not measured.** A dead service must not look perfect, and nexovue going blind must not look quiet.
2. **A burn does not repeat news.** Today only PagerDuty shares one alert for one problem.
3. **A monthly number where most customers will run it.** A self-hosted install keeps rollups for 30 days.
4. **A browser key cannot add to these counts.** Real traffic, including an attacker's real requests, still moves the number (BR11).
5. **F19 adds no free text to what AI reads.** Incidents already reach AI, so a target's name, description and reason stay out of them. `rule_name` is already there, as customer text handled as a name (see *AI*).

**What reading the code changed, beyond the research:**

| Research left open or assumed | The code says | Effect |
|---|---|---|
| Self-hosted plan row "not traced" | No code assigns a plan at creation, so `organizations.plan` stays `'free'` (`models.py:182-187`). `project_retention` joins it to `plans` (`0005:71-79`), and the worker stamps from that view (`projectconfig/postgres.go:24-28`, `worker/run.go:486-494`). **So self-hosted is 7 days raw and 30 days derived** | An hourly rollup could never hold last month there. History moves to Postgres |
| Check readings' retention "not traced" | `metrics_gauge` rows are stamped with **derived** retention (`metricwriting.go:345-348`, `rows.go:21`), and expire on `received_at` (`000004:38`) | 30 days on Free and self-hosted |
| Browser door "not traced to the end" | Nothing filters it. See *Forged spans* | A separate change comes first |
| "A view must be tested" | Rollups moved **away** from views in 000024, and no view is needed under D2 | No ClickHouse migration in F19 |
| "`nothing else reads this table`" comment in 000035 | Its granules were tuned for short reads (`000035:25-30`). F19's minute reads are at most 8 days and mostly 24 hours, because long windows read stored days | The tuning still holds. The frozen migration stays as is; the architecture docs record the new reader |
| `step()` covers firing and escalating | `step()` returns no action once firing (`thresholds.py:92-95`), and `fire` at the same severity writes nothing (`incidents.py:221-223`) | The job calls `fire` itself every breached minute, and a new `refresh_detail` keeps figures current |

## User experience

**Creating a first target** starts from `/slos/new`, or from a service's page, which opens it with `?service=` filled in, the way `RuleEditor.tsx:81-96` does. It ends on the target's page.

1. Pick what to watch: a service, an endpoint in it, or a check. A service availability target shows the health-check endpoints it pre-ticked to leave out (FR34).
2. Pick the kind. Availability is the default, and the window is 28 days.
3. See one suggestion with its arithmetic: "Your worst week of the last four was 99.93%. We suggest 99.9%." Change it if you like.
4. See the generated name, *checkout availability*, in an editable field. A name already taken is reported beside the field.
5. Answer one question on a request kind: "Do you sample before sending to nexovue?" (FR5).
6. See who is told, and tick an existing channel if owners are not the right people (FR25a).
7. Save. The page shows **Counting**, then real figures once history is read from rollups already held.

That is one form on one screen. On a wide screen the fields sit on the left, and the arithmetic and who is told sit on the right, with sentences capped and no cap on the container. At 375px they stack. AC38 times it.

**Reading a burn at 3 a.m.** The message says how fast, how long what is left lasts, since when, on which service or check, which finding goes with it, and one link. The incident page adds the last deploy and the links to act from. Nothing else needs opening to decide whether to act.

**Monthly number.** `/slos`, *Last month*: "5 of 7 targets met in October. 1 missed. 1 not judged. 2 of those met have time not counted." One row per target, each with its label. It is a figure for the team and the board, and it says it is not a contract figure.

**What this feature never does:** tell a team to stop deploying, promise anything to a customer of theirs, or ask the reader to compare two lines
on a chart to find out what is wrong (`docs/design/principles.md`, rule 1).

## UI structure

Routes `/slos`, `/slos/new`, `/slos/$sloId` and `/slos/$sloId/edit`, and a nav entry in *Home* after Alerts (`route-manifest.ts:18-34`). Feature folder `apps/web/src/features/slos/`.
All screens use the full width of the window, with sentences capped and containers not.

### `/slos`: the list

**Its question: which targets are in trouble, and which need me now?**

- A headline sentence by FR10a: "2 of 7 targets need attention. 2 have too little traffic to judge." With none: "0 of 7 need attention." Never "all is well". With any **Not up to date** target, a banner says how many.
- **A list of linked rows, not `DataTable`.** `DataTable` scrolls sideways inside its container (`DataTable.tsx:43`), and its header says it is for history records. So a 375px phone would hide state and figures. Findings already uses a linked list (`FindingsScreen.tsx:165-167`, `:311-319`). Each row is `<li><a>`, read as a sentence: "checkout availability. Running out, act now. 14 times faster than allowed, what is left lasts about 2 days. 99.81% against 99.9%." CSS grid places those parts in aligned columns on a wide screen, using the window's width, and stacks them at 375px with state and sentence first.
- **Order:** the counted rows first, so the top N rows are the N in the headline. Among those, critical before warning, then shortest time left. Then the other unjudged states, and Disabled last.
- A *No alerts* or *Muted* chip on a row (FR25a), and a line for each deleted target: "checkout availability was deleted by [person] on [date]. Its daily figures go on [date]." with *Restore* inside `Permitted`.
- Views: *Now* and *Last month*.
- *New target* inside `Permitted` for `configure_slos`, hidden from viewers as other gated actions are.

**Deliberately not shown:** burn charts per row, a grade, a single blended percentage, or a colour with no words (rule 6).

### `/slos` *Last month*

**Its question: how did we do last month, and what does that leave out?** Heads "N met, X missed, Y not judged of M targets in October", with how many of those met have an outage or excluded time. A row per target: figure, objective, met, missed or not judged, what it leaves out. It says why it gives no single percentage. A target deleted after the month ended still counts, and reads "deleted on 3 Nov". A row has a copy action, and the view downloads as CSV. `DataTable` is right here: this is a record, wide on a phone by nature, and the headline and copy action stay outside it.

**Deliberately not shown:** an organization-wide line. A read takes one project, because a project is an environment; a sentence says so (D47).

### States and tones

The design system wants a shape and position per severity (`docs/design/design-system.md:121-136`), and health shows by absence (`docs/features/console-redesign/design.md:82-94`). So:

| State | Tone | Badge |
|---|---|---|
| Running out, critical | Critical, filled | "Running out, act now" |
| Running out, warning | Warning, outlined | "Running out, can wait" |
| Nothing left; Source gone; Not up to date; Not measured; No recent requests past the attention limit | Warning, outlined | Its name |
| No recent requests below the limit; Counting; Building history; Too little traffic; Disabled | Neutral words, no colour | Its name |
| On track | None | None |

Each state's next step is in FR10's table, and comes straight after the sentence.

### `/slos/$sloId`: one target

**Its question: where does this stand, what was not measured, who is told?** On a wide screen: sentence and next step on the left, figures and who is told on the right.

1. **The sentence** from FR10, its state in words, and **the next step**.
2. **Qualifiers,** in this order: not up to date; no alerts; snoozed; missed; thin traffic; the rest. At most two show, with "N more notes" opening the others. Stale and no-alerts notes never collapse.
3. **Who is told** (FR25a), with the silence-rule and check links for people allowed to add them. Viewers read "Ask an owner, admin or member to add one."
4. **What was measured:** coverage, quiet time, lost time, excluded time, each with its own wording, and "As of 14:32 (IST)".
5. **How fast:** `TrendChart`, labelled "Times faster than allowed", over the last 24 hours, read on demand from `/slo-readings/{id}/burn`, with deploy markers. It plots the long window of the tier the status names: the holding tier, the fastest if several hold, or with none holding the slowest tier able to judge. The response names that tier, and a change of tier redraws the whole line for its window. So the chart's point at `evaluated_minute` equals the stored rate (AC109), and a read between two writes may show one minute more. When no tier can judge it has no such point. The rate formula is one function in `nexovue_platform/slo.py`, because `burn.py` is in the scheduler and the API must not differ from it. The chart has a second series "On pace (1)" through the existing multi-series `label` (`TrendChart.tsx:95-100`). A minute with no events is a gap, never 0. The description states the current value, the peak and the on-pace line. A failure here is an `InlineFailure` with a retry, and the figures above still show.
6. **What goes with it:** an open incident, a linked finding, the traffic finding for quiet hours.
7. **Tabs** (`Tabs`): *By month* (with copy and CSV), *Exclusions* (add and remove, inside `Permitted`), *History*, *Definition*, which is where "error budget" appears.

**Edit** is `/slos/$sloId/edit`, the create form with its definition lines. **Disable** is a plain action saying "Alerts stop and any open burn closes. It keeps counting." **Delete** uses `ConfirmPanel`, with FR31's wording: "Takes this target off the list. Its N days of figures go on [date]. It can be restored until then. Past months' results stay."

**Refresh.** The list and the status refetch every 60 seconds (a guess, the job's own pace) through `shared/refresh.ts:119-124`, as alert screens do at 15 (`features/alerts/api.ts:15`). The age is worked out in the browser against the last `evaluated_minute`, so a row turns **Not up to date** even when refetches fail. A failed refresh keeps the figures and says "Could not refresh, last read at 03:04". A polite live region announces a change from **Counting** to figures, and into or out of **Running out**.

**Times.** Every time in a sentence, qualifier, message and the incident page shows the project's zone, and the date when not today: "since 23:10 yesterday (IST)".

### Form states

| State | Behaviour |
|---|---|
| Suggestion loading | A `Skeleton`, with the objective field usable |
| Suggestion failed (the form's only ClickHouse read) | "Could not read this service's history. Enter a target yourself", with a retry. Saving still works (AC106) |
| Name taken | Beside the field |
| Same definition exists | "A target with this definition already exists", linking to it, because two would read the same in a message (D35) |

### Elsewhere

| Place | Change | File |
|---|---|---|
| Rule editor | New kind *A target's allowed failures run out too fast*, with which targets and which severities, and `describeCondition` wording | `features/alerts/RuleEditor.tsx`, `alert-words.ts:52-59` |
| Incident page | For `slo_burn`: current figures from `detail`, with "As of"; the target link; the linked finding; "Last deploy: 02:30, 11 minutes before this started", "No deploy recorded", or "Deploys are not connected"; and links to the finding, the service or check page, the service's errors filtered to the burn's start, and the target. "This target has since been deleted. What it recorded is above." The container's `max-w-3xl` (`IncidentScreen.tsx:56`) goes, with sentences capped and the evidence using the window. Times get the project's zone, as `FindingScreen.tsx:109` passes it | `features/alerts/IncidentScreen.tsx` |
| Finding page | "Also using allowed failures too fast: checkout availability, 99.9% over 28 days. 14 times faster than allowed. What is left lasts about 2 days." The label comes with `LinkedIncident`, and the figures from `/slos/{id}` in the browser | `features/findings/FindingScreen.tsx`, `finding-words.ts`, `findings/schemas.py:105-112` |
| Service page | One line: "2 targets. 1 running out." | `features/servicedetail/` |
| Service snooze and mute controls | Snooze: "Also holds back reliability target messages." Mute: "Stops findings only. Reliability target alerts continue." | The controls' screen, read at T5.7 |

### Every state, from rule 3

| State | `/slos` | `/slos/$sloId` |
|---|---|---|
| **Loading** | `Skeleton` holding the rows' shape | `Skeleton` holding sentence and figures |
| **Empty** | `EmptyState`: what a target is, in two lines, and *New target*. No suggestion call. With no services or checks: "Nothing is reporting yet", linking to setup. Viewers are told who can add one | Not applicable |
| **Zero results** | Filter by service or state matched nothing: names the filter, offers to clear it | Not applicable |
| **Partial** | A row whose status is missing shows an em dash and "Not worked out yet". It is never hidden | Figures show if the chart fails, and the chart shows if a linked finding fails |
| **Failed** | "The targets could not be loaded", with a retry. No headline, no empty state | Page and each tab have their own failure line |
| **Validation** | Inline, with a summary on the form | Same form |
| **Unsaved changes** | Not applicable | `UnsavedChanges` |
| **Dangerous** | Not applicable | `ConfirmPanel`, FR31's wording |
| **Refused** | Not applicable | A viewer following an edit link sees `Refused` |
| **Success** | Not applicable | "Saved. Counting the past 28 days." |
| **Owned centrally** | 401, 403, 404, 429, 5xx and offline are `query-client.ts`'s and `AppShell`'s, and are not rewritten | Same |

## Proposed design

### Where it runs

**On `scheduler-light`, with no new role or container** (D66, the founder's decision of 2026-11-02, superseding D39's `scheduler-slo`). The three queues `slo`, `slo_counts` and `slo_history` join the worker's default queues (`__main__.py:78-85`) and `QUEUE_CAPS` (`jobs/worker.py:72`), where detection is capped today at `DETECTION_CONCURRENCY`, 2 (`detection/__init__.py:24`). Caps: **`slo` 2, `slo_counts` 1, `slo_history` 1, guesses.** The job code reads ClickHouse only through the reader account (`CLICKHOUSE_DSN`, `deploy/compose/nexovue.yaml:360`); the container also holds the eraser and detection-writer accounts, which F19's code must never open (AC68's structure test names the DSN each SLO statement uses).

**How probes and alert sweeps keep their slots.** A cap alone does not protect them: `_claim_into_idle_slots` serves capped queues first and the uncapped lane last, with what is left (`jobs/worker.py:261-287`). F3's floor (A56, `ai-insights/design.md`, *The floor (A56)*) closes that: all capped queues together hold at most `concurrency - uncapped_floor` slots, 8 − 4 = 4, taking turns so none starves. With F19 the capped queues are `detection` 2, `evidence` 2, `releases` 2, `slo` 2, `slo_counts` 1 and `slo_history` 1: **10 seats asked for, 4 given.** Probes and alert sweeps (`light`, `light.<region>`) always keep the other 4.

**The trade-off, stated plainly.**

- **SLO work waits; probes and alert sweeps do not.** When detection, evidence and release reads are busy, SLO jobs wait their turn. A burn job expires at 60 seconds, so under load a project's burn is judged a minute or two late, longest-waiting first (*Overload*, AC65), and its screen says **Not up to date**. It is never judged never.
- **A history read holds one shared seat** for up to 330 seconds. Its cap of 1 keeps it to one.
- **One failure domain.** A read that crashes `scheduler-light` (an OOM kill of the whole container, not one child) stops probes and alert sweeps with it. D39's own container avoided that. F19's reads keep detection's bounds, 5 seconds and 256 MiB on the ClickHouse side, and each child keeps the 64 MiB limit after a job (`jobs/worker.py:75-77`).
- **Why the caps are these.** `slo` 2 matches detection's: at 200 projects, 0.6 seconds a job fills a minute (see *Slots*). `slo_counts` and `slo_history` at 1 keep the slow work from taking more than one shared seat each. A higher cap would only lengthen everyone's turn under the floor.

**Memory.** `scheduler-light`'s ceiling stays **768M, a guess.** The slot count does not change, so neither does the number of children, and the 64 MiB per-child limit already sized eight children for it (`jobs/worker.py:73-77`). What grows is the forkserver's preloaded imports, by the `slos` package, shared by all children. AC50 measures it on the deployed server; if it OOM-kills there, the ceiling rises and `sizing.md` changes with it. **This removes F19's planned 384 MB container from the Phase 2 total** (foundation section 1).

**Depends on F3's floor.** The floor is F3's T1.7 (`uncapped_floor`). If F19's Step 1 is built before F3's T1.7 merges, F19 builds the floor exactly as F3's design describes, and F3 rebases onto it (the tracker's shared-file rule). Without the floor, SLO queues must not ship on `scheduler-light`.

**Why three queues.** History jobs may take 330 seconds. In one capped queue, two of them would stop burn judging for every organization while 60-second jobs expired. Apart, 20 queued history jobs hold one seat at most, and leave burn its own cap (AC64).

**The trigger is F6's coordinator** (`detection/coordinator.py:137-163`), which already knows the settled minute and the holds. After queueing detection's jobs it also calls `slos.queue_jobs(engine, settled, trouble, now)`. That gives one clock and one reading of what nexovue's intake lost, and it avoids a second set of Kafka reads each minute. **Rejected:** its own beat entry, which would have to repeat `holds.decide` and its lock, or read `detection_sweep.settled_minute`, which never moves backwards and so can sit past a newly found hold (`coordinator.py:71-81`).

**A cost of this:** if the detection sweep stops, so does SLO judging. No minute is settled then. F6's *detection stalled* alarm covers an operator with monitoring connected, and it sends nowhere by default (`deploy/monitoring/alertmanager.yml:1-5`). So customers are told by *When nexovue goes blind*, below. A failure queueing SLO jobs must be caught there and logged, and must not stop the sweep recording itself (AC95).

**Which projects get a job.** A new statement, not `active_projects`, which drops a project silent for 24 hours with no open finding (`detection/projects.py:22-23,29-45`) and would freeze a dead service as **Not up to date** rather than **No recent requests**:

```sql
SELECT t.org_id, t.project_id, min(s.evaluated_minute) AS oldest
FROM slo_targets AS t
JOIN project_retention AS live ON live.org_id = t.org_id AND live.project_id = t.project_id
LEFT JOIN slo_status AS s ON s.org_id = t.org_id AND s.slo_id = t.id
WHERE t.deleted_at IS NULL
GROUP BY t.org_id, t.project_id
ORDER BY oldest NULLS FIRST, t.org_id, t.project_id
```

A project with no target gets no job, and costs one line of this one statement. It ignores traffic, so a 27-day silence is still judged (AC8). It reads every organization on purpose, so it is named in `CROSS_TENANT_STATEMENTS` (`test_scheduler_postgres_tenancy_structure.py:117`).

**Overload.** `fair_order` (`projects.py:121-131`) keeps the order it is given within each organization, and jobs expire at 60 seconds (`coordinator.py:64`), and run in `run_after` order (`jobs/queue.py:49-51`). A fixed order would mean that if only 100 of 200 jobs fit, the same last 100 expire every minute. **Longest-waiting first** corrects itself: whoever was skipped leads the next minute (AC65). `nexovue_scheduler_slo_oldest_status_age_seconds` exports the maximum. This is a statement of intent until AC65 runs it.

### Modules

```
packages/py-platform/.../slo.py      kinds, tiers, boundaries, FR12's arithmetic, FR10's state, label wording, queue and task names; no store
apps/scheduler/.../slos/
    __init__.py   names, queue_jobs
    burn.py       pure: which tier holds, severity, what is left lasts how long
    counts.py     pure: a day's row from a source reading; coverage; longest quiet stretch
    store.py      the SLO statements, through a reader with lowered limits
    job.py        judge_slos, one project each settled minute; count_slos; read_slo_history
    notices.py    exhausted warning, exclusion notice, "cannot judge" notice
    retention.py  purge after 30 days; lapse at 400 days
apps/api/.../slos/  router, schemas, service, queries, csv
```

FR10's state is one pure function in `nexovue_platform/slo.py`, shared with the API so an alert and a screen cannot disagree. There is no second `state.py`.

**Reads.** `TenantScopedReader._query` hard-codes `READ_SETTINGS` (`alerts/telemetry.py:144-149`), so it cannot lower them. `_query` gains a `settings` argument, with today's value as the default, and `slos/store.py` passes the lowered ones. `alerts/telemetry.py` is not the only holder of the raw driver: `detection/store.py:27` also imports it, and `test_tenant_chokepoint_structure.py` walks both. API statements live in `slos/queries.py` and go through `TenantRepository`.

### What each job does

**`judge_slos(project_id, settled_minute, trouble)`.** On `slo`. Soft and hard limits of 20 and 30 seconds, as detection's project job (`project_job.py:160-161`). Expires in 60 seconds, so a late one is overtaken.

1. Take `session_lock(connection, "slo-judge", project_id)` (`projects.py:154`), as detection does (`project_job.py:503-507`). Not held: end.
2. Load the project's targets, enabled and disabled, oldest status first, and rules of kind `slo_burn`.
3. **If `settled_minute` is not newer than a target's `evaluated_minute`, skip that target,** so an older job never moves a position back (AC66).
4. **Read once per project:** at most one statement per source, each only if a target needs it (see *Performance*), for the last 24 hours up to the settled minute. Check readings are read at the cadence of the check's interval, not every minute.
5. For each target: work out the burn (`burn.py`), save `slo_status`, with `ON CONFLICT … WHERE slo_status.evaluated_minute < EXCLUDED.evaluated_minute`. Then for each rule covering it, in `incidents.judged_alone`:
   - take the position and run `step()`, which decides position and when to clear. **The save is F19's own statement, not `thresholds.py`'s `_SAVE_POSITION`** (`thresholds.py:55-63`), because that one writes `evaluated_at` on every judgment, including `breached=None`. F19's keeps the old `evaluated_at` on conflict when `breached` is None, so on an `slo_burn` row **`evaluated_at` means the last minute a tier could judge** (see *What a burn may withhold*). No other code reads that column today (`models.py:1336` and `thresholds.py:58,62` are its only appearances);
   - **if `breached` is true, call `incidents.fire` at the current severity,** whatever `step()`'s action, because `step()` returns none once firing (`thresholds.py:92-95`). That is how a slow burn escalates to one `escalated` row;
   - **while an incident is open, call `incidents.refresh_detail`,** a new statement restricted to `resolved_at IS NULL`, before any `clear`, because `fire` at the same severity writes nothing (`incidents.py:221-223`) and `_RESOLVE` writes only `resolved_at` (`:125-129`). So repeats, the recovery and the incident page all read current figures (AC19, AC104).
6. Close a burn its source has stopped feeding (below). Notice exhausted budgets (FR19).

Per-target reads are used only when a target has an exclusion inside the last 24 hours, because the shared statement cannot leave out one target's time. That is rare.

**`count_slos(project_id)`.** On `slo_counts`, soft 120 and hard 150 seconds (guesses). Queued by `slos.queue_jobs` from what is **due, not from the clock**: a project whose today or yesterday row has `computed_at` older than 5 minutes, or whose days not yet final have one older than an hour. So a skipped run is done by the next (AC65), and the hourly work spreads out instead of all landing at the top of the hour. Marks days older than 7 days final. Works out each day's quiet stretch.

**`read_slo_history(target_id)`.** On `slo_history`, enqueued by the API on create or on a change of definition. It reads a day at a time, back as far as the source holds, and writes rows. Soft 300 s, hard 330 s, as baselines'. A second one queued while one runs finds the target locked and ends, as `compute_baselines` does (`detection.py:99-111`), **so that alone would lose a definition change made during a read.** So `slo_targets` holds `definition_version` and `history_read_version`. The job compares them at its end and runs again if they differ, and `queue_jobs` picks up any target where they differ, which also covers a crash between commit and enqueue (AC64).

### Reading latency from buckets

`derive/services.go:27-39`: bucket `i` holds durations above boundary `i-1` and at most boundary `i`. So "at or under boundary `i`" is the sum of buckets `0` to `i`. In ClickHouse, whose arrays start at 1: `arraySum(arraySlice(sumForEachMerge(latency_buckets), 1, i + 1))`.

The boundary constants move from `detection/method.py` (`BUCKET_COUNT`, `LOG_EDGES`) to `nexovue_platform/slo.py`, and `method.py` imports them, because the API needs them for the form's preview and cannot import the scheduler. So the copies stay at three (`derive/services.go`, the platform, `tools/detection-seed`), with a parity test reading the Go constants as `test_seed.py` does.

**Windows inside one statement** use `sumIf` and `sumForEachMergeIf`. **`sumForEachMergeIf` is untested here.** T1.3 tests it against 26.3. If refused, the fallback is `GROUP BY service_name, minute >= x` branches or a subquery per window.

### Burn (`burn.py`)

For a tier with long `L` and short `S`:

```
share(W)  = failures in W / events in W
burn(W)   = share(W) / (1 - objective)
limit     = min(tier rate, 0.9 / (1 - objective))        # FR15
nominal   = limit × L / window                           # budget this tier spends when it fires
floor     = nominal × budget / 2                         # FR14; budget from the whole window
severe    = share(L) >= 50% and share(S) >= 50%          # a guess

tier can judge when events(L) >= minimum (20, or 10 for a check)
                and coverage(L) >= 90% and S holds at least one event
tier holds     when it can judge
                and burn(L) > limit and burn(S) > limit
                and failures(L) >= 2
                and (failures(L) >= floor or severe)

breached = True   if any tier holds
           None   if no tier can judge
           False  otherwise
severity = critical if a fast or medium tier holds, else warning
```

**Fewer than 2 failures is an answer, not a silence.** The earlier draft made it a condition of judging, so a window of healthy requests read as *cannot judge*, and a burn from Friday stayed open on Monday. Now it reads as *does not hold* (AC93).

**The floor must not hide a complete failure.** By arithmetic, at 99% over 28 days the budget is 6.72 average hours of traffic. The fast tier's floor is half of 2.14% of that, 0.072 of an average hour. A night at 5% of average traffic never reaches it in a 1-hour window, however completely it fails. So at least half failing in both windows skips the floor. FR16's minimums still apply, so 2 of 20 stays silent, and AC92 tests both. Below 20 requests an hour, the fast tier cannot judge, and the medium tier takes up to 6 hours. FR14 says so.

**While history is still being read, the budget is unknown,** so the floor and FR15's cap use what is counted so far, and with no counts at all the floor is FR16's 2 (AC103).

`step()` (`thresholds.py:74-107`) takes `breached` directly, with `fire_after=0`, because the windows are the wait, and `resolve_after=10 minutes`, a guess. `SloBurnCondition` has no window field, so `Window`'s limit of 60 (`model.py:163`) stays.

```python
class SloBurnCondition(_Condition):
    kind: Literal["slo_burn"] = "slo_burn"
    slo_ids: list[uuid.UUID] | None = None  # absent means every target in the project
    minimum_severity: Severity = "warning"
```

A rule with `minimum_severity="critical"` treats a slow burn as not breached for that rule. `alerts/service.py`'s `_validated_condition` must check that each id belongs to the project.

**What reads a condition must learn this kind.** It has no `covers(service)`, and T4.2 adds a default rule to every project, so as written today:

- `coverage_of_service` calls `condition.covers(service)` on anything not a check (`alerts/service.py:325-329`). It must skip `SloBurnCondition` (FR25), or the route raises for every project.
- `_covers_subject` returns True for any key it does not know (`:236-246`). It must read `slo:<id>` against `slo_ids`, or a narrowed rule leaves incidents open for ever (AC70). `_stop_watching_what_the_rule_no_longer_covers` then also removes the position.
- Each `parse_condition` consumer is reviewed in the same task (`alerts/service.py:222,328,380`; `detection/notify.py:451`).

**A burn on a source that stopped has an end.** `step(None)` holds an open incident (`thresholds.py:86-90`), so without this a retired service would read as burning for ever, and hold a shared PagerDuty alert open with it. **When a target's source has sent nothing for 24 hours, and coverage of those 24 hours is at least 90%, the job closes the incident:** `incidents.close_unjudged`, a scheduler statement modelled on the API's `_close` (`alerts/queries.py:113-132`). 24 hours is the longest long window, so no tier could hold again without new events, and if failures resume a new incident rightly pages. Unlike `_close`, it tells people too, because no person chose this and FR24 owes a channel told "critical" an ending: "No longer judged: `checkout` has sent nothing for 24 hours. The burn may not be over." The coverage test means nexovue's own lost minutes never close a burn. The 24 is arithmetic on the window, and a guess as a policy.

**Ending a burn for other reasons.** A rule narrowed, a target disabled, deleted or re-pointed, and a check deleted, each close the open incident and remove the position through `alerts/queries.py`'s `_close`, machines only, because a person chose it (AC70). `slos/service.py` calls it in the same transaction, and removes `alert_evaluations` rows there too.

### Notices: once per target, whatever the rules

An incident is per rule and subject (`incidents.py:101`), and a notification per incident and channel (`:164`), and `new_errors.py:170-200` is per rule. So two rules naming one channel would send two. And `_open` writes no notification when a rule is muted (`:367`). So the two event notices, *Nothing left* and *Exclusion*, have their own function, `incidents.fire_notice`. *Cannot judge* stays open, so it has its own path (*When nexovue goes blind*):

- It takes the **union of channels** across all rules covering the target, and writes one incident under the first such rule by name, with rows for that union, closed as an event as it opens. A muted rule's channels stay in the union for an exclusion notice. A channel's own mute still drops its rows, visibly.
- Its `about_signal` is null. That is what exempts it from withholding, either way.
- It holds no reason, and no target name or description. Its `rule_name` is the rule's, as on every incident (see *AI*). The message is fixed wording: "Someone left 2 h out of checkout availability, from 14:00 to 16:00. See the target's page."

| Notice | When | Subject | Severity | No rule covers it |
|---|---|---|---|---|
| Nothing left (FR19) | Allowance reaches zero and `exhausted_armed`, and `exhausted_warned_at` is over 7 days old. Not while **Building history**. Re-armed when the allowance is back above 5% | `slo:<id>:exhausted` | Warning | No message; the screen shows it |
| Exclusion (FR29) | Each creation | `slo:<id>:excluded:<exclusion id>` | Warning | **The organization's owners,** with `rule_id` null, so removing a rule first cannot hide an exclusion |
| Cannot judge (FR13b) | See below. Stays open until judging returns, and `rule_id` is always null | `slo-project:<project id>:blind` | Critical | The owners. The channels are a union, or the owners, in the same way |

An exclusion overlapping an open burn also adds its id, start and end to that incident's `detail`, numbers and ids only (AC60). The incident page reads the reason from the API, for a person.

### When nexovue goes blind (FR13b)

If the SLO worker, the sweep or intake stops, burns stop too, and until now only a screen would say so. That is worse than no alert system, so:

- **`sweep_deliveries` (`alerts/tasks.py:51-68`) gains a step,** `notices.report_blind`. It runs in `scheduler-deliveries` (`beat.py:390-394`), a different container from `scheduler-light`, where SLO judging runs, and needs only Postgres.
- For each project with a target whose `slo_status` is older than 15 minutes, or whose `unjudged_since` is older than 15 minutes (every tier unable to judge for coverage), it opens one incident per project: "Reliability targets in `production` are not being judged since 02:14. A burn would not reach you." When judging returns it resolves, which sends "judging again".
- **It stays single through its own open and resolve path, not `fire` and `clear`.** Those find the open row by `rule_id = :rule_id` (`incidents.py:62-69`), and the unique index is on `(rule_id, subject_key)` (`incidents.py:101`, `models.py`, `uq_incidents_rule_id_subject_key_open`). Two nulls never conflict, and `rule_id = NULL` matches nothing, so built on those a sweep would open a new critical incident each run, or never resolve one. So:
  - `notices.report_blind` writes every blind incident with **`rule_id` null, whoever is told,** and a fixed `rule_name` composed by nexovue, "Reliability targets not being judged". So deleting a rule can change nothing about it.
  - A new partial unique index, `uq_incidents_blind_open`, on `(project_id, subject_key) WHERE rule_id IS NULL AND resolved_at IS NULL AND subject_key LIKE 'slo-project:%'`, built `CONCURRENTLY` in its own migration. **The `LIKE` is required.** A rule's deletion sets `rule_id` to null on its incidents (`models.py`, `ondelete="SET NULL"`), so without it, deleting two rules that each hold an open burn on one target would fail on the index.
  - Opening is `INSERT … ON CONFLICT … DO NOTHING RETURNING id`, and notification rows are written only when a row came back, so a second sweep, or two at once, opens nothing and sends nothing.
  - Resolving finds the open row by `(org_id, project_id, subject_key)`, stamps `resolved_at`, and writes one `resolved` row for each channel that holds its `opened` row, so a rule changed in between loses nobody. The sweep runs every 15 seconds (`beat.py:77`).
  - Nothing else acts on it: `_DUE_REPEATS` joins `alert_rules` on `rule_id` (`incidents.py`), so a null sends no repeat; a null `about_signal` keeps it from withholding; it names no service, so a snooze cannot drop it. **F6's other way was weighed:** a project-subject notification kind, as its stop notice uses (`detection/notify.py:298-322,507`), reaches only the owners, and FR13b must reach the rules' channels.
- **Critical,** as F6's stop notice is, for F6's reason: while it lasts, no burn can page (`detection/notify.py`, `STOPPED_SEVERITY`). It goes through the union of `slo_burn` rules' channels, or the owners.
- Thin traffic never starts it. **If beat or Postgres is down, nothing here can speak,** so the setup page documents an outside heartbeat for the whole install.

### The link to findings

**The problem.** Both link statements compare a subject key with `service:<name>`: `_open` removes the prefix (`incidents.py:357-366`), and `_LINK_INCIDENTS` matches `subject_key` (`project_job.py:344-355,1405-1418`). A burn's key must be `slo:<id>`, or two targets on a service would share one open incident under the unique index (`incidents.py:6-7`).

**The decision.** Typed nullable columns on `incidents`, written by the job, null for every other kind:

| Column | Holds | Used by |
|---|---|---|
| `about_key` | `service:<name>`, or `check:<id>` for a check target with no service (D19) | Links; PagerDuty; two burns |
| `about_signal` | `availability` or `latency`. Null on a notice | FR21's "same type" |
| `about_endpoint` | The endpoint, for an endpoint target | FR21's "unless each names a different endpoint" |
| `about_check_id` | The check, for a check target | The `check_state` pair, also when `about_key` names a service |

- `incidents.Subject` gains these. `_INSERT_INCIDENT` writes them.
- `_open` links when `rule.condition_kind in SERVICE_KINDS`, as today, or when `about_key` starts with `service:`.
- `_LINK_INCIDENTS` gains: `OR (incidents.condition_kind = 'slo_burn' AND incidents.about_key = :subject_key)`. The existing open-incident index serves it (`0031`).

**Alternatives rejected:** matching `detail->>'service'` (untyped, and a name in JSON is one typo from silently not linking); a join through `slo_targets` from `incidents` (couples F21's module to F19's table, and the subject may have been deleted).

### What a burn may withhold (FR20 to FR24)

**Where.** In `outbox.claim`, in two places. `_WITHHOLD_SECOND` runs for a channel in `READ_BY_PEOPLE`, after `_shape` and before `_OLDEST_DUE` (`outbox.py:515-518`), because "everything due goes as one message". PagerDuty and webhooks are not touched by it. `_RELEASE` and `_DROP_SNOOZED` run for people's channels **and PagerDuty**, so outside that `READ_BY_PEOPLE` branch: after the channel's own mute (`outbox.py:508-513`), before `_shape`, and before `_PIN_KEY` (`:569`). Webhooks are untouched by all three.

**New columns on `notifications`,** so release is a query and not text parsing (`outcome_reason` is free words, `outbox.py:133,265`):

| Column | Holds |
|---|---|
| `withheld_why` | `same_news` or `service_snoozed`. Null otherwise |
| `withheld_by_incident_id`, `withheld_by_finding_id` | The subject that caused a `same_news` row |
| `released_at` | When a release was written for this row, so it is written once |

`NOTIFICATION_KINDS` is unchanged (`model.py`). `outcome_reason` still holds the words for the delivery log. A partial index on `(org_id, channel_id) WHERE withheld_why IS NOT NULL AND released_at IS NULL` serves the release, built `CONCURRENTLY` in its own migration.

**Two named fragments of SQL, and three statements that use them.** The numbers below are only for reference. At a claim they run in this order: release, snooze, withhold.

**1. `_SAME_NEWS(x, y)`** is FR21's table, in the same organization **and project**, with at least one of them a burn with `about_signal` set:

| Pair | Condition |
|---|---|
| Burn and finding | A `finding_incidents` row, and `findings.signals` holds `failing` for `availability`, `slower` for `latency` |
| Burn and threshold incident | `burn.about_key = other.subject_key`, and `service_error_rate` for `availability`, `service_latency_p95` for `latency` |
| Two burns | Same `about_key` and `about_signal`, and not both `about_endpoint` set and different |
| One target under two rules | Same `subject_key` |
| Check burn and `check_state` | `burn.about_check_id` names that check. Only the burn may be withheld |

So `service_silent`, a `check_state` opening, a finding with only `traffic`, and a notice can never match, whichever side they are on (AC53, AC55, AC22).

**2. `_TOLD_AND_OPEN(x, c)`** finds a Y that is same news, still open, and has reached channel `c`: a row that is `delivered`, `delivering`, or **`pending` with `attempts > 0`**, because a row waiting on a retry has a future `deliver_after` (`outbox.py:346-352`) and is on its way (AC57). A burn counts as Y only if its `alert_evaluations.evaluated_at` for that rule and subject is under 15 minutes old, so a frozen burn silences nothing (AC53). **`evaluated_at` here is the last minute a tier could judge,** because F19's save leaves it alone when `breached` is None (*What each job does*). The job keeps running every minute for a silent service (AC8), so with the existing save this test would never trigger. That freshness comes from a table already in F21's module, so the outbox joins no F19 table. **How long a freeze takes, by arithmetic:** a tier can judge while its short window holds an event, so after a service stops the fast tier ends after 5 minutes and the slow tier after 2 hours. Until then each minute is a real judgment, and the burn either holds or clears. So the burn can withhold for up to 2 hours 15 minutes after the last event. The 60-minute limit below still applies to new rows, and a row already withheld is released when this test fails.

**3. `_WITHHOLD_SECOND`**, for due rows on channel C with `batch_id IS NULL`, so a row in a batch is never withheld (AC57):

- **News row** (`opened`, `flapping`, `escalated`, `joined`, `repeated`) of X, where `_TOLD_AND_OPEN` finds a Y whose reached row is at the same or higher severity, and sent within the last 60 minutes (a guess). Then `dropped`, with `withheld_why`, the subject, and the reason "not sent: this channel was told about this within the hour". So a higher severity always goes, and a finding told 61 minutes ago withholds nothing.
- **Tie:** both rows due in one claim. The finding or threshold row wins over the burn row, because it has the richer page. Between two burns, the older `created_at`, then the lower `id`.
- **Ending row** (`resolved`, `closed`) of X, where X has a row with `withheld_why` set on C, and **no news row of X has reached C.** Then `dropped`. An opening withheld and an escalation delivered therefore still gets its ending (AC26). A subject with no withheld row is untouched, which is what keeps F6's behaviour the same.

**4. `_DROP_SNOOZED`** (FR26), in the place *Where* gives, on people's channels and PagerDuty. A due `slo_burn` row on a people's channel, whose service has `service_detection_settings.snoozed_until > now`, becomes `dropped` with `withheld_why = 'service_snoozed'`. On a PagerDuty channel, only news rows are dropped, because a page wakes a person and an ending must still close what was opened. Webhooks are untouched. **This is the outbox reading a detection table on purpose,** stated here, because the snooze belongs to the service, not to F21. The earlier draft passed `silenced` into `fire`, which would have stored `muted` on the incident for its whole life (`incidents.py:234,272,184,351`), so nothing would have spoken after a 4-hour snooze ended. `incidents.muted` stays false. A rule's own mute and a channel's mute behave as they do today.

**5. `_RELEASE`**, one statement, called from two places:

- at the start of `claim` for that channel, so a release and Y's ending go in **one message** (`_ALL_DUE`, `outbox.py:145-153`, AC52);
- across every channel from `sweep_deliveries`, before `_deliver_due`, for endings that write no row on a channel, such as a person closing a finding (AC52).

It takes rows with `withheld_why` set and `released_at` null, whose subject is still open, where nothing withholds them any more: for `same_news`, `_TOLD_AND_OPEN` finds no Y, **with no age test**, so Y ended, its message failed for good, or its burn froze; for `service_snoozed`, the snooze ended. For each subject and channel it writes one new row, kind `opened`, `sequence` one above the highest there (unique on `(incident_id, channel_id, kind, sequence)`, `incidents.py:164`), at the subject's current severity, and stamps `released_at` on all of them in the same transaction. A snoozed **ending** whose channel had been told is released too, as the `resolved` row, once the snooze ends, because FR24 owes it.

**The 60 minutes do not release anything on their own.** Y told at 02:00 and open at 04:00 still holds back what it held back at 02:30, or an hour after every problem each channel would get a second message. It stops withholding **new** rows. A repeat is a new row, and so goes out.

**It decides at claim, not at write.** Nothing waits. An alert still never waits to see whether another arrives (AC31).

**PagerDuty (FR24a).** `_PIN_KEY` (`outbox.py:224-259`) follows `finding_incidents` only, so a burn with no finding shares nothing today. A new branch: a burn's news row shares the alert of a subject still open there with the same `about_key`, or whose `subject_key` is the burn's `about_key`, or the check named by `about_check_id`, and a threshold incident's or finding's news row shares a burn's the same way. Both branches gain a severity test **when a burn is involved:** the host must have been sent at the same or a higher severity, or the row takes its own key, so a critical burn after a warning alert pages rather than becoming a note (AC27). The existing branch keeps its behaviour with no burn (AC29). `service_silent` and `check_state` **openings** always take their own key. `_WITHHOLD_ENDING` needs no change: the last to end resolves it, and `close_unjudged` means a burn on a stopped service ends within 24 hours (AC94). So a check burn on PagerDuty is a note on its check's alert, not a dropped row, and **same-news withholding does not run on PagerDuty.** `_DROP_SNOOZED` does, for news rows.

**Webhooks.** Every event is still sent. A machine keys on each subject (`outbox.py:71`). `details` carries the target's figures, with `docs/product/setup/alerts/webhooks.md` updated. No schema version change: `incident.opened` is already the shape.

**F6's told logic.** `RESOLVE_FINDING_SQL` and `notify._told` do not filter on state (`detection.py:251-253`, `notify.py:458-463`), so a dropped opening still counts as told when they write rows. That is the right place for it: rows are written, and the claim decides. So an ending is written for a channel whose opening was withheld, and the rule above decides if it goes. AC95(c) walks this row by row.

**Cost.** One statement per claim of a people's channel, guarded by `EXISTS` an open `slo_burn` incident in the project of a due row. A channel with no target pays for that lookup. Unmeasured: AC50 measures claim time with and without it, because delivery is the path that must not slow down.

**This is the riskiest change in F19,** because it touches a path F6 has tested closely. That is why the rule is gated on a burn being involved, why AC29 requires the existing tests to pass unedited, and why AC95 runs them again with a burn open beside them.

### Messages

`alerts/messages.py`'s `_said` (`:134-193`) gains a case, with the kind's word from FR12:

```
happening: "is using its allowed failures 14 times faster than its target allows"
still:     "is still using its allowed failures too fast"
recovered: "is no longer using its allowed failures too fast"
facts:     "Since 02:41 IST, on checkout."
           "Target: 99.9% of requests over 28 days."
           "At this rate, what is left lasts about 2 days."
           "Failures still allowed: 312 of 672."
           "With finding: checkout is failing more than normal."     # when linked
           "Last deploy: 02:30, 11 minutes before this started."      # when known
link:      the incident
```

**The label is composed by nexovue, and a target's own name never enters a message.** `subject_label` comes from `nexovue_platform/slo.py`: source name, kind words, objective and window. "checkout availability, 99.9% over 28 days"; "checkout /pay availability, 99.9% over 28 days"; "checkout latency under 487.2 ms, 95% over 28 days"; "Payment check availability, 99% over 7 days". Two targets on a service differ in one of these, and a target with the same definition as another is refused, by a partial unique index on a `definition_key` (D35, AC105). `Subject.__post_init__` flattens it (`outbox.py:398-428`) and each channel escapes it, as for every name.

Every value comes from `incidents.detail`, which holds **numbers, ids, and nexovue's own composed finding title,** computed in the job and refreshed each minute. A target's name, description and an exclusion's reason never enter `incidents` or `notifications`. `incidents.rule_name` is the rule's name (`incidents.py:96,345`), which a member may have typed, so it is customer text handled as a name (see *AI*). A message has no join to `slo_targets`, so a deleted target's message still reads. The target's name shows only on console pages, as plain React text.

### Status and days

**The list reads Postgres only.** `slo_targets` joined with `slo_status`, plus a sum over the last 28 rows of `slo_daily_counts`. That is cheap, and it means the list still answers when ClickHouse is down (AC40). So does `/budget`.

**State** is one pure function from those values to FR10's state, in `nexovue_platform/slo.py`.

**The longest quiet stretch crosses midnight.** A daily count alone cannot hold it. Each day stores `quiet_leading_minutes`, `quiet_trailing_minutes` and `longest_quiet_minutes`, computed from the source in `count_slos`. A window's longest stretch joins each day's trailing minutes to the next day's leading ones, so a stretch from 22:00 to 02:00 reads as 4 hours, from Postgres alone. FR10a's attention limit reads `slo_status.longest_quiet_7d`, kept by the same arithmetic.

**Lost minutes** come from `intake_refusals` and from `detection_events`' guard and expired-hold rows (`detection/holds.py:37-51`). `holds.py` reads them across organizations on purpose. F19 uses the same reads and writes only a per-target count. **This changes `holds.py`'s stated rule,** whose header says nothing read there is shown to a tenant (`holds.py:4-7`), and T1.4 changes that header. **D37's limit:** a tenant sees that nexovue may have lost minutes, and which minutes. It never sees why, the guard's vote, how many organizations, or anything from `detection_events`' detail, and AC33 checks no response holds them. `intake_refusals` and `detection_events` are kept 29 days (`detection/retention.py`), and days become final after 8, so a lost minute is always read in time. A day first read beyond 29 days is marked `loss_known = false`.

**A guard's or refusal's limit.** A lost minute is lost for the whole install, whichever tenant was hurt. Coverage may read low on a tenant whose traffic happened to get through. Accepted: coverage says "may have lost", and its counts stand.

### `/slo-readings/suggestion`

Reads the last 28 days of one service or endpoint, split into four 7-day windows, in one statement in the API. Applies FR7. Returns the arithmetic as data so the form can show it. Plain arithmetic: **no model**. It has never been tried on real traffic, so Step 2 prints what it would suggest for each demo service, for the founder to judge. It takes `CurrentIdentity` with no action, so a person of any role reads it, including a viewer (AC32), and a token gets 403 (AC33).

### Copy and CSV

One function per kind builds the fixed label (D42), used by the copy action and the CSV, so they cannot differ. The CSV route lives in the API, as `exports/` does, and every customer-text cell passes `spreadsheet_safe` (`exports/csv_cells.py`), because a service or endpoint name starting `=` would run when a buyer opens the file (AC115).

### Forged spans

**What the code shows.** It was not run.

- `/browser/v1/traces` takes OTLP traces with a public site key (`receiver/internal/otlp/browserdoor_test.go:30-35`, `auth/auth.go:43-45,83`).
- `acceptTraces` splits and publishes with no look at the span kind (`otlp/signals.go:164-181`). A search of `apps/receiver/internal` for span kinds matched nothing.
- `ingest.DoorFor` stamps the envelope (`ingest/ingest.go:46-55`), and the only reader of that stamp in the workers is the usage meter (`pipeline/usage/usage.go:281-291`).
- The worker takes `service.name` from the sender's resource attributes (`traceassembly/flatten.go:142`), and counts any `SERVER` or `CONSUMER` span (`derive/endpoints.go:100-102`).
- A check target reads `metrics_gauge`, and the browser door also serves `/browser/v1/metrics` (`browserdoor_test.go:34`). The prober stamps `DOOR_SYNTHETIC` (`publish.py:187,249`), so the fix is buildable.

**So:** anyone who can read a customer's web page can send `SERVER` spans under any service name. They can add good requests to hide failures, add bad ones to fire a page or open a finding, or mint names sorted early (`aaa-1`, …) so that real services fall past the cap of 2,000, which keeps the first in sort order (`derive/services.go:205-222`). They can forge a check's reading too. The allowed origin list does not stop a script that sets its own header (`otlp/origin.go`).

**The change P2 must make** (its own record under `docs/changes/`, not in this feature):

1. Spans from `DOOR_RUM` never enter `service_stats_1m` or `endpoint_stats_1m`. Whether they should reach `spans` for the browser's trace view stays as it is.
2. When a tenant goes over the cap, keep the busiest, with name as a tie-break, so a replay still drains the same block (`services.go:172-176`).
3. Record which tenant-minutes went over the cap, so a screen can say so.
4. A reading named `synthetic.*`, or carrying a `synthetic.check.*` resource attribute, is dropped unless its envelope's door is `DOOR_SYNTHETIC`. This also blocks a secret-key sender.

**What it needs to check.** `packages/browser-sdk` sets span kinds (`otlp/spans.ts:9`), and it never sends `SERVER`: its one span, the page view, has no kind, so `INTERNAL` (`rum.ts:68-71`). OpenTelemetry's fetch spans are `CLIENT` and document-load's are `INTERNAL`. So P2 filters by door, not kind, and loses no real span (spec D63). F6 is exposed to the same thing, and so is any threshold rule reading `endpoint_stats_1m`.

**P2's record must require AC37's end-to-end test as a condition of starting Step 4,** through the real receiver and worker. Its timing is foundation item 7, approved 2026-11-02: before Stage 1 reaches a customer. Its owner is F28 (foundation item 22, approved 2026-11-02).

## Data flow and interfaces

```
worker ──► service_stats_1m, endpoint_stats_1m, metrics_gauge        (no change)
                    │ read (reader account, 24 h and 8 d)
detection sweep ──► queue_jobs ──► judge_slos ──► slo_status          (Postgres, scheduler-light)
                               │              └► alert_evaluations, incidents, notifications, finding_incidents
                               ├► count_slos ──► slo_daily_counts
                               └► read_slo_history
outbox.claim ──► _RELEASE, _DROP_SNOOZED, _WITHHOLD_SECOND ──► delivery, as today
sweep_deliveries ──► _RELEASE, report_blind                           (scheduler-deliveries)
API /slos ──► Postgres only
API /slo-readings ──► ClickHouse: burn and suggestion, on the telemetry tier
web /slos, /slos/new, /slos/$sloId, /slos/$sloId/edit
```

### API, under `/api/v1`

| Route | Who | Reads |
|---|---|---|
| `GET /slos?project_id=` | Any reader | Postgres. Includes deleted targets' lines |
| `POST /slos` | `CONFIGURE_SLOS` | Postgres, and queues the history job |
| `GET /slos/{id}` | Any reader | Postgres |
| `PATCH /slos/{id}` | `CONFIGURE_SLOS` | Postgres |
| `DELETE /slos/{id}` | `CONFIGURE_SLOS` | Postgres. A soft delete |
| `POST /slos/{id}/restore` | `CONFIGURE_SLOS` | Postgres, and queues a recount |
| `GET /slos/{id}/budget?window=` | Any reader | **Postgres only.** Status. The `slo_budget` panel's source |
| `GET /slo-readings/{id}/burn` | Any reader | ClickHouse: the rate over the last 24 hours, for the chart. Telemetry tier |
| `GET /slos/{id}/months` | Any reader | Postgres |
| `GET /slos/months?project_id=&month=` | Any reader | Postgres. *Last month*, in one read rather than up to 200 |
| `GET /slos/months.csv?project_id=&months=` | Any reader | Postgres. `spreadsheet_safe` |
| `GET /slos/{id}/history` | Any reader | Postgres. No person's name for a token |
| `POST /slos/{id}/exclusions`, `DELETE …/{exclusion_id}` | `CONFIGURE_SLOS` | Postgres, and queues a recount of the days touched. The id must belong to that target |
| `GET /slo-readings/suggestion?project_id=&kind=&service=&endpoint=` | People only (`CurrentIdentity`) | ClickHouse. Telemetry tier |

`/slos/{id}` loads its row and compares project and organization, because `slo_daily_counts` has no `project_id`. So a token scoped to project A is refused project B's ids, in one organization (AC33). **Two routers, because the tier follows a static prefix.** `tier_for_path` uses a plain `path.startswith` (`ratelimit/policy.py:286`), so `/slos/{id}/burn` cannot be listed alone, and `test_ratelimit_coverage.py:48-53,76-80` puts a whole router on the telemetry tier if its `router.py` names `TenantRepository`. All of `/slos` on that tier was rejected: its cap of 8 reads at once (`policy.py:92`), where a read over the cap waits for a slot, would be shared with the list's 60-second refetch, so ClickHouse reads hanging could delay or fail the very list that must answer when ClickHouse is down (AC40). So `slos/router.py` reads Postgres only, stays on the default tier, and must never name a repository. `slos/readings/router.py`, prefix `/slo-readings`, holds the two ClickHouse reads and joins `TELEMETRY_PATHS` when its first route lands (T2.4), because `test_no_telemetry_rule_protects_a_path_that_does_not_exist` fails on a rule with no router. It must be named `router.py`, or the coverage test does not see it (`:41`). The load-then-compare rule below applies to it too. Every `CurrentReader` route joins `THE_SURFACE` in `test_read_surface_structure.py`, in the same task, along with the customer document that list feeds. An API token may read, never write (foundation section 5). A token's response schemas hold no `created_by`, `removed_by`, `deleted_by` or `actor_user_id`. **F4's ticket is a `Reader` too (foundation section 5), and gets 403 on every `/slos` and `/slo-readings` route** until a design adds a tool and its suite (AC97).

**The `slo_budget` panel, for F20: settled by name.** F20 stores an SLO by name, resolved in the displayed project at read time, so one dashboard works on staging and production and a file holds no ids (`dashboards/design.md:436,970`). F19's names are unique per project among live targets (*Data and migration impact*), so that works, and F20 is right. The panel stores `{slo_name}`, not `{slo_id}`. A name lookup sees only live targets, so a deleted or renamed target reads as not found.

**The split F20 asks for (`dashboards/design.md:446-447,970`):** `slos/service.py`'s budget function takes no repository and reads Postgres only, rendering the same `SloBudget` schema `/budget` returns, including the rate `slo_status` stored at `evaluated_minute`. The burn line is a separate function in `slos/readings/`, which takes a repository. **Recommended for the panel: the Postgres part alone,** because the stored rate is at most about a minute behind, so its burn rate needs no ClickHouse scan. F20 counts no scan for this panel. If F20 ever wants a line on the panel, it declares a scan for the second function. It is one more fixed panel type, never a query.

## Data and migration impact

**ClickHouse: none.** No table, view, column or account.

**Postgres: one migration per step that changes the schema, Steps 1, 3, 4 and 5,** the next free numbers when each starts. `docs/engineering/database.md:13-20` applies as written, enforced by `test_migration_rules.py`, and pre-deployment does not change it:

- `alert_rules` and `incidents` CHECKs widen with `slo_burn` as `NOT VALID`, then a `VALIDATE` migration, as 0041 and 0050 do. New frozen `_CONDITION_KINDS`, which `test_alert_vocabulary.py:37-48` reads.
- Any index on an existing table, such as the one on `notifications` and `uq_incidents_blind_open` on `incidents`, is built `CONCURRENTLY` in a migration of its own.
- **The downgrades past Steps 1, 3 and 4 raise "roll forward only",** and those revisions join `ROLL_FORWARD_ONLY` (`test_migration_rules.py`), because `slo_daily_counts`, `slo_target_changes`, `slo_exclusions` and `slo_deleted_months` hold records that exist nowhere else once rollups expire. Going back past them is a restore.

```
slo_targets
  id, org_id, project_id, name (1..120), description (0..500)
  kind CHECK IN (service_availability, service_latency, endpoint_availability, check_availability)
  service_name (<=255, null for a check without one), endpoint_name (<=255, null unless endpoint)
  check_id FK checks ON DELETE SET NULL
  objective_percent numeric(5,2) CHECK BETWEEN 90 AND 99.99
  window_days smallint CHECK IN (7, 28)
  boundary_index smallint CHECK 0..62, asked_ms double, null unless latency
  left_out_endpoints text[] (at most 20, service_availability only)
  upstream_sampling text CHECK IN (no, yes, not_sure), null on a check     # FR5
  enabled bool, definition_key text
  definition_version int, history_read_version int, history_read_through date
  first_event_at timestamptz
  exhausted_warned_at timestamptz, exhausted_armed bool
  created_by FK users SET NULL, created_at, updated_at
  deleted_at timestamptz, deleted_by FK users SET NULL
  UNIQUE (org_id, project_id, name) WHERE deleted_at IS NULL
  UNIQUE (org_id, project_id, definition_key) WHERE deleted_at IS NULL
  Checks that each kind has its own fields set

slo_status                        one row per target, rewritten each minute
  slo_id PK, org_id, project_id, evaluated_minute, tiers jsonb
  last_event_minute, burning_since, last_held_minute, unjudged_since
  longest_quiet_7d int

slo_daily_counts                  PK (org_id, slo_id, day)
  good bigint, total bigint, failures bigint
  quiet_minutes, lost_minutes, excluded_minutes smallint
  quiet_leading_minutes, quiet_trailing_minutes, longest_quiet_minutes smallint
  expected_runs, missed_runs, maintenance_runs int, null unless a check
  loss_known bool
  definition_version int, final bool, computed_at
  changed_at timestamptz, changed_why text CHECK IN (exclusion, exclusion_removed, definition)

slo_exclusions                    (Step 4)
  id, org_id, project_id, slo_id, starts_at, ends_at CHECK > starts_at
  reason (10..500), created_by, created_at, removed_at, removed_by
  removal_not_recounted bool

slo_target_changes                one per change, for FR30. The one update it allows is members.scrub
  id, org_id, project_id, slo_id (no FK to the target), at, actor_user_id, what, before jsonb, after jsonb

slo_deleted_months                (Step 3) written at purge, so a report stays true
  org_id, project_id, slo_id, month, label, figure, objective, result, deleted_at
```

- `definition_key` is the kind, source, endpoint, check, boundary, objective and window, as one text value, so nulls cause no trouble in the unique index.
- `name` uniqueness applies to live targets, so a name is free again during the 30 days. **Restoring a target whose name was taken asks for a new one** (AC116).
- `quiet_since` is not stored, because `last_event_minute` says it.

**Changes to what exists:**

- `incidents`: `about_key`, `about_signal`, `about_endpoint`, `about_check_id`, all nullable.
- `notifications`: `withheld_why`, `withheld_by_incident_id`, `withheld_by_finding_id`, `released_at`, all nullable. `NOTIFICATION_KINDS` is unchanged.
- `alert_rules` and `incidents` CHECKs, above.
- A default rule is inserted for every existing project, as 0028 did (`0028_alerts.py:363-372`).

**Inventories** (foundation's checklist):

| Adding | Also changes |
|---|---|
| `slo_targets`, `slo_target_changes`, `slo_deleted_months` → `projects`; `slo_status`, `slo_daily_counts`, `slo_exclusions` → `slo_targets` | `organizations/tables.py`, `test_deletion_live.py` |
| Purge: 30 days after `deleted_at`, write each complete month to `slo_deleted_months`, then delete the target. `slo_target_changes` stays. Lapsing: `slo_daily_counts`, `slo_target_changes`, `slo_deleted_months` and removed exclusions at 400 days | `detection/retention.py`'s pattern, a new `slos/retention.py`, read by `jobs/expiry.py:31-44` |
| People's ids in `slo_targets`, `slo_exclusions`, `slo_target_changes` | The row stays and is renamed by `members.scrub` (`members/service.py:188-234`), so ids need no change. The export (`profile/personal_data.py`) gains them |
| Free text naming a person | **`_without_person` replaces only exact top-level values (`members/service.py`), so a new function replaces the person's email and full name inside `name`, `description`, `reason` and at any depth in `before` and `after`, across all the organization's rows, whoever wrote them.** A first name or nickname cannot be found, and the docs say so. `slo.*` audit detail holds ids only, as `record_check_created` does, so `entries_about` has nothing to miss |
| A scheduler Postgres statement | `org_id` bind, held by `test_scheduler_postgres_tenancy_structure.py`, which already exists. Each statement that reads every tenant is added by name |
| ClickHouse | `erasure/tables.py` unchanged, because there are no new tables |

**Moves.** `_COUNTED`, `UNMEASURED_OUTCOMES` and the attribute expressions go from `apps/api/.../synthetics/queries.py:37-60` into `nexovue_platform/readings.py`, so a check target and the Synthetics screen share one rule, as that module's header asks (`readings.py:1-12`).

**Rollback.** See the plan. The tables' downgrades refuse, as above.

## Performance and scale

**All of this is arithmetic on unmeasured reads. AC50 replaces it.** The cost target is F6's: 200 projects and 2,000 services, each with one target here. Each kind costs differently, because the tables are built differently:

| Source | How it is ordered | A read for one target | Across the install (arithmetic) | How often |
|---|---|---|---|---|
| `service_stats_1m` | Minute right after service; granules of 256 (`000035:25-30`) | 1,440 rows for 24 h | 2.88 million | Each minute |
| `endpoint_stats_1m`, named endpoints | `span_kind`, `span_name` before `minute`; granules of 8,192 (`000008`) | With all of those bound, about 1 to 2 granules per name, so up to 16,384 rows **(a guess at how the sort key skips)** | If every one of 2,000 services leaves out 2 endpoints: up to about 65 million | Each minute |
| `metrics_gauge` | `metric_name`, then `toStartOfHour`; JSON attributes; a `GROUP BY` collapse per reading (`synthetics/queries.py`) | 288 rows for a 5-minute check; 1,440 for a 1-minute one | 200 check targets at 5 minutes: 57,600 rows, **plus JSON parsing that nothing has measured** | At the check's interval |
| Days, today and yesterday | As above | Up to 2,880 per service | 5.76 million for services | Each project every 5 minutes, by age |
| Days not yet final | As above | About 11,500 per service | 23 million for services | Each project hourly, by age |
| History on create | As above | 43,200 per service, once, for 30 days | | Per create |

**Leaving out endpoints.** FR34 pre-ticks health checks, so most availability targets may read the endpoint table each minute. So one shared statement per project reads **only the named endpoints**, as `span_name IN (the union of left-out names)`, by minute. The job subtracts them from the service's row **minute by minute, in Python**, only where a service row exists for that minute, because the 2,000-service cap applies to service rows and not to endpoint rows (`derive/services.go:205-222`; `derive/endpoints.go:108-135`), so a dropped service minute must not go negative.

**Rejected: computing these targets purely from `endpoint_stats_1m` with `span_name NOT IN`.** It is simpler and needs no subtraction, but it reads every endpoint of the service, because `NOT IN` leaves `minute` unusable in the sort key. At 20 endpoints that is up to about 328,000 rows per service per minute, or 650 million across the install: 10 times the named read (arithmetic).

**Why this looks affordable, and where it may not be.** The service read averages 14,400 rows per job, and fits its granules. The named endpoint read is the largest figure here, and it is unmeasured. The list and the target's figures read none of it.

**Slots.** On `slo`, 200 jobs at a cap of 2 must average under 0.6 seconds each to finish in a minute (arithmetic). On `slo_counts`, one slot and 200 projects every 5 minutes must average under 1.5 seconds each (arithmetic). Both assume the SLO queues get their caps; under F3's floor they share 4 seats with detection, evidence and releases, so when those are busy the figures above are not met and judging runs late. Over budget, jobs expire and are overtaken, and the longest-waiting go first, so a project is judged late, not never. **Unproved until AC65 runs.**

**If the measurement fails, in this order:** (1) read 6-hour and 24-hour tiers every 5 minutes, holding their result in `slo_status`, since a slow burn needs no minute's precision; (2) raise `scheduler-light`'s slots (keeping the floor of 4) and its memory ceiling, through `roles.py:22` and every compose file; (3) read 6 h and 24 h from stored day and hour figures. **Do not build these first.**

**Postgres.** One upsert per target per minute in `slo_status`, 2,000 a minute at the cost target. `slo_daily_counts`: 2,000 rows times 400 days is 800,000 rows, and at a guessed 100 bytes each about 80 MB. Small and fine.

**ClickHouse's read account** keeps its limits of 30 seconds and 1 GiB (`users.d/reader.xml:23-46`). F19's scheduler reads lower those to 5 seconds and 256 MiB through `lowered_read_settings`, as detection does (`store.py:53-57`), passed through the new `settings` argument. History reads a day at a time, for the same reason.

**Memory.** No new container, so nothing is added to the roughly 13 GB of ceilings on a 16 GB server (`docs/self-hosting/sizing.md:8`). `scheduler-light` keeps 768M, a guess (*Where it runs*). AC50 must show no OOM kills at it with every capped queue busy, and no added lag on probes or alert sweeps; if it fails, the ceiling and its `sizing.md` row rise together.

### Capacity and AI cost

**AI cost: none.** F19 calls no model, stores nothing for one, and spends no token. See *AI*.

**Money.** **No test server exists, and none is rented** (foundation item 9, 2026-11-02): long runs happen on Oracle after Phase 2 is deployed. AC49 needs about 3 weeks of warm-up and 7 days of reference on a bed not yet run end to end. If F6's AC36 run has not started, F19 reuses it by creating its target first. **Without that run, AC49 reads "not run", and F19 cannot be called proved on faults.**

## AI

**Telemetry is attacker-controlled text, and so is a target's name, because a member types it.** F19 holds no AI link, and it must not become one by accident. Incidents already reach AI: `assistant/tools.py:248-255` returns `incident.model_dump()` from MCP's `list_incidents`, including `rule_name`, `subject_label` and `detail` (`alerts/schemas.py:86,89,96`), and that server is outside the AI switch (foundation section 4). `LinkedIncident` carries `rule_name` too (`findings/schemas.py:105-112`). F4's `get_incident` and `get_finding` (`ai-chatbot/design.md:131,136,405`) and F3's linked-incident evidence (`ai-insights/design.md:294`, which already treats `rule_name` as customer text) read the same.

| Rule | How it is held |
|---|---|
| No F19 module imports an AI module | `test_detection_has_no_ai.py`, with new roots `nexovue_scheduler.slos`, `nexovue_platform.slo` and `nexovue_api.slos`, **from Step 1** (AC47). **The API root needs no exception.** Any route using `CurrentReader` or `require(...)` imports `auth/dependencies.py:51`, which imports `lifespan.py`, whose `from mcp.server import MCPServer` (`:21`) only types one argument. So `lifespan.py` takes a small Protocol and drops that import, as F20's D38 also decided; whichever of F5, F19 or F20 lands first does it. A planted `mcp` import in an `nexovue_api.slos` module must fail. It walks subfolders, so `slos/readings/` is covered. It cannot see a provider reached by plain HTTP, so a second rule says `slos/` modules import no network client, and review checks it |
| No target name, description or reason in an incident | `subject_label` is composed; `detail` holds numbers, ids and composed titles; `LinkedIncident` gains the composed label and never a target's name (AC72) |
| `rule_name` is existing customer text, handled as a name | Every burn copies it from the rule (`incidents.py:96,345`), and a member types it. It reaches AI by the paths above, so it gets the treatment of *A name is a name*, and AC72 feeds a hostile one |
| A target's name cannot reach `rule_name` | The rule FR25a writes in the form's save gets a fixed name composed by nexovue, `Reliability target rule <first 8 characters of the target id>`, never the target's name. The default rule and the blind incident have fixed names too. A member may rename a rule later, which is the row above |
| A name is a name | A service, endpoint or check name is still telemetry or customer text in that label. It arrives as an alias at F3, and labelled untrusted at MCP and F4, by those features' own rules. AC72 feeds hostile ones through |
| F4 reads nothing here | No `/slos` tool in this version, its ticket gets 403 (AC97), and a test holds that no assistant tool reads `/slos`. A later tool needs its own suite covering name, description and reason |
| AI off changes nothing | F19 never reads the setting. With the AI role stopped and AI off, then with AI on, one seeded burn must produce identical rows and messages (AC96) |

So F19 adds no AI link to put a suite on. AC72's payload suite is in its gate regardless, because F19 adds to what AI reads.

## Failure handling

| Failure | Behaviour |
|---|---|
| ClickHouse unreadable in a job | The job writes nothing and moves no position (`thresholds.py:160`'s rule). Status ages into **Not up to date** |
| Postgres unreadable | The job fails and the next minute's replaces it. No retry, since it would be stale |
| One target's judgment fails | `incidents.judged_alone` costs that target alone (`incidents.py:427-443`), for example when the database refuses a NUL in a service name |
| Job exceeds its limit | `SoftTimeLimit` ends it; targets not reached wait for the next minute, and go first then |
| Job runs twice, or two overlap | `session_lock`, the guarded upsert and the older-minute skip (AC66). `fire` reads the open incident `FOR UPDATE`, and the unique index holds (`incidents.py:6-9`) |
| Worker dies mid-history | The lease lapses and it runs again. Rows are upserted, and `queue_jobs` sees the versions differ |
| A hold is in force | No settled minute past it, and windows overlapping `guarded` judge nothing |
| Whole SLO worker stopped | Status ages; **Not up to date**; `report_blind` sends from `scheduler-deliveries` |
| Delivery fails | As every notification. A failed first row no longer counts, and `_RELEASE` tells the withheld one |
| Rule does not parse | Logged and skipped, as thresholds do (`thresholds.py:146-148`) |
| Target deleted, disabled or re-pointed, rule narrowed, or check deleted | Closed by `alerts/queries.py:_close`, machines told, position removed (AC70) |
| Source sends nothing for 24 hours, while coverage holds | Closed by `close_unjudged`, people told too (AC94) |
| Project's time zone changes | Days not final are rewritten in the new zone. Final days stay as they are. A month that straddles it is off by the difference, unmarked. Accepted |
| Clock skew between stores | Windows end at the settled minute from ClickHouse's own clock (`settle.py:15-17`) |

**Retry.** None within a minute's job. Every job is overtaken by the next. By reasoning, not by test, a missed minute changes the result by one minute's data; AC50 and AC65 are what will show it.

## Security, tenancy, and privacy

### Tenancy path

| Path | How it stays in one tenant |
|---|---|
| Screen → API | `CurrentReader`, `CurrentIdentity` or `require(Action.CONFIGURE_SLOS)`; `require_readable_project` proves the caller's project before anything is read (`findings/router.py`). `/slos/{id}` compares the row it loads, as `get_incident` does |
| API → ClickHouse | `TenantScopedRepository`; statements are constants that bind tenant and project |
| Job → ClickHouse | `TelemetryStore.for_project`, `binds_tenant` (`alerts/telemetry.py:106-149`). The job reads `org_id` from the project row, never from its arguments (F6's m6) |
| Job → Postgres | Every statement binds `org_id`, except `queue_jobs`' statement, and reading `intake_refusals` and `detection_events`, which describe nexovue's intake (`holds.py:1-7`). All are named in the structure test |
| Target → source | `service_name`, `endpoint_name` and `check_id` are checked against the project at write. A wrong one reads nothing, since every read binds the project |
| Rule → target | `slo_ids` are checked at write, and re-applied in the job against the project's own targets |
| Withholding | `_SAME_NEWS` binds organization **and project** on both subjects, so a staging burn never silences production for a service of the same name, on a channel both use (AC56) |
| Notifications | Each row carries `org_id`, as `outbox.py:34-36` says |

### Permissions

New action `CONFIGURE_SLOS`: owner, admin, member, as foundation section 5. A viewer stays in no row (`permissions.py:49`). `POLICY_OF` gains each write route, restore included. **C14's five copies** change together: `test_policy.py`, web `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, and the FR1 table in `team-and-api-access/spec.md`. A support view's writes are refused by method. Editing a `slo_burn` rule stays `OPERATE_ALERTS`: it is routing, and members may already do it. The audit names `slo.created`, `slo.target_changed`, `slo.deleted` and `slo.restored` ship with their routes in Step 1, joining each action's transaction (`audit/service.py:1-15`), and `slo.excluded`, `slo.exclusion_removed` with theirs.

### Who can move the number

| Attacker | Could | Defence | Remaining |
|---|---|---|---|
| Anyone with a browser key | Forge spans or check readings | P2 | **Until P2 lands, F19 must not ship.** After it, real traffic still moves the number (BR11) |
| Same, minting names | Push real services past the cap | P2's busiest-first | A real tenant over 2,000 services still loses its smallest |
| Member with `CONFIGURE_SLOS` | Lower a target, exclude an outage, or delete it to tidy a bad month | Every change is audited and shown to viewers. Exclusions show their reason, and everyone on its channels gets a notice. A deletion can be undone for 30 days, and past months' results and the change history stay after it (`slo_deleted_months`) | **Accepted.** Visible, not preventable. Nobody should call these tamper-proof (BR10) |
| Same, removing the rule first | Exclude with nobody told | The exclusion notice falls back on the owners | None |
| Secret-key holder | Backdate spans up to 7 days | **Accepted.** They can already send anything. Final days do not change after 7 days | Noted |
| Someone writing a hostile service name | Text in messages | `one_line` and each channel's escaping (`outbox.py:398-402`, `alerts/payloads.py`). Alias or untrusted label for AI | Webhook docs already say to treat it as untrusted |
| Same, in a CSV | A formula in a buyer's spreadsheet | `spreadsheet_safe` | None. Endpoint names can be reached by real traffic, so P2 does not remove this |
| A person writing a hostile SLO name or reason | Text in AI's input | Never enters `incidents` or `notifications`; plain React text | None on any AI path, by AC72 |
| Someone probing rules | Use `slo_ids` of another tenant | Checked at write, and read by project | None |

### Privacy

No telemetry text is stored beyond the names the customer picked. Daily counts are numbers. **Keeping them 13 months on every plan is a deliberate choice,** founder decision 1. People's ids are scrubbed with `members.scrub` and shown only to session readers, never tokens (F6's m2). `service_name` in `incidents.about_key` is removed with the incident, as `subject_label` is today. A target's name, which people may fill with a customer's name, does not leave the server through any channel, and the setup page says which fields do.

### Writing

Model words cannot enter messages, since there is no model. No `dangerouslySetInnerHTML`; names and reasons are plain React text.

## Observability and operations

- **Metrics** (`metrics.py`, no label names a tenant, `metrics.py:15`): `nexovue_scheduler_slo_jobs_late_total`, `nexovue_scheduler_slo_oldest_status_age_seconds` (by maximum), `nexovue_scheduler_notifications_withheld_total`, `nexovue_scheduler_notifications_released_total`.
- **Alert** (`deploy/monitoring/rules/nexovue.yml`, with its test): SLO status older than 15 minutes for 10 minutes, while detection is running, since otherwise *detection stalled* has already fired.
- **Logs:** project and target ids at warning on a failed read, never an SLO's name.
- **Runbook:** a section in `docs/operations/detection.md`, or its own file if it grows: "SLO status not updating", "a message I expected did not arrive", which leads to the delivery log's reason.
- **Readiness:** run by `/readiness-check` in Step 6.
- **Docs:** `docs/product/setup/` gets a page on targets, with what they do not measure, what leaves the server, the default routing, and the outside heartbeat. `webhooks.md`, `current-system-reference.md`, `database.md`, `security-and-tenancy.md` and `observability.md` change as the code does. `docs/self-hosting/sizing.md` gains the role. `feature-catalog.md` F19 is narrowed.

## Alternatives and tradeoffs

| Alternative | Why rejected |
|---|---|
| **`service_stats_1h`** (research's choice) | A self-hosted install keeps 30 days, so it could never show a whole last month. Serves service kinds only. Adds a second path in ClickHouse. It would make long reads cheaper, and F20 might want one; F20 can make that case for itself |
| **Raise `service_stats_1m`'s TTL** | 395 days of minutes on a table tuned for short reads, and no use for endpoints or checks |
| **A burn as a finding** | FD16: three signals only (`detection.py:38`), self-closing, adopting after 7 days |
| **Keep monthly figures only** | Cannot produce a rolling 28-day figure, and a day's row is what makes the list free of ClickHouse |
| **Rolling windows by the minute** | Exact, at 40,320 rows per target per read. A step at midnight is a fair price, and alerts stay by the minute |
| **Time-slice for spans** | One slow minute at 3 a.m. counts like one at peak, and a threshold decides each minute |
| **Percentile targets** ("p95 under 300 ms") | Percentiles do not add, so there is no budget. "95% of requests within 300 ms" reads the same and counts |
| **Burn alerts by exhaustion time** | Google's multi-window rates are what the method rests on, and they stop soon after failures end, for the reason research gives. F19's own exhausted warning is one notice, by choice (FR19) |
| **Holding a burn message briefly, to merge** | Breaks "an alert never waits" (plan) |
| **Withholding at write time** | The other subject may be pending, may fail, or may arrive a moment later. Only the claim sees all of this |
| **Widening the rule to everything now** | Would fix an existing two-email problem between findings and thresholds, and it changes F6's tested behaviour. Offer to F21 later with evidence from this |
| **A new role, `scheduler-slo`** (D39, superseded) | The founder chose no new container (D66, 2026-11-02). It would have kept SLO failures apart from probes and given SLO work its own 4 slots, for one more container and 384 MB |
| **SLO work on `scheduler-light`, with 12 or more slots** | 12 children at the 64 MiB limit is 768 MiB, the whole of today's ceiling. Kept as the fallback if AC50 fails (*If the measurement fails*) |
| **Reading the service's snooze in `fire`** | Stored for the incident's whole life. See *What a burn may withhold* |
| **A new notification kind for a release** | `opened` at a new `sequence` already reads right, and repeats and PagerDuty need no change |
| **Each kind with its own rule type** | Rule editor grows fourfold, for no real difference in routing |

**Accepted costs, stated plainly.** Whole-day windows step at midnight. Fast failures count as good on latency. A member can tidy a bad month, visibly. A check target and its check's own alert need care to stay one message. If the detection sweep stops, SLO judging stops with it, and people are told. SLO work waits behind detection, evidence and release reads on `scheduler-light`, and shares that container's failures (D66).

## Specification impact

- The spec holds what this design decided, including D2, D4, D11, D13, D19, D35 and D37 to D54, and D66, which supersedes D39.
- `feature-catalog.md` F19's line narrows, on approval.
- F21's spec: BR and FR notes on `slo_burn`, the `about_*` columns, the withheld columns, and the withholding and release rules.
- F6's spec: FR on links gains "and a burn on the same service"; FR28's messages gain FR22's exception; the service snooze control's wording. Its mute is unchanged for burns.
- F20's design agrees: by name, with the budget and burn split, and the panel's burn rate from Postgres (*API*, above).
- Foundation section 9's F19 row, section 10's "Plan clocks" line, and section 12's "counts are exact", which needs P2's qualifier, now note this (2026-11-02).

## Proposed ADRs

Written here, not in `docs/decisions/`. Each becomes an ADR only when the founder approves this design. Numbers are given then.

### Proposed ADR: an SLO's history is daily counts in Postgres, not another rollup

- **Context.** A monthly number must exist on every install. A self-hosted organization is on Free's row, so rollups and check readings expire at 30 days. There are three different sources across four kinds.
- **Decision.** The scheduler stores one row per target per day in Postgres: good, all, failures, quiet, lost and excluded minutes, the day's quiet stretches, and the definition's version. A day is rewritten until 7 days have passed, then final. Rows last 400 days.
- **Why.** Works alike for all four kinds. Survives rollup expiry. Makes the list independent of ClickHouse. Adds no second write path.
- **Consequences.** A figure can only be recomputed while its source holds. A definition change leaves old days under the old definition, marked. Rolling windows move by whole days. Stored counts outlive the plan's rollup retention, which is deliberate and is founder decision 1. A stale figure is possible only by a bug in the job, since days under 7 days old are rewritten by age.
- **Rejected.** An hourly ClickHouse table; a longer TTL.

### Proposed ADR: an incident can say what it is about

- **Context.** Rule and subject key together are an incident's identity. A target needs its own key. Linking compared the key with `service:<name>`. Withholding must compare type and endpoint too.
- **Decision.** `incidents.about_key`, `about_signal`, `about_endpoint` and `about_check_id` hold what a burn concerns, typed and separate from identity.
- **Consequences.** Links, withholding and PagerDuty use typed columns. Other kinds may adopt them, with no change to their identity.
- **Rejected.** Reading JSON; joining F19's table from F21's module.

### Proposed ADR: a burn does not repeat news, decided at the claim and released when its reason ends

- **Context.** One problem can raise a burn, a finding and a threshold incident. Only PagerDuty shares (D29, D35).
- **Decision.** When a channel people read was told within the last 60 minutes, about the same news, in the same project, at the same or a higher severity, a burn or the subject beside it is `dropped` with a structured reason, at claim time, in either order, only where a burn is involved. When that reason ends and the subject is still open, one new message goes.
- **Consequences.** Nothing waits. The delivery log shows every decision. No news is lost by being withheld. The outbox gains a release path and reads a service's snooze. F21 can later drop the "only where a burn is involved" condition, and this would also fix today's two emails.
- **Rejected.** Holding and merging; deciding at write; widening it now.

### Proposed ADR: SLO judging runs on `scheduler-light`, under the shared floor

- **Context.** ADR-019 allows a role per container on one image. `scheduler-light` holds probes and alert sweeps. F3's floor keeps them 4 of its 8 slots, so all capped queues share the other 4, and F6, F3 and F28 already ask for 6. The founder decided on 2026-11-02 that SLO work adds no container (D66, superseding D39).
- **Decision.** Queues `slo` (cap 2), `slo_counts` (1) and `slo_history` (1) run on `scheduler-light`, under the floor. Its 8 slots and its 768M ceiling, a guess until AC50, are unchanged.
- **Consequences.** No new container and 384 MB less ceiling than D39. Probes and alert sweeps never wait behind SLO work. SLO work waits behind detection, evidence and release reads, and is judged late, not never. A crash of the container stops probes and SLO judging together.
- **Rejected.** A new role, `scheduler-slo` (D39), by the founder's decision. 12 or more slots on `scheduler-light`, kept as the fallback.

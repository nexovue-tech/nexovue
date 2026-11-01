# Status: anomaly-detection

## Lifecycle status

In development. Specification (through D32), design (revision 2.1) and plan (revision 2) approved.

## Current task

Building the plan, step by step, without stopping (the founder, 2026-11-01).

## Completed

**The specification**

- Read the code the Phase 2 plan names, and decision 0013 in full from git history (`e0f11a0^`).
  Researched current methods (`spec.md`, *Research*).
- Spec revision 1 went to four independent reviews, which found 5 blockers and about 15 major findings.
  Revision 2 answers them, and the founder approved it on 2026-10-30, with D1 to D10.
- The founder later added D11 to D20, recorded in `spec.md`, *Decisions taken*. D15 was superseded by D17.

**The design**

- Design revision 1 went to five independent reviews: statistics, architecture, operations, security and
  tenancy, and product and QA. They found it not ready. Every claim they made about the code was checked
  against the source.
- The security review found a separate injection bug in Slack, Teams and Discord messages. It was fixed
  and merged in [nexovue-tech/nexovue#20](https://github.com/nexovue-tech/nexovue/pull/20).
- Design revision 2 answers every review finding. Each section names the finding it answers. It proposes
  ADR-022 and ADR-023.

**The persona reviews, 2026-11-01**

- Six read-only reviews read the spec, design and plan: a solo developer, an on-call lead, the implementing
  engineer, security, QA and a startup advisor. They found 8 defects, including a blocker that let outsiders
  switch detection off, plus about 20 smaller fixes.
- The founder decided D22 to D32. Stage 1 now ships on its own (`phase-2-plan.md`).
- The spec, design (revision 2.1) and plan (revision 2) are updated to match.

**The build**

- **Step 2a**, the worker's per-service rollup, progress record and three ClickHouse migrations, was built on
  2026-11-01. Its checks:
  - 27 worker packages pass, and `just lint-go` reports 0 issues;
  - the erasure and tenancy structure tests pass (23);
  - `test_erasure_live.py` passes (6), against the dev stores;
  - the progress store-probe and the migrations probe pass against the dev ClickHouse;
  - `ruff` passes, and mypy reports no issues in 651 files.
- **Step 2b**, the settled minute, the project-bound store and the writer account, was built on 2026-11-01.
  Its checks:
  - 163 new or changed tests pass, the live ones included;
  - red-then-green for the lowered-limits override, three planted structure violations, and the upgrade
    secret;
  - across the repo, ruff passes, mypy reports no issues in 662 files, and the offline Python suites give
    3,869 passed and 1 skipped.

  The plan's 2b demo check, "the scheduler's log names the settled minute", needs T2c.3's coordinator. It is
  proved there.
- **Step 2c**, one signal end to end, was built on 2026-11-01:
  - migration 0041, and the *slower* method with its parity to the simulation;
  - the baseline, project and coordinator jobs;
  - the findings read API, the web feed, and the `detection-seed` tool.

  Its checks:
  - the live suites pass (78), and a seeded slowdown opens one finding and closes it as recovered;
  - the offline Python suites give 4,619 passed and 2 expected failures (AC8, below);
  - the web gives 1,399, and all 27 worker packages pass;
  - ruff passes, and mypy reports no issues in 681 files.

  Not yet seen: the jobs running inside a real `scheduler-light` worker on the demo, because the application
  images cannot be built here.
- **AC8:** the founder chose the 80% rule, 4 of 5 minutes at normal (2026-11-01). AC8 and FR12 are amended.
- **Step 3**, every signal and the states that make silence honest, was built on 2026-11-01:
  - *failing more* with per-endpoint tests and D18's switch for services and endpoints; *losing traffic*,
    with a stop judged at any traffic (D30);
  - the lifecycle: closing, the 6-hour close, rising severity, the project finding, *service gone*;
  - the detection status API, the headline's detection lines, and times in the project's zone.

  Its checks:
  - every detection suite passes, unit and live (4,382), with no expected failures left;
  - the Python suites give 8,981 passed. The 85 that fail or error all need the running application's
    telemetry (`gate-seed`, the load generator) or internet DNS, which this environment lacks; none is a
    file this step touches;
  - the web gives 1,429, plus 124 in the browser SDK; typecheck, Biome and the bundle sizes pass;
  - all Go packages pass, and `golangci-lint` reports 0 issues;
  - ruff passes, and mypy reports no issues in 690 files. `buf lint` could not reach its server; no
    contract changed.

  Its demo checks wait, like Step 2c's, on images built off this network.
- **Step 4**, notifications, was built on 2026-11-01:
  - the outbox's second subject, webhook version 2, finding messages, the flap guard, mute and snooze;
  - the *Critical problems* default rule, and `CONFIGURE_DETECTION` checked on every alerts route;
  - `finding_incidents`; *detection stopped* and *running again*, including a project that never ran (D34);
  - one PagerDuty page per problem in either order (D35);
  - the rule editor's *anomaly finding* kind, brought forward from T5.6;
  - migrations 0042 to 0044.

  Its checks:
  - ruff passes, and mypy reports no issues in 697 files;
  - the Python suites give 9,053 passed, with the same 85 environment-only failures as Step 3;
  - the web gives 1,433, plus 124 in the browser SDK; Biome, typecheck and bundle sizes pass.
- **Step 5**, the user's controls and the finding page, was built on 2026-11-01:
  - acknowledge, false alarm, new normal, snooze, mute and detection settings, each role-gated and audited;
  - the false-alarm hold, shifts and adoption in the scheduler;
  - the finding page, the service screen's badge and markers, the settings section, and the signup zone.

  Its checks:
  - ruff passes, and mypy reports no issues in 702 files;
  - the Python suites give 9,159 passed. Besides the 85 environment-only failures,
    `test_profiles_live.py`'s TTL test fails here: ClickHouse removed an expired row inside the test's
    3-second wait. The test is timing-sensitive, and no profile code changed;
  - the web gives 1,474, plus 124 in the browser SDK; Biome, typecheck and bundle sizes pass.
- **Step 6**, safety under nexovue's own failures and a hostile tenant, was built on 2026-11-01:
  - the receiver's `broker_unavailable` reason and `intake_refusals`;
  - holds, the pipeline signal, and the install-wide guard with organization votes;
  - the prune; migrations 0047 and 0048; two paging alerts.

  Its checks:
  - ruff passes, and mypy reports no issues in 708 files;
  - the Python suites give 9,189 passed, with only the 85 environment-only failures. The profile TTL test
    passed this time, which confirms it is timing-sensitive;
  - all 42 Go packages pass, and lint reports 0 issues; the intake store probe passes;
  - promtool passes, with 18 rules;
  - the web gives 1,474, plus 124 in the browser SDK.

  Unproved here: the plan's demo checks, and the receiver's writer inside a real receiver with a real
  Kafka.
- **Step 7**, operations, was built on 2026-11-01: the sweep's own record (migration 0049), three
  install-wide gauges, the *detection stalled* and *project detection failing* alerts, and the runbook
  `docs/operations/detection.md`. It fixed two Step 6 defects: *held by intake* could never fire, and a
  failing job didn't name its project.
  - **The runbook walk** ran on the dev stack, with the receiver, worker, beat and `scheduler-light` run
    from source, a Prometheus loading the repo's rules, and steady span traffic:
    1. a stopped trace worker fired *stalled*, and restarting it resolved it;
    2. a sweep that never ran, with every process healthy, fired both alerts;
    3. one failing project (a wrong writer password) fired *project failing* alone; the log named the
       project, and the owners' *stopped* and *running again* notices were queued;
    4. a simulated 35-minute broker refusal wrote one `hold_expired` row, and *held by intake* fired and
       resolved.

    Not walked: a real `intake_guard` (it needs 5 organizations; the live test covers it), a single stuck
    partition, the two receiver alerts, and a deployed install.
  - **Its checks:**
    - ruff passes, and mypy reports no issues in 716 files;
    - promtool passes, with 20 rules;
    - all 42 Go packages pass;
    - the web gives 1,474, plus 124 in the browser SDK;
    - the Python suites give 9,246 passed. The 75 that fail all need gateseed's data or internet DNS. The
      walk's one service now makes two `test_services_live.py` tests fail rather than error.
- **Step 8** so far (2026-11-01): the fault bed (T8.1, its run waits on the founder's Mac), AC27's
  no-AI test (T8.4), the docs (T8.5), and AC29 (T8.2).
  - **AC29 passes,** measured here with 200 organizations × 10 services, live spans through the real
    receiver, and `scheduler-light` from source in a real 768 MiB cgroup, over 79 judged minutes and a full
    hourly baseline sweep:

    | Clause | Result |
    |---|---|
    | Judged within 90 s of settling | max 78.2 s, p95 73.9 s, 0 service-minutes over 90 s |
    | Inside the ceiling | cgroup peak 553 of 768 MiB, 0 OOM kills |
    | Rows read | 4.17 M per pass; a project's window, month-full, 2,931 rows for 64 needed |
    | Memory | children 45–58 MiB each; trace worker 233–250 MiB; worker CPU 0.13 cores |
    | Storage | `service_stats_1m` +2.58 M rows, about 22 MiB a day; `detection_readings` about 78 MiB a day, 2.7 GiB over 35 days; Postgres `detection_baselines` 553 MiB, rewritten hourly |

    The machine (4 vCPUs, 15 GiB) is close to the 16 GB install; memory transfers directly. ClickHouse
    reads grow as the month's partition fills. The 200 `ac29-*` tenants remain in the dev stores
    (`just detection-scale remove --yes`).
- **A stop's limit:** at high sensitivity a stop needs about 2.2 expected requests a minute, not 2
  (spec, FR32, *Limit*; AC41).
- **The dev stores run in this environment**, through a local compose override for the sandbox's open-file
  limit. The application images cannot be built here, because the network blocks `ghcr.io` blobs. So an
  end-to-end check on the stack needs the worker run from source, or your Mac.

**The evidence**

- `tools/detection-sim/` is a standalone uv project, numpy and scipy, with its own strict mypy. The root
  mypy excludes it.
- The D17 run is in `design.md`, *Simulation*, and in `tools/detection-sim/RESULTS.md`.
- `tools/detection-measure/` (`just detection-measure`) is for the plan's demo run (D14, D17).

## Next action

Step 8: the cost measurement (T8.2), the docs (T8.5) and the gate (T8.6). Step 1, the demo run on the founder's Mac, can run at any time alongside.

## Open decisions

None.

## Decisions made without asking

The founder asked for the feature to be finished without stopping, with each recommended decision listed
here for review (2026-11-01).

- **An early resolve sends nothing to PagerDuty.** When one of a linked finding and incident closes while
  the other is open, no note is posted. PagerDuty's documentation doesn't say how a later trigger changes an
  open alert, and a wrong guess could re-page or close it.
- **Step 5's own choices** are listed in `design.md`, *As built*, Step 5. They include the new-normal
  threshold of 15 readings, refusing a partial new normal, a false alarm on a recovered finding sending
  nothing, and the snooze and mute ranges.
- **The migration lock is fixed, so concurrent indexes work.** `database.md` requires `CREATE INDEX
  CONCURRENTLY` on a table with rows, but the migration runner's lock deadlocks two setups started together on
  such an index. No migration had needed one before. Both runners, Postgres's and ClickHouse's, are fixed at the root, and 0045 indexes the
  PagerDuty alert key. The rejected alternatives were no index, which would scan a channel's whole history,
  and a plain index, which would break the standard.
- **Step 6's own choices** are listed in `design.md`, *As built*, Step 6. They include the hold and guard
  rules, the "fallen" threshold of half the reference rate, the 5-refusal and 5-organization thresholds
  (first guesses until the demo run), and the 30-day retention.
- **A project finding names nexovue's intake** as a possible cause, as the design says. When the guard can
  blame the intake, no finding opens, so one that opens couldn't rule it out. On most self-hosted installs
  there are too few organizations for the guard. Spec FR15's example sentence is amended to match.
- **A control plane that is down still pages.** Step 6 narrowed the Kafka alert to Kafka's own failures,
  so a sender cancelling no longer pages. A new alert pages when over half the exports are refused for a
  dependency other than Kafka, so a control-plane outage is not left to a ticket.
- **The sweep records itself in a table, not only a metric:** a worker's metric resets on restart, and
  *stalled* must fire when the sweep stops. Both Step 7 alerts page, like D33's email.
- **AC29 missed on memory, so its root cause is fixed rather than a fallback chosen.** At 200 projects and
  2,000 services, lag passed (75–80 s) but `scheduler-light` hit its 768 MiB ceiling, with OOM kills that
  also hit the probes sharing it. The plan said the design's fallbacks would come back to the founder; the
  founder asked for decisions to be made and listed instead. The order chosen: lower the jobs' own peak
  memory, then bound each child's memory, then cut the rows read, then the async insert the design
  promised. Concurrency or the ceiling only with numbers. A separate container stays rejected (D32).
- **Four design items found unbuilt are built, not deferred:** the scheduler's Postgres tenancy structure
  test, the cap of 500 judged services per project, the *too many services* state, and "Not judged: no
  incoming requests". The approved design promised them, and the first is a tenant-safety check.
- **AC29's fixes:** a forkserver with preloaded modules, a 64 MiB per-child limit, a streamed baseline job,
  and `index_granularity` 256 changed in the unreleased migrations 000035 and 000036, not by a fixup
  migration. Concurrency and the ceiling are unchanged.
- **`endpoint_stats_1m`'s read cost is a follow-up,** not fixed here. AC29 passes with it, and its fix
  rebuilds a table older than F6.
- **"Busiest" for the service cap** puts services with open findings first, then ranks by the larger of
  current and expected traffic, so a stopped service is still judged and its stop is still caught.
- **The connector poller's two updates by id are allowlisted** in the new tenancy test, not changed. The
  poll is install-wide and carries no `org_id`; binding one needs a change to the poller beyond F6.
- **Step 7's *detection stalled* alert is the heartbeat** for a scheduler whose minute sweep never runs. The
  owners' email can't report that case, because the email is sent by the sweep itself.

## Blocked

AC37, the two-week no-fault run, waits on the test server's price (`phase-2-plan.md`, Open 2).

## Risks

- **Real failure bursts are unknown.** Every burst rate, length and height is a guess until the plan's demo
  run (D14, D17). The simulation covers a range of guesses, not the real value.
- **The traffic is synthetic.** It has no deploys, daylight-saving changes or late data, and no user
  actions.
- **Per-pass cost and storage** are estimated, not measured. Plan Step 1 measures them.
- **Nothing is pushed.** Commits on `feature/anomaly-detection` exist only in this container until the
  feature's final push, by the founder's decision.

## Validation

- Simulation runs are recorded with their seeds in `tools/detection-sim/RESULTS.md`.
- `ruff` and the tool's own strict mypy pass.
- Each built step's checks are recorded under *The build*, above.

## Specification impact

- New specification.
- `design.md`, *Specification impact*, lists the amendments it would make to FR28, FR29, F21, F1 and
  `phase-2-plan.md`.

## Documentation impact

- `phase-2-plan.md` and `feature-catalog.md` record the plan's approval and link this spec.
- The ADR index lists ADR-022 and ADR-023 as proposed.

## Last updated

2026-11-01

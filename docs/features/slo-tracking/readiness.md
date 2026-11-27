# Readiness: SLO tracking, reliability targets (F19)

> **Assessed 2026-11-28, as one feature, on `feature/phase-2` at `c22a3698`** (clean working tree; this page is the only
> change). **Verdict: NOT READY for a production rollout of F19.** The code is built and its tests pass, but nothing
> about F19 has been seen on a deployed server, the multi-day fault run (AC49) has never run, no restore of its
> Postgres history has been performed, and seven of its own decisions are still the founder's. Blockers are listed
> below. `docs/operations/production-readiness.md` is kept outside the repository's history (founder, 2026-10-29);
> it names nothing about F19, and this page does not update it. Nothing here approves a rollout: that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass. The founder's Mac is not a deployed server:
> what ran there is evidence about the code, not about production.

## What F19 is, in one paragraph

A project sets a reliability target ("checkout answers 99.9% of requests without error over 28 days") on one of
four kinds: service availability, service latency, endpoint availability, check availability. Once a minute
`scheduler-light` judges every live target from ClickHouse's rollups (queues `slo`, `slo_counts`, `slo_history`),
writes the figure to Postgres, and opens a *burn* (an alert that the allowed failures are being used too fast) on
the rule's channels. Daily counts live in Postgres (`slo_daily_counts`), so a month's figure outlives ClickHouse's
30-day rollups. A burn that repeats news a channel already heard is held back for up to an hour. If judging stops,
`scheduler-deliveries` tells people from Postgres alone.

## What was read and run for this assessment

| What | Result |
|---|---|
| `uv run pytest apps/api/tests apps/scheduler/tests -q -n 4 --dist loadfile -k slo`, 2026-11-28, against the running dev stack | **619 passed, 1 skipped** in 6 min 26 s (exit 0). No F19 test file holds a skip marker, so the one skip is a test outside F19 that `-k slo` also selected (not identified). This includes `test_slo_system.py`, which drives the stack's real receiver, Kafka and worker; run again on its own: **1 passed** |
| `pnpm exec vitest run src/features/slos src/shared/slo` in `apps/web` | **3 files, 52 tests passed** |
| The dev stack's Postgres, read-only (`default_transaction_read_only=on`) | **No reliability target exists on it**: `slo_targets` empty, no `slo*` job queued, no open burn. The OpenTelemetry Demo is sending real traffic, but no target judges it. So nothing about F19 was seen on real traffic in this assessment, and the status records it was never tried there (*Risks*: "The suggestion has never been tried on real traffic") |
| `scheduler-beat`'s `/metrics` on port 8081, both replicas | The three F19 gauges exist in the running build: `nexovue_scheduler_slo_targets_late 0`, `_slo_oldest_status_age_seconds 0`, `_notifications_withheld_held 0`; leader `beat_leading 1` on one replica, `0` on the other. With no target these zeros prove the gauges are wired, not that they move |
| `docs/changes/phase-2-live-test/checklist.md` (2026-11-27) | **Nothing about F19.** The live test covered the AI connection, detection, cases and GitHub, chat and fixes. Its *Not covered here* names "the long fault runs" and "the deployed server" |
| `status.md`, Step 6 (2026-11-22): the measurements, the failure drill, the short demo, the earlier `/readiness-check` | Read as the record of what was run then. Re-run here: only the tests above |
| `deploy/backup/common.sh`, `backup.sh`; `deploy/monitoring/prometheus.yml`, `rules/nexovue.yml`; `deploy/compose/nexovue.yaml` | Read for what is declared (below) |

**The seed tool's known gap does not touch F19's evidence.** `tools/detection-seed/seed.py` writes
`endpoint_stats_1m` rows without `latency_quantiles`. F19 never reads that column: its endpoint read is counts only
(`slos/store.py:80`; endpoint latency is out of scope, spec D65), and a latency target reads `service_stats_1m`'s
`latency_buckets`, which the seed tool does write (`seed.py:86-93`). The Step 6 scale run and short demo are
unaffected by it.

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | Tests | **pass** | The run above. History of each step's seen-failing-first tests is in `status.md`, *Validation*. The whole-stack suites (`test_slo_system.py`, `test_browser_door_live.py`) passed through a real receiver, Kafka and worker on 2026-11-22 (T6.5, 4 passed); `test_slo_system.py` passed again today (above). `test_browser_door_live.py` was not re-run here |
| 2 | Faults: a burn opens when a real fault happens, and not otherwise (AC49) | **open: a machine that stays on** | **Never run.** It needs about 3 weeks of warm-up and 7 days of reference on a machine that stays on (plan, *Validation plan*); foundation item 9 puts it on Oracle after deployment. What exists: the target-aware score in `tools/fault-bed/` (`fault_score_burns.py`, tested on fakes) and one short labelled demo on 2026-11-22: a `checkout` failure written straight into `service_stats_1m` (the OpenTelemetry Demo was not started), one burn opened critical 4 minutes in and linked to its finding. The bed's own verdict was **"not proved: 1 injection, 5 needed"**. The no-fault rate (how many burns open when nothing is wrong) has never been measured |
| 3 | Capacity and memory (AC50, AC86, NFR1) | **open: the deployed server** | Measured 2026-11-22 on a shared 4-CPU, 16 GB development machine under load, **at 30 of 200 projects** (490 targets; open decision 9). Judging under 3 s per project, the largest 2.97 s with 190 of 200 targets burning; `scheduler-light` peaked at 644.5 MiB of its 768M ceiling with no kill, but not under a 200-project live feed. 200 projects is arithmetic, not a measurement. AC86 (save to first figure) was not timed end to end. A deployed server's figures are owed, and Oracle's free allowance (2 Arm CPUs, 12 GB) is below the 16 GB sizing |
| 4 | Health and degraded behaviour (AC40, AC48, AC67, AC91) | **pass on a development stack; open on a server** | The 2026-11-22 drill on its own stack: SLO queues unserved, metrics worker, whole worker, prober and ClickHouse each stopped, each seen doing what the spec says (targets read *Not up to date*, the "not being judged" notice once per project within 16 minutes, `/slos` answering in 0.55 s with ClickHouse down, `/readyz` 503). Two defects it found were fixed at the root. **Not walked:** `scheduler-light` stopped altogether (AC91's second half); 300 s and 3,600 s check intervals; a thin target left alone |
| 5 | Monitoring and alerts | **pass in rules; not seen firing** | Three gauges on beat's leader, seen live above. `NexovueSloStatusStale` in `deploy/monitoring/rules/nexovue.yml:133`, with `promtool` cases (Step 6). No alert has fired on a real install. No log line names the project whose read failed (design asked for one; not built, recorded in the runbook) |
| 6 | Migrations (0052, 0053, 0058, 0059, 0060, 0063, 0064) | **pass** | `test_migration_rules.py` and the schema tests in the batch above; 0063 and 0064 were run forward, back to 0060 and forward again (Step 5). 0059 validates 0058's checks and 0064 builds its index without stopping writes. 0058's downgrade refuses and 0059's does nothing, so the alert-rule half of F19 is roll-forward only |
| 7 | Backup and restore of F19's history | **fail as evidence: never restored** | `slo_daily_counts`, `slo_deleted_months` and `slo_target_changes` are the **only** copy of a month's figure once ClickHouse's 30-day rollups expire. `backup.sh`'s `pg_dump` takes them (they are not in `POSTGRES_ROWS_NOT_KEPT`, `common.sh:45`), so they are in a backup by construction. **No restore including them has been performed**, and `backup-and-restore.md` says the real `nexovue restore` has not been run end to end outside `just insight-fence`. A backup never restored is not a backup |
| 8 | Configuration and secrets | **pass** | No secret and no setting added; F19 reads the existing `POSTGRES_DSN` and `CLICKHOUSE_DSN`. Queues `slo`, `slo_counts`, `slo_history` are declared on `scheduler-light` (`nexovue.yaml:391`), under F3's floor of 4 capped slots |
| 9 | Security and tenancy | **pass for what the code does** | Read in Step 6's review and tested in the batch: every new Postgres statement binds `org_id` or is named as install-wide (`test_scheduler_postgres_tenancy_structure.py`, `test_postgres_tenancy_structure.py`); routes need `CONFIGURE_SLOS` to change and a readable project to read (`slos/router.py`, `exclusions.py:204`; `test_authorization_coverage.py`); no SLO module reaches a network client (`test_slo_no_provider_calls.py`, seen failing on a planted import); an exclusion's free-text reason never reaches an AI path (`test_slo_ai_paths.py`). Forged spans and `synthetic.*` readings change no figure (P2, AC37, `test_browser_door_live.py`). No tenancy hole found |
| 10 | Alert delivery: the hold-back rule does not lose news (AC24 to AC31, AC52 to AC59) | **pass in tests; open on real channels; one known gap** | `test_slo_one_message_live.py` and `test_slo_regression_live.py` (F6's delivery tests unedited, again beside an open burn). **Known gap, open decision 3/6:** a burn noted on a warning-level PagerDuty alert that then turns critical stays a note and **does not page**. Not seen with a real inbox or a real PagerDuty (Step 5's *Judgeable*, owed after Phase 2) |
| 11 | Screens | **pass in tests; partly seen** | Web tests above. The list and form seen in Chromium at 375 and 1280 px with no sideways scroll (Step 6). **Not walked:** the target page in a browser, AC81's keyboard half; **no Playwright spec exists for F19**. The *Exclusions* tab is not built: exclusions are API-only (open decision 7) |
| 12 | Rollback | **pass on paper; not rehearsed** | Take `slo`, `slo_counts`, `slo_history` out of `scheduler-light`'s queues: no job, no burn. Step 5's hold-back can be backed out on its own (plan, *Rollout and rollback*). The migrations go down in tests; a rollback on an install with real targets was never done |
| 13 | Operational ownership | **open: the founder** | `docs/operations/slo-tracking.md` is written from the drill and has never been used on call. Nobody is named to own it |
| 14 | Customer and operator documents | **pass, unreviewed** | `docs/product/setup/reliability-targets.md`, the runbook. `test_slo_words.py` refuses "uptime", "exact" and the jargon on screens and the page. No launch text exists yet; T6.9's list is the founder's to sign (T6.12) |
| 15 | Product proof: demand, an outside tester, real traffic (demand gate, AC38, T2.7, T6.10, T6.11) | **open: customers** | The demand gate's result is still to be read; AC38 has no tester; T2.7's suggestion list awaits the founder's judgement and was never tried on real traffic. T6.10 does not block release by the plan's own words; the others are the founder's to weigh |
| 16 | Deploy, TLS, release smoke test | **open: the founder** | Nothing is deployed. F19 adds no public route beyond the API's existing tiers |

## The founder's open decisions (from `status.md`)

None of these is a defect in what was built; each changes what a customer gets. Listed so a rollout is not decided
past them.

| # | Decision | Recommended | Bearing on rollout |
|---|---|---|---|
| 1 | The finding page's burn figures come from the incident, not `/slos/{id}` | keep (a) | none: wording in the design |
| 3, 6 | A burn on a lower-severity PagerDuty alert that escalates stays a note and does not page | fix (a), as its own change | **a critical that never pages**: see blocker 4 |
| 4 | `/budget` reads the target's own window, no `?window=` | keep (a) | none |
| 5 | No "released lately" metric (no index on `released_at`) | keep (a) | small: the delivery log shows it |
| 7 | The Exclusions tab is not built; API only | build it (a) before release | a customer cannot leave planned maintenance out of a figure on screen |
| 8 | The fault bed reads "one email delivered" from the burn's side only | accept (a), read the log by hand in the run | affects how AC49 is read |
| 9 | Scale measured at 30 projects, not 200 | measure at 200 on the first server (a) | is gate 3 |

## Blockers and recommendations

**BLOCKER**, a production rollout of F19 is unsafe without each:

1. **AC49, the multi-day fault run, on a machine that stays on.** Without it nobody knows how often a burn opens
   when nothing is wrong, or misses a real fault. A customer would be paged on a rate never measured, and the plan
   says F19 is not called proved on faults until it runs. One injection of five has been scored, and it was written
   into the table by hand, not caused by the demo's fault flag.
2. **A capacity measurement on the deployed server, at 200 projects.** Judging is one job per project per minute
   on two shared slots; if it does not fit the minute on the real machine, every target reads *Not up to date* and
   no burn is judged, and `scheduler-light` at 768M also runs the probes, so an out-of-memory kill stops checks and
   targets together. Measured only at 30 projects on a development machine.
3. **A restore that includes `slo_daily_counts`, `slo_deleted_months` and `slo_target_changes`, performed and
   checked.** Past 30 days these tables are the only record of a month's figure; a lost Postgres with an
   unrehearsed restore loses every customer's monthly history for good, because ClickHouse has already expired the
   rollups it was counted from.
4. **Open decision 3/6 answered.** As built, a burn first sent as a note on a warning-level PagerDuty alert keeps
   that alert's key, so its later escalation to critical is a note and never pages anyone: the exact failure
   PagerDuty exists to prevent. Either fix it (recommended) or the founder accepts it in writing and the setup page
   says so.
5. **An owner for the runbook**, and the platform's own deploy gate. `NexovueSloStatusStale` is a ticket that
   nobody receives yet.

**RECOMMENDED**, should be done, does not by itself stop a rollout:

- Build the Exclusions tab (open decision 7) so planned maintenance can be left out on screen, as FR29 to FR31 say.
- A Playwright spec for the target page and its keyboard path (AC81); walk the target page at 375 and 1920 px.
- Walk Step 5's *Judgeable* with a real inbox and a real PagerDuty: one burn, one held-back message, one release.
- Create one target on the dev stack's OpenTelemetry Demo traffic and read its figure and suggestion against what
  the demo is doing (T2.7), before any customer sees a suggestion.
- Log the project and target ids on a failed read (the design asked; today an operator finds the project by reading
  `slo_status` ages).
- Walk the drill's missing cases on the server: `scheduler-light` stopped altogether, 300 s and 3,600 s check
  intervals.
- Answer open decisions 1, 4, 5, 8 and 9 so the design matches what was built.

## What could not be checked here

- Anything on a deployed server: capacity, memory, alerts reaching a person, TLS, a real restore. Nothing is deployed.
- F19 on real traffic: the dev stack has no target, and creating one would change data, which this assessment does
  not do.
- AC49, AC38, the demand gate, T6.10: they need a machine that stays on, a tester or customers.
- The Step 6 measurements and drill were not re-run; they are read from `status.md` as recorded on 2026-11-22.

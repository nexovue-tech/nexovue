# Status: Continuous profiling (F17)

## Lifecycle status

**Built, reviewed, QA'd — awaiting the founder.** All five steps of the approved plan are implemented and
proved; nothing is committed. NFR3 decided (D6, option A). Production readiness: **NOT READY** — the platform's
blockers (`docs/operations/production-readiness.md`, *F17*).

## Current task

None. Waiting on the founder to read the diff before the commit.

## Completed

- Spec, design (ADR-015) and plan approved 2026-09-19.
- Steps 1–5 (T1.1–T5.9) — see `plan.md`'s *Outcome* for what was built and every change from the plan.
- `/review-diff` by three independent reviewers (receiver and contracts; workers and deploy; API and web).
  Every verified finding fixed and re-proved, except those recorded under *Risks*.
- Design QA on the real screens with the repository's Playwright tooling and a throwaway account: desktop and
  375 px, every state, compare, the span section, the service strip, Usage, the setup page. Found and fixed:
  the timeline hid isolated readings; whole-host process names pushed the pickers off a phone screen; the
  filters sat a gutter too far in; "Compare cpu time".

- **Customer walkthrough and principles review** (after NFR3 was decided): every user story on the real
  screens with a fresh account and a realistic service, checked against `docs/design/principles.md`. Rule 1
  was not met — the screens opened on a number and a chart — and now every graph, span and comparison leads
  with a sentence (`profile-reading.ts`). Also fixed: compare named one side only; "1 samples"; "100 % … more
  than any other function" for a lone function; filters shown in span mode; "time" captions on memory
  profiles; a stopped profiler flagged only by a dim timestamp (US17.8). Still open, outside the feature: the
  shared top bar crowds a pinned window on a phone.

- **Found by the founder — a long profile scrolled the whole page.** At the end of the functions table the
  page kept scrolling and dragged the sidebar and top bar out of view. The table's screen-reader caption
  (`sr-only`, absolutely placed) sat in a scroller that was not positioned, so it was placed against the page
  and stretched the document to 1,674 px in a 900 px window. Fixed at the screen (its scroller is `relative`)
  and in the app frame (`AppShell` is `relative`, so no screen's hidden caption can stretch the page — the
  shared `DataTable` carries the same caption). `profiles.spec.ts` now scrolls a 3,000-stack profile to the
  end and holds the page to the window: 1,674 against 720 without the fix, equal with it. Also: long
  function names were cut while the number columns took half the row — the numbers now have fixed widths,
  and the full name is on hover.

## Next action

Founder:
1. Open `/profiles` on the development console with a range covering today's test data (or start a profiler
   as the setup page shows); try search, zoom, compare (`Compare` → *Two time ranges*), and a trace's span.
2. Read the diff; then the commit (profiles, the gate-found-defects follow-up and ADR-014 are all in the tree).

## Open decisions

None. **Decided 2026-09-19 — NFR3 restated per busy core (spec D6, option A).** The measurement behind it:

**NFR3 — storage per profiled host.** Measured on 2026-09-19 with the eBPF profiler at its default rate on the
development host (a Docker VM, ~4 cores busy): **44.5 bytes a sample compressed, ~75 samples a second — about
73 MB per busy core per day, 288 MB for this host**, against the spec's 100 MB per host per day. Nearly every
sample is a different stack (147,000 distinct in 46 minutes, 27 frames deep), so deduplicating rows saves under
10 %. Where the bytes go: attributes 7.8, timestamp 6.4, function names 5.7, stack hash 5.7, unsymbolized
addresses 4.8, the rest under 3.5 each.

Options, each a change to the spec or the design:
- **A. Restate NFR3 per busy core** (≤ 100 MB per busy core per day) — met today; honest about what drives it.
- **B. Store less per sample** — drop the profiler's `cpu.logical_number` and `thread.id` attributes and keep
  sample times to the minute (no screen reads finer): about 31 bytes, ~51 MB per core-day. Changes FR5.
- **C. The design's fallback, a deduplicated stacks table** — frames stored once per distinct stack; saves on
  frames only, and every read then joins.

The founder chose **A**; B is kept for when a paying customer's fleet is sized.

## Blocked

Nothing.

## Risks

- **A partly failed produce double-counts.** A split request is one produce; if the broker keeps some records
  and refuses others, the sender retries the whole request and the kept parts count twice in the graph.
- **A flood across many keys can crash-loop `worker-profiles`** — profiles only: fetches are bounded by
  compressed size, and profile messages decompress by orders of magnitude. Measured: one key's flood is
  throttled at the door and the worker holds; many keys were not tested.
- **No per-query memory cap on API reads** (platform): `READ_SETTINGS` bounds rows and time only. The profile
  reads are bounded by their shape (196 MiB for a 20-host day); a heavy read of any signal can still exhaust a
  shared ClickHouse.
- **The development ClickHouse sits near its cap.** It was OOM-killed three times today: twice by this work's
  measurement (an insert cross-join, and the pre-fix stack read) and once by resident-memory creep at 3.7 of
  4 GiB, which `memory.xml` already documents. The full Python gate then ran slowly enough for five live tests
  to time out; all 34 of their suites' tests pass re-run.
- **The OTLP profiles format will change** before it is stable (ADR-015): a Collector much newer or older than
  0.161.0 may be refused with 400. The setup page says which version was tested.
- Most eBPF samples carry no span link yet (upstream work in progress), so a span's *Profile* section usually
  says "no sample landed in this span"; AC10 is proved with linked fixture samples.

## Validation

- **Measurements** (T5.7):
  - NFR2 / AC18 — one service, 20 simulated hosts, real eBPF stacks, 8.7 M rows, through the API's own code,
    p95 of 10: flame graph **1.20 s** for an hour (target 3), **2.42 s** for a day (target 10); compare 2.65 s
    and 3.51 s; timeline and filters under 0.2 s.
  - Receiver conversion: 0.92 ms for a real 100 KB eBPF export (421 samples), 108 MB/s.
  - AC15 — a 400,000-samples-a-second flood from one key: accepted at 20,000 a second (1,120 accepted and
    30,178 refused requests of 2,000 samples); spans sent during it readable in a median 14.3 s against 11.2 s
    without it; the span worker never restarted; the profiles worker never killed; profile lag 0. Before the
    fixes the same flood OOM-killed the shared worker and no span was readable within 120 s.
  - NFR3 — above.
- **Live, real sources:** the eBPF profiler 0.161.0 (CPU, the demo's services) and the contrib `pprof`
  receiver 0.161.0 (a Go heap: four types, AC2), both through the development receiver to the screen. Captured
  exports of both are the converter's testdata.
- **End to end:** `tools/e2e/tests/profiles.spec.ts` — a new account's key → flame graph, search share, zoom →
  the samples on Usage. QA: 427 samples sent (421 real + 6), 427 metered (AC13).
- **Live suites:** `test_profiles_live.py` (11: tenancy AC7, filters AC8, compare AC9, span AC10, retention
  AC14 proved red without its predicate, range empty AC12); `test_tenant_isolation_live.py` with a real profile;
  `test_purge_live.py` erasing `profile_samples`; the store probes.
- **`just check`, 2026-09-19:** lint, format, mypy, `tsc`, `buf lint` and `buf breaking` clean; Go every
  package `ok`; web 1,246; browser SDK 120; Python 3,535 passed, 3 skipped — five live tests timed out on an
  overloaded development ClickHouse (see *Risks*) and pass re-run, 34 of 34.

## Specification impact

- FR4: `partial_success` counts profiles, not samples — the wording corrected.
- FR5: values stay in their type's unit; an unnamed process is `unknown_service:<executable>`.
- FR12: a box names its line only when every frame merged into it was on that line.
- NFR3: restated per busy core (D6) — met, ~73 MB measured.

## Documentation impact

- `docs/product/feature-catalog.md` — F17 built as a preview.
- `docs/decisions/ADR-015` — added.
- `docs/architecture/{system,database,deployment,current-system-reference}.md` — the fourth signal, the
  profiles worker, the new settings and table.
- `docs/features/profiling/design.md` — brought to what was built (types, the read, the door's bounds).
- `docs/product/setup/profiling/continuous-profiling.md` — the setup page (FR14), linked from the setup index.
- `docs/operations/production-readiness.md` — F17 assessed as one change.
- `docs/changes/gate-found-defects/status.md` — the air `rerun` defect found here.

## Development stack, as left

- Removed after testing: `nexovue-ebpf-profiler`, the real source for Step 1, the converter fixes and the NFR3
  measurement. It profiled every process in the Docker VM, nexovue's own containers included, so the demo
  project holds `unknown_service:clickhouse`, `…:app` and `…:worker` rows until retention ends. To see live
  flame graphs again, run it as the setup page shows, with the demo's ingest key.
- Removed: `nexovue-pprof-collector`, which scraped the worker's heap as `nexovue-worker` to prove AC2. Its
  rows (~715,000 per heap type, demo project) stay until the 14-day retention ends.
- `worker` and `worker-profiles` are two services now (`docker compose up -d` applied it).
- Removed today, all throwaway: the flood backlogs on `profiles-raw` (both profile consumer groups moved to the
  end), and the partition holding the 8.7 M NFR2 measurement rows and a few test-tenant rows.

## Last updated

2026-09-19

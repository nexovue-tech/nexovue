# Status: Synthetic checks (F18)

## Lifecycle status

**Implemented.** Specification and design approved 2026-09-17; plan approved 2026-09-17, with Q1 answered by
approving the plan's proposal. Built, verified and reviewed on the dev stack, and committed 2026-09-18 as
`75b5424` under the founder's identity. Not deployed.

## Current task

None. The pre-existing defects the QA pass turned up were fixed under their own change record,
`docs/changes/qa-found-defects/`.

## Completed

- Specification, design and plan approved 2026-09-17.
- **Step 1 — browser checks removed** from the scheduler, compose files, contracts, API and web filter. The running
  `scheduler-browser` container was stopped and removed from the dev stack, and stale kombu bindings for the
  `browser` queue deleted from Redis.
- **Shared check model** in `packages/py-platform/src/nexovue_platform/checks/`: configuration (api, multistep,
  heartbeat, tcp, dns), `{{name}}` substitution, run evidence, sealing, maintenance windows, JSONPath, the probe task
  contract and its time limits.
- **Migration `0027`**: kinds, state, state changes, secrets, variables, files (with `detached_at`), maintenance
  windows. Applied, rolled back and re-applied on the dev database; the one existing HTTP check was carried over with
  its old rule as an assertion.
- **API**: checks CRUD with project-aware validation, Run now (ADR-011), secrets and variables with usage counts,
  streamed file upload with a 3 MB cap, maintenance windows, heartbeat pings, run evidence and run counts, cURL /
  OpenAPI / Postman import with collision refusal, deletion impact, rate tiers, tenant purge of the file prefix.
- **Scheduler**: any-method requests with bodies, auth and files; assertions; multistep; TCP; DNS; redaction by value
  before truncation; per-location queues; confirmation from another location; task time limits on every dispatch;
  state recorded by a separate task after publishing; heartbeat and unattached-file sweeps.
- **Console**: synthetics list with kind filter, create for every kind, check detail (evidence, runs, edit, pause,
  resume, delete with counted impact), Run now, import, project settings sections for secrets, variables and
  maintenance windows; shared `Tabs`, `EditableRows`, `CodeEditor`.
- **Documentation** updated (see *Documentation impact*).
- **`/review-diff`** run in three parts (API, scheduler, console); findings fixed are listed under *Validation*.
- **Observed on the dev stack:** a check failed at attempt 1 (published `final=false`), was confirmed at attempt 2
  (`final=true`), its evidence landed in ClickHouse `logs`, and `check_states` / `check_state_changes` recorded
  `down` with the reason. The OpenTelemetry demo's Storefront checks were created through the doors script and run.

## Deviations from the plan, and why

- **One migration, not six.** Every table is needed before any check can be saved; pre-deployment, one reviewable
  revision is simpler than six that only make sense together.
- **`checks.target` is dropped rather than made nullable** (Q2). The URL, host or name lives in `config`; a second
  column copying one field of it would have to be kept in step forever (AGENTS.md rule 11).
- **The check model lives in `packages/py-platform`, not `apps/api/checks/config.py`.** The scheduler parses the same
  configuration the API validates; `docs/engineering/code-structure.md` puts code two apps need in `packages/`.
- **JSONPath uses `jsonpath-rfc9535`, not `python-jsonpath`.** Its author recommends it when nothing beyond RFC 9535 is
  wanted, and it is strict by construction. `match()` and `search()` are disabled: they run a backtracking regex engine
  on customer input.
- **Assertion patterns run on RE2 (`google-re2`)**, linear-time, so a customer's pattern cannot hold a worker.
- **Secrets travel sealed in the task** (ADR-010 amendment) and state is written by a separate task after publishing,
  so no probe reads Postgres.
- **Routes differ from the design's first table**: `/check-runs`, `/projects/{id}/check-secrets`, `/check-files`. The
  design was amended to match.
- **Uploaded objects are never deleted inside a request.** FastAPI 0.141 runs background tasks *before* the
  yield-dependency commit (probed), so a delete there could remove an object whose row then rolls back. Rows are
  detached; the sweep deletes the row (SKIP LOCKED), then the object.
- **Multipart bodies are encoded by us**, not libcurl's deprecated form API.
- **Work is built in layers (shared model → API → scheduler → console) rather than step by step.** The founder asked
  for the whole feature overnight; each layer is verified before the next.
- **Heartbeats are judged every 60 s, not 30 s** — the one-minute floor every check shares (D5).
- **Run now's result read waits server-side** (up to 25 s) so a run costs one or two requests against its quota.

## Next action

None for the approved scope — it is committed (`75b5424`); ADRs 006–009 went in separately (`2c33a9c`). What the
specification leaves out (screenshots, true multi-region, browser checks — D13) waits for a founder decision to
reopen it.

## Open decisions

None blocking. Two worth a decision later:

- ~~**The telemetry tier's quota and concurrency cap are keyed by IP first.**~~ Resolved 2026-09-20: signed-in reads
  are counted per person under a per-organization ceiling (`docs/changes/rate-limit-per-person/`).
- **Refusing rather than briefly queueing a legitimate burst** (already recorded in `ratelimit/policy.py`).

## Blocked

Nothing.

## Risks

Recorded from the review and not fixed:

- **Source-map upload has no streamed body cap** — the same gap the review found in check-file upload, which was
  fixed there. Pre-existing; the wider fix is a body-size limit that runs before routing.
- **No `CHECK_SECRET_KEY` rotation procedure.** Losing or changing the key makes every stored secret unreadable, and
  the checks using them misconfigured. Recorded in `docs/operations/production-readiness.md`.
- **The import success message does not name the created checks or open the list filtered to them.** Skipped as
  low value against the rest of the review.
- **Every metric point carries `run.id`, so each run is a new series** in the metric tables. It is what joins a
  reading to its evidence; the cost is series cardinality growing with runs, which needs measuring before many checks
  run at one-minute intervals.
- **NFR5** (500-operation import preview time) is enforced as a limit and not timed.
- **The heartbeat and unattached-file sweeps** are unit-tested; neither sweep was run as a test against real Postgres
  and the object store. The heartbeat ping itself is live-tested.
- **AC26 end to end**: every kind is created, filtered, paused, resumed and deleted in the e2e suite; *editing* is
  end-to-end for the API check only, and render-tested for the other kinds.

## Validation

All run against the dev stack, sequentially (running them concurrently exhausted ClickHouse's 4 GiB memory limit
with the demo up).

**After the QA pass and its fixes, 2026-09-18:**

- **`just check`: exit 0.** Python **3139 passed, 3 skipped**; web **1156 passed** (95 files); browser SDK **120
  passed**; Go every package `ok`; ruff, mypy, biome, `tsc`, `buf lint` and `buf breaking` clean. The metrics test
  that was red is fixed in `docs/changes/qa-found-defects/`, which this count includes.
- **`synthetics.spec.ts` end to end: 5 passed**, run alone.
- **Observed in the browser after the fixes:** a heartbeat's p95 and every unmeasured run read "—" rather than
  `0ms`; a misconfigured run's rows likewise; the heartbeat's own copy no longer talks about probes. (Readings
  published *before* the fix keep their zeros in ClickHouse until they age out of the window.)

**Before the QA pass, 2026-09-17:**

- **`just check`** — exit 1, one failure, **not in F18's diff**:
  - fmt-check, lint (ruff, mypy, biome, tsc): clean.
  - Go: every package `ok`.
  - Python: **3131 passed, 3 skipped, 1 failed** —
    `apps/api/tests/test_metrics_live.py::test_the_window_delta_is_far_below_the_number_of_points_stored`
    (`assert 543 >= 2423`). The file and `metrics/` are untouched by this change. The test assumes the k6 exporter
    started inside its two-day lookback; once the window slides past that start, a correct window delta is
    smaller than the largest cumulative point. A test assumption about data age, recorded here rather than widened
    into F18.
  - Web: **1147 passed** (93 files). Browser SDK: **120 passed**.
  - Contracts: `buf lint` and `buf breaking` clean.
- An earlier run, before the demo was stopped, failed only
  `test_synthetics_live.py::test_the_phases_of_a_probe_sum_to_the_duration_it_reported` for want of a successful probe;
  with the demo back up that suite passed alone: **13 passed**.
- **Focused suites after the review fixes**: API checks and imports 243 passed; `test_checks_live.py` 18 passed
  (including sealed Run now, files, import collision); scheduler 191 passed plus the platform suite.
- **End to end (`just e2e`)**: **15 passed, 1 failed.** The failure was
  `synthetics.spec.ts` "an API check created in the console … deleted there": the delete panel's run count got a 429
  from the per-IP telemetry quota while 16 specs ran in parallel through one proxy IP (API log:
  `rate limit rejected a request … run-count … policy=telemetry`). The screen showed its honest fallback ("The numbers
  could not be counted just now"). **Run alone, `synthetics.spec.ts`: 5 passed.**
- **Review findings fixed** (each with a test):
  - API: heartbeat token redacted from every log line; check-file upload streams with a running byte cap before the
    body is spooled; import confirm refuses a secret or variable name that already exists; stored objects are never
    deleted before the row change commits; duplicate check ids in a maintenance window are a validation error, not a
    500; the counted-outcomes SQL is built from one constant; Run now's task arguments are held against the worker's
    signature in `test_cross_service_contracts.py`; files are swept on `detached_at` and re-checked before the object
    is deleted; design and ADR-011 updated to the code.
  - Scheduler: time limits on every dispatch so our own cut-off is never reported as the target failing; redaction by
    value (raw, URL-encoded, JSON-escaped, base64) before truncation, including failure text; the refusal reason names
    no address; non-ASCII URLs encoded; DNS resolvers configured rather than implicit; an unexpected exception in a run
    is a `prober_error`, never a lost task.
  - Console: binary body defaults to `application/octet-stream`; the edit form keeps an edited draft when the check
    refreshes; "No runs in this window" with a widen action instead of "Not run yet"; API errors placed on the rows
    they name and cleared on edit; Run now validates first; oversized files refused visibly before upload; secret and
    variable deletes state how many checks use them; the e2e kind filter asserts exactly one row per kind.

### Hands-on QA pass, 2026-09-18

Driven through the console in a real browser against the dev stack — every screen, every kind, edge cases, dark mode
and a 390 px viewport — rather than only through the suites. A throwaway organisation was created through the
product's own signup so the pass started from an empty project.

**Acceptance criteria exercised by hand and observed passing:** AC1 (method, headers, query and body echoed back by
httpbin), AC2 (a multipart field and a file's bytes arrived), AC3 and AC16 (a failing JSON-path and a failing DNS
answer), AC4 (a value extracted in request 1 reached request 2), AC5 (a private address, the cloud metadata address,
`localhost`, a private resolver, `file://` and `ftp://` all refused), AC7 (a cURL token became a secret and its value
was never shown), AC9 (three OpenAPI operations became three checks, path parameter from its example, assertions from
the described statuses), AC10 (a remote `$ref` refused with no outbound request), AC12 (folders become multistep
checks), AC13 (Postman v2.0 refused with re-export steps), AC14 (a heartbeat went down after period plus grace, then
came back up on the next call), AC15 (open port up, closed port down), AC17 (a secret's value absent from the
evidence, the sent request, the URL, the stored ClickHouse run, Redis, Postgres plaintext and every container log),
AC18 (Postgres holds sealed ciphertext), AC21 (degraded above the threshold), AC22 and AC23 (maintenance runs marked
and excluded from uptime; a misconfigured run left uptime alone), AC24, AC25, AC26, AC27, AC29.

**Defects found and fixed in this pass** (each with a test):

- **A run that timed nothing published a duration of zero.** A heartbeat is never probed and a misconfigured run
  never sends a request, yet both reported `0ms` — and a heartbeat's p95 read `0ms`, the one reading it can never
  earn. The duration is now absent at every layer: no metric point is published, `RunEvidence.duration_ms` and
  `RunMeasurement.total_seconds` are nullable, the read path distinguishes "no points" from zero with a count beside
  each aggregate, and the console renders an em dash. This is the rule `status_code` already followed.
- **AC6 was only half met.** A fourth attached file was uploaded and stored, and refused at save; the object then
  waited for the sweep. It is now refused before the upload, like an oversized one.
- **An unknown `{{variable}}` or `{{secret.NAME}}` was reported against the whole request**, so the message appeared
  under the URL however deep in the body the reference was. The console now checks references against the project's
  own names and marks the field that wrote one; the API stays the authority.
- **Run now said "This check cannot be saved"** when nothing was being saved. It has its own refusal now.
- **Heartbeat screens used probe copy** ("Not run yet — the first run is due within one minute of saving", "No runs
  in this window. Widen the time range") for something that is never probed, and the list said "last run" for a
  check that is only ever called. A never-run check was also told to widen a window that cannot help.
- **A paused check still read "runs every minute from every location"** beside the word *paused*.
- **An assertion row kept the previous source's expected value**, so switching a status-code row to a header left
  "Content-Type equals 400" ready to save.
- **Accessibility:** a section's empty state rendered an `h2` inside an `h3`; the secret and variable forms both
  labelled their fields "Name" and "Value", giving one page three identically named inputs. `EmptyState` now takes a
  heading level and the fields name what they hold.
- **The refresh-interval control appeared on the new-check and import screens**, which read nothing on a timer.
  `useNothingLive` existed and had no callers; these two screens now use it.
- **Two live tests rested on assumptions my QA data broke:** the synthetics subject was "the check with the most
  readings", which can now be a heartbeat (no timing) or a check failing all day, and the phase-sum test only looked
  at the newest page of runs. The subject is now chosen by successful duration readings, and the arithmetic test
  searches the window.

**Found outside F18, and since fixed** under `docs/changes/qa-found-defects/` (all pre-existing, none in F18's diff):

- **`seaweedfs`'s health check probes its master port, not its S3 port**, so Docker calls it healthy before S3
  answers and the API exits on a cold `docker compose up` with "could not prepare the bucket". It is healthy after a
  restart, which is why this has not been noticed. `deploy/compose/compose.infra.yaml`.
- **`RefreshControl` sets its own state while another component renders.** `useNewestData`
  (`app/layout/RefreshControl.tsx:165`) subscribes to the query cache and reacts to every event, including the
  "query added" one React Query emits during a first render, so React logs "Cannot update a component while
  rendering a different component" on every screen that creates a query.
- **The unsaved-changes prompt is not reachable by keyboard.** It is an `aria-modal` `alertdialog`, but focus never
  moves into it, Escape does not dismiss it, and Tab walks the page behind it
  (`shared/components/UnsavedChanges.tsx`). It guards every F18 form.
- **Two nested `<main>` landmarks** on every settings screen (`AppShell.tsx:64` and
  `shared/settings/SettingsLayout.tsx:50`).
- **`--series-1` and `--series-2` are 20° apart in hue** (`packages/ui/tokens.css`), so `dns` and `ttfb` read as the
  same colour in the five-phase chart, in both themes.
- **A cancelled request is logged as "Request timed out"** (`shared/api-client.ts:176`); navigating away writes a
  warning per in-flight read.
- **`test_metrics_live.py::test_the_window_delta_is_far_below_the_number_of_points_stored`** fails on data age, not
  behaviour: it assumes the k6 exporter started inside its two-day lookback, and a correct window delta is smaller
  than a cumulative point once the window slides past that start. It is the one red test in `just check`.

**Not fixed, recorded:** the URL's query string is not split into the Params tab; a JSON body accepts unparseable
JSON (deliberate — `{{name}}` is not JSON until it is substituted) with no editor hint; import refusal cards end
with an unlabelled correlation id; a file chooser's hint reads as a claim about the chosen file.

## Specification impact

None to requirements. The design was amended for the routes, the sealed-secret task arguments and file detachment;
ADR-010 amended and ADR-011 executed.

## Documentation impact

Updated to match the code: `design.md`, `plan.md` (Outcome), ADR-010, ADR-011,
`architecture/current-system-reference.md` §7 and its tables, `architecture/integrations.md`, `backend.md`,
`product/vision.md`, `product/feature-catalog.md` (F18 built 2026-09-17), `operations/production-readiness.md`
(check secret key; blockers 8 and 10), `design/design-system.md`.

## Last updated

2026-09-18

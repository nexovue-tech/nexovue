# Status: deployment-release-tracking (F28)

## Lifecycle status

**In development. Step 2 is on `feature/phase-2` (2026-11-08).** The founder approved the specification, design and
plan on 2026-11-02. Step 1a (the version rollup and its cost measurement) and Step 1b (releases from traffic and the
comparison by version) are on `feature/phase-2`. Step 2 (a person can set it up) is built in the worktree, not
committed; T2.10's timed new person is not yet run (founder tests after Phase 2). Step 3 is next.


## Current task

**Step 2 is on `feature/phase-2`** (*Step 2, built 2026-11-08*, below): migration **`0057`**
(re-chained at commit), **ADR-033**. Step 1b is on `feature/phase-2` (*Step 1b*, below), and Step 1a's cost gate
passed on demo data ([report](../../research/release-rollup-cost-2026-11-07.md)).

## Step 2, built 2026-11-08

A person can set it up (plan T2.1 to T2.9; T2.10 below). What exists now, read from the code:

- **The record** (T2.1): Postgres migration `0057_deploy_envelope_and_sealed_secrets` (revision "0057", down
  revision "0054" for now: 0055 and 0056 are held by parallel work, so the coordinator re-chains it). `deploy_events`
  gains `state`, `commit_sha`, `url`, `release_id` and `webhook_secret_id` (both `ON DELETE SET NULL`), `delivery_id`
  NOT NULL at 200 with a plain unique key, and indexes on `created_at`, `release_id` and `(webhook_secret_id,
  created_at)`; `project_webhook_secrets` swaps `secret` for `sealed_secret`, `secret_hint`, `last_used_at`;
  `deploy_intake_state` is new, in `organizations/tables.py`. Indexes are built concurrently and the new keys and
  check validated after `NOT VALID`. Existing secrets and id-less deploys are deleted (pre-deployment). Roll
  forward only, listed in `test_migration_rules.py`.
- **The webhook** (T2.2): `POST /deploys/webhook/{project_id}` takes `{"deploys": [...], "dry_run": false}`, 1 to
  100 items, 512 KiB ("at most 512 KiB; send fewer items"). Order: open the live secrets, verify over the raw bytes
  (`signatures.matching_secret`, constant time over all), mark the secret used, then parse. Every FR9 check, with
  one 422 naming item and field; a signed refusal is recorded in `deploy_intake_state` and committed before the 422
  unwinds (the precedent is sign-in's refusal). Repeats answer with what they stored. New items are charged to the
  project's exact hourly quota (`DEPLOYS_ACCEPTED`, 1,000 per 3,600 s) by a conditional update, so a refused batch
  charges nothing; then evict past 50,000 (now `nexovue_platform.releases.RELEASES_PER_PROJECT`, shared with the
  sync), insert in (service, version) order `ON CONFLICT DO NOTHING`, file each under its release by the start
  rule, place periods, one `deploy.batch_recorded`. `metadata` is masked by `nexovue_platform.masking`, the port of
  the worker's default rules, held with Go to `packages/contracts/fixtures/metadata-masking.json`. Each answer:
  `deploy_id`, `release_id`, `repeated`, `known`, `version_seen`, `recent_versions`.
- **Secrets** (T2.3): sealed under `CREDENTIAL_ENCRYPTION_KEY`, purpose `deploy-webhook`, owner
  `project_id:secret_id` (the id read from `uuidv7()` first); in `SEALED_COLUMNS`; shown once in
  `MintedWebhookSecretResponse`; at most 5 live, under a project row lock. A seal that will not open is no match,
  logged by id. Without the key, minting and the webhook answer 503.
- **Audit** (T2.4): `deploy.webhook_secret_viewed` and `deploy.recorded` are gone; `deploy.batch_recorded` (secret,
  count, first deploy) and `deploy.removed` (who, and the deploy or the secret and time) are new.
- **Rate limit comment** (T2.5) corrected: the address tier bounds requests; the new quota bounds deploys.
- **The console** (T2.6): *Deploys* in project settings for `manage_credentials` only: URL, secrets with ending,
  created and used, mint (disabled at 5, with why), a minted secret shown once with `UnsavedChanges`, withdraw with
  inline confirmation and "also remove what it sent since", numbered rotation steps, `Tabs` of recipes, a
  `role=status` intake line polled every 5 seconds for 10 minutes, the release-cap sentence, current versions. The
  non-admin sentence names deploy secrets.
- **Setup** (T2.7): `docs/product/setup/deploys.md` (both ways in, `service.version` for Helm, Kustomize, Docker and
  systemd, one project per environment, the three recipes, the body's rules and the 7-day limit, clear text,
  reachability, sampling, rotation and removal, a post-deploy comparison read), with entries in `index.md`,
  `api.md` and `troubleshooting.md`; Kubernetes, EC2, the four language pages, `browser.md` and onboarding now set
  `service.version` from a build variable. The Kubernetes block also declared its variables after the line using
  them, against its own ordering rule: fixed.
- **The demo** (T2.8): `tools/otel-demo/doors.py` sends one batch for both services, minting a secret for the run and
  withdrawing it after.
- **Removing** (T2.9): `DELETE /deploys/{id}` and `DELETE /deploys/webhook-secrets/{id}?remove_since=`, both
  `MayManageCredentials` (in `POLICY_OF`), each recalculating: a release's reported facts are worked out again from
  the deploys left, one nothing supports is deleted, and report-decided periods are rebuilt.
- **ADR-033** filed (the design's third proposed ADR).
- **T2.10, the timed new person: not yet run, founder tests after Phase 2** (foundation item 28). The five-minute
  claim is not withdrawn here: AC1 makes that the outcome only if nobody is found, which is the founder's to
  decide.

**Readings made while building** (none blocking; each open to overrule):

- **On a service that sends no version in traffic, a newer report ends the period it replaces**, at the new start
  (a `started` or `finished` one only: *Founder answer, 2026-11-09*);
  a report arriving late for an earlier time opens a period ending where the next one starts; periods are placed in
  claimed-time order within a batch. The design names only the return rule; without these, AC59's "v1, v2, v1" had no
  v1 period to end. A service with versions in traffic is still the sync's alone to end and return.
- **Names are stored exactly as sent, not stripped.** The old schema stripped whitespace; Python counts U+0085, a C1
  control, as whitespace, so `v1\u0085` was accepted as `v1` while the worker folds it. Found by the new test holding
  the door to `release-names.json`.
- **An unknown field is refused** (named as "unknown field"), not ignored as before, so a misspelt `commit_sha`
  shows in CI's output.
- **The webhook answers 200**, with `stored` and per-item `repeated`, rather than 201: a batch can store some, all or
  none.
- **Signup no longer mints a deploy secret**: shown once, one minted there could never be read. `first-accounts.md`
  and two billing comments that described door 4a's old header id are corrected.
- **The section's current versions come from `GET /deploys/webhook-secrets`**, the one endpoint it polls; the design
  names the list, not its route.
- **Recipes sign with `python3` reading the secret from the environment**: `openssl dgst -hmac` takes the key as a
  process argument, which FR14 forbids. They `set +e`, give each attempt 10 seconds and the retries 30, warn, and
  exit 0, and leaving out `NEXOVUE_DRY_RUN` means a real deploy.
- **A charge is spent before the insert**, in its own transaction: two copies of one body racing can each be charged,
  and a deploy transaction that fails afterwards does not refund. Bounded by the batch size.

**Tests, each seen failing first** (outputs kept in the session's scratch space):

- `test_platform_masking.py` (26): collection failed on the missing module, then passed. Go
  `mask/metadata_fixture_test.go` passed against the worker as written; with one fixture case altered it failed
  naming the case, then the fixture was restored.
- `test_deletion_live.py` (25): with `deploy_intake_state` left out of the inventory it failed naming it; restored,
  passed.
- `test_ratelimit.py` (+3), `test_keys_live.py` (+1, and the reseal case now covering deploy secrets): each failed
  against the previous `limiter.py`/`policy.py`/`keys.py`, then passed.
- `test_audit.py` (+3): failed on the missing functions, then passed.
- `test_deploys.py` (134): the previous suite failed against the new code as expected; the new one was checked by
  removal: without the time bound 2 failed, without the refusal's commit 1, without masking 1. Its name-fixture
  test failed twice on U+0085 (above) before the fix.
- `test_deploys_live.py` (30): written after the service, passed first run; checked by removal: the limiter's
  condition (1 failed), the return rule (2), ending the previous period (1), recalculation (3), claimed-time order
  (1).
- **Founder answer of 2026-11-09, built the same day** (`releases/service.place_periods`, the one place every report
  reaches a period, from the webhook and from recalculation after `DELETE /deploys/{id}` and a withdrawn secret's
  removal): `test_deploys_live.py` now 39. Eight new cases, `failed` and `rolled_back` each: no period and the open one
  left open on a service with no version in traffic and on one with versions in traffic; no return; recalculation
  after a removal skips it. The opening case runs for `started` and `finished`. With the existing removal case's
  expectation corrected (a lone failed report keeps no period), 9 failed against the code as built and 39 passed
  after, on this session's own Postgres (port 25432, since torn down).
- `test_authorization_coverage.py`: failed on the unplaced `delete_deploy` and the renamed `receive_deploys`, then
  passed. `test_postgres_tenancy_structure.py`: failed on three period statements whose `org_id` sat in a helper,
  then passed with it in each statement. `test_cross_service_contracts.py` failed on the eviction written as raw
  SQL in a module that also holds ClickHouse SQL; rewritten in SQLAlchemy Core, passed.
- `test_deploy_recipes.py` (53): against the original Kubernetes page the version check failed; with `exit 0`
  replaced in the curl recipe, its three exit tests failed.
- Web: `DeploysSection.test.ts` (15): with the section ungated 2 failed, with the guard off 1, with the secret as an
  argument 3. `DetectionSection.test.ts` gained the Deploys stub its owner render now needs.

**Counts.** Offline, with live suites left out (`NEXOVUE_SKIP_LIVE_TESTS=1`): `apps/api`, `packages/py-platform`,
`apps/scheduler`, `packages/contracts`: 9,347 passed, 4 skipped. Live, on this session's own Postgres (port 25432,
since torn down): `test_deploys_live.py`, `test_keys_live.py`, `test_deletion_live.py`: 59 passed. Go: the `mask`
package's new test passed. Web: `features/projects`, `shared/components`, `features/onboarding`: 227 passed.
`uv run mypy .` (798 files), `ruff check`, `ruff format --check`, `pnpm typecheck`, `biome check` on the changed web
files and `verify-tokens.js`: clean. HMAC cost: 5 secret openings and 5 HMAC-SHA256 over 512 KiB, 1.82 ms median
(3.06 ms max) per request on this machine (4 vCPU Xeon at 2.1 GHz), about 0.11 s of CPU a minute at the 60-a-minute
address limit.

**Not run:** `just check` (the coordinator's); `test_releases_live.py` and `test_release_sync_live.py`, which need
ClickHouse (the sync's only change is reading the shared cap constant); the recipes on a hosted runner (no reachable
address); the full dev stack (T2.8 ran against an API started locally on this session's Postgres); Design QA at 375px;
T2.10.

**Residual risks** (recorded, not fixed):

- **Two copies of the recipes**: `deploys-api.ts` renders them for the console and `deploys.md` holds them for the
  page. `test_deploy_recipes.py` runs the page's, and `DeploysSection.test.ts` checks the console's; a change to one
  that misses the other is caught only where it breaks a check.
- **AC51 races a sync-shaped writer**, the sync's own upsert statement in its order, not the scheduler job itself,
  which the API's tests cannot import.
- **"Remove since" is entered in the browser's time**, labelled so; the design's rule is the project's zone. Design
  QA should judge it.
- **A recipe's JSON is built with `printf`**: a service or version containing `"` or `\` makes an invalid body,
  refused with a warning. Telemetry names rarely do; the page does not yet say so.
- **Deploys that fail on the server after charging** do not refund the hour's allowance (above).
## Step 1b, built 2026-11-08

A release compared by version, on real traffic (plan T1.8 to T1.16). What exists now, read from the code:

- **The record** (T1.8): Postgres migration **`0054_releases`** (the next free number after F19's 0053; F22 may also
  take numbers in parallel, so the coordinator renumbers at commit if they collide), `releases`, `release_periods` and
  `release_sync_state`, with models and `organizations/tables.py`. One open period per release and one period per
  release and start are unique; '' and `__other__` are refused by the database. Roll forward only, like F19's.
  **ADR-029** filed (the design's second proposed ADR). The third, the deploy envelope, is Step 2's.
- **The comparison** (T1.9): `nexovue_platform/releases.py`, pure: the one name check (held to
  `release-names.json`, as the Go side is), `storable`, `bucket_p95` (moved from detection's `method.py`, which
  re-exports it), `compare_failure_share`, `compare_p95` and `choose_method`, the design's ordered list in full.
- **The sync** (T1.10 to T1.13): `nexovue_scheduler/releases/` with `sweep:releases` on beat every minute, on its own
  `releases` queue, capped at 2 under `scheduler-light`'s floor, in `worker_queues`' default and both compose files'
  comments; `observe_project` under the project's session lock; the per-minute walk (`sync.walk`); writes sorted by
  service and version in one transaction; `synced_through` forward only; 200 new releases a run; eviction past 50,000.
  The store reads through `alerts.telemetry`'s reader, so it holds no driver. `test_detection_isolation.py` walks
  detection and the alert roots and finds no path to the sync. The one cross-tenant statement, the sweep's
  `_MOVED_SINCE_SYNC`, is named in the scheduler Postgres tenancy test.
- **The API** (T1.14): `GET /releases` (window overlap, service and version filters, keyset page, the window's
  totals, `has_any` and `latest` for the three empties), `GET /releases/{id}`, `GET /releases/{id}/comparison`
  (`period`, `window` of 1, 6 or 24 hours, to the settled minute). On the read surface and `setup/api.md`; the
  `/releases` prefix is on the telemetry rate-limit tier, as the design says, though only the comparison reads
  ClickHouse.
- **The console** (T1.15): `/releases` in Signals after Errors, and `/releases/$releaseId` with the opening sentence
  (`role=status`), the two blocks with a table from `md` and stacked lines on a phone, the window tabs, the facts and
  periods, each section failing on its own; `shared/format/version.ts` for long versions.
- **AC22** (T1.16) and **the gate's alert evaluation start delay** (added to T1.10 by the founder on 2026-11-08):
  both measured and passed on demo data (cost report, *Addendum, Step 1b*): about 60 seconds from the settled
  minute to the release row, against 3 minutes; and an alert sweep claimed in 0.049 s (median) with 51 release
  jobs queued and one on a 360,000-row first read, against 0.071 s with none.

**AC28, recorded as a fact.** F6's project job over four weeks at 0.2% failing, then 40 minutes at 0.8% mixed (3% on a
fifth of traffic), **opened no finding**: its minimum change at normal sensitivity is 2 percentage points
(`detection/method.py`, `SENSITIVITIES`). A 30% control opened a critical one, so the harness can open one. The
release page reads the same rollout side by side, 3.0% against 0.25%, "higher". `test_releases_live.py` asserts both,
so a change in F6 shows there.

**Readings made while building, against the plan's or design's wording** (none blocking; each open to overrule):

- **`LOG_EDGES` had already moved** to `nexovue_platform/slo.py` with F19, so T1.9 moves only `bucket_p95`.
- **`releases.store` is not added to the raw-client list** (T1.11): it reads through `alerts.telemetry`'s reader, as
  F19's `slos/store.py` does, so one module fewer holds the driver. `PROJECT_BOUND_PACKAGE` became
  `PROJECT_BOUND_PACKAGES = ("detection", "releases")`.
- **The sync reads the hour before its new minutes again, not five minutes.** The 60-minute rule needs that hour, so
  a version first counted in rows that arrived late within it also gets its true first minute. The design's stated
  risk now reads "more than an hour late". A first run reads exactly 6 hours.
- **How an end is dated** (the design names the rule, not the minute): a takeover ends a period at the first minute
  of the other version's 10-minute run; a quiet end at the minute after its last request. "Named requests" leaves out
  '' and `__other__`.
- **Every version first counted in a service's first counted minute is already running** (connecting mid-rollout).
- **Every request failing on both sides** has a spread: the pooled share is held one request from 1, as the design
  holds it one from 0.
- **The comparison response leaves out** `incomplete`, `minutes_differing`, `versions_combined` and
  `mixed_environments` until T4.1 computes them, rather than answering `false`. `time_before_after` is computed from
  the version rows when every row has no version (those rows are the service's totals, FR16); T4.1 adds last week
  and completeness. No release has a reported time before T2.2, so it cannot occur yet.
- **The sync's figures are log fields, not Prometheus metrics** (`release_sync`: `duration_ms`, rows read, new
  releases and periods; `release_sync_bounded`: rows skipped, evictions, the cap). Jobs run in child processes, which
  export no metrics in this codebase; the `releases` queue's age is the existing per-queue gauge.
- **The list's empty state links the setup documentation only.** The Deploys section it would also link is T2.6.

**Tests, each seen failing first** (outputs kept in the session's scratch space):

- `test_platform_releases.py`: collection failed on the missing module, then 47 passed.
- `test_deletion_live.py`: failed naming the three unclassified tables, then 25 passed.
- `test_release_sync.py`: failed on the missing package, then 21 passed (one fixed in the code on the way: the cap
  stopped a run on a minute bringing nothing new).
- `test_beat_schedule.py` and `test_compose_contract.py`: failed on the missing beat entry and queue, then 71 passed.
- `test_tenant_chokepoint_structure.py`: with the project predicate removed from the store, failed naming
  `releases.store`; restored, 18 passed. `test_detection_isolation.py`: with an import of the sync placed inside a
  function in `alerts/silence.py`, failed naming the chain; removed, 5 passed.
  `test_scheduler_postgres_tenancy_structure.py`: failed naming `_MOVED_SINCE_SYNC`, then 14 passed.
- `test_release_sync_live.py` (13) was written after the walk: its first run failed on a fixture mistake (a version
  name colliding with a junk one); with eviction removed from the code, the 50,000-release case failed; restored, 13
  passed.
- `test_read_surface_structure.py`: failed on the three new reader routes, then passed with `setup/api.md` updated.
- `test_detection_coordinator.py`: failed on the worker's default queues, then passed.
- Web: `comparison.test.ts` failed with no module, then 18 passed; `route-manifest.test.ts` failed on the missing
  Signals entry, then passed; `ReleasesScreen.test.ts` (9) failed 2 with the empty and zero-result branches broken,
  then passed. `version.test.ts` (3) was written with its formatter and not seen failing.
- `test_releases.py` (14) and `test_releases_live.py` (13) were written after the routes; they passed on first run.

**Counts.** Offline, `packages/py-platform`, `apps/scheduler`, `apps/api` and `packages/contracts` tests with live suites
left out: 9,142 passed and 1 failed, the worker-defaults test above, then fixed. Live, on this machine's stores:
`test_release_sync_live.py`, `test_releases_live.py`, `test_deletion_live.py`, `test_jobs_live.py`,
`test_detection_project_job_live.py`, `test_detection_shifts_live.py`: 106 passed. Web: the releases folder, the
route manifest and the version formatter, 38 passed. `uv run mypy .`, `ruff check .`, `ruff format --check .`,
`pnpm -r typecheck`, `biome check` on the changed web files and `verify-tokens.js`: clean.

**Not run:** `just check` (the coordinator's); the full live suites beyond those named; Go (no Go file changed);
Design QA in a browser at 375px (Gates); `test_erasure_live.py` (unchanged, needs SeaweedFS).

## Step 1a, built 2026-11-07

What exists now, read from the code:

- **`service_versions_1m`**, ClickHouse migration `000038` (the next free number on the branch), `000035`'s shape plus
  `version`, in the erasure inventory (`erasure/tables.py`, `erasure/statements.py`, `test_erasure_live.py`). T1.1.
- **The worker's version map** (`derive/versions.go`): fed from `observeService`, so it counts exactly the spans
  `service_stats_1m` counts; the drain cap of 20 per service per drain, the arrival caps of 64 per partition, service and
  minute and 512 per tenant, partition and minute; folding into `__other__` for over-long or failing names; a service
  the per-tenant cap leaves out leaves its versions out; `Disown`, `PendingVersions`, the fold report. The Go side of the
  name check is `derive.NameAllowed`, held to `packages/contracts/fixtures/release-names.json`, which the webhook's
  Python check reads in T2.2. T1.2.
- **The browser door counts under no version** (`traceassembly/flatten.go`, `countedVersion`), until the browser-door
  change keeps such spans out of both rollups and deletes that branch (D55). This matches that change's plan, which
  filters before `counters.Observe` (`docs/changes/browser-door/plan.md`, T1.1): no conflict. T1.3.
- **The writer** (`derive/versionwriter.go`) with its deduplication token, inserted last in `insertDerived`, never an
  error and never holding progress: a refusal is counted in `derived_rows_refused`; a transient failure has its own
  30-second budget (`consume.RetryWithin`, new), then is stepped over and the insert paused for 5 minutes, counted in
  `version_rows_stepped_over` and `version_rows_skipped`. Heartbeat and release log lines carry `version_series` and
  `versions_folded`. T1.4.
- **`tools/release-demo`**: `send.py` (every named mode; a ledger of what the receiver accepted; `verify` against the
  store) and `release_measure.py` (the timed runs and the gate's table), with `just release-demo` and
  `just release-measure`, and both in `pyproject.toml`'s `testpaths` and `pythonpath`. `--deploys N` is not built: the
  task gives it "from T2.2 on", when the new deploy body exists. T1.5, T1.6.
- **`tools/detection-measure/scale.py`** reads `service_versions_1m` and each table's index memory in its storage census,
  so `just detection-scale watch` at AC29's volume reports the version table beside `service_stats_1m`.
- **ADR-025** filed (the first of the design's three proposed ADRs). The other two, the releases record and the deploy
  envelope, are filed when Step 1b and Step 2 start, since nothing they decide is built yet.

**Tests, each seen failing first.** Go: `derive/versions_test.go` (14 tests: AC15 for any mix over 40 seeds, AC18's
25 versions, the two-drain fold, the 64-name junk burst, the per-partition and per-tenant arrival caps, long and
control-character names, the shared name cases, the service cap, the empty version, a reproducible drain, the revoke
with its arrival room, the arrival room freed with its minute), `versionwriter_test.go`, `consume/permanent_test.go`
(the budgeted retry), `traceassembly/flatten_test.go` (AC17 before the change), `refusal_test.go` (AC19 for this table,
AC54), `counterhandover_test.go` and `restart_test.go` (AC16), and `counters_order_test.go`, `disown_test.go` extended.
A store probe, `derive/versionwriter_storeprobe_test.go`, writes one drain to both tables in a real ClickHouse 26.3 and
reads the version rows summing to the service rows bucket by bucket, with a retried insert deduplicated: passed.
Python: `tools/release-demo/test_send.py` (12), `test_release_demo_measure.py` (19: 9, then 10 more for T1.7, of which
the folded-churn, GOMEMLIMIT and compose-limit tests failed against the earlier tool first; the five "totals differ"
cases passed against it too, since it was stricter), `test_erasure_inventory.py` (failed on the missing table, then
passed). `uv run pytest tools/release-demo -q`: 31 passed; `ruff check`, `ruff format --check` and `mypy .` clean.

**End to end, on this machine's own stores** (ClickHouse, Postgres, Kafka on non-default ports, receiver and worker
from source): `release-demo send rolling` then `verify` printed 240 and 239 requests sent on v1 and v2, the same stored
in `service_versions_1m`, and the version rows summing to `service_stats_1m`.

**Not run:** `just check` (the coordinator runs it); `test_erasure_live.py`, which needs the full dev stack with
SeaweedFS; the Step 1b, 4 and 5 tasks.


## Completed

- [`research.md`](research.md), written on 2026-11-02, with its competitor rows and claim corrected after the reviews.
- [`spec.md`](spec.md), [`design.md`](design.md), [`plan.md`](plan.md), written on 2026-11-02 and revised the same day.
- Decisions D1 to D58 recorded in `spec.md`, each open to the founder's overrule (D56 to D58 added 2026-11-02, below).
- Eight persona reviews, 131 findings, each checked and recorded below.

**What changed from the research, on reading further**

- A release is a stored row, and each stretch in service is its own period (D2, D15).
- Secrets are shown once rather than re-readable (D7).
- The replay fix signs the id inside each item, and requires a signed time (D6).
- The browser-key question is answered from the code, not yet run: a browser key is expected to be able to send a SERVER
  span. F28 excludes it (D12, D38).
- The cost gate moves into plan Step 1a, since measuring needs code (D27).

## Next action

Coordinator: review and commit Step 2 (re-chain migration `0057` after 0055 and 0056; ADR-033). Then Step 3, markers
that can be read. T2.10's timed new person is the founder's, after Phase 2. `docs/architecture/database.md` gains the
new tables in T5.7, as planned.


## Open decisions

**One reading from Step 1b, not blocking.** **The per-run cap of 200 new releases never splits a minute.**
`synced_through` is kept by the minute, so a strict cap would stop the sync for good on any minute holding more than
200 new versions (500 services each first counted in one minute is enough: the measurement saw 1,000 in one run). The
walk therefore takes a whole minute even past the cap, and stops only before a later minute that would cross it
(`sync.walk`, `test_one_minute_over_the_cap_is_still_taken_so_a_run_always_moves`). Options: keep this (recommended:
the cap still bounds every later minute, and a first run is bounded by its 6 hours and the arrival caps); or keep the
minute but split its writes across runs, which needs a second position inside a minute. Overrule by saying which.

**From Step 1a, none blocking.** Readings made while building Step 1a. **Founder answer, 2026-11-08:** the last three below (the gate's
rows, churn run once, the alert-delay row) are **accepted as recommended**: the gate stands as passed on demo data, and the
alert evaluation start delay is added to Step 1b's sync-job task when the plan is next revised and measured before Step 1b
is called done. The first two stay open to overrule:

- **The name check refuses DEL (U+007F).** FR9 says "C0 and C1 control characters"; DEL is in neither range strictly, but
  it is Unicode category Cc with them. The check uses Cc, and the shared fixture states it. Overrule by removing the DEL
  case from `release-names.json` and narrowing `derive.NameAllowed`.
- **The empty version takes arrival room like any version.** The design does not say whether `''` is exempt from the
  arrival caps. Treating it as a version means a junk burst can fold "no version" traffic into `__other__` too, which the
  "versions were combined" label already covers.
- **The gate applies the four ratio rows to the demo traffic and the churn row to churn** (design, *The gate*; founder
  decision 24 splits cost checks from correctness checks the same way). "Totals exact" means nothing lost and every
  service's versions, `__other__` included, holding what was sent, with no named version above it; folding a named
  version short is allowed (FR17, AC18). Overrule by making churn meet the ratio bounds too, which it does not (21× rows,
  +15.8% memory, 2.065× disk; a cost failure under decision 24, which keeps version comparison if it fits 16 GB).
- **Churn ran once for 15 minutes,** not the design's 3 repeats of 30 minutes. Its margin is wide (151 of 1,088 MiB),
  so the gate is called passed; the founder may ask for the full repeats.
- **The gate's alert evaluation start delay row has no task.** It needs Step 1b's sync job. Proposed: add it to Step 1b's
  sync-job task when the plan is next revised, and measure it before Step 1b is called done.

The customer conversations (foundation item 8) and the browser-door change (item 7) were settled by the foundation on
2026-11-02. The conditional gate after Step 1a was answered on 2026-11-02 (foundation item 24).

## Blocked

- **Founder decision, 2026-11-08:** Step 1b no longer waits on the buyer round (foundation item 27). Built 2026-11-08.
- **Step 4 is blocked on the shared browser-door change** (below; foundation item 7, D58). **F28 owns it** (foundation item 22,
  2026-11-02). The plan holds no task for it yet; they are added when the plan is next revised.
  **2026-11-07:** the browser-door change is built (all four steps; its live test asserts `service_versions_1m` too,
  and it deleted `countedVersion`, D55) and is on `feature/phase-2` (2026-11-07) (`docs/changes/browser-door/status.md`).

## Reviews

Eight personas reviewed `spec.md`, `design.md` and `plan.md`. Each finding was checked against the code or the vendor's page
before anything changed. **Nobody re-reviewed the result.** The verdicts below are what the reviewers said before these
changes, not a judgement of the documents as they now stand.

**Result: 131 findings. All 131 led to a change, and none was rejected whole. Twelve had a part rejected, listed below.** A finding counts as fixed
when the documents now answer it. That means a requirement, design and test are written. It does not mean anything works,
since nothing is built and nothing was run.

Reviewers' line numbers point at an earlier revision and have moved. Where a claim was about the earlier text of these
documents, it could only be checked as the reviewer quoted it. Those rows say *quoted*.

### Verdicts

| Persona | Verdict at review | Findings | Blockers |
|---|---|---|---|
| Architect | Not ready | 21 | 1 |
| Developer | Ready with fixes | 19 | 0 |
| UX | Ready with fixes | 17 | 0 |
| Sales | Ready with fixes | 14 | 0 |
| Marketing | Ready with fixes | 9 | 0 |
| Customer | Not ready | 17 | 1 |
| Security | Ready with fixes | 15 | 0 |
| QA | Not ready | 19 | 2 |

### Conflicts between personas, and how they were decided

The task named a decision rule without stating it, and none was found in the repository under that name. The authors used this one, drawn from the run's standing rules: the correct end state first, then what a tired engineer can trust, then cost. The founder may overrule any row.

| Conflict | Decided | Why |
|---|---|---|
| Customer wants the CI link first on the page, including for viewers; Security wants all sender text limited to members | `url` and `source` for any signed-in person, `metadata` for members and above (D46) | A viewer on call needs the commit. A `url` now holds no query or fragment, and `metadata` is the free text |
| Conversations before Step 1 (Sales), before Step 4 (Marketing) | Before Step 1b, as the founder's choice | Step 1a is a cost measurement and needs no buyer. Step 1b is where the big spend starts |
| Worker counter for environments (Customer); stated limit only (Sales's option) | Read stored spans once per comparison window (D39) | It asks which labels appear, not a rate, so sampling does not matter. It adds nothing to the worker's hot path |
| Summary on `higher` readings (Marketing's example); every release (Customer) | Every release, when opted in (D40) | Sending only on `higher` is an alert from a second detector, which F6 D1 refuses |
| `release_spells` (Architect), `release_periods` (Customer, QA) | `release_periods` | Plainer word |
| Body cap of 256 KiB (Architect), 512 KiB (Developer) | 512 KiB with 1 KiB `metadata` (D32) | The worst plain batch is about 395 KiB, by arithmetic |
| Deleting a release's events on lapse: `CASCADE` (Architect), `SET NULL` (Developer) | `SET NULL`, and retention by arrival time | Arrival time removes the disagreement. `SET NULL` covers eviction |
| Dashboards: shared route (Architect), direct function (F20's current D14) | One function behind the route (D30) | Keeps F20's reasons against HTTP and one set of markers |

### Parts of findings rejected

No finding was rejected whole. These parts were, and nothing else was dropped.

| Finding | Part rejected | Why |
|---|---|---|
| Marketing 1 | Datadog has a `PATCH` route marking a change failure | Not on either DORA setup page read on 2026-11-02, both of which describe failures as detected automatically. Left out of the table, and listed as not established |
| Architect 13 | F20 uses `deploy_events_between` with a cap of 200 | Out of date. F20's D14 now calls `deploys_in_window`, cap 500 (`dashboards/spec.md:531`). The conflict is real but different: D29 makes that read signed-only, so dashboards would lose seen releases |
| Customer 13 | Measure per-endpoint cost in the gate | Measuring means building the table for a feature D18 defers. The report gives an arithmetic estimate instead, labelled as such |
| Customer 12 | A counter in the worker's version rollup | D39. It adds work to the hot path, when a read of stored spans answers it |
| Security 2, Security 9 | Members and above for all of `sender-details` | D46, above |
| QA 15 | Rename the sender's field to `sender_source` | The confusion is fixed by the new word `basis`, and AC39 now says it. F3 and F4 already name `source`, and renaming adds churn with no further safety |
| Marketing 5 | A summary carrying only the `higher` reading | D40, above |
| Customer 15 | A new customer setting for how long `metadata` is kept | It follows the project's derived retention (D20). A separate setting adds a promise nobody asked to make. Masking and the members-only read answer the risk |
| Customer 17 | An optional `deployed_by` field | D42: personal data on a long-lived record, when the commit link already names the author. The finding allowed recording a reason instead |
| Customer 9 | A warning when a version has not changed for an unusual time | No sound definition of "unusual" exists yet. Settings list each service's version and age, which the finding allowed as the minimum |
| Marketing 9 | Fill in the price column, and install Coroot now | Prices stay "not read", with no cost comparison allowed. No install can happen in a documents-only run, so T6.1 owns it |

### What was changed, finding by finding

Checked against: **code** (read at the cited path), **arith** (redone by hand), **web** (vendor page read 2026-11-02), **doc** (another
repository document), **quoted** (earlier text of these documents).

**Architect**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Moving `started_at` erases history (blocker) | quoted | `release_periods`, FR19, FR42, D15, AC25, AC59 |
| 2 | Sweep on the `light` queue can crowd out alerts | code | Own queue, cap, expiry, lock, skip; D31, AC72, gate row |
| 3 | Sync can miss a version or misread order | code | Per-minute walk, `synced_through` rule; AC55. Residual risk below |
| 4 | Batch cannot fit the body cap | code, arith | 512 KiB, 1 KiB `metadata`, new words; FR6, D32, AC8 |
| 5 | Four requirements with no storage | code | `deploy_intake_state`, `known` and `none` from `service_last_seen`, counted limit |
| 6 | Cap of 20 is per drain | code | FR17, D14, AC18 restated; label fires on any `__other__` |
| 7 | "About double" memory does not follow | code, arith | 512 per-tenant arrival cap, worst-case arithmetic, AC18 and gate row |
| 8 | Transient failure stops the worker | code | Own 30-second budget, skip, step-over; D13, AC54 |
| 9 | The 99% check hides lost minutes | arith | Per-minute check; FR28, D33, AC36 |
| 10 | Algorithm gives wrong answers | quoted | Ordered list, `new_version_not_seen`; FR21, AC30, AC32 |
| 11 | Flat 395 days breaks retention | code | Project's derived retention; FR40, D20, AC74 |
| 12 | F3 contract undefined | doc | Signatures, two reads, no blanket "measured"; D29. F3's own document changes at approval |
| 13 | F20 draws different markers | doc | `markers_in_window`; FR35, D30. One part rejected. F20's own document changes at approval |
| 14 | Cost gate comes last, fallback understated | quoted | Step 1a and 1b; the design lists what disappears |
| 15 | Batch upserts can deadlock | code | Sorted writers, including the sync (added on checking); AC51 |
| 16 | `LEAST` and `GREATEST` conflict with "measured beats told" | quoted | One start rule; FR42, AC57 |
| 17 | `TenantScopedRepository` missing in the scheduler | code | `releases/store.py` allowed, tuple; T1.11 |
| 18 | Foreign key and retention clocks disagree | code | `SET NULL`, arrival time; AC74 |
| 19 | Step 3 cannot run with the seed tool | code | T3.0 |
| 20 | Errors read has no cost estimate | code | Measured in T4.3, timeout words; FR51 |
| 21 | FR39 contradicts itself | quoted | `basis`; FR39 |

**Developer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Version numbers can be silently wrong | code | Caps stated truthfully, `__other__` label, Go tests; FR17, D14, AC18, AC67 |
| 2 | Algorithm's order is wrong | quoted | Ordered list, `''` and `__other__` excluded from previous; AC65 |
| 3 | Largest risk tested last; gate cannot fail | quoted | Step 1a, `measure.py`, baseline, 3 repeats |
| 4 | Body cap makes batches impossible | code, arith | As Architect 4; HMAC cost measured in T2.2 |
| 5 | Limiter cannot count a batch | code | `cost`, exact quota, `app.state`. On checking: a refused batch must charge nothing, since today's statement adds first (`limiter.py:169-186`) |
| 6 | "Last check received" has nowhere to live | quoted | `deploy_intake_state`; AC13 |
| 7 | Nothing defines who writes the start | quoted | FR42, AC57 |
| 8 | Return rule misfires, rollback compares wrong | quoted | Share-based rule; FR19, FR20, AC25, AC55. On checking, the new rule had two holes: a quiet service's swap never ended a period, and one stray request could return. Both are closed |
| 9 | One bad name stops a project's sync | code | Per-row skip; FR18, AC56 |
| 10 | Coverage flips falsely at 30 days | quoted | As of a moment, last report's age; AC73 |
| 11 | Structure test does not cover the package | code | T1.11, T5.2 |
| 12 | Step 3 and 4 lines cannot be run | code | T3.0, `--deploys N`, T1.5 modes |
| 13 | Retention can break the expiry sweep | code | Arrival time, `SET NULL`, `ix_deploy_events_created_at`; AC74 |
| 14 | Files and tasks missing | code | T2.3 names, full sealing path, T4.4, T4.1 |
| 15 | Sealing needs the id before insert | code | `uuidv7()` read first; T2.3 |
| 16 | Proofs that cannot stay unchanged | code | T5.1 re-pointed, T5.4 adds `partial`, AC6 |
| 17 | Spec contradictions, FRs with no AC | quoted | FR39, AC23, AC26, AC65, AC69, AC74 |
| 18 | Work that could be deleted | code | No window on `GET /deploys`, one coverage route, per-table predicates |
| 19 | F28 deletes code F22 is told to reuse | doc | Contradictions recorded; design's impact list |

**UX**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Default window gives false reassurance | quoted | FR22, `?finding=`, "Back to finding"; AC64 |
| 2 | Default `/releases` is usually empty | code | Overlap window, new state; AC27 |
| 3 | No phone layout | code | Phone rows and blocks; NFR, AC75 |
| 4 | Failures fall to a toast | code | Per-section `InlineFailure`, chart failure line; AC38, AC68 |
| 5 | Rollout chart cannot be read | code | Categorical mode, top four, labels, share table; FR26, T4.5 |
| 6 | Marks not legible | code | One dash, HTML strip, visible line; FR36, FR37, D49 |
| 7 | Hover link cannot be reached | code | Click to pin, one list per group; AC39 |
| 8 | Setup signal has no data | quoted | Times, polling, `role=status`, words, `UnsavedChanges` |
| 9 | Errors list hides the difference | code | Words in cell, panel always states it; FR38, D23, AC73 |
| 10 | Finding sentence can mislead | code | Wording table; FR32, AC40 |
| 11 | Opening sentence written for one method | quoted | Sentence table; AC66 |
| 12 | Times have no clock | code | Project's zone; NFR, AC75 |
| 13 | Long versions have no display rule | quoted | Shared formatter; AC75 |
| 14 | Colour undefined, threshold differs | code | FR24, D50 |
| 15 | No-permission paths incomplete | code | `useCan`, words, anchor; AC75 |
| 16 | Sidebar entry contradicts IA3 | doc | Contradiction recorded; D25, T5.7 |
| 17 | Screen-reader and refresh missing | quoted | `role=status`, `Tabs`, 60-second refetch, descriptions |

**Sales**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | "No CI work" cannot be defended | code, doc | Claim withdrawn below; FR41, AC48, T2.7 |
| 2 | "Every", "exact", "own server" fail | doc | Claim withdrawn; FR50; T1.7 reports `side_by_side` reach |
| 3 | "About the same" on a worse release | arith | Four readings; FR24, D17, AC33 (z is about 2.39) |
| 4 | "Same traffic" overclaims | quoted | Wording, warm-up; FR48, AC63 |
| 5 | "Will you tell me?" has no answer | quoted | D3 partly met, AC28 records F6, FR14 recipe, lag list below |
| 6 | One rollout, two releases | quoted | `version_seen`, one variable, FR46, error row |
| 7 | Environment mixing | code | BR11, FR45, FR14, FR41, error row |
| 8 | Self-hosted CI may not reach us; "five minutes" unmeasured | doc | FR14, error row, AC1, T2.10 |
| 9 | Silent release cap | arith | Evict, stated sentence; FR56, D36, AC60 |
| 10 | BR9 is untrue | code | BR9 rewritten with its table, the 35-day answer |
| 11 | First-day facts can be wrong | quoted | `already_running`; FR43, D47, AC58 |
| 12 | Largest step rests on unknowns | doc | Founder decision, with questions and what changes the plan |
| 13 | Rival lag list incomplete | doc | List below; T5.7, recipes fill `url`, D43 |
| 14 | Three buyer questions unanswered | doc | Demo answers below; absolute figures in T1.7; design's threat row; T6.2 |

**Marketing**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Competitor table misstates rivals | web | Rows corrected, Observe added, conclusions rewritten. One part rejected |
| 2 | Approved claim is false | code, doc | Replaced here and in `research.md`; "exact p95" banned |
| 3 | "No CI work" is untrue | web, code | Wording changed; Seen says "if they send it"; T2.7 |
| 4 | No true reason to choose F28 | web | Spec's *Positioning*, with the candidate story, marked unproven |
| 5 | D3's promise can go unannounced | quoted | D3, AC28, FR55, lag list. One part rejected |
| 6 | No launch step | doc | Step 6, Claims check gate |
| 7 | "Five minutes" is unmeasured | quoted | Marked a target; AC1, T2.10 |
| 8 | "Rule" and "impact" are the wrong words | code | Release marker, comparison; D44, AC37 |
| 9 | Rows too narrow | web | Grafana and Coroot corrected. One part rejected |

**Customer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Overlap-only comparison misleads forever (blocker) | quoted | Two blocks; FR47, D34, AC62 |
| 2 | CI and telemetry name versions differently | quoted | FR12, FR46, recipes, AC52 |
| 3 | Moving start rewrites history | quoted | As Architect 1; AC25 |
| 4 | Finding answers only for its own service | code | Project list, callees marked; FR52, D41, AC71 |
| 5 | The page carries no release | code, doc | FR44, D24, AC70. On checking, a grouped message cannot carry the line, which is now stated |
| 6 | Depends on remembering to look | quoted | `release_id`, version filter, FR55, D40 |
| 7 | Broken hookup looks healthy | quoted | Signed refusals recorded, last report's age; FR10, AC9, AC73 |
| 8 | CI may be unreachable; recipes could break deploys | doc | FR14, BR13, AC49 |
| 9 | "No CI work" overstated | code | FR14, FR41, AC48, current versions in settings. One part rejected |
| 10 | Cold starts read as `higher` | quoted | FR48, method words, `cold-start` mode, AC63 |
| 11 | Teams with little tracing | code | `no_request_traffic`, *Not served*; FR21, FR27 |
| 12 | Two environments in one project | code | FR45, D39, AC67. One part rejected |
| 13 | Service level hides one broken endpoint | quoted | FR49, arithmetic estimate. One part rejected |
| 14 | Connecting creates fake releases | quoted | `already_running`; AC58 |
| 15 | `metadata` is unmasked and widely shown | code | Masked, members only; FR9, FR54, AC53, AC76. One part rejected |
| 16 | MCP still leaks `source` and `url` | code | Explicit field list; D28, AC46 |
| 17 | "What's in it?" has no required answer | quoted | Recipes fill `url`, shown first; D42. One part rejected |

**Security**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | MCP removes only `metadata` | code | As Customer 16 |
| 2 | Sender text reachable by F4's ticket | code | `sender-details`, person only; FR54, D45, AC76. One part rejected |
| 3 | No way to remove what a stolen secret wrote | code | `webhook_secret_id`, delete routes; FR53, D37, AC61 |
| 4 | Release cap allows a lockout | arith | One cap, eviction order; FR56, AC60 |
| 5 | Flat retention outlives telemetry | code | As Architect 11 |
| 6 | Tenancy tests leave gaps | code | `require_readable_project`, AC26, deleted-project clause |
| 7 | Scheduler statements unenforced | code | T1.13, D48, AC77 |
| 8 | Browser key reaches F28 by other paths | code | `door` column, precondition; D38, AC69. Risk below |
| 9 | Webhook text skips masking | code | FR9, masking port, setup warning. One part rejected |
| 10 | F3 guard is a deny list | quoted | Exact allow list; AC45 |
| 11 | Name checks differ by door | code | One name check; FR9, AC12 |
| 12 | Hover box has no safeguard | code | React portal, direct test; AC43 |
| 13 | One audit row per deploy floods the log | code, arith | `deploy.batch_recorded`; AC8. Risk below |
| 14 | Recipes may leak the secret | quoted | FR14, AC49 |
| 15 | F22 would copy a known hole | doc | Contradictions recorded; impact list |

**QA**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Rollup fault can stop detection (blocker) | code | As Architect 8; AC54, T1.4 |
| 2 | Requirements with no AC (blocker) | quoted | AC48 to AC84 added. Checked in both directions, by script and by hand: every AC is cited by a plan task, and every FR is covered by an AC. See *Second review* below |
| 3 | Algorithm cannot produce the ACs | quoted | Ordered list; failure floor selects the method |
| 4 | Time comparison fires falsely | quoted | Only when every row has no version; AC30 |
| 5 | History rewritten, no AC | quoted | AC25 with the sequence |
| 6 | AC18 cannot be met | code | AC18 restated; two-drain and junk-burst tests |
| 7 | Test bed cannot produce the claims | code | T1.5 modes, T3.0 |
| 8 | AC42 tested in the wrong place | quoted | T1.12, T3.9 |
| 9 | Alert timing not gated | code | Gate row, AC72 |
| 10 | `synced_through` undefined | quoted | Rule stated; AC55, timed check T1.16 |
| 11 | ACs test what cannot be stored | quoted | `deploy_intake_state`; AC13 |
| 12 | Limit and repeats under-specified | code | FR7, FR13, AC10, AC14, AC51 |
| 13 | Text can stop the sync; `__other__` can be sent | code | AC12, AC56 |
| 14 | Scheduler Postgres untested | code | T1.13, AC77 |
| 15 | "Source" means two things | quoted | `basis` defined and used. One part rejected |
| 16 | Gate may not prove its claim | quoted | Baseline, repeats, spread, floor label, one stopping rule |
| 17 | What cannot be tested is unstated | quoted | AC49 contract test, AC2 API test, wider *Not provable here* |
| 18 | ACs too loose | quoted | AC37, AC34, AC23, AC41, AC40 |
| 19 | AC28 assigned too early | quoted | T1.14 computes it directly; AC66 asserts the sentence; gate first |

### Found while checking, beyond what was reported

- **The comparison's "about 360 requests" was mislabelled.** It holds near 0.2%, and near 3% it is about 5,200 (arithmetic).
- **F20's D14 has moved, and F3's and F20's documents cite this design's old line numbers.** Both are in the impact list.
- **`coverage` needs no ClickHouse,** since `releases` stores the times. F20 had assumed it did.
- **FR44's line had the wrong file.** The composer sees only `Subject.detail`. It was first placed in `alerts/outbox.py`, and the second review moved the read to `alerts/delivery.py` (D51).
- **FR41 assumed language pages teach a literal.** They do not mention `service.version` at all.

### Second review: 14 problems, each verified before the fix

A second pass reported 14 problems after the changes above. Each was read against the code and these documents first. All 14 held, and all 14 are fixed in the documents. Nothing was built or run, apart from the coverage script below and the reviewer's own reproduction of the collection error.

| # | Severity | Problem | Verified by | What changed |
|---|---|---|---|---|
| 1 | Blocker | FR46, FR49, FR50 and FR55 had no AC, while QA 2 read as fixed | Script, spec | AC78 to AC83, cited by T4.1, T4.5, T5.6. QA 2's row corrected. The full check also found FR24's colours untested, which AC84 now covers |
| 2 | Major | The summary could not be delivered | `models.py:1454-1458`, `outbox.py:272-287,708-719`, `messages.py:365-381` | `notifications.release_period_id`, design section 6, T5.6, D52 |
| 3 | Major | The opt-in had no route, storage or permission | Design, `projects/router.py:91`, `permissions.py:56` | `projects.release_summary_channel_id`, the `PUT` route, `POLICY_OF`, T5.6, AC83, D53 |
| 4 | Major | Three test filenames repeated, stopping collection | `pyproject.toml:103-112`, no `__init__.py` in those folders | `test_scheduler_postgres_tenancy_structure.py`, `test_platform_releases.py`, `test_release_demo_measure.py` |
| 5 | Major | `tools/release-demo` tests would never run | `pyproject.toml:107,112`, `justfile:205` | `pyproject.toml` in T1.5 |
| 6 | Major | AC42's promise conflicted with T3.8 | `detection/notify.py:52`, `delivery.py`, `jobs/registry.py:22` | The read moved to `delivery.py`, T1.12 is transitive, D51 |
| 7 | Major | "It came first" on the wrong rows | Design table, FR32 | Rows named in the design, FR32 and AC40 |
| 8 | Major | Rule U6 conflict unrecorded | `phase-2-foundation.md:204-205` | Contradiction recorded, mapping table in the design, both impact lists, D54 |
| 9 | Major | AC17 and FR16 conflicted with the F6 change | `services.go:142`, FR16, AC17 | End state stated, the F6 change's test named, D55 |
| 10 | Minor | AC66's "about the same" with 50 requests each cannot occur | Arithmetic: SE about 0.0199, 3·SE about 6 points. Equal sides need about 300 each | The row now reads "not clearly different" |
| 11 | Minor | FR44's read could abort the claim, and N reads could pile up | `delivery.py:53-56`, `outbox.py:534-544` | Own connection after commit, one read per batch, D51, AC70 |
| 12 | Minor | AC18 and AC1 could not fail | Spec, T2.10 | AC18 asserts the outcome. AC1 states what happens if nobody is found |
| 13 | Minor | Datadog "gates" had no source | `research.md` has no such row. That shows it is unsourced here, not that it is false | Deleted |
| 14 | Minor | Two signatures for `coverage_of` | Design | One signature, the pure one |

**Corrections to the reports themselves.** Problem 4 said no duplicate test name exists today. `test_logs.py`, `test_metrics.py` and `test_traces.py` do repeat, because of `tools/otel-demo/upstream`, which is outside `testpaths`, so the point stands for what the gate collects. Problem 2 suggested only a `_TAKEN` join and composer changes. On reading, the partial unique index would have dropped every summary after the first on a channel, and `_IN_ORDER` would have treated all of them as one subject. `colour` would also have shown a report in amber. All three are in the design.

**Found while fixing, not reported.** `tools/detection-measure/measure.py` already holds the module name `measure` on `pythonpath`, so a second `measure.py` would have imported the wrong file. The new tool's is `release_measure.py`. FR44's read also now names what it leaves out: a project finding has no service.

**FR-to-AC check.** A script over the *Acceptance criteria* section found 18 of the 56 FRs named by number. The other 38 were matched by reading, as follows. FR24 is named too, and is listed because of AC84. A match means an AC tests the requirement's main promise, not every clause in it.

| FRs | ACs | FRs | ACs |
|---|---|---|---|
| FR1 | AC2 | FR24 | AC33, AC84 |
| FR2 | AC4 | FR32, FR33 | AC40, AC75 |
| FR3 | AC3 | FR34 | AC41 |
| FR4 | AC5 | FR35 | AC38, AC45 |
| FR5 | AC6 | FR36, FR37 | AC39 |
| FR6 | AC8 | FR38 | AC73 |
| FR7 | AC10 | FR39 | AC45 |
| FR8 | AC11 | FR41 | AC48 |
| FR9 | AC12, AC53 | FR42 | AC57 |
| FR10 | AC9 | FR43 | AC58 |
| FR11 | AC13 | FR45 | AC67 |
| FR13 | AC14 | FR47 | AC62 |
| FR14 | AC49 | FR48 | AC63 |
| FR15, FR16 | AC15, AC17 | FR51 | AC68 |
| FR17 | AC18, AC67 | FR52 | AC71 |
| FR18 | AC24, AC56 | FR53 | AC61 |
| FR19 | AC25, AC55, AC59 | FR54 | AC76 |
| FR23 | AC31 | FR56 | AC60 |

**Still not re-reviewed.** These fixes were checked by their author and the script above, not by a separate reader.

### What remains as a risk after the reviews

- **Nothing was run.** Every limit is a guess, and every fix above is a written requirement.
- **No one re-reviewed the changes.** Scope grew by 16 requirements (FR41 to FR56) and 37 acceptance criteria (AC48 to AC84), including a stored reading, a summary notice and a new raw-table column. It is a much bigger feature than the first draft. D57 keeps D40 and FR52 (2026-11-02); the founder may still cut either at approval.
- **A version seen only in a span more than 5 minutes later than its settled minute is missed until it is counted again.**
- **Folding can understate a real version.** The screen says so. It cannot say which.
- **A stolen deploy secret can still plant a believable marker,** on a version never seen. F3 labels the basis, and removal exists, but only after someone notices.
- **Audit rows do not expire,** and single-item requests at the limit reach about 8.76 million a year (arithmetic).
- **F6's browser-door exposure stays until the separate change lands.** See below.
- **Period thresholds can misread blue-green flips and long canaries.** All guesses.
- **Other features' documents still disagree with this one** until changed at approval. F20 now agrees (`dashboards/design.md:967`); F3's T1.4 must call, not add, D56's reads.
- **A grouped alert message does not carry the release line.**
- **Whether anyone wants this is unknown.** No buyer was asked.

## Risks

**A risk found here that belongs to F6, and that the founder should hear about.** In plain terms: a website's public key is
in its page, so anyone can read it. From the code, nothing stops that key sending a span labelled as server traffic
(`receiver/internal/otlp` reads no span kind; `traceassembly/flatten.go:185` takes it from the payload; the door is read only
for billing, `usage/usage.go:281,291`). So anyone could push made-up request counts into `service_stats_1m`, and so into
detection, which could raise a false finding or teach a false normal. **Expected from reading; not run.** `alerts/payloads.py:204`
already treats names from a browser key as untrusted, and this is about counts. F28 excludes that door from its own table, and
its `time_before_after` and completeness check inherit the exposure until the change lands.
Recommendation: a separate change record before Stage 1 ships, and required before F28's Step 4. Approved by the founder
on 2026-11-02 (foundation, *Founder decisions*, 7), and F28 owns it (item 22). Not done here: this run
changes only F28's documents, and F6 is outside it.

**Assumptions not validated**

- Rollup cost: rows, disk, worker memory. Unmeasured; the gate in plan Step 1a. If it fails, the fallback is comparison by time
  with F6's readings. That also loses seen releases and the no-webhook route, as the design lists.
- How many customers set `service.version`, and what they put in it.
- All limits and thresholds marked *starting guess* in the spec.
- **The whole 16 GB server is not measured before deployment** (foundation item 9: no rented test server). Step 1a's per-container
  figures come from the Mac's Docker; the whole-server fit waits for Oracle after deployment. Oracle's free allowance is 2 Arm CPUs
  and 12 GB, below the 16 GB minimum, so that measurement needs a paid shape, which returns to the founder then.
- Whether people want a release screen at all rather than the fact on the finding. No interview data.
- Whether F6 opens a finding when a release fails 3% at 20% of traffic (0.76% mixed). AC28 records it.
- GitHub's own delivery payload was never confirmed, and that route is deferred.
- Competitor facts were read through a summarizing tool on 2026-11-02, and must be checked before any public claim. **We must
  not claim to be more accurate than anyone.**

**Known to lag rivals, plainly**

- Datadog: per-endpoint comparison, any two versions, automatic faulty-deployment detection.
- Sentry: suspect commits, commit and author lists, deploy emails.
- Coroot: an automatic post-rollout audit across restarts, CPU, memory and log errors, with a Slack notice.
- Commits appear as plain text with a link from CI, until F22.
- A release comparison opens no finding. Only F6 can, so the catalog's "regression surfaced as a finding" is only partly met.

**What may be said once built, and true**

"When your spans carry a build id in `service.version`, Nexovue compares old and new from counts taken before its own sampling,
and every comparison says how it was made. Request and error counts are exact for the spans Nexovue receives; p95 is an estimate
from latency buckets. If your deploy already passes a build id into `service.version`, releases appear with no new webhook."

Only in the future tense until shipped, and re-checked in plan Step 6. **The earlier line is withdrawn.**

**Do not say:** "every release is compared", "exact p95", "no CI work", "on your own server" to a hosted buyer, "no extra charge"
to anyone paying for their own disk, "automatic deploy detection", "more accurate than" anyone, or a rival's name in public.

**Demo answers, for when someone asks**

| Question | Answer |
|---|---|
| Will you tell me when a release goes bad? | Not by itself. F6 tells you if the service as a whole looks wrong. A release can be compared on its page, by your pipeline through the API, or in an optional summary. Whether F6 fires on a small rollout share is being measured |
| Do I need to change CI? | One step, or one change per service to pass a build id into `service.version`. Comparisons start from your next rollout |
| Will it compare my small service? | Not always. Low traffic reads "too little traffic" or "not clearly different" |
| Is old against new fair? | They are the same minutes. They may not get the same mix of requests, and new instances start cold, so p95 leaves out each version's first 5 minutes |
| I sample before sending | Counts are short by that factor, and nothing in the data shows it. The page says counts assume no sampling |
| How much more disk? | Not known yet. The cost report will give figures per million requests. A self-hoster pays for that disk |
| Can I import past deploys? | Up to 7 days back |
| Why 35 days on a 395-day plan? | Per-minute version counts are kept 35 days at most, as detection's are. The screen states your project's limit |
| Can a public browser key fake my numbers? | Not version numbers or a server release's errors. Totals in the time comparison can be, until the separate change lands, which is required before that page ships |
| My CI cannot reach a private install | Use a self-hosted runner, or the `service.version` route |

## Validation

**Nothing was run:** no test, query, request or measurement. The documents rest on reading the code on branch
`feature/anomaly-detection`, with uncommitted work present, and vendor pages read on 2026-11-02. "Expected" throughout means read,
not reproduced. Scripts confirmed that every acceptance criterion is cited by a plan task, that FR and AC numbers have no gaps, and which FRs the ACs name. The rest were matched by hand (*Second review*).

## Specification impact

These documents are the specification. Other documents affected at approval, none changed in this run. The design's
*Specification impact* has the detail.

- `phase-2-foundation.md`: sections 3 (rule U6, D54), 8, 9 and 12, and line 495.
- `feature-catalog.md:91,104,371-372`.
- F3: two reads, `release_comparison`, release lag, `basis`, fixture, line citations.
- F20: D14 moves to `markers_in_window`, and coverage to `coverage_of`.
- F22: its own header-id check and dedupe; the savepoint it cites is rewritten.
- F4: `sender-details` refuses its ticket; `list_deploys` has an explicit field list.
- F21: a new line in `_compose_finding`, and a new frozen kind.
- Console redesign: IA3.

## Documentation impact

None updated yet, since nothing is built. At build: `docs/product/setup/` (a new page, corrected platform pages, language pages
and onboarding), `docs/architecture/database.md`, `docs/features/integrations-deploy-events/spec.md`, and
`docs/self-hosting/sizing.md` if the gate shows growth. `000035`'s comment "nothing else reads this table" will go stale, and
`database.md` carries the correction.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- The scheduler Postgres tenancy test: corrected from "does not exist, F28 builds it" to "exists at `apps/api/tests/test_scheduler_postgres_tenancy_structure.py`, F28 extends it" (design, spec D48, plan C7 and T1.13). Read from the code.
- Bases: F28's table now uses F3's `verified`, `telemetry`, `telemetry_text`, and a seen version is `telemetry_text` everywhere (design, spec D54).
- The cap on `releases` now states F3's floor on `scheduler-light`, which F3 and F19 asked for (design).
- The finding page: F28's section is folded into F3's as its first row, and F5 extends it (design). F28's AC40 is proved through it.
- F22's webhook has no delivery table, so F28's ask for a dedupe is withdrawn; `signatures.py` stays a plain verifier (design).
- The browser-door change is now one record shared with F3 and F19 (plan, foundation section 9).

## Last updated

2026-11-09: Step 2 committed to `feature/phase-2`; the founder's answer on failed and rolled-back reports built into it.

## Founder answer, 2026-11-09

**Does a report of a `failed` or `rolled_back` deploy open a period?** The design's
step 6 opened one for a version with none, whatever its state, and that was built first; AC57 says only that
`rolled_back` on a *seen* version sets its state. So a version first reported as `failed`, never seen, gets a
reported period, appears on `/releases` as starting then, and on a service with no version in traffic ends the
period before it. Options: keep it (as built: the sender said it was deployed, and the release page shows the
state); or open periods only for `started` and `finished`, keeping the release and its state with no period, so a
failed deploy never reads as serving. **Recommendation: the second**, since a failed deploy did not replace what was
running; it is a small change in `releases/service.place_periods` and its live tests. **Founder answer, 2026-11-09:
started and finished only.** Built: such a report is stored and its state shown, but opens no period, ends none and
brings no version back (design, *From the webhook*, step 6; tests under *Step 2, built 2026-11-08*).

Left as they are, found in the self-review, none blocking (each open to overrule):

- **`first_reported_at` still counts a failed report.** It is the release's reported fact, so it stays, but two
  readers lean on it: the sync's `already_running` (`_first_on_service`: a version reported, even as failed, before
  its first counted minute is not "already running"), and the comparison's time-based fallback on a service with no
  version in traffic (`choose_method`'s `reported_at`: a version reported failed at 10:00, then finished at 11:00,
  is compared from 10:00 while its period starts at 11:00). Neither opens or ends a period. The second is the one
  worth changing (compare from the period's start); not done here, since it is the comparison's rule, not the
  founder's question.
- **A `started` report then a `failed` one for the same version** keeps the period `started` opened: the answer
  says which reports open periods, not that a later failure closes one.
- **`OPENS_A_PERIOD` names the states as plain strings** beside `deploys/schemas.DeployState`; a new state added
  there would open no period until listed here.

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in this feature's documents was checked against the code, the foundation and fresh reading.

- **Settled by the foundation, removed from the open lists:** customer conversations before Step 1b (item 8: one round,
  before Step 1b); the browser-door change before Step 4 (item 7, so the "accept the exposure" path is withdrawn, D58); and
  D1's difference from the foundation, which no longer exists (`phase-2-foundation.md:547`).
- **D56, decided:** F28 builds the two scheduler reads F3 found missing, `release_before` and `releases_near`, in T3.8. F3 calls them.
- **D57, decided:** the opt-in release summary and the project release list stay. Coroot and Sentry both notify on a release,
  and F3 already relies on the list. The summary is built last, so it can still be dropped cheaply.
- **D58, decided:** Step 4 waits for the browser-door change.
- **Still the founder's, only if it happens:** if the Step 1a cost gate fails after one tightening, whether to pay the extra disk
  or drop version comparison and its "no new webhook" promise. Recommendation recorded in `spec.md`.

**Review, 2026-11-02.** A second pass checked each answer against the code, the foundation and the cited pages.

- **Held:** the customer conversations (foundation item 8), D1 (`phase-2-foundation.md:547` reads as stated), D56 (no
  `nexovue_scheduler/releases/` exists yet, and F3's `design.md:300` and `plan.md:125` ask for exactly these two reads), and
  D58 (foundation section 9 says F28's Step 4 waits). D57 stays the agent's call: it keeps written scope and changes nothing sold.
- **Fixed, design:** `releases_near` had no `service` argument, so it could not put "the same service first" (FR52). Added,
  with a note that marking callees reads `service_edges_1m` and is the caller's part.
- **Fixed, spec D57:** Sentry's deploy notice goes to the people who committed to that release, not to everyone, and Coroot's
  goes only on notable changes. Both pages were re-read directly on 2026-11-02.
- **Fixed, the conditional founder question:** the old recommendation said "after one more tightening", but the plan allows
  one, and it used the 2x disk bound, which is one of the checks that would have failed. It also called "no new webhook" the
  buyer's main promise, while the spec calls it table stakes. The recommendation now splits by which check fails: a cost
  check alone means keep and pay if it fits the 16 GB server (`docs/self-hosting/sizing.md`); a correctness check means time
  comparison only. It stays the founder's, and only if the gate fails.
- **Still outside F28:** F3's `plan.md:125` (T1.4) must say it calls D56's reads instead of adding them.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** F28 owns the shared browser-door change (item 22). The later gate after Step 1a (item 24): if only a
  cost check fails, keep version comparison and pay, if it fits the 16 GB server; if a correctness check fails, time comparison only.
- **Changed, A: no rented test server.** Long runs and server measurements happen on Oracle Cloud after Phase 2 is deployed. For F28,
  Step 1a still runs as planned, on the Mac's Docker: its thresholds are shares and per-container ceilings. **What now waits for Oracle:**
  the whole-server fit on 16 GB, so `sizing.md`'s new figures are labelled "measured on a development machine" until then.
- **Changed, B: no paid model calls.** F28 calls no model, so nothing changes here.
- **Open decisions:** none for the founder. The Oracle shape to measure on comes back at deployment (foundation, *What A and B change*).

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 3 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.

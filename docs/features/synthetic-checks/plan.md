# Plan: Synthetic checks (F18)

> **Status:** Approved 2026-09-17 (Q1 answered by approving its proposal) · Implemented 2026-09-17, uncommitted,
> awaiting the founder's review — see *Outcome*
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order; tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited.**

## Preconditions

- Specification approved 2026-09-17 ([`spec.md`](spec.md)).
- Design approved 2026-09-17 ([`design.md`](design.md)).
- **Q1 answered before Step 4 starts.** Steps 1–3 do not depend on it.

## Current system

Read from the code on 2026-09-17. **Five places disagree with the approved design** — marked ⚠ and carried into
*Questions*.

### Control plane

| What | Where |
|---|---|
| CRUD routes; writes gated `MayAdminister`, list uses `CurrentIdentity`; **no `GET /checks/{id}`** | `apps/api/src/nexovue_api/checks/router.py:27-71` |
| Create proves the project, enforces the per-project limit, validates target and step URLs, audits in the same transaction | `checks/service.py:43-80` |
| Update re-validates target and config on every edit | `checks/service.py:83-109` |
| `CheckType = Literal["http", "browser"]`; `MAX_STEPS_PER_FLOW = 20`; `MAX_CHECKS_PER_PROJECT = 100`; interval 60–86,400 s; `config: dict[str, Any]`, untyped | `checks/schemas.py:14-78` |
| Every query binds `org_id` in the statement | `checks/queries.py:20-72` |
| Tenant erasure deletes one object prefix — source maps — so check files would survive it | `organizations/service.py:222-231` |
| Target must be public `http`/`https`, ≤ 2,048 chars, not an internal hostname, not a private literal address — explicitly *not* the security boundary | `checks/targets.py:50-69, 103-126` |
| Browser-only step URL validation | `checks/targets.py:72-100` |
| ⚠ **`target` is its own `NOT NULL` column.** The design puts the URL in `config` and does not mention it; a heartbeat has no target, and a TCP or DNS target is not a URL `validate_target` accepts | `data/postgres/models.py:391` |
| No `name` or `updated_at` on checks | `data/postgres/models.py:377-400` |
| Audit functions exist for create, update, delete | `audit/service.py:421, 434, 446` |
| Latest migration | `migrations/versions/0026_the_charged_amount_is_recorded.py` |
| ⚠ **`CredentialBox` is not reusable as-is.** Its associated data is hard-wired to `totp:{owner_id}`, and its missing-key message speaks of two-factor authentication; one key setting, `credential_encryption_key`. ADR-010 assumed drop-in reuse | `auth/encryption.py` (`_associated_data`, `_KEY_MISCONFIGURED`), `settings.py:264` |
| Rate tiers: auth, telemetry, default, deploy-webhook; chosen by longest path prefix | `ratelimit/policy.py:51-87, 169-179` |
| Unauthenticated mutating routes need a named, reasoned entry | `tests/test_authorization_coverage.py:39-43` |
| Any router reading ClickHouse must be on `TELEMETRY_TIER` | `tests/test_ratelimit_coverage.py:71` |
| Synthetics reads: `check_type: Literal["http", "browser"]`; four endpoints | `synthetics/router.py:44-141` |
| Metric names and JSON-path constants defined once | `synthetics/queries.py:30-41` |

### Scheduler

| What | Where |
|---|---|
| ⚠ **A recorded rule the design breaks:** *"Beat hands the whole check definition over rather than an id, so this worker never touches Postgres … a probe that cannot run because Postgres is briefly busy is a blind minute for that customer."* The design's tasks carry ids, and ADR-010 has workers read sealed secrets from Postgres on every run | `apps/scheduler/src/nexovue_scheduler/probes/tasks.py:3-10` |
| `run_check` probes, logs, publishes; retries only publishing | `probes/tasks.py:54-99` |
| Only `http` has a prober; an unknown type raises | `probes/tasks.py:186-196` |
| Browser task and its step budget | `probes/tasks.py:102-168, 199-210` |
| **`GET` only**: no method, headers or body; `up = status < 400`; body capped at 1 MiB and discarded | `probes/http_check.py:43, 48-98, 101-121` |
| Every redirect hop judged by the connect guard | `probes/http_check.py:104-106` |
| `ProbeResult`; `error` deliberately **not** published — unbounded text would become a high-cardinality label | `probes/result.py:40-63` |
| Address guard: `is_publicly_routable`, `may_connect_to`, `ConnectGuard` | `probes/addresses.py:32, 67, 119` |
| **HTTP checks publish metrics only** — no evidence reaches logs today | `publish.py:142, 180-231` |
| Metric names; `PROBER_SERVICE_NAME` | `publish.py:39, 56-59` |
| Beat: one entry per check, task chosen by type, whole definition in kwargs | `beat.py:227-256` |
| Poll entries already pass `options={"queue": …}` | `beat.py:294` |
| Routes: `probes.browser.*` → browser, `probes.*` → light; `task_acks_late`; no result backend | `celery_app.py:27, 39, 54-58` |
| Scheduler reads checks by raw SQL: `id, org_id, project_id, type, target, interval_seconds, config` | `checks.py:28-35, 65-88` |
| `QUEUE_LIGHT`, `QUEUE_BROWSER`, browser time limits, topics, `prober_region = "local"`, **unused** `synthetics_bucket` and `synthetics_endpoint_url`, the private-target development switch | `settings.py:26-27, 56-87, 147-160` |
| **No test sends a real HTTP request.** `test_http_check.py` checks only timing arithmetic | `tests/test_http_check.py:60-102` |
| `loopback_site.py` records only `GET` paths; its fixture is used only by browser tests | `tests/loopback_site.py:22-55`, `tests/conftest.py:9-12` |
| ⚠ **The design's removal list missed** `test_browser_steps.py`, `proxy_client.py` and the browser-only fixture | `apps/scheduler/tests/` |

### Contracts, deployment, frontend, end to end

| What | Where |
|---|---|
| Fixture generators and map; regenerated with `uv run --no-project --with protobuf --with opentelemetry-proto python packages/contracts/tools/build_fixtures.py` | `packages/contracts/tools/build_fixtures.py:8-9, 410, 494, 972-973` |
| Light worker `--queues light`; browser worker `--queues browser` | `deploy/compose/docker-compose.yaml:371, 398` |
| `CheckType = "http" \| "browser"` | `apps/web/src/features/synthetics/api.ts:19` |
| Kind filter lists HTTP and Browser | `features/synthetics/filters.ts:11-28` |
| `/synthetics` route | `app/router.tsx:201-203` |
| Detail routes excused from the sidebar | `app/routes/route-manifest.test.ts:75-79` |
| Forms that must warn on unsaved changes | `shared/components/unsaved-changes-coverage.test.ts:35` |
| Project settings sections pattern | `features/projects/ProjectSettings.tsx:60-372` |
| **No tabs component, no code editor dependency** | `shared/components/`, `apps/web/package.json` |
| E2E creates `type: "http"` through the API | `tools/e2e/tests/synthetics.spec.ts:142-146` |

**The Go workers need no change.** The metric and log pipelines do not inspect the door and store resource
attributes as JSON.

## Change required

The approved [`spec.md`](spec.md) and [`design.md`](design.md). Not restated here.

## Impact

| Area | What changes |
|---|---|
| Files | 98 tasks across API, scheduler, web, contracts, compose and docs; ~20 files deleted with browser checks |
| Services | API (checks, imports, files, heartbeats, secrets, variables, maintenance, run now, results); scheduler (all probes, state, beat, routing); web (synthetics feature, project settings, three shared components) |
| Schema / migrations | Six Postgres migrations, `0027`–`0032`; no ClickHouse schema change |
| UI | Three new routes; `Tabs`, `EditableRows`, `CodeEditor` shared components; list, new-check, import and check screens; two project-settings sections |
| Tests | New unit and live tests per step; three contract fixtures changed; e2e rewritten; three structural tests extended |

## Reuse

`targets.validate_target` · `ConnectGuard` and `may_connect_to` · the phase-timing and certificate code in
`http_check.py` · `publish.wrap_envelope` and `prober_resource` · the Go metric and log pipelines, unchanged ·
the duplicate-collapsing statements in `synthetics/queries.py` · `data/objectstore/store.py` `put`, `delete` and
`delete_prefix` · the streamed byte cap in `deploys/router.py:_bounded_body` · `CredentialBox`, once generalised ·
`audit.record_check_*` · beat's `_unchanged` and leader election · `loopback_site.py`, extended rather than deleted ·
`FilterBar`, `EmptyState`, `InlineFailure`, `UnsavedChanges`, `SkeletonRows`, `RunStrip`, `Sparkline`,
`describeFormProblem`, `SectionHeading`.

## Gaps

`Tabs`, `EditableRows` and `CodeEditor` · a typed configuration per kind · assertions · multistep and extraction ·
substitution · redaction · state and confirmation · per-location queues · run now · secrets and variables · files in
requests · three import parsers and a guarded YAML loader · heartbeat, TCP and DNS probes · maintenance windows · an
evidence read over logs.

## Steps and tasks

### Step 1 — Browser checks are gone

*Judgeable:* `just check` passes; a repository search finds no browser probe outside `tools/e2e/`; the development
stack starts without `scheduler-browser`.

- [x] **T1** — delete `probes/browser.py`, `probes/browser_proxy.py` · proved by the suite and T12
- [x] **T2** — `probes/tasks.py`: remove `run_browser_flow`, `_step_budget`, `BROWSER_TASK` · proved by `test_beat_schedule.py`
- [x] **T3** — `beat.py`: remove the browser branch · proved by `test_beat_schedule.py`
- [x] **T4** — `celery_app.py` route and `settings.py` `QUEUE_BROWSER` and browser limits removed · proved by `test_scheduler_settings.py`
- [x] **T5** — delete `test_browser_flow.py`, `test_browser_proxy.py`, `test_browser_envelope.py`, `test_browser_egress_live.py`, `test_browser_steps.py`, `proxy_client.py`; the browser case in `test_beat_schedule.py`; the now-unused `loopback_site` fixture stays for T18 · proved by the suite
- [x] **T6** — `apps/scheduler/pyproject.toml` drops `playwright`; root `pyproject.toml` drops the `playwright.*` mypy override · proved by `uv lock --check` and `mypy`
- [x] **T7** — delete `apps/scheduler/Dockerfile.browser`; remove `scheduler-browser` from `docker-compose.yaml`, `docker-compose.app.yaml`, `docker-compose.onprem.yaml` · proved by `docker compose config -q` for each
- [x] **T8** — contracts: remove `door2_synthetic_browser` and its map entry, `fixtures/door2-synthetic-browser.bin`, and its cases in `packages/contracts/roundtrip_test.go` and `packages/contracts/tests/test_envelope_roundtrip.py` · proved by both round-trip tests
- [x] **T9** — API: `browser` out of `CheckType`, `synthetics/router.py`, `synthetics/service.py`; delete `validate_step_urls` and `MAX_STEPS_PER_FLOW` · proved by `test_checks.py`, `test_synthetics.py`
- [x] **T10** — web: `browser` out of `api.ts` and `filters.ts` · proved by `pnpm run typecheck`
- [x] **T11** — docs: `architecture/current-system-reference.md`, `architecture/integrations.md`, `architecture/backend.md:9`, `product/vision.md`, `product/feature-catalog.md`, and blockers 8 and 10 in `operations/production-readiness.md` · proved by T12
- [x] **T12** — new `apps/scheduler/tests/test_no_browser_probe.py`: searches API, scheduler, web, contracts and deploy for the removed names, excluding `tools/e2e/` · proves AC31

### Step 2 — Walking skeleton: an API check built in the console, run, and explained

*Judgeable:* in the console, create a `POST` check with a JSON body and a JSON-path assertion that fails. Within 90
seconds the check page shows it down, with the request that was sent and the expected and actual values.

- [x] **T13** — `migrations/versions/0027_checks_become_kinds.py`: delete browser rows; rename `http`→`api`; `CHECK (type IN ('api','multistep','heartbeat','tcp','dns'))`; `target` nullable; add `name`, `updated_at` · proved by `test_checks_live.py` and upgrade/downgrade
- [x] **T14** — `data/postgres/models.py` `Check` fields · proved by `mypy`
- [x] **T15** — new `checks/config.py`: discriminated union with `ApiCheckConfig` — RFC 9110 method token, URL, query, headers with carriage returns and line feeds refused, body (none, text, JSON, XML, form), at least one assertion, redirects, timeout; errors as `errors[{field, reason}]` · proved by new `tests/test_check_config.py` (AC25)
- [x] **T16** — `checks/schemas.py` and `checks/service.py`: typed requests; `target` derived and validated per kind · proved by `test_checks.py`, `test_checks_live.py`
- [x] **T17** — `checks/router.py`: `GET /checks/{check_id}` under `CurrentIdentity` · proved by `test_checks_live.py`, `test_authorization_coverage.py`
- [x] **T18** — `tests/loopback_site.py` → `tests/recording_site.py`: records method, path, headers and body; serves chosen statuses, JSON and redirects; fixture renamed in `conftest.py` · proved by T20
- [x] **T19** — `probes/result.py`: outcome, request summary, response status, headers and body with a truncation flag, assertion results, run id · proved by `test_http_check.py`
- [x] **T20** — `probes/http_check.py`: any method via `CUSTOMREQUEST`, headers, query, text, JSON, XML and form bodies, response capture; the redirect guard kept on every hop · proved by new `tests/test_api_check_live.py` (AC1, AC5)
- [x] **T21** — new `probes/assertions.py`, JSON paths through `python-jsonpath` strict mode; add the dependency to `apps/scheduler/pyproject.toml` · proved by new `tests/test_assertions.py` (AC3)
- [x] **T22** — `probes/tasks.py` and `beat.py`: `api` dispatch; the definition stays in the task · proved by new `tests/test_run_check.py`, `test_beat_schedule.py`
- [x] **T23** — `publish.py`: `synthetic.check.outcome` and `synthetic.check.run.id` on metrics; a new evidence log envelope · proved by `test_publish.py`
- [x] **T24** — contracts: regenerate `door2-synthetic-http.bin` as `door2-synthetic-api.bin`; add `door2-synthetic-api-evidence.bin`; update both round-trip tests · proved by them
- [x] **T25** — `synthetics/queries.py`: `CHECK_RUN_EVIDENCE_SQL` over `logs`, a module constant with the tenant predicate · proved by `test_synthetics.py`
- [x] **T26** — `synthetics/service.py`, `schemas.py`, `router.py`: `GET /synthetics/checks/{check_id}/runs/{run_id}` · proved by `test_synthetics_live.py`, `test_ratelimit_coverage.py`
- [x] **T27** — new `shared/components/Tabs.tsx` to the WAI-ARIA tabs pattern · proved by `Tabs.test.ts`
- [x] **T28** — new `shared/components/EditableRows.tsx` · proved by `EditableRows.test.ts`
- [x] **T29** — new `shared/components/CodeEditor.tsx`, a thin wrapper over `@codemirror/view`, `@codemirror/state`, `@codemirror/lang-json`, `@codemirror/lang-xml`; add them to `apps/web/package.json` · proved by `CodeEditor.test.ts` and `pnpm build`
- [x] **T30** — new `features/synthetics/request-draft.ts`: draft ↔ config, mirrored validation, error-field mapping · proved by `request-draft.test.ts`
- [x] **T31** — new `RequestEditor.tsx`, `BodyEditor.tsx`, `AssertionRows.tsx` · proved by `NewCheckScreen.test.ts`
- [x] **T32** — new `NewCheckScreen.tsx`; `useCreateCheck` in `api.ts` · proved by `NewCheckScreen.test.ts`
- [x] **T33** — new `CheckDetailScreen.tsx` with runs and evidence; `useCheck`, `useRunEvidence` · proved by `CheckDetailScreen.test.ts`
- [x] **T34** — `app/router.tsx` routes; `route-manifest.test.ts` and `unsaved-changes-coverage.test.ts` lists · proved by both
- [x] **T35** — `SyntheticsScreen.tsx`: New check action, empty state, rows linking to the check page; `filters.ts` kinds · proved by new `SyntheticsScreen.test.ts`
- [x] **T36** — `tools/e2e/tests/synthetics.spec.ts`: `type: "api"`; a new test creating a failing API check through the UI and reading its evidence · proved by the e2e

### Step 3 — Run now

*Judgeable:* on an unsaved check, press Run now and see the full request, response and assertion results; the list
records no run.

- [x] **T37** — `apps/api/pyproject.toml` adds `celery`, used only to send by name · proved by `uv lock --check`
- [x] **T38** — new `checks/run_now.py`: `send_task` onto `light.<location>`, request id, Redis result read · proved by new `tests/test_run_now.py`
- [x] **T39** — `checks/router.py`: `POST /checks/run`, `GET /checks/runs/{request_id}`; a run-now tier in `ratelimit/policy.py` · proved by `test_run_now.py`, `test_authorization_coverage.py`
- [x] **T40** — `probes/tasks.py`: `run_now` skips publishing and state and writes the result to Redis with an expiry · proved by `test_run_check.py` (AC24)
- [x] **T41** — `apps/api/tests/test_cross_service_contracts.py`: task name and argument names agree across API and scheduler · proved by it
- [x] **T42** — new `RunNowPanel.tsx`; `useRunNow` polling · proved by `RunNowPanel.test.ts`

### Step 4 — Locations, confirmation and state *(needs Q1)*

*Judgeable:* a single failure followed by a successful retry leaves the state unchanged; a confirmed failure changes
it and appears on the timeline; with one location, the retry comes from that location.

- [x] **T43** — `0028_check_state.py`: `check_states`, `check_state_changes`; `checks.locations`, `degraded_ms`, `failing_ms`, `retries` · proved by `test_postgres_tenancy_structure.py`, `test_checks_live.py`
- [x] **T44** — `models.py` `CheckState`, `CheckStateChange` · proved by `mypy`
- [x] **T45** — `settings.py` and `celery_app.py`: per-location queue names; **the `probes.*` route removed so every dispatch names its queue explicitly** · proved by `test_scheduler_settings.py`
- [x] **T46** — `beat.py`: one entry per check per location, `options={"queue": …, "expires": interval}`, a stable start offset from the check id · proved by `test_beat_schedule.py`
- [x] **T47** — compose files: light workers consume `light.${PROBER_REGION}` · proved by `docker compose config -q`
- [x] **T48** — new `probes/state.py`: attempts, thresholds and transitions, pure · proved by new `tests/test_state.py` (AC19–AC22)
- [x] **T49** — `probes/tasks.py`: retry to another location or the same one; publish every attempt; **then** record state, per Q1 · proved by `test_run_check.py`
- [x] **T50** — `publish.py`: `attempt`, `final`, `location` · proved by `test_publish.py`; regenerate fixtures
- [x] **T51** — `synthetics/queries.py`: final attempts only; misconfigured runs outside uptime · proved by `test_synthetics.py`, `test_synthetics_live.py` (AC22, AC30)
- [x] **T52** — checks API returns state, since and state changes · proved by `test_checks_live.py`
- [x] **T53** — new `CheckSettings.tsx`; the check page's state, timeline and retry marks · proved by `CheckDetailScreen.test.ts`

### Step 5 — Secrets, variables, auth helpers and multistep *(needs Q1)*

*Judgeable:* a multistep check logs in with a token held as a secret and uses an order id extracted from the first
response in the second; a marker stored as a secret appears in no output.

- [x] **T54** — `auth/encryption.py`: associated data and the missing-key message take a purpose; TOTP ciphertext still opens · proved by the existing encryption tests plus a new check-secret case
- [x] **T55** — `settings.py` in API and scheduler: `check_secret_key` · proved by settings tests
- [x] **T56** — `0029_check_secrets_and_variables.py` · proved by `test_postgres_tenancy_structure.py`
- [x] **T57** — new `checks/secrets.py`, write-only · proved by new `tests/test_check_secrets_live.py`
- [x] **T58** — new `checks/variables.py` · proved by new `tests/test_check_variables_live.py`
- [x] **T59** — new `probes/substitution.py`: `{{name}}`, `{{secret.NAME}}`, unknown → misconfigured · proved by new `tests/test_substitution.py`
- [x] **T60** — new `probes/redaction.py` with a minimum secret length · proved by new `tests/test_redaction.py`
- [x] **T61** — secret delivery to the worker, as Q1 decides · proved by `test_run_check.py` (AC18)
- [x] **T62** — `http_check.py` auth helpers · proved by `test_api_check_live.py`
- [x] **T63** — new `probes/multistep.py` and `MultistepCheckConfig` · proved by new `tests/test_multistep.py` (AC4)
- [x] **T64** — new `tests/test_secret_never_leaks_live.py`, searching every output for a stored marker · proves AC17
- [x] **T65** — new `features/projects/CheckSecretsSection.tsx` and `CheckVariablesSection.tsx` in project settings; `GUARDED` list · proved by their tests
- [x] **T66** — new `AuthHelper.tsx`, `StepList.tsx`, `ExtractionRows.tsx` · proved by their tests

### Step 6 — Files in requests

*Judgeable:* a check uploading a 2 MB file sends it byte-for-byte; a fourth file, or one over 3 MB, is refused.

- [x] **T67** — `0030_check_files.py` · proved by `test_postgres_tenancy_structure.py`
- [x] **T68** — new `checks/files.py`: streamed upload with a 3 MB cap, `synthetics/{org_id}/files/{file_id}`, delete; an upload tier · proved by new `tests/test_check_files_live.py` (AC6)
- [x] **T69** — `organizations/service.py:_erase_objects` erases only source maps today (`sourcemaps/service.py:48`); it must also erase `synthetics/{org_id}/` · proved by the organization-erasure tests
- [x] **T70** — scheduler reads files with boto3 into a bounded cache, using the existing `synthetics_bucket` setting · proved by new `tests/test_check_file_cache.py`
- [x] **T71** — `http_check.py` multipart and binary bodies from memory · proved by `test_api_check_live.py` (AC2)
- [x] **T72** — sweep of unattached files after 24 hours, with its beat entry · proved by new `tests/test_file_sweep.py`
- [x] **T73** — new `FileAttach.tsx`; `BodyEditor.tsx` multipart and binary · proved by their tests

### Step 7 — Imports

*Judgeable:* a pasted cURL command previews with its token as a secret; an OpenAPI 3.2, a Swagger 2.0 and a Postman
v2.1 file each preview their items and what was skipped; Create makes the checks.

- [x] **T74** — `apps/api/pyproject.toml` adds `pyyaml` and `openapi-spec-validator` · proved by `uv lock --check`
- [x] **T75** — new `checks/imports/plan.py`: the preview shape · proved by new `tests/test_import_plan.py`
- [x] **T76** — new `checks/imports/curl.py` on `shlex` with an explicit option table · proved by new `tests/test_import_curl.py` (AC7, AC8, EC5)
- [x] **T77** — new `checks/imports/yaml_safe.py`: safe loader, aliases refused on the event stream, size cap · proved by new `tests/test_import_yaml.py`
- [x] **T78** — new `checks/imports/openapi.py`: remote `$ref` refused first, then validation, operations, servers, parameters, security, success status · proved by new `tests/test_import_openapi.py` (AC9, AC10)
- [x] **T79** — new `checks/imports/postman.py` · proved by new `tests/test_import_postman.py` (AC11, AC12, AC13)
- [x] **T80** — new `tests/test_import_is_offline.py`: the import package imports no HTTP client or socket · proves AC10 structurally
- [x] **T81** — new `checks/imports/router.py`: preview, and confirm in one transaction; an import tier · proved by new `tests/test_check_imports_live.py`
- [x] **T82** — new `ImportScreen.tsx`, `ImportPreview.tsx`; `/synthetics/import`; manifest and `GUARDED` lists · proved by their tests

### Step 8 — Heartbeat, TCP and DNS

*Judgeable:* a heartbeat nobody calls goes down after its period and grace, and up on the next call; a TCP check on a
closed port and a DNS check with the wrong answer both go down.

- [x] **T83** — `0031_heartbeats.py`: `heartbeat_token_hash`, `last_ping_at` · proved by `test_postgres_tenancy_structure.py`
- [x] **T84** — new `heartbeats/router.py`: `GET|POST /heartbeat/{token}`, hashed lookup, `EXEMPT` entry with its reason, its own tier · proved by new `tests/test_heartbeat_live.py`, `test_authorization_coverage.py`
- [x] **T85** — new `probes/heartbeat.py` sweep, publishing the same metrics · proved by new `tests/test_heartbeat_sweep.py` (AC14)
- [x] **T86** — new `probes/tcp_check.py` through the address guard · proved by new `tests/test_tcp_check.py` (AC15)
- [x] **T87** — `apps/scheduler/pyproject.toml` adds `dnspython`; new `probes/dns_check.py` with the resolver guarded · proved by new `tests/test_dns_check.py` (AC16)
- [x] **T88** — `checks/config.py` `HeartbeatCheckConfig`, `TcpCheckConfig`, `DnsCheckConfig` · proved by `test_check_config.py`
- [x] **T89** — heartbeat, TCP and DNS forms; heartbeat URL shown once, with rotate · proved by `NewCheckScreen.test.ts`

### Step 9 — Management, maintenance and the whole list

*Judgeable:* every kind can be edited, paused, resumed and deleted in the console; a maintenance window keeps a failing
check's state unchanged; the list shows every kind with state, uptime and p95, and the filter returns each kind.

- [x] **T90** — `0032_maintenance_windows.py` · proved by `test_postgres_tenancy_structure.py`
- [x] **T91** — new `maintenance/router.py` and service · proved by new `tests/test_maintenance_live.py`
- [x] **T92** — `probes/state.py` consults active windows · proved by `test_state.py` (AC23)
- [x] **T93** — `checks/service.py`: `updated_at` on edit; a deletion preview counting runs and files · proved by `test_checks_live.py` (AC27)
- [x] **T94** — `synthetics/queries.py` and service: every kind, per location · proved by `test_synthetics.py`, `test_synthetics_live.py` (AC29)
- [x] **T95** — list, check-page settings, pause, resume, delete confirmation, the four empty reasons; new `features/projects/MaintenanceWindowsSection.tsx` · proved by `CheckDetailScreen.test.ts`, `SyntheticsScreen.test.ts` (AC28)

### Step 10 — End to end and records

*Judgeable:* `just check` and the e2e suite pass, with counts read; documentation describes what was built.

- [x] **T96** — `tools/e2e/tests/synthetics.spec.ts`: create, edit, pause, resume and delete for every kind; a cURL import; Run now · proves AC26
- [x] **T97** — `docs/architecture/current-system-reference.md` synthetics section; `feature-catalog.md` F18 row; spec and design status · proved by review
- [x] **T98** — `/review-diff`; design QA of each screen against `design.md`; `status.md` and **Outcome** · proved by their records

## New risks

- **The worker-never-reads-Postgres rule** (Q1). Per-run state writes and secret reads put the control plane on the
  measurement path the rule was written to keep it off.
- **Evidence volume.** Capturing response bodies up to the probe's 1 MiB cap on every run of every check, into logs,
  could reach about 1.4 GB a day for one check at the 60-second floor. See Q3.
- **Two envelopes per run** — metrics and evidence logs — doubles the Kafka messages checks produce.
- **Library versions disagree between sources.** `python-jsonpath` is reported as both 1.3.1 and 2.0.x; strict mode
  arrived in 2.0. T21 pins at install time and fails loudly if strict mode is absent. `dnspython` is reported as both
  2.8 and 2.9.
- **`@uiw/react-codemirror` does not state React 19 support**, which is why T29 wraps CodeMirror directly.
- **Celery's precedence between an explicit queue and `task_routes`** is described ambiguously in its documentation.
  T45 removes the question by routing every dispatch explicitly.
- **A very large uncommitted working tree.** Under the founder's rule nothing is committed until F18 is complete: ten
  steps and roughly a hundred files in one review. Each step's end is recorded in `status.md` so it stays reviewable.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| **Q1** | `probes/tasks.py:3-10` records that workers never read Postgres, so a busy database never blinds a measurement. The design breaks it twice: tasks carrying ids, and ADR-010's per-run secret reads; state writes add a third. **Proposed:** keep the whole definition in the task; carry each secret **sealed** — ciphertext, never plaintext, so AC18 still holds — so no read is needed; **publish the measurement first, then write state**, retrying the write without ever blocking or losing the measurement. | **Yes — Steps 4 and 5** | ADR-010 amendment; `design.md` |
| Q2 | `checks.target` is `NOT NULL` and URL-only. **Assumed:** make it nullable and validate per kind — a URL for API checks, host and port for TCP, a name for DNS, none for heartbeats. | No | `design.md`, data section |
| Q3 | The evidence cap. **Assumed:** 16 KiB per captured request or response body in logs, with the truncation recorded; assertions still read up to the probe's 1 MiB cap. | No | `spec.md` FR31 |
| Q4 | **Assumed:** a thin wrapper over CodeMirror 6's own packages, not `@uiw/react-codemirror`. | No | `design.md`, components |
| Q5 | The heartbeat sweep reads `last_ping_at` from Postgres every 30 seconds — one query per sweep, not per check. **Assumed** acceptable under Q1's rule. | No | `design.md`, heartbeats |

## Gates

- [x] Specification approval — 2026-09-17
- [x] Design approval — 2026-09-17
- [x] **Plan approval — before the first edited file** — 2026-09-17
- [x] Implementation
- [x] Tests
- [x] Review — three independent reviews of the diff (API, scheduler, console); see `status.md`
- [ ] Design QA — the built screens against the approved design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

At the end of **every step**: the scheduler and API unit suites, the web suite, `pnpm run typecheck`, ruff, mypy, the
contract round-trip tests, and `docker compose config -q` for each compose file — read with counts, never by exit
code.

**Live tests** run against the development stack from Step 2, and must fail loudly when it is absent.

At the end of **F18**: `just check`, the full e2e suite, `/review-diff`, and design QA of every screen.

## Rollout and rollback

nexovue is pre-deployment, so nothing is rolled out. Each migration has a downgrade, exercised in its task.

**The real risk is the first steps shipping and nobody judging them.** Every step ends at a demonstrable state; the
founder is asked to look at Step 2 before Step 3 begins.

## Outcome

**Built, uncommitted, awaiting the founder's review.** Every step's judgeable outcome was observed on the dev
stack; the evidence is in `status.md` under *Validation*.

**Changed from the plan, and why** (each also in `status.md`):

- **Built in layers, not step by step** — shared model, API, scheduler, console, end-to-end — because the founder
  asked for the whole feature in one sitting; each layer was verified before the next.
- **One migration (`0027`), not six.** None of the tables is usable without the others.
- **`checks.target` dropped, not made nullable** (Q2): the URL, host or name lives in `config`.
- **The check model is in `packages/py-platform/.../checks/`, not `apps/api/checks/config.py`**, because the
  scheduler parses the configuration the API validates.
- **Secrets travel sealed in the task and state is a separate task after publishing** — Q1's approved answer,
  recorded as ADR-010's amendment. `tasks.py`'s invariant holds: no probe reads Postgres.
- **`jsonpath-rfc9535`** replaced `python-jsonpath` (strict by construction; its regex functions are disabled),
  and **assertion patterns run on RE2**.
- **Multipart bodies are encoded by us**, not libcurl's deprecated form API.
- **Heartbeats are judged every 60 s**, not 30 s.
- **Run now's result read waits server-side**, so a run is one or two requests against its quota.
- **Imports, project-settings sections, the shared `Tabs`/`EditableRows`/`CodeEditor` and the screens' render
  tests** were built by delegated agents under file-ownership rules, then integrated and verified here.
- **The e2e lifecycle test waits on the checks API**, not a live page, because the suite shares one IP's
  telemetry quota.

**Not built, or only partly:**

- **AC26's end-to-end coverage**: every kind is created, filtered, paused, resumed and deleted end to end; *editing*
  is end to end for the API check only, and render-tested for the other kinds.
- **NFR5** (500-operation preview time) is enforced as a limit and not timed.
- **The heartbeat and unattached-file sweeps** are unit-tested and the heartbeat ping live-tested; neither sweep was
  run as a test against real Postgres and the object store.
- **Review items left open** — source-map upload's unbounded body (pre-existing), no `CHECK_SECRET_KEY` rotation
  procedure, the import success message, per-run metric series — are recorded under *Risks* in `status.md`.

**A hands-on QA pass followed on 2026-09-18**, driving the console in a browser rather than through the suites. It
found ten defects in this feature — chiefly that an unprobed run published a duration of zero (so a heartbeat's p95
read `0ms`) and that AC6's fourth file was refused at save rather than at upload — all fixed with tests, plus seven
pre-existing defects outside this feature left for a decision. `status.md` records every one under *Validation*.

# Design: Synthetic checks (F18)

## Status

**Approved** — 2026-09-17, by the founder. **Amended 2026-09-17 during implementation** where the build found a better answer; each amendment is marked *Amended* and listed in `status.md`. Against the [`spec.md`](spec.md) re-approved the same day: API, multistep,
heartbeat, TCP and DNS checks, imports from cURL, OpenAPI and Postman, files in requests, and browser checks
removed. Replaces the earlier design written for the browser-check version.

## Context and goal

The specification asks for checks created, imported and understood entirely in the console; a failure
confirmed before it counts; any HTTP request a developer can express; and one screen and one definition of
uptime for every kind. **nexovue never runs customer code** (BR5), which is what makes this design ordinary
server work rather than a sandboxing problem.

**Kept and extended:** the HTTP probe's phase timings and certificate read (`probes/http_check.py`), the address
guard every probe connects through, which already judges every redirect hop (`probes/addresses.py`),
leader-elected scheduling (`beat.py`), the checks CRUD API, the metrics read path with its proven duplicate
handling (`synthetics/queries.py`), and object storage with tenant-first keys (`data/objectstore/store.py`, as
source maps use).

**Removed:** the browser probe and everything around it (see *Data and migration impact*).

## User experience

The developer's job is *"tell me when my API breaks, and show me the request that broke"*.

**Create.** Synthetics → **New check** → choose API, multistep API, heartbeat, TCP or DNS → fill the form →
**Run now** shows the full request and response beside the form, recording nothing → **Save** with interval and
locations → the check page says when the first run is due. The simplest case, *"is this URL up"*, is a URL and
nothing else.

**Import.** Synthetics → **Import** → paste a cURL command, or upload an OpenAPI or Postman file → a **preview**:
what will be created, which credentials become secrets, which parameters need values, which files must be
attached, and what was skipped and why → fill the gaps → **Create** → the list, filtered to the new checks.
Nothing exists until Create.

**Diagnose.** The check page shows state and since when, uptime and response time, the state-change timeline,
and every run. A failed run opens to the request that failed: what was sent, what came back, and the assertion
with expected against actual. The developer never goes to Logs to find it.

## UI structure

| Route | Screen |
|---|---|
| `/synthetics` | **List** (existing, amended) |
| `/synthetics/new` | **New check** — kind, then form |
| `/synthetics/import` | **Import** — source, preview, create |
| `/synthetics/$checkId` | **Check** — state, history, runs, evidence, settings, pause, delete |

Plus **Secrets** and **Maintenance windows** as sections of the existing project settings. The three new routes
join `reachedByFollowingSomething` in `route-manifest.test.ts`; every form file joins `GUARDED` in
`unsaved-changes-coverage.test.ts`.

### List

One table for every kind: state mark and word, name, kind, uptime, p95, locations, trend. The `Kind` filter
lists `api`, `multistep`, `heartbeat`, `tcp`, `dns` — `browser` is gone (AC29). Header actions: **New check**,
**Import**. The empty state's instruction to `POST` to the API becomes both buttons.

### The request editor — Postman's layout, deliberately

Developers already know Postman's request builder, so the editor follows it and costs nothing to learn:

```
┌──────────────────────────────────────────────────────────────────────┐
│ [ POST ▾ ]  https://api.myshop.com/orders/{{order_id}}   [ Run now ] │
├──────────────────────────────────────────────────────────────────────┤
│  Params │ Authorization │ Headers (3) │ Body │ Assertions (2) │ Settings │
├──────────────────────────────────────────────────────────────────────┤
│  Body:  ○ none  ○ text  ● JSON  ○ XML  ○ form  ○ multipart  ○ binary │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ { "item": "shoe", "qty": 2 }                                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────┤
│  RESPONSE   201 Created · 184 ms · 1.2 KB                            │
│  Body │ Headers │ Timings │ Assertions                               │
│  ✓ status equals 201                                                 │
│  ✗ $.order.id is not empty  —  expected a value, got null            │
└──────────────────────────────────────────────────────────────────────┘
```

Where it differs from Postman, it differs on purpose:

- **Run now** stands where **Send** does, and saves nothing.
- **Assertions** replaces Postman's **Tests / Scripts**: rows of source, comparison and value, never code (BR5).
- **Settings** holds what a monitor needs and a request builder does not — interval, locations, thresholds, retries.
- The response pane adds an **Assertions** result, each passed or failed with expected against actual.
- A custom method can be typed as well as chosen.

**Multistep** is a small collection: the steps listed on the left, the selected step in the same editor on the right,
with its extraction rows beneath.

**This needs a shared `Tabs` component.** The console has none, and `docs/design/principles.md` does not mention
tabs. It is built to the WAI-ARIA tabs pattern — arrow keys move between tabs, one tab stop, selection announced — so
it can be reused elsewhere.

### Components

| Component | Serves |
|---|---|
| `RequestEditor` | method (common seven, plus a free field for any other method), URL, query and headers, body, auth |
| `Tabs` | **new shared component** — WAI-ARIA tabs; the request editor's Params, Authorization, Headers, Body, Assertions and Settings, and the response's Body, Headers, Timings and Assertions |
| `EditableRows` | **the console's first editable list** — query, headers, form fields, assertions, extractions. Add and remove; rows keyed by `crypto.randomUUID()`, never by index |
| `BodyEditor` | a body-type switch: none · text · JSON · XML · form · multipart · binary. Text, JSON and XML use **CodeMirror 6** (~50 kB, mobile-friendly, against Monaco's 2–5 MB) |
| `FileAttach` | multipart and binary files; states the 3-file, 3 MB limit before upload |
| `AuthHelper` | none · basic · bearer · API key in header or query — each value picked from secrets |
| `AssertionRows` | source, property, comparison, target |
| `StepList` | multistep requests as cards, with move-up and move-down — the one place order carries meaning |
| `CheckSettings` | name, interval, locations, degraded and failing thresholds, retries |
| `RunNowPanel` | the request sent and the response received, each assertion passed or failed |
| `ImportPreview` | per item: create or skip, reason, secrets detected, values and files still needed |

### States, per `docs/design/principles.md` rule 3

| State | List | New / Import | Check |
|---|---|---|---|
| Loading | skeleton rows | import preview: skeleton while parsing | skeletons per section |
| Empty | "No checks yet" + New + Import | — | the four reasons: not yet run, paused, in maintenance, overdue |
| Zero results | names the filter, offers to clear | preview with nothing importable says why | names the window, offers to widen |
| Partial | em dash per missing cell | preview items needing values or files are marked, not hidden | a truncated response body says so, with the limit |
| Validation | — | inline **and** summarised, naming row and step numbers | same, on settings |
| Unsaved changes | — | guarded, including an unconfirmed import preview | guarded |
| Dangerous | — | — | delete states runs and stored files lost; pause offered instead |
| Success | — | "Created 12 checks from *Orders API*; 3 skipped" → the filtered list | "Saved. The next run uses this." · "Paused." |

## Proposed design

### Kinds and configuration

`checks.type` is one of **`api`, `multistep`, `heartbeat`, `tcp`, `dns`**, enforced by a `CHECK` constraint.
`http` is renamed `api`; browser rows are deleted (pre-deployment — AGENTS.md rule 15).

`config` stays JSONB, validated by a **Pydantic discriminated union on `type`**, `extra="forbid"` throughout.
Errors are raised as `InvalidRequestError` carrying `errors=[{field, reason}]`, the RFC 9457 shape
`features/auth/problem-fields.ts` already maps to fields (FR38, AC25). Every `api` and `multistep` check needs at
least one assertion (BR1).

**Methods** (FR1): any token valid as an HTTP method under RFC 9110, which rules out spaces and control
characters that could split a request.

**Header values** from any source — typed, imported or substituted — are refused if they contain a carriage
return or line feed, so no value can inject a header.

### Variables

One substitution syntax everywhere: **`{{name}}`** for a variable or a value extracted in an earlier multistep
request, and **`{{secret.NAME}}`** for a secret. `{{name}}` is also Postman's own syntax, so imported collections
keep working unchanged. Substitution applies to URL, query, headers and body. An unknown name is
*misconfigured* (EC2), never sent as literal text.

### Where each kind runs

| Kind | Queue | Runner |
|---|---|---|
| `api`, `multistep`, `tcp`, `dns` | `light.<location>` | Python, in the scheduler |
| `heartbeat` | none — nothing is probed | ping received by the API; a sweep task decides overdue |

**Locations are queues.** Each prober worker consumes only its own location's queue and stamps its
`prober_region` on every run, as today. Beat creates one entry per check per location, dispatched with
`options={"queue": …}` — the mechanism it already uses for polls (`beat.py:294`). There is **no browser queue**.

### API and multistep runs

`probes/http_check.py` gains:

- **Any method** via pycurl's `CUSTOMREQUEST`.
- **Headers, query and bodies** of every FR3 type. Multipart parts and binary bodies come **from memory**, via
  pycurl's buffer-backed form parts, so a customer's file never touches the worker's disk.
- **Auth helpers** resolved from secrets immediately before the request.
- **Its existing connection guard**, which judges every redirect hop (FR6, AC5).
- **Response capture** under the existing body-size cap; a larger body is truncated for evidence and the
  truncation recorded (EC13).

New `probes/assertions.py` evaluates FR9 against a response. **JSON paths use `python-jsonpath` in strict mode**,
which enforces RFC 9535.

**Multistep** runs requests in order in one task, extracting values by JSON path, header or regular expression into
variables for later requests, and stops at the first failure, naming it (AC4).

### Files in requests

- **Upload:** `POST /check-files`, streamed with a byte cap enforced on what actually arrives — the pattern
  `deploys/router.py:_bounded_body` already uses. Refused past 3 MB (AC6).
- **Storage:** object storage under `synthetics/{org_id}/files/{file_id}` — the tenant-first layout of
  `sourcemaps/service.py:tenant_prefix`, so a tenant purge removes them by prefix (BR9). A `check_files` row
  records name, type and size.
- **Drafts:** a file uploaded before its check is saved is unattached; a sweep deletes unattached files after
  24 hours, so an abandoned form leaves nothing behind. *Amended:* a file a check stops using — or a deleted check's —
  is detached and swept an hour on, row first and object after, so no object is deleted before the change that freed it
  has committed.
- **Limits:** 3 files per request, enforced at save as well as at upload.
- **At run time:** the worker reads the file with boto3, which the scheduler already depends on, using its
  existing and so far unused `synthetics_bucket` setting — into a small in-memory cache keyed by file id, so a
  check every minute does not re-download unchanged files.
- **Deletion:** a check's files go with it; FR37's confirmation counts them.

### Imports

**All parsing happens in the API, server-side, as pure functions with no network access.** One implementation,
tested once, validated by the same models a save uses.

```
POST /check-imports/preview  (source: curl text | openapi file | postman file [+ environment file])
   → parse → validate → plan: {creates[], secrets_detected[], needs_values[], needs_files[], skipped[{item, reason}]}
   → nothing written

POST /check-imports/confirm  (the plan, with gaps filled in)
   → one transaction: secrets, variables, checks → created ids
```

**No outbound request during import** (AC10) is made structural: the import package imports no HTTP client, and a
test asserts it.

**cURL** (FR12). A parser on Python's built-in `shlex` in POSIX mode, and an **explicit table of supported
options** — method, URL, headers, the data and form variants, basic credentials, redirect-following. Everything
outside the table is listed in `skipped`, never silently dropped (EC5). `-u` credentials and `Authorization`
headers become secrets. `-F name=@path` becomes a file the user must attach (AC8). Bash-style quoting only; a
Windows command-prompt paste is refused with a message to copy the bash form.

**OpenAPI** (FR13). JSON or YAML under a size cap.

1. **YAML is loaded with the safe loader, and any alias is refused** on the parser's event stream before
   anything is built. That closes the "billion laughs" attack, where a few kilobytes of nested aliases expand into
   gigabytes. OpenAPI files rarely use aliases; refusing them costs little.
2. **Every `$ref` is scanned, and any that does not begin with `#/` is refused** (EC6, D14). This happens *before*
   validation, because no documented option was found to stop the validator itself from following a remote
   reference.
3. **Validated with `openapi-spec-validator`**, which covers Swagger 2.0 and OpenAPI 3.0, 3.1 and 3.2.
4. Operations are listed. The server comes from `servers` (3.x) or `host`, `basePath` and `schemes` (2.0). Path and
   query parameters take the description's examples where present and appear in `needs_values` where not.
   Security schemes become secret prompts. Each chosen operation asserts its lowest documented `2xx`, or `2xx` when
   none is documented (AC9).

**Postman** (FR14). The collection's `info.schema` must name v2.1; v1 and v2.0 are refused with re-export steps
(AC13). Requests become checks; a folder becomes one multistep check or separate checks, as chosen (AC12).
Collection, folder and environment variables become variables — **values the environment marks secret become
secrets**. Basic, bearer and API-key auth become secrets; other auth types are listed unsupported.
**Pre-request and test scripts are listed in `skipped` and never executed** — there is no JavaScript engine
anywhere in this path (BR5, AC11).

### Heartbeats

- `GET|POST /heartbeat/{token}` in the API, no session. The token is the credential, **stored as a SHA-256 hash**
  like API keys, and joins the `EXEMPT` set in `test_authorization_coverage.py` as `receive_deploy` did.
- A ping sets `last_ping_at`. A sweep task on `light`, every 30 seconds, records monitors past period plus grace as
  down; the next ping records them up (AC14).
- The token is **shown once**, with a rotate action — only its hash exists afterwards.

### TCP and DNS

`probes/tcp_check.py` connects through the address guard (BR2, AC15). `probes/dns_check.py` resolves with a DNS
library — **dnspython is the expected choice, to be confirmed by research in the plan**. A customer-chosen
resolver is itself an address and passes the same guard (AC16).

### Check state

Decided in the scheduler after a run and written to Postgres — the scheduler already writes the control plane
(`pollers/integrations.py`).

- **Confirmation (FR25).** A failed attempt is not recorded as a failure. The task re-dispatches the check to
  another selected location, or the same one when only one exists, with `attempt + 1`, up to its retry count
  (AC19, AC20).
- **Every attempt is published**, marked `synthetic.check.attempt` and `synthetic.check.final`; uptime counts only
  final attempts, so retries stay visible.
- **Transitions** among `up`, `degraded`, `down` and `misconfigured` write a `check_state_changes` row — F21's
  input (FR28). A paused check is not run, so it records nothing; *paused* and *in maintenance* are read from the
  check and its windows, never recorded as states. (Corrected 2026-09-18 by the F21 design: this line listed
  both as recorded states, which the code never did.) An active maintenance window marks runs
  `synthetic.check.maintenance` and changes no state (AC23). Uptime excludes misconfigured, paused and maintenance
  runs (AC22).
- **No stale queue (FR26).** Every task is dispatched with `expires = interval_seconds`.

### Run now

As [ADR-011](../../decisions/ADR-011-api-dispatches-run-now.md): the API dispatches the run task by name with
Celery's `send_task`, marked `run_now`; it publishes nothing and writes no state (AC24); its result waits in Redis
under a request id with a short expiry; the console polls. An unsaved check can reference unattached files and
existing secrets.

### Secrets

As [ADR-010](../../decisions/ADR-010-check-secrets.md) and its amendment: sealed with their own key, held by API and scheduler;
associated data binding each value to its project and name; *Amended:* tasks carry the secrets they use **sealed** — never
in the clear (AC18) — so no probe reads Postgres; every decrypted value redacted from every captured string, before
any cut to size, before storage (AC17), with a minimum secret length.

### Results — one shape for every kind

Every kind emits the same gauges (FR33, D6):

| Metric | Meaning |
|---|---|
| `synthetic.check.status` | 1 up, 0 not |
| `synthetic.check.duration` | seconds |
| `synthetic.check.phase.duration` | API and multistep, as today |
| `synthetic.tls.certificate.remaining` | API and multistep, as today |

Resource attributes `synthetic.check.id` and `synthetic.check.type`; point attributes `synthetic.check.outcome`
(up · degraded · down · misconfigured), `…location`, `…attempt`, `…final`, `…maintenance`.

**Evidence goes to logs**: per request, what was sent and what came back, redacted and size-capped, and the failing
assertion with expected and actual (FR31).

**Reads** stay on `metrics_gauge` for every kind, extending `synthetics/queries.py` and keeping its
`GROUP BY check_id, metric_name, series_hash, timestamp` collapse (AC30), with `outcome` and `final` filters.
Evidence is read from `logs` by check and run. Current state and state changes come from Postgres.

## Data flow and interfaces

### API

| Method and path | Purpose | Gate |
|---|---|---|
| `GET /checks`, `GET /checks/{id}` | list, one | signed in |
| `POST /checks`, `PATCH /checks/{id}`, `DELETE /checks/{id}` | manage | `MayAdminister` |
| `POST /check-imports/preview`, `POST /check-imports/confirm` | import | `MayAdminister`, own quota |
| `POST /check-files?project_id=&name=` (the body is the file) | files | `MayAdminister`, own quota. *Amended:* no delete route — files go when a check stops using them, by the sweep |
| `POST /check-runs`, `GET /check-runs/{request_id}?wait_seconds=` | run now | `MayAdminister`, own quota. *Amended:* its own prefix so its quota covers both; the read waits server-side |
| `GET /projects/{id}/check-secrets`, `PUT/DELETE …/check-secrets/{name}` | secrets, names and usage counts only | `MayAdminister` to change |
| `GET /projects/{id}/check-variables`, `PUT/DELETE …/check-variables/{name}` | variables, with usage counts | `MayAdminister` to change |
| `GET/POST/PATCH/DELETE /maintenance-windows…` | maintenance | `MayAdminister` to change |
| `GET\|POST /heartbeat/{token}` | ping | token only, `EXEMPT` |
| `GET /synthetics/checks…` (extended), `…/runs/{run_id}` | results and evidence | signed in, `TELEMETRY_TIER` |

Result reads stay in `synthetics/` because `test_ratelimit_coverage.py` puts any router reading telemetry on
`TELEMETRY_TIER`. Imports, uploads and run-now each get their own quota: each is either expensive to parse or
causes outbound traffic.

### Scheduler tasks

*Amended:* `probes.run_check(definition, location, attempt, run_now_request_id)` for `api`, `multistep`, `tcp` and `dns`,
carrying the whole definition with its secrets sealed; `probes.record_outcome(...)`, dispatched after publishing;
`probes.sweep_heartbeats()`; `probes.sweep_unattached_files()`. Never a secret in the clear or file contents.

## Data and migration impact

**Postgres (alembic), every table with `org_id`, `project_id` and a UUID primary key** — the tenancy structure test
and ADR-007's primary-key rule both apply:

| Change | Detail |
|---|---|
| `checks` | delete `browser` rows; rename `http`→`api`; `type` CHECK constraint; add `name`, `locations`, `degraded_ms`, `failing_ms`, `retries`, `updated_at`, `heartbeat_token_hash` (unique, nullable), `last_ping_at` |
| `check_states` | one row per check: state, since, confirming run |
| `check_state_changes` | from, to, at, location, run — F21's input |
| `check_secrets` | project, name, sealed value; unique on project and name |
| `check_variables` | project, name, value; unique on project and name |
| `check_files` | project, check (nullable while a draft), name, content type, size, created |
| `maintenance_windows`, `maintenance_window_checks` | one-off or recurring; all checks or chosen ones |

**ClickHouse:** no new tables; new attributes live in the existing JSON columns.

**Object storage:** `synthetics/{org_id}/files/`.

**Removed — browser checks (FR39, AC31).** Enumerated by searching the repository:

| Area | What goes |
|---|---|
| Scheduler | `probes/browser.py`, `probes/browser_proxy.py`, `run_browser_flow` in `probes/tasks.py`, the browser route and `QUEUE_BROWSER` in `celery_app.py`, `BROWSER_TASK` in `beat.py`, browser settings in `settings.py`, the `playwright` dependency in `pyproject.toml` |
| Scheduler tests | `test_browser_flow.py`, `test_browser_proxy.py`, `test_browser_envelope.py`, `test_browser_egress_live.py`, the browser case in `test_beat_schedule.py`, and helpers used only by them |
| Images and deploy | `apps/scheduler/Dockerfile.browser`; the `scheduler-browser` service in `docker-compose.yaml`, `docker-compose.app.yaml` and `docker-compose.onprem.yaml` |
| Contracts | `door2-synthetic-browser.bin` and its generator in `build_fixtures.py`, and its cases in `roundtrip_test.go` and `test_envelope_roundtrip.py` |
| Root | `playwright.*` in the root `pyproject.toml` mypy overrides |
| API and web | `browser` in `CheckType`, `CHECK_TYPES` and the synthetics router; the `browser` filter option in `features/synthetics/filters.ts` |
| Documentation | `docs/architecture/current-system-reference.md`, `docs/architecture/integrations.md`, `vision.md`, and the browser-worker notes in `docs/operations/production-readiness.md` |

**Kept deliberately:** `tools/e2e/` uses Playwright **to test our own console**. That is unrelated to browser
checks and stays — and so do the lines describing it in `AGENTS.md`, the `justfile` and
`docs/engineering/testing.md`, which a search for "playwright" also matches. `docs/research/market-analysis-2026.md` is a dated snapshot and is not rewritten.

**Contracts:** the door-2 HTTP fixture is regenerated with the new attributes, and the stale docstring citing a
deleted architecture file is corrected.

## Performance and scale

- **An API run is a few HTTP requests** on the light queue — no sandbox, no browser. Run now's overhead is dispatch
  and polling, not start-up (NFR2).
- **Two locations double runs.** At the 60-second floor, one check is 2,880 runs a day; per-project check limits
  bound it.
- **Staggered starts.** Beat offsets each check's first run by a stable hash of its id across the interval, so
  checks do not all fire on the same second.
- **File cache** bounded by entries and bytes; a changed file gets a new id, so the cache never serves stale content.
- **Imports** are bounded by file size, operation count (500 previewed within a stated time, NFR5) and the YAML
  alias refusal.
- **Reads** use the existing window-bounded, collapsed statements; the check page's run list is paged.

## Failure handling

| Failure | Behaviour |
|---|---|
| A worker dies mid-run | `task_acks_late` redelivers; the repeat is collapsed on read (AC30) |
| A file cannot be fetched at run time | *misconfigured* if it no longer exists; a **prober** fault, changing no state, if storage is unavailable |
| Object storage unavailable at upload | the upload fails with a retry; no half-recorded file |
| Kafka unavailable | the existing publish retry |
| Redis unavailable | nothing dispatched; run-now fails with a message; beat keeps its schedule |
| An import file is malformed | preview refused, naming where parsing failed (FR15) |
| A confirm fails part-way | one transaction: nothing is created |
| The prober's own network fails | not confirmed when a second location succeeds; indistinguishable with one location — stated in EC10 |

## Security, tenancy, and privacy

- **No customer code runs anywhere** (BR5). There is no JavaScript engine in the import path, and no Postman script
  is executed or translated.
- **SSRF.** The address guard judges every redirect hop, the TCP host and a customer-chosen DNS resolver. Imports make
  no outbound requests; remote `$ref` is refused before validation.
- **Parser attacks.** Import size caps; YAML aliases refused; JSON parsed with a size cap.
- **Header injection.** Carriage returns and line feeds refused in header names and values from every source.
- **Secrets.** ADR-010; imported credentials become secrets before anything is stored (BR10, EC8).
- **Files.** Tenant-first keys, never served publicly, never executed or rendered; removed with the check and by
  tenant purge (BR9).
- **Heartbeats.** Hashed tokens; the endpoint rate-limited.
- **Tenancy.** Every new Postgres table passes the tenancy structure test; every new ClickHouse statement is a module
  constant with the literal tenant predicate.

## Observability and operations

- Per-location queue depth and run duration, through the scheduler's structured logs and beat's heartbeat line.
- A **prober** fault has its own outcome, so the operator sees our failures without them reaching customers' uptime.
- Retries visible per check — how a flaky check is found.
- Import outcomes logged with counts only, never file contents.

## Alternatives and tradeoffs

| Rejected | Why |
|---|---|
| A cURL-parsing library | FR12 and EC5 require listing every unsupported option; an explicit table on `shlex` guarantees that |
| Parsing imports in the browser | Two implementations of one format, validated differently from a save |
| Resolving remote `$ref` | Makes an import fetch arbitrary URLs from our servers (D14) |
| Running or translating Postman scripts | Customer code (BR5, D16) |
| Uploading files straight to object storage with a signed URL | Bypasses the byte cap and the tenancy check at upload |
| Storing files in Postgres | Bloats the control plane and every backup of it |
| Downloading files to the worker's disk | Leaves customer data behind on a shared machine; memory buffers leave nothing |
| Monaco editor | 2–5 MB against CodeMirror 6's ~50 kB; nothing here needs IntelliSense |
| XPath assertions | Out of scope; XML is asserted as text |
| Heartbeat endpoint on the receiver | Its gate chain order is fixed and its path is OTLP; a ping is a control-plane write |
| State in ClickHouse | A transition is a read-modify-write that needs a transaction |

## Specification impact

- FR4 and FR19 were amended on 2026-09-17 with the 24-hour expiry of unattached files and the substitution syntax.
- **NFR2's overhead** no longer depends on a sandbox, and is set once measured during implementation.

## ADRs

No new ADR. The two this design relies on stand unchanged:
[ADR-010 — check secrets](../../decisions/ADR-010-check-secrets.md) and
[ADR-011 — run now](../../decisions/ADR-011-api-dispatches-run-now.md). Removing browser checks withdrew the only
decision here that was expensive to reverse.

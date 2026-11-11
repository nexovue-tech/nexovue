# Status: Finding explanation, with optional AI wording (F3)

## Lifecycle status

**Step 4 built 2026-11-11, awaiting commit** (worktree `wt/f3s4`, from `wt/f3s3`): connect a provider, test it, set a limit, see the bytes, consent (plan T4.1 to T4.10). Built and tested against fake providers only. T4.7 (Playwright), T4.8 (customer doc), T4.9 (Bedrock signing) and T4.10 (the dated real calls) stay unticked. migration 0071 sits on 0070 for now; the coordinator re-chains.
**Step 3 built 2026-11-11, awaiting commit** (worktree `wt/f3s3`, from `feature/phase-2`): the fence, an idle AI container that can reach almost nothing (plan T3.1 to T3.11). The spike passed; T3.4, T3.10 and T3.11 stay unticked until `just insight-fence` runs. Migration 0070 needs F22 Step 6's 0068 and 0069 on the branch first.
**Step 2 is on `feature/phase-2` (2026-11-09).** Links, the card, and the follow-up alert (plan T2.1 to T2.9, T2.12 to T2.14). T2.13 stays unticked for two of AC104's dates (*Open decisions*, 5). **Step 1 is on `feature/phase-2` (2026-11-09):** facts on the finding page, with no AI at all (plan T1.0 to T1.15); two of its tasks stay unticked for checks this machine could not run (*Not run*, below). The founder approved the specification, design and plan on 2026-11-02.


## Current task

Step 4 awaits the founder's review of the diff and open decisions 9 to 11, then commit after Step 3 (migration chain 0070, then 0071). Then the founder's first real call per format with a free-tier key (`tools/ai-eval/real_call.py`), which is what puts a format on the screen.

Step 3 awaits the founder's review of the diff, then commit after F22 Step 6 (migration chain). Then the coordinator's first run of `just insight-fence`. Open decision 7 is the founder's.

Step 2 is committed (`55703b4`). Step 2b is the founder's after Phase 2 (foundation item 30); Step 2c's code (T2.15, T2.16) is still to build.


## Completed

### Step 4, built 2026-11-11 (worktree `wt/f3s4`)

**What a person sees.** `/settings/ai`, under "This organization". Everyone reads whether AI wording is on. An owner or admin sees what is missing, each step linked: a provider connection (format, base URL, model, region as typed, a write-only key, time per call, monthly token limit, optional prices), a test that runs on the AI container, the exact request for the newest finding's facts (or a labelled sample) with its stand-in names, the owner's consent, the switch, per-project opt-out, the month's tokens with an estimate, and each run with "View what was sent". **No format is offered yet**: each appears once a dated real call is recorded (AC77), and none has run here, so on this build the form says so and cannot be saved. **Nothing is sent to a provider except a test.**

**What was built.**

| Task | What | Where |
|---|---|---|
| T4.1 | `AIRequest` (trusted parts only), `AIResponse` with one closed outcome, `HttpCall`; three wire formats as pure `build` and `parse`; the constant check request; `explain_request` over a stored `model_input` (`PROMPT_VERSION` `explain-1`), the sendable-field list held to `ModelFacts`, a labelled sample; FR23's rules shared by the API and the AI role; both fingerprints; the key's owner; the dated real-call record | `nexovue_platform/ai/` |
| T4.2 | `PrivateEndpoints` (address or range, with port; loopback, link-local, reserved, multicast, unspecified refused even when listed; wider than /16 or /48 refused); the guard's `private_endpoints` and `public_allowed`; `user_agent`; a header with a control character never sent (a tab still is); `AI_PRIVATE_ENDPOINTS` parsed at boot | `probes/addresses.py`, `probes/http_check.py`, `scheduler/settings.py` |
| T4.3 | The transport (no redirects; `http` only to a listed address; closed failure words); the connection test as a task: run row before the call, tokens to the month with no reservation, the result on the connection only for the values it tested, the person's result through migration 0070's function | `insight/transport.py`, `check.py`, `store.py`, `registry.py` |
| T4.4 | `MANAGE_AI` (owner, admin), `CONSENT_AI_DISCLOSURE` (owner) | `permissions.py`, `policy.py`, `test_policy.py`, `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts` |
| T4.5 | `/ai/connection` (read, save, delete), `/connection/check` and its result, `/preview`, `/consent`, `/switch`, `/project-opt-outs`, `/usage`, `/runs`, `/runs/{id}/sent`; audit names with old and new hosts; owners emailed after commit (`Message.AI_CONNECTION_CHANGED`) | `nexovue_api/ai/`, `audit/service.py`, `main.py`, email templates |
| T4.6 | `ai_connections.sealed_key` under the `ai` ring, bound to organization, connection and address; `SEALED_COLUMNS`; the AI account's grant; the API reads `AI_SECRET_KEY` and `SCHEDULER_INSIGHT_REPLICAS`; Compose gives the API both | migration `0071`, `keys.py`, `models.py`, `insight_account.py`, `settings.py`, `nexovue.yaml`, `docker-compose.yaml`, `key-rotation.md` |
| T4.7 | The screen in every role and state, the rail entry, the route, the unsaved-changes guard | `features/aisettings/`, `router.tsx`, `settings-nav.ts`, `console-routes.json` |
| T4.10 | One real call through a format and the record line it earns; the screen offers only recorded formats | `tools/ai-eval/real_call.py`, `real_calls.py` |

Migration **0071** (down revision `0070` for now; F5 Step 1 may take 0074 to 0075 and F20 Step 5 0076 to 0077, so the coordinator re-chains), roll forward only. **ADR-047** filed (one provider interface, three wire formats, no SDK). `docs/operations/ai.md` gains allowing a local model and an unreadable key.

**Decisions taken while building** (technical; none changes product behaviour):

- **`ai_runs.outcome` gains `not_found` and `bad_request`** (migration 0071), so a failed test says "model not found" apart from a refused key (spec, *Error cases*). The check's own reasons are a closed list too (`jobs.CHECK_REASONS`), never a provider's words.
- **Plain `http` is saved only to a private address written as an address** (`http://192.168.1.20:11434`); a name over `http` is refused on save, because the API cannot know what it resolves to. At call time `http` reaches only a listed address. `localhost` and loopback are refused with spec's words.
- **The test's call limit is the connection's, capped at 45 s**, so a test answers inside the screen's 60-second wait.
- **The test is refused with 409 when `SCHEDULER_INSIGHT_REPLICAS=0`**, rather than queued for no one.
- **`AIRequest` holds trusted parts only** until `untrusted.py` (T5.1); run ids live on the run row, not in the request, so the request is only what is sent.
- **`insight/store.py` exists from this step** (run rows, metering); reservations are T5.3's.
- **A run is "not consented" when no `ai.disclosure_agreed` audit row names its host at or before it**, read from `audit_log`, which the AI account cannot write. Tests are marked not applicable.
- **A support view reads on or off only** on `GET /ai/connection`: a base URL's path can carry a customer's secret.
- **`test_sealed_columns_listed.py` not created**: `test_keys_live.py` already reads every `sealed_*` column from the catalog (F22's) and now names this one, with a rotation case.
- **Private-endpoint cases live in `test_ai_private_endpoints.py`**: `test_connect_guard.py`'s module stops on live DNS (`nip.io`), which this machine cannot resolve, so cases added there would never run here.
- **The admin's consent view copies the page's link, not the owners' names**: `GET /ai/connection` returns no owner list (open decision 11).

### Step 3, built 2026-11-11 (worktree `wt/f3s3`)

**What a person sees.** Nothing on screen. An install now runs one more container, `scheduler-insight`, idle, with its own Postgres account and network; `nexovue setup` says whether that account is ready and, if Postgres refused part of it, what is left. `SCHEDULER_INSIGHT_REPLICAS=0` leaves it out.

**The spike (T3.1), run 2026-11-11 on Postgres 18 (`pgvector/pgvector:0.8.6-pg18-trixie`): passed.**

- A column-level `UPDATE` grant does **not** permit `LOCK TABLE`: refused in `ACCESS EXCLUSIVE`, `EXCLUSIVE`, `SHARE`, `ROW EXCLUSIVE` and `ACCESS SHARE` on `finding_explanations`. A table-level `INSERT` does permit `ROW EXCLUSIVE` (measured on `job_results`), which is why the account holds none on the queue.
- Revoking from `PUBLIC` holds against the account, and the install account keeps each function. **Revoking only the two functions the design named left `pg_try_advisory_xact_lock` open**, so `setup` revokes every `pg_advisory_*` and `pg_try_advisory_*` overload, read from the catalog.
- The four functions and the shared statements agree on one table of cases (due, not yet due, leased, lapsed, expired, spent, order, limit, the lease fence, `SKIP LOCKED`), and never touch another queue; the store function writes `ai_check` and keeps the first result.
- **Two things the design assumed are false** (open decision 7): the `NOTIFY` command needs no privilege, so revoking `pg_notify` does not stop it; and the account's timeouts are defaults it can `SET` to zero. A NOTIFY flood from 8 connections did not stall ordinary commits here (about 4,100 to 2,600 commits a second, CPU shared; one run on one machine).
- `setup` cannot state `NOSUPERUSER`, `NOREPLICATION` or `NOBYPASSRLS` without being a superuser, even to restate false, nor `NOCREATEDB` without `CREATEDB` (measured), so it states only `LOGIN NOINHERIT`, the limit and the password, then reads the role back and refuses to call it ready if it holds any of those.

**What was built.**

| Task | What | Where |
|---|---|---|
| T3.1 | The spike, as a suite: parity table, locked row, other queues, the store, every refusal, the limit, the two gaps as they are | `apps/scheduler/tests/test_insight_account_live.py` |
| T3.2 | `ai_connections`, `ai_project_opt_outs`, `ai_usage_monthly`, `ai_runs` with their `CHECK`s; the four `SECURITY DEFINER` functions, `search_path` fixed, tables named with their schema, `EXECUTE` taken from `PUBLIC`; `ai_check` in `JOB_RESULT_KINDS` and the check; deletion lists and statements | Migration `0070`, `models.py`, `organizations/tables.py`, `queries.py`, `nexovue_platform/ai_records.py` |
| T3.3 | `provision`: role, limit, timeouts, password; everything taken, then `INSIGHT_GRANTS`; advisory, `pg_notify`, `TEMP`, `CREATE` on `public` from `PUBLIC`; each refusal said; `nexovue setup grants` | `nexovue_api/insight_account.py`, `setup.py`, `justfile`, `nexovue.yaml` |
| T3.4 | Restore prints `setup` as next; `scheduler-insight` stopped first; the doc | `restore-postgres.sh`, `deploy/nexovue`, `backup-and-restore.md` |
| T3.5 | `SCHEDULER_ROLE=insight`; `AI_SECRET_KEY` required in production for it alone; `AI_PRIVATE_ENDPOINTS` read as written (T4.2 parses it) | `scheduler/settings.py` |
| T3.6 | Insight task set and preload; queue statements by role; `store_ai_check`; a worker refuses queues its functions cannot claim; `--tasks insight` only as `SCHEDULER_ROLE=insight`; 48 MiB children | `registry.py`, `preload_insight.py`, `insight/`, `queue.py`, `results.py`, `worker.py`, `__main__.py` |
| T3.7 | `scheduler-insight`: `--queues insight --concurrency 4 --tasks insight` | `roles.py` |
| T3.8 | `x-insight-env`, the service, `ai` network, replicas, 384M; Postgres on both networks; Prometheus on both; hosted, quickstart, build overlays; dev `insight` service replaced | `deploy/compose/` |
| T3.9 | The password and the key written at install and added at upgrade | `deploy/nexovue` |
| T3.10 | The reach test from inside the container (marker `insight_fence`); the sizing row, not measured | `test_insight_network_live.py`, `sizing.md` |
| T3.11 | `just insight-fence` and its CI job: build, kit, install, reach, backup, empty volume, restore, catalog read, the account suites. **Not yet run** | `tools/insight-fence/fence.sh`, `justfile`, `.github/workflows/ci.yml` |

Migration **0070** (on F22's **0069**; renumbered from 0068 at the coordinator's request), roll forward only (added to `test_migration_rules.py`). **ADR-040** filed. `docs/operations/ai.md` started (the account's runbook; Step 5 adds the rest), `current-system-reference.md` gains the second account.

**Decisions taken while building** (technical; none changes product behaviour):

- **`ai_connections.sealed_key` waits for T4.6.** `test_keys_live.py` holds every binary column to `SEALED_COLUMNS`, and the `ai` ring that seals it is T4.6's; adding the column now would need the ring now. `key_id` is here.
- **The API does not hold `AI_SECRET_KEY` yet:** it reads it from T4.6, so Compose names it on `scheduler-insight` alone for now; the Compose test allows `api` too.
- **The closed lists live in `nexovue_platform.ai_records`**, not `nexovue_platform.ai`: the models import them, and every module importing the models would then "reach AI code" in the no-AI suites. `nexovue_platform.ai` is created by T4.1.
- **`ai_runs.purpose` allows `explain`, `check`, `chat`, `fix`** (foundation's three and A51's), so F4 and F5 need no migration for it; outcomes are the design's failure table plus `started`, `done`, `abstained`, `died`.
- **Timeouts:** statement 10 s, idle in transaction 30 s (guesses; see open decision 7 for why they are not a bound).
- **`setup` also takes `CREATE` on `public` from `PUBLIC`** (Postgres 15's default, said again for an older database) and clears any role setting it does not set.
- **The dev `scheduler-insight` has no `AI_SECRET_KEY`**: development allows it, and committing a new literal key would need a gitleaks entry; Step 4 sets one.
- **T3.6 is proved in `test_insight_role.py`**, F22's pattern for its own role, rather than `test_jobs_live.py`.
- **The `ai` network is declared once, in `compose.infra.yaml`**, where Postgres is defined; the deploy overlay inherits Postgres's list.

### Step 2, built 2026-11-09 (on `feature/phase-2`)

**What a person sees.** Every "Look next" link, and each fact's own link, opens its data at the finding's minutes: the window travels in the address (`?from=&to=&project=&finding=`), so a copied link opens the same minutes, the person's own window on every other screen is left as it was, and a line under the window control says "Showing this finding's window, 13:47 to 14:20 Kolkata time", with "Back to the finding" and "Use my own window". An error fact opens its group on the Errors screen; a dependency fact opens traces through the callee, or the caller's calls named by the attribute that named it, or, with none, the caller's calls labelled "not narrowed"; failing calls or slow ones. Raw data past retention reads "Raw traces aged out on {date}" instead of a link. A finding card names its likely cause in one wrapping line. Members rate an explanation ("Was this useful?"); admins see each rule's counts on `/settings/explanations`, can stop a rule naming causes, and copy a counts-only summary. A labelled sample sits at `/findings/sample`, linked from the empty feed with the first findings' date. An incident's page has "Look next" too. A channel people read that was told a finding gets one follow-up when a signed deploy is its likely place to look, with the deploy's base rate, unless the channel turns it off. **While FR50 holds (production), no cause is stated, so no card line and no follow-up appear**; the override shows them outside production.

**What was built.**

| Task | What | Where |
|---|---|---|
| T2.1 | F28's chart markers re-run: "marks the charts and lists the window's releases (AC40)" passes | `FindingScreen.test.ts` (unchanged) |
| T2.2 | Parse and build the address window; drop a project or finding that is not an identifier; apply on the six screens only | `shared/url-window.ts` |
| T2.3 | `useWindowChoice` prefers the address window and never writes the stored one; the six routes keep it; each screen keeps it on a filter change; the window line, with "Back to the finding" and "Use my own window"; `project` selected on arrival | `shared/selection.ts`, `app/router.tsx`, `TraceListScreen.tsx`, `ErrorsScreen.tsx`, `LogsScreen.tsx`, `InfrastructureScreen.tsx`, `app/layout/WindowControl.tsx` |
| T2.4 | Every link, built in one place; a `target` on each fact in the API (the values a link needs, no message text); `?group=` on Errors | `shared/evidence-links.ts`, `shared/components/LookNextLinks.tsx`, `findings/explanations.py`, `findings/schemas.py`, `features/errors/filters.ts`, `ErrorsScreen.tsx`, `ExplanationSection.tsx` |
| T2.5 | The card's cause line, wrapping, from `likely_cause_sentence` | `FindingsScreen.tsx` |
| T2.6 | `explained` in the vocabulary and the kind check; `notification_channels.sends_explained`; the channel switch | `alerts/model.py`, migration `0067`, `channels/` (API), `ChannelDetailScreen.tsx`, `features/alerts/api.ts` |
| T2.7 | `explained.write`: on any read after the settled time, written or not, for a `likely` cause on a verified record once released; told channels people read with it on and no follow-up at any sequence; 5 per channel in 10 minutes under a per-channel transaction lock, the fifth saying so | `evidence/explained.py`, `evidence/gather.py` |
| T2.8 | `_TAKEN` reads the newest likely-and-verified version's deploy fact; the composer's own branch (no signals, the base rate, the page as current, no previews); `compose_batch` keeps it out of the count, severity and "still open"; a row that cannot be written, or a finding closed since, is dropped alone with its reason | `alerts/outbox.py`, `alerts/messages.py`, `alerts/payloads.py`, `alerts/delivery.py` |
| T2.8 (F19) | `explained` joins `_WITHHOLD_UNTOLD_ENDING`: it goes only where news of its finding reached the channel (F19 AC58, F19's *Open decisions* 2) | `alerts/outbox.py` |
| T2.9 | `explained` never on a webhook; the follow-up, frequent deployers, the 7-day clock and pages with no finding; held until the bar is met | `docs/product/setup/alerts/webhooks.md`, `channels.md`, `setup/deploys.md`, `docs/features/alerts/spec.md` |
| T2.12 | Ratings (one per person per version, carried until the cause changes, reasons that fit what was shown, audited); rule counts and stopping a rule (audited, per organization); gather passes the stopped rules to `rules.decide`; the rating control; `/settings/explanations` | migration `0067`, `findings/explanations.py`, `explanation_queries.py`, `findings/router.py`, `explanations/router.py` (new), `audit/`, `organizations/`, `evidence/reads.py`, `gather.py`, `features/explanationsettings/` (new), `settings-nav.ts` |
| T2.13 | `/findings/sample` from fixed data (likely, abstained, absences), no actions or links; the empty feed's sample link, deploy setup link and first findings' date. **Not built:** AC104's deploy-cause and traffic dates (*Open decisions*, 5) | `sample-explanation.ts`, `SampleExplanationScreen.tsx`, `FindingsScreen.tsx`, `app/router.tsx`, `console-routes.json` |
| T2.14 | An incident's "Look next", its service's or the project's, at its window | `IncidentScreen.tsx` |

Migration **0067** (on 0066): the kind check widened (`NOT VALID`, then validated), `sends_explained`, `finding_explanation_ratings`, `explanation_stopped_rules`; roll forward only (ratings and stopped rules exist nowhere else; added to `test_migration_rules.py`'s list). **No ADR:** nothing settled here is material beyond the design's.

**Decisions taken while building** (technical; none changes product behaviour):

- **The link helper lives in `shared/`, not `features/findings/`** as T2.4 named: T2.14 reuses it from `features/alerts`, and biome refuses one feature importing another. Its type `ExplanationFactTarget` lives there too; `findings/api.ts` re-exports it.
- **The fact's link values are a typed `target` on each fact** in `GET /findings/{id}/explanation`, read from the frozen `values`: an error group's id and services; a dependency's caller, callee, the attribute that named it, and its failure shares and p95s; a release id; another record's id. The design said links are built only by the console; the API only says what the data is. Never message text or exception type.
- **A dependency link's slow-call threshold is the normal p95** (`min_duration_ms` = `p95_before`), and a failure move wins over a latency move. A `slower` finding's traces link uses its worst reading, as the design says.
- **The address window is ignored past 31 days**, and on any path but the six, so a stray `from` elsewhere changes nothing.
- **The follow-up's words are built from the cause's deploy fact**, not the page's header segments: "A likely place to look on checkout: the deploy of checkout v2.1, 4 min before this (3 deploys in 7 days). See the finding's page for the current reading." A service name failing `[A-Za-z0-9._-]{1,80}` reads "a service" as the design says; **a version failing it reads "a version"** (the design names only services). The base-rate words moved to `nexovue_platform.evidence.deploy_rate_words`, so the page and the message say it one way.
- **A follow-up is dropped at delivery when its finding has closed since** (FR21's "nor once the finding has closed"), and when no likely-and-verified version stands. Any composer that raises drops only its own row (AC78), so a bug in one kind cannot hold back a batch: this applies to every kind, not only `explained`.
- **One alert with follow-ups in a batch reads as that alert**, with each follow-up as one more line; follow-ups alone read "N findings have a likely place to look".
- **The channel switch is on the channel's own page**, beside quiet hours, not the list (`ChannelsScreen.tsx` as T2.6 named); its test is in `ChannelsScreen.test.ts`, which mounts both.
- **`/explanations` has its own router module** (`nexovue_api/explanations/router.py`): in the findings router it inherited the telemetry rate-limit tier, which the coverage test holds to every route in a file that reads ClickHouse; these read Postgres only.
- **A rule counts only where it was shown:** a held cause (FR50) counts as no cause on `/settings/explanations`, as the page shows it; so in production every rule reads 0 until the bar is met.
- **The rating's "Yes" carries no reason and "No" always one**, a `CHECK` on the table.
- **An incident's "Look next" shows on every incident page**, after its evidence, not only those with no finding: the API does not say whether one is linked, except a burn's.

### Step 1, built 2026-11-09 (on `feature/phase-2`)

**What a person sees.** Under a finding's title, one line says where to look first: a likely cause by rule, "The facts that line up with this are below" while causes are held (FR50), "Not enough evidence to name a likely cause" only when every read completed, or "Could not finish reading the facts, so no likely cause yet". The right column now leads with *What lines up with this*: F28's *Release before this* as its first row (T1.15, A45), "Look next" (traces, logs, errors, deploys, infrastructure; not yet window-aware, T2.3), the numbered facts with what each rests on, the setup step when nothing was found, and "Read the facts again" for members and up, once a minute. With no AI anywhere, every new finding gets a frozen explanation within a minute of the job running.

**What was built.**

| Task | What | Where |
|---|---|---|
| T1.0 | `door` (`server`, `browser`) and `server_peer_attr` on `service_edges_1m`, carried from the envelope and the caller's span. A row is `browser` when either span came through the browser door. `door` joins the sorting key at its end; `server_peer_attr` stays out, read with `max` (A84's shape, also in `design.md`) | `serviceedges/`, `traceassembly/stages.go`, `flatten.go`; ClickHouse `000040` |
| T1.1 | `finding_explanations` (with `wording_generation` and `read_again_at`), `evidence_error_group_reads`; deletion lists; the hour's expiry | Migration `0066` (on `0065`, the coordinator's), `models.py`, `organizations/tables.py`, `evidence/retention.py`, `jobs/expiry.py` |
| T1.2 | The evidence contract (queue, task names, `gather_evidence_request`, kinds, bases, rule names, `CAUSE_BAR_RECORDED` and `RELEASED_RULES`, `display_name`), `ModelFacts` and `Alias`, the alias book; `ai_available`; `SHOW_HELD_CAUSES`, refused in production by both settings | `nexovue_platform/evidence/`, `deployment.py`, both `settings.py` |
| T1.3 | Edges (server door first, the bound inside the statement), traffic, new error groups (A62), hosts (FR52), under 10 s and 256 MiB | `evidence/store.py`; `evidence` in `PROJECT_BOUND_PACKAGES` |
| T1.4 | Postgres reads: finding, other open findings, incidents, checks (name and state only), the intake (self-hosted, project findings); F28's `coverage`, `release_before`, `releases_near`, and `deploys_in_window`, `releases_seen_in_window` (see *Open decisions*, 1) | `evidence/reads.py`, `releases/facts.py` |
| T1.5 | The rules (FR8 to FR12) and the sentences, pure | `evidence/rules.py`, `sentences.py` |
| T1.6 | The task: a session lock per finding, each read on its own, a failed read an absence and one re-read in 60 s, the fingerprint (FR43), versions, `model_input` with aliases | `evidence/gather.py`, `inputs.py`, `jobs/registry.py` |
| T1.7 | Gathers queued in the opening's own transaction (opened, settled at 300 s, changed, closed by every path including adoption) and from the deploy webhook per new item; `evidence` on `scheduler-light`, capped at 2 under F19's floor | `detection/project_job.py`, `deploys/service.py`, `deploys/queries.py`, `jobs/worker.py`, `__main__.py` |
| T1.8 | The evidence job and its contract walked for AI imports; `nexovue_platform.ai` and `nexovue_scheduler.insight` denied before they exist; no probes client | `test_detection_has_no_ai.py`, `import_walk.py` |
| T1.9 | Confirmed: the scheduler tenancy test walks `evidence/`. No new exemption: `intake_refusals` is already install-wide | `test_scheduler_postgres_tenancy_structure.py` (unchanged) |
| T1.10 | `GET /findings/{id}/explanation` (`?version=`), `POST .../read-again` (409 with `next_read_at`), `raw_kept_until` on the finding, `likely_cause_sentence` on a person's feed rows, `Reader.is_person`, the held cause | `findings/explanations.py`, `explanation_queries.py`, `router.py`, `schemas.py`, `service.py`, `auth/dependencies.py` |
| T1.11 | The section, the header line, polling (5 s for 15 minutes, then 60 s while open), Look next, read-again, the setup step, the shared-cause count, held facts on a failed refetch | `features/findings/explanation.ts`, `ExplanationSection.tsx`, `api.ts`, `FindingScreen.tsx` |
| T1.12 | `just explain-demo --rule N`, dev only, saying plainly that detection did not judge the finding | `tools/detection-seed/explain_demo.py`, `justfile` |
| T1.13 | Detection and its alerts compared across Step 1's five arms, and a failed `jobs` insert aborting the opening loudly | `test_detection_unmoved_live.py` |
| T1.14 | `REQUEST_EXPLANATION` in all five copies, plus `test_auth_routes.py`'s list | `permissions.py`, `policy.py`, `test_policy.py`, `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, `team-and-api-access/spec.md` |
| T1.15 | F28's section folded in as the first row; its tests (AC40, AC43, FR33) re-run through it and pass | `FindingScreen.tsx` |

ADR-039 (an explanation is a frozen evidence record, with code choosing the cause) is filed. The other three proposed ADRs wait for the evidence their own steps produce (the AI role, Step 3; the provider interface, Step 4); the floor is already ADR-028's.

**Decisions taken while building** (technical, recorded here; none changes product behaviour):

- **000040's down keeps `door`.** ClickHouse refuses to drop or rename a sorting-key column, and a migration here is one statement, so the down drops `server_peer_attr` only. The kept column is harmless to the code before it (its writer names its columns), and running 000040 again changes nothing but `server_peer_attr`. Proved by up, down, up on `f3s1-ch`.
- **`evidence_error_group_reads` cascades from `projects`** (`REMOVED_BY_CASCADE`), not `DELETED_DIRECTLY` as the design listed: it has a project key with `ON DELETE CASCADE`, and the deletion test holds that.
- **"Once a minute" is stored as `finding_explanations.read_again_at`.** The design named no store; the rate limiter cannot be read without spending, and the page must show when the next read is allowed. With no version yet, a click is deduplicated by `ENQUEUE_UNLESS_WAITING`.
- **`WORDING_REASONS`** is the design's own outcome names (`died`, `expired`, `run_cap`, `too_long`, `bad_key`, `unavailable`, `limited`, `refused`, `invalid`), frozen in 0066; Step 5 widens it by a migration if it needs more.
- **The error-group read is reused within its minute,** keyed by the window's start minute, so a later read never reuses a stale opening read.
- **Host disk is per filesystem** (used over the sum of its states), not the node total the screen shows, so a device mounted twice is never summed twice.
- **A deploy's base rate counts one rollout once:** a version's `started` and `finished` events inside one 30-minute lookback are one deploy. A week of records is known from F28's coverage a week back.

- **2026-11-02.** [`research.md`](research.md) was already in place. Written from it, the foundation, the F6 documents and a fresh reading of the code:
  [`spec.md`](spec.md) (106 acceptance criteria), [`design.md`](design.md) (with four proposed ADRs), [`plan.md`](plan.md) (seven steps and a founder gate after Step 2).
- **What reading the code added to the research:**
  - A check has no service, so the research's rule 3 cannot be built as written (`data/postgres/models.py:492-531`, A17).
  - Browser spans reach the exact counts: the research's open question is answered, by reading (`otlp/signals.go:176`, `traceassembly/stages.go:39,64`).
  - A backup restore drops all grants, so the AI account's grants live in `setup`, which runs after every restore (`deploy/backup/restore-postgres.sh:43`, A24, A34).
  - The address guard sees only an address, so `AI_PRIVATE_ENDPOINTS` cannot hold hostnames (`probes/addresses.py:139`, A23).
  - All window reads go through one hook, though each screen must keep the window when it writes (`shared/selection.ts:342`, A12).
  - The AI role can be kept from even reading names, by column grants (A19). A stronger position than the foundation asks for.
- **79 decisions taken,** A1 to A79, recorded in `spec.md`, *Decisions taken*, and each marked as the founder's to overrule.
- **Eight persona reviews, 125 findings,** each checked and recorded below.

## Next action

Founder: review Step 4's diff and open decisions 9 to 11; commit after Step 3. Then, with a free-tier key, `AI_EVAL_KEY=... uv run python tools/ai-eval/real_call.py --format openai_chat ...` once per format that has an account, and add the printed line to `real_calls.py` in a reviewed change: that is what offers a format on `/settings/ai`. Step 5 next.

Earlier (Step 3):
Founder: review Step 3's diff and open decision 7; commit after F22 Step 6. Coordinator: first run of `just insight-fence` by hand (expect fixes to the harness), which ticks T3.4, T3.10 and T3.11 and settles ADR-040's owed evidence. Then Step 4.

Earlier:
The founder reviews Step 2's diff; then commit. The founder's checks after Phase 2: send a test finding to Slack and email and read the follow-up (Step 2's *Judgeable*; A55 can be overturned there), follow each link from a demo finding and copy one into a private window, and the Playwright checks at 390 and 1920 pixels. T0.1 and T0.2 are still unticked and not part of Step 2. F22's T2.9 (the `explained` timeline event beside F3's row) can now be built, since `explained.write` exists.


## Open decisions

**From Step 4, 2026-11-11:**

9. **The dev stack has no `AI_SECRET_KEY`** (`DEV_AI_SECRET_KEY` defaults empty on `api` and `scheduler-insight`), so `/settings/ai` on `just dev` says "AI is not available: AI_SECRET_KEY is not set" until a developer sets one in `.env`. A committed dev literal, as `DEV_CONNECTION_SECRET_KEY` has, needs a `.gitleaksignore` fingerprint, which names the commit, so it is a follow-up commit. **Recommendation:** commit a dev literal on both services, then add its `.gitleaksignore` line in the next commit, as F22's key was.
10. **Mute and project deletion do not yet stop queued wording** (plan T4.5 points to T5.11). No wording run exists before Step 5, so nothing can be sent meanwhile; the opt-out route already does it. **Recommendation:** keep it in T5.11, with the `word` job's own re-check.
11. **The admin's consent view shows no owners' names** (design, *Consent*: "with owner names and `CopyButton`"); it says "Waiting for an owner" and copies the page's link. **Recommendation:** add the active owners' display names to `GET /ai/connection` for `MANAGE_AI` in the hardening pass; it is a read, no new risk.

**From Step 3, 2026-11-11:**

7. **Two holes the fence cannot close with grants** (spike T3.1, measured). (a) **`NOTIFY` needs no privilege.** A broken-into AI process can notify, and a listener of its own that never reads can fill Postgres's notification queue (8 GB by default), after which every notifying commit in the database fails: Run now, send tests, read-again, connection syncs. Alerts and beat's schedule do not use it. (b) **The timeouts are defaults.** The account can `SET statement_timeout = 0` (and the idle and transaction timeouts) and hold a transaction open: that holds back vacuum for the whole database, so `jobs`, the busiest table, bloats over hours, and a migration that alters a table it read waits and fails its lock timeout, so `setup`, and with it every service's start, fails until the process is stopped. **Options:** (i) a watchdog on beat's leader, every minute, ending any `nexovue_insight` backend whose transaction is older than a bound (2 minutes) or that has been listening, with `pg_terminate_backend` as the install account, plus an alert; (ii) accept and document (done in `docs/operations/ai.md`); (iii) give the AI role no Postgres at all and pass its rows through the API (design's FD2 rejected this). **Recommendation: (i)**, built with Step 5's sweep (T5.5) and its metrics (T5.10), which already run on beat's leader; it bounds both holes to two minutes without a design change. The rest of Step 3 does not depend on it.
8. **Migration 0070's down revision is F22's 0069,** which is not on this worktree's branch: `test_setup_schema.py` and `alembic upgrade` fail here (`KeyError: '0069'`) until F22 Step 6 is committed first, as the coordinator plans. **Recommendation:** commit F22 Step 6 first, then this; nothing to change here.

**From Step 2, 2026-11-09:**

5. **AC104's second and third dates have no source** (FR55: "Deploy causes can reach 'likely' from {date}" and "traffic drops from {date}"). The first needs the project's first deploy record, the second when detection starts judging traffic drops; no route gives either (`/releases/coverage` gives a per-service last report; `DetectionStatus` gives only `earliest_judging_date`, which is built as "First findings from {date}"). **Options:** (a) add `first_deploy_record_at` (the earliest signed deploy event) to F28's `/releases/coverage` and a traffic-judging date to F6's detection status, then finish T2.13; (b) say the rule without dates ("once deploy notifications have run for 7 days"), as built now. **Recommendation: (a)**, a small change to two other features' routes, which this step may not make; T2.13 stays unticked until then.
6. **AC23's token refusal on the rating** is held structurally (`test_authorization_coverage.py`: the route's gate needs a signed-in person, as every token-refused route's does), not by a live request: the explanation test harness overrides the reader and cannot run the real identity dependency for a token. **Recommendation:** accept; the same holds for Step 1's read-again.

**From Step 1, 2026-11-09:**

1. **F28's `deploys_in_window` and `releases_seen_in_window` did not exist** (they are F28's T5.2, Step 5), yet T1.4 and A71 need them: rules 1, 3, 4 and the base rate read signed deploys, rule 5 seen versions. Built in F28's `releases/facts.py` by F28's published signatures (D29, *Signatures, for F3*), signed events in `OPENING_STATES` only, no sender text. **Recommendation:** accept them as F28's; F28's T5.2 keeps `release_comparison` and the AC45 allow-list test, and adds its own `test_release_facts_live.py` cases for these two. F28's owner should confirm.
2. **FR11's two project rules name no strength.** Built as `possible` for both (a deploy to half the services; a shared dependency with its own finding). **Recommendation:** keep `possible` until the bed scores them; neither sends `explained`, which needs `likely`.
3. **AC14's live arm, spans through the real receiver with a browser key,** needs the dev stack's receiver, Kafka and workers, which this build did not run. The worker's part is proved by unit and store tests. **Recommendation:** run it on the dev stack as part of Step 2's `just dev` session, and build it then.
4. **T1.3's error-group read at a stated volume on the 16 GB install** ("a timeout there fails this task") cannot be measured here. **Recommendation:** measure on the Mac's dev stack with `tools/detection-seed` volume before Step 2 is reported, and on Oracle after deployment (foundation item 9).

**Earlier: none for the founder.** Both gates and the test server were answered on 2026-11-02 (*Founder answers, 2026-11-02*, below). The go or stop is still read against its approved rule after T2.11, and the bar is met or not only after Step 2c's runs.

## Own questions answered, 2026-11-02

Every open question in this feature's documents was checked. Decided by the authors, recorded in `spec.md`, *Decisions taken*:

- **A81. The local-model test runs on the founder's Mac,** an M4 Pro with 24 GB, replaying recorded `model_input` with the dev stack stopped, only after a go. No money is spent. It proves a named model on that Mac, not on a customer's server, and the wording says so. If no model that fits passes AC63, a rented GPU comes back to the founder as a money question. Plan T6.3 and the validation plan updated.
- **A82. AC76 has no separate bar.** The 42 payloads pass or fail. Forged-cause counts count as wrong causes inside AC62's bar.
- **A83. The accuracy bar is written before the report rounds,** so it cannot be fitted to the results. Plan T2.18 updated.
- **A84. The edge door's shape is the developer's call,** recorded in the design; only dropping or delaying the door returns to the founder. Plan T1.0 updated.

Also: the founder's list in `spec.md` now holds only the two gates, each with a recommendation (a concrete bar for the second), FR30 and FR31 point to foundation items 2 and 1, and `design.md` and `plan.md` point to the approved items instead of asking again.

**Review, 2026-11-02.** A skeptical panel checked each answer against the code, the foundation and the web. Most held. Changed:

- **A81 now answers decision 0047** (`decision-log.md:247`, flagged: Ollama will not run usefully on the dev machine). The answer had contradicted it without saying so. 0047 assumed the demo runs beside the model; A81 stops the stack and replays. Memory re-checked: `sysctl` reads 24 GB on an M4 Pro, Ollama's MLX page still asks for more than 32 GB, and `qwen3:14b` is about 9.3 GB (third-party listings). If the first replay proves 0047 right, the GPU fallback returns to the founder as money.
- **New A85, decided here: causes are released rule by rule,** through a list beside `CAUSE_BAR_RECORDED`. The earlier text asked the founder to approve this mechanism; it is a technical choice, and the design already has a customer-facing per-rule switch. AC92, T2.18 and the design's evidence contract updated.
- **The bar's recommendation was made exact.** "1 wrong for every 10" did not say what a rule stating 5 to 9 causes may get wrong. Now: at most one tenth, rounded down, so none for 5 to 9. It also repeats AC62's warning that a deploy rule's pass on the bed proves it fires, not that it is right.
- **The go-or-stop now says when the rule is fixed:** before T2.11's calls, so the result cannot bend it (A61 already allowed a change only then).
- *How we will judge it* said the bar comes "from the first results", which read as after the report rounds. Now points to A83.

Held up: A82, A83, A84, the six pointers to approved foundation items, the test server's price staying in `phase-2-plan.md`, and the plan table staying an assumption. Both founder gates stay the founder's: the first decides what is built and sold, the second when the product first tells customers a cause. Not edited, outside this folder: foundation item 2 still points to "F3 item 2" (now A81), and `decision-log.md` row 0047 should later point to A81.

## Blocked

Nothing is blocked now. Implementation cannot start until the three gates pass, F6 has merged, and F28's readers exist. Steps 3 to 6 no longer wait for the founder's written go: **Founder decision, 2026-11-09 (foundation item 30):** Step 2b's stop-or-go is read after Phase 2, and Steps 3 to 7 are built now. **Step 2c's runs, T2.17's report and T2.18's check wait for Oracle after Phase 2 is deployed** (foundation item 9); until then no likely cause is stated.

## Reviews

### Second review, verified and fixed, 2026-11-02

A later review raised 17 findings, 10 major and 7 minor. **All 17 were checked against the cited code and documents, and all 17 held.** One detail differed: the Azure page has now been read, which is better than the finding asked for, though the older route's rule still rests on a search summary. Nothing was run and nobody re-reviewed this result. T3.11's install harness and the new `server_peer_attr` column are designs, not working code.

| # | Severity | Finding | Checked | What changed |
|---|---|---|---|---|
| 1 | Major | A `seen` version could be the stated cause | doc | `seen` maps to `telemetry_text`, and rule 5 is support only (A75). FR5, AC8, design *Basis*. Now agrees with F28's D54, so no difference row |
| 2 | Major | On a stop, nothing could release causes | self | Tuning, the rules-only report and the bar moved to T2.17 and T2.18 in Step 2c, which runs on either decision (A79). Step 6 keeps model runs. The test server's price still needs approval on a stop |
| 3 | Major | T1.9 named the API's test | code, doc | F28's real file name in T1.9 and the design |
| 4 | Major | `just insight-fence` could not run `nexovue restore` | code | T3.11 really installs a local kit, takes a snapshot and restores (A78). Whether `compose pull` passes is left as a stated check |
| 5 | Major | `just e2e`'s "smoke flow" does not exist | code | Named specs, a new `non-ai-screens.spec.ts`, and `just e2e-insight-stopped` on the dev stack (A77, AC48, T5.9). No CI runs Playwright, stated plainly |
| 6 | Major | The AI account could not run the sweep's `expired` check | self | `wording_changed_at` added to the `SELECT` grant; a live case runs the sweep as `nexovue_insight` (AC81, T5.5) |
| 7 | Major | The dependency link returned nothing on a normal edge | code | `service` the callee, the caller's attribute for told-only callees, a labelled fallback, and `server_peer_attr` (A76, AC105, T1.0, T2.4) |
| 8 | Major | AC99, `group` and the override had no task for their files | code | T2.12, T2.4, T1.2 |
| 9 | Major | AC47's memory proof sat behind the go | code | Moved to T1.6. T3.6 keeps the insight child's check |
| 10 | Major | Tests under new tools folders would never be collected | code | `pyproject.toml` in T0.2 and T2.15 |
| 11 | Minor | F3's account of F19 was out of date | doc | A56, A38, NFR2, AC47 and the design re-argued from three queues; the F19 row removed. **The floor stands, but its reason is weaker than first stated:** without it probes keep 2 of 8, not zero. 4 remains a guess |
| 12 | Minor | Citations into other features were wrong | doc | Section names and task ids in `design.md` and in this file |
| 13 | Minor | Founder section's count, and two leftover fragments | self | Count corrected, FR5 and FR11 fragments deleted, citation script re-run |
| 14 | Minor | Three undeclared differences from the foundation | doc | Design rows 31 to 33, three rows in the spec's list |
| 15 | Minor | AC48 and AC42 could not be tested as written | code | AC48's "ClickHouse down" arm redefined; AC42's peer defined |
| 16 | Minor | Connection limit, `requested_by`, beat-lock state, a placeholder | code, arith | Limit 20 (A58); `requested_by` in `word`'s arguments; a *Beat lock exposed* state; new AC106 |
| 17 | Minor | "11.5 weeks", two unsourced claims | code, web | Plan wording corrected; three URLs added, with Docker Hub and the Azure v1 page read 2026-11-02 |

### First review: eight personas

Eight personas reviewed `spec.md`, `design.md`, `plan.md` and `research.md`. Each finding was checked against the code, the vendor's page or the other document before anything changed. **Nobody re-reviewed the result.** The verdicts below are what the reviewers said before these changes, not a judgement of the documents as they now stand.

**Result: 125 findings. All 125 led to a change, and none was rejected whole. Thirty had a part rejected, listed below.** A finding counts as fixed when the documents now answer it, meaning a requirement, design and test are written. It does not mean anything works, since nothing is built and nothing was run. Many findings repeated each other across personas, and one change answers them all: the table shows which.

Reviewers' line numbers point at the earlier revision and have moved.

### Verdicts

| Persona | Verdict at review | Findings | Blockers |
|---|---|---|---|
| Architect | Ready with fixes | 14 | 1 |
| Developer | Not ready | 17 | 4 |
| UX | Ready with fixes | 21 | 0 |
| Sales | Ready with fixes | 12 | 0 |
| Marketing | Ready with fixes | 14 | 0 |
| Customer | Ready with fixes | 16 | 0 |
| Security | Ready with fixes | 12 | 0 |
| QA | Not ready | 19 | 2 |

### Conflicts between personas, and how they were decided

The task named a decision rule without stating it, and none was found in the repository under that name. The authors used the one F28's status recorded, drawn from the run's standing rules: the correct end state first, then what a tired engineer can trust, then cost. The founder may overrule any row.

| Conflict | Decided | Why |
|---|---|---|
| `explained` must fire on the common path (Architect 2, Developer 1, blockers); it mostly repeats F28's opening line, so send it only when that line did not name the deploy (Customer 3) | Always one message, on any read after the settled time, carrying the rule's judgement and the base rate (A55) | F28's line is left out of grouped messages, and says order only. Step 2's judgement can overturn this |
| A function for `DELETE`, keeping row-level security (Architect 5); no AI rule on alert jobs (Customer 13); keep the foundation's row-level security | Four functions and no table rights (A57) | It closes `LOCK TABLE`, puts nothing on the alert queue, and keeps the foundation's stated end |
| A group cap or `light` first (Architect 1); F19's spec then raised to 12 slots with a summing test, and has since moved to a role of its own | A floor shared by all capped queues (A56) | Claiming first cannot free a slot long jobs hold. A floor needs no re-counting |
| Narrow the error-group question (Architect 11); fill the seen-groups record for every project (Customer 11) | Narrow, one earlier hour, and worded so (A62) | A per-minute read on every project for a fact that only supports a cause |
| If F28 is late: an absence and Step 1 not complete (Architect 9); a hard precondition (Developer 12); "partial" (QA 13) | Hard precondition (A71) | The foundation orders F28 first, and the rule and message cannot be tested without it |
| "Sent" proof: prompt version (Architect 14); plus a hash (Sales 10, Security 8) | Both (A74) | A hash holds no content, and lets the screen state a match as fact |
| Holding the cause: a plan rule or a switch (QA 12); a switch (Sales 3, Marketing 6, Customer 6) | A named constant, with a non-production override (A60) | A rule on paper cannot hold across six steps, and the founder must still see causes to judge Steps 1 and 2 |
| Score the rules before any AI spending (Sales 3, Marketing 6); stop-or-go right after Step 2 (Architect 6 and four others) | Stop-or-go first; scoring starts at the same time, and holds any release | The bed takes weeks, and the spike costs little and asks another question |
| How to answer about Coroot: no edition facts (Sales 5); with edition facts (Marketing 7) | Facts about Nexovue in the script; Coroot's, dated, in research section 4 | The foundation bans naming rivals in public, and buyers do ask |
| Selling line: Sales 2 and Marketing 2 wrote two versions | Merged, with fixed labels (Marketing), scoped to explanations with chat named (Sales) | It stays true when F4 arrives |
| Admins told on the page, one line (UX 14); email (Customer 14); screen first (A53) | One line for a system-caused off; email only for acts that may move data (A53, A67) | The AI role holds no email key, and a failing provider harms no alert |
| Bedrock: sign or remove (Customer 7); hide until a real call (QA 18); three formats (foundation) | Format built, hidden until signing passes and a call is on record (A59) | Keeps the foundation's India route without offering what its own screen warns against |
| Connection limit of 8 (Security 2); F4's slots | 20, built on arithmetic from the pool sizes; first written as 16, which was short (A58) | 8 could refuse the role's own children |
| Who may stop a rule: `MANAGE_AI` (Customer 8) | `CONFIGURE_DETECTION`, on `/settings/explanations` (A64) | It must exist if AI is stopped, and it is not an AI matter |
| Recipe name: `insight-fence` (Developer 10), `install-layout-check` (QA 9) | `just insight-fence` | One recipe does both |

### Parts of findings rejected

No finding was rejected whole. These parts were, and nothing else was dropped.

| Finding | Part rejected | Why |
|---|---|---|
| Architect 1 | Correct F28 and F19 in the same change | This run may not edit another feature's folder. F28's correction is listed in `design.md`, *Specification impact*. F19 needs none: its spec now gives SLO work its own role (its D39, AC68) |
| Architect 3 | `wording_from_version` NULL meaning "this row" | Its own version number is simpler, and one meaning is enough |
| Architect 4 | A `wording_held` flag | The mute route setting `held` does the same. The small race is accepted in writing, as the finding allowed |
| Architect 5, Security 2 | A function for `_FINISH` alone; revoking advisory functions from `nexovue_insight` alone | All four statements become functions (A57). A grant to `PUBLIC` cannot be revoked per role, so it is revoked from `PUBLIC` |
| Architect 9, Developer 11 | Removing `release_context` | That name is no longer in F28's design. F3's own line naming it is corrected; the finding-page section is what folds in |
| Architect 13 | That no one owns the rise past 384 MB | F4's T2.3 already takes it to 576 MB (`ai-chatbot/plan.md`, T2.3). The rest stands |
| Architect 11, Customer 11 | Filling `error_groups_seen` for every project | A62 |
| Developer 4, QA 1 | Regenerating the plan whole with AC-to-test tables | The task list was rebuilt, and each AC is cited by a task naming its test. T0.2 enforces it |
| UX 7 | A fallback for when `/releases` does not exist | F28 is now a hard precondition, so it does |
| Sales 3, Marketing 6 | Rules scored before any AI spending | See conflicts. Scoring starts then and holds releases |
| Sales 7 | A backfill over past incidents | The frozen record needs reads made at the time. Stated plainly, and out of scope |
| Sales 11 | Testing one Azure endpoint now | A documents-only run. Listed as untested. The v1 page was later read through a summarizing tool; the older route's rule rests on a search summary |
| Marketing 4 | Treating OpenObserve's edition terms as settled | The 50 GB figure and how credits apply to self-hosted stay labelled as unresolved |
| Marketing 11 | That HolmesGPT is unconfirmed on any official URL | Its repository's evaluations page links to results, read 2026-11-02. The numbers themselves were seen only on preview hosts, so none is quoted |
| Marketing 14 | "$20 for each further user" from the invoice page | That page, read directly, gives the three included users and points to pricing for the rate. Kept as an earlier reading, not re-read |
| Customer 1 | Deploys and check changes as facts on an incident page | That needs a second record, which the foundation leaves to later. Links only (A66), stated out of scope |
| Customer 3 | Sending only when not already named; a PagerDuty line through `noted` | A55. The foundation says no follow-up on PagerDuty, and a trigger on a closed key could open a page; F28's line already reaches it |
| Customer 4 | Recording the risk in F6's documents now | This run may not edit that folder. It goes to the founder in `spec.md` and here |
| Customer 5 | A term glossary as a planned task | Noted at the gate as worth adding. T2.10 can show whether people need it |
| Customer 7 | Signing or removal decided now | A check first (T4.9), since it rests on an unverified option |
| Customer 10 | The finding page fetching the group's template | The link reaches it on the Errors screen, and A47 stands more simply |
| Customer 13 | A separate queue table | The functions leave the alert queue untouched with less moved code |
| Customer 14 | Email on a run's first failure | The AI role holds no email key (BR11), and a failing provider harms no alert. Stays on screen |
| Security 1 | A 15-minute `deployed_at` rule inside F3 | The hole is real in today's code (`deploys/service.py:95-101`), and F28's D6 closes it with a signed id and bounded signed time, which is stronger. F3 rests on F28 and tests it (AC93) |
| Security 2 | A connection limit of 8 | See conflicts |
| QA 17 | A non-superuser enqueuing under a policy | With no policy (A57) the risk goes. The test stays, to prove none is added |

### What was changed, finding by finding

Checked against: **code** (read at the cited path), **arith** (redone by hand), **web** (vendor page read 2026-11-02), **doc** (another repository document), **self** (these documents as they stood).

**Architect**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Probes can get zero slots (blocker) | code, doc | Floor; A56, NFR2, AC47, design row 24, T1.7 |
| 2 | `explained` not sent on the common path | self | Gather steps 7 and 8, FR21, AC25, T2.7 |
| 3 | AI account cannot set `wording_from_version` | self | Grant, step 7, AC73 |
| 4 | Versions can be stranded | code, self | Click row, sweep's `expired`, `wording_generation`, mute row, AC81, T5.5, T5.11 |
| 5 | AI process can stop alerts through Postgres | code, web | A57, A58, grants, *Beyond tables*, AC43, AC44, T3.1, T3.3 |
| 6 | Stop-or-go missing | self | Step 2b, T2.10, T2.11, A61 |
| 7 | Steps depend on later work | self | `model_input` in T1.6, `ai_available` in T1.2, aliases in `evidence/`, action and read-again in Step 1 |
| 8 | Required work has no task | code, self | T1.1, T1.7, T2.3, T2.6, T2.8, T2.12, T3.2 |
| 9 | Reads do not match F28 | doc | Reads table, Dependencies, A71, A72, T1.4 |
| 10 | Rule 2 has no tie | code | Rule 6's `same_minute`, A73, AC6 |
| 11 | Error-group read likely to time out | code | A62, statement spelled out, T1.3 fails on a timeout |
| 12 | Intake rule rarely fires, and speaks for the install | code | FR11, AC13, Reads row |
| 13 | 384 MB leaves no room for F4 | code, arith, doc | Per-child limit, T3.10 at 4 and 6, NFR3 |
| 14 | "Sent" view can show what was not sent | self | A74, FR29, AC36 |

**Developer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | `explained` never sent (blocker) | self | As Architect 2 |
| 2 | Step 4 cannot be judged (blocker) | self | T1.6, T4.5, T5.4 |
| 3 | No stop-or-go (blocker) | self | Step 2b; plan's spending text; Questions |
| 4 | 15 ACs have no task (blocker) | script | Every AC cited; T0.2; counts here |
| 5 | Design elements in no task | self | T1.2, T1.7, T1.11, T2.4, T2.6, T2.8, T2.12, T2.13, T4.1, T5.1, T5.10 |
| 6 | Read-again and rating cannot ship in Step 1 | self | T1.10, T1.11, T1.14, T2.12 |
| 7 | Step 1's recipe has no working command | code | T1.12, `just explain-demo` |
| 8 | T2.3 undersized, wrong proof | code | T2.3 |
| 9 | T3.4 contradicts the design | code | T3.4, A24, AC46 |
| 10 | T3.8 and T3.10 cannot be done | code | T3.8, T3.11, design *Network* |
| 11 | Duplicates F28 | doc | T2.1, T1.9, T1.3, T1.15, A72 |
| 12 | F28 lateness stated two ways | self | A71, Preconditions |
| 13 | `aliases.py` in the wrong package | self | T1.2, T5.1 |
| 14 | Cap breaks at-most-once and races | code | `explained.write`, lock, AC25 |
| 15 | BR1 unproved until Step 5 | self | T1.13, AC48 |
| 16 | Lock wait can lose a read | code | 30 seconds, 8 attempts, T1.6 test |
| 17 | Account omissions | code | T3.1, T3.3 |

**UX**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Failed reads read as "looked and found nothing" | doc | *States*, FR45, AC11, AC87 |
| 2 | Header undefined before first read | code | Header column, reserved space, AC86 |
| 3 | "Look next" missing from states | self | FR42, *States*, AC66, built from `FindingDetail` |
| 4 | Nothing to tap in the first screen | code | FR14, layout, AC70 |
| 5 | "Likely" with no sign of forgery | self | "from telemetry only", AC67, design example |
| 6 | Back link cannot work on a copied link | code | `finding` value, AC21 |
| 7 | Two links ignore the window | code | `/infrastructure`, label, T2.3, AC21 |
| 8 | Old words beside a new cause | self | FR19, AC89, design |
| 9 | One failed poll wipes the facts | code | Stale state, AC88 |
| 10 | `/settings/ai` has no state criteria | self | Settings table, NFR4, AC90, T4.7 |
| 11 | Screens with no task | code | T1.11, T2.4, T2.13 |
| 12 | Too little deploy history reads false | self | FR7, words, AC98, days covered |
| 13 | Member checklist exposes admin detail | code | Status row, AC85 |
| 14 | A53 conflicts with the design | self | A53, FR26, row 26 |
| 15 | No too-soon state | code | 409, FR41, AC80 |
| 16 | "Runs today" is wrong | self | FR41, error table, layout |
| 17 | "Not enough evidence" twice with two meanings | self | Wording state's words |
| 18 | Card drops the base rate | self | FR18, card, AC67 |
| 19 | Rating has no words | self | FR20, words, AC75 |
| 20 | Section names compete | doc | A69, heading |
| 21 | Phone targets and semantics | code | NFR4, layout, AC17 |

**Sales**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | False script on forging | self | Answer, A42, must-not-claim, AC76 |
| 2 | Selling line false twice | doc | The line, FR30, two new answers |
| 3 | May-say contradicts AC62 | self | Table, FR50, A60, AC92, T2.18 (first T6.6) |
| 4 | Commercial checks not in plan | self | Step 2b, T2.13, T4.10, T6.3, counts |
| 5 | Coroot material unsupported | web | Research section 4, answer, founder's note |
| 6 | Deploy rule works against frequent deployers | arith | FR9, table, setup docs, AC62's band |
| 7 | No time-to-value | doc | *What a trial shows*, FR55 |
| 8 | Fence claim too broad | self | Table, `docs/product/setup/ai.md` |
| 9 | Product copy still promises otherwise | doc | Specification impact, T0.1 |
| 10 | "Exactly what was sent" overclaims | self | UR12, A74 |
| 11 | No provider answer | web | Provider table, answer |
| 12 | No packaging | code | *Who gets what*, BR13, target users |

**Marketing**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | False answer on forging | self | As Sales 1 |
| 2 | Selling line untrue | self | As Sales 2 |
| 3 | No single positioning | self | *Positioning*, research, risks here |
| 4 | Only Coroot answered | web | New answer, research rows and two nearest rivals |
| 5 | Launch judgement cannot run | self | Step 2b |
| 6 | Cause rule has no mechanism | self | As Sales 3 |
| 7 | Coroot claims unverified | web | Research section 4, sources, founder's note |
| 8 | No warning on side-by-side reading | code, web | Publishing note, AC62's grouping |
| 9 | Two measures cannot be collected | self | *How we will judge it*, FR54 |
| 10 | "No paid edition" is a promise | doc | Table, must-not-claim, research |
| 11 | Research understates rivals | web | Research, design's foundation row |
| 12 | Catalog rename in the wrong place | self | T0.1, titles, risk row |
| 13 | First week undisclosed | doc | Table row, FR46, FR55 |
| 14 | Rival table too simple | web | Grafana, Datadog, Sentry rows; design's *Not checked* |

**Customer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Many pages get no explanation | code | FR53, A66, Out of scope, AC101, T2.14 |
| 2 | Folding F28 narrows the answer | doc | FR49, rule 13, AC91, difference rows |
| 3 | Follow-up repeats; PagerDuty misdescribed | doc, code | A55, base rate required, answer corrected |
| 4 | Browser key and false "Likely" | code, doc | A63, T1.0, marker, founder item 3 |
| 5 | I would not turn it on; no gate | self | Step 2b, A61, answer |
| 6 | Promised behaviour has no task | self | As Developer 5; T2.13; FR50; counts |
| 7 | Bedrock labelled not for production | self | A59, T4.9, Out of scope |
| 8 | Only the message can be silenced | self | FR54, A64, AC99, T2.12 |
| 9 | Common causes do not even appear | code | FR52, A65, AC100, abstention at Step 1 |
| 10 | Links land near the data | code | FR16, link table, AC105 |
| 11 | New-error fact may be unavailable | code | As Architect 11, T1.3 |
| 12 | Time to value not told | doc | FR55, AC104 |
| 13 | AI-only rule on my alert queue | self | A57, AC71 |
| 14 | Not told when AI stops or moves | code | A67, FR26, AC102 |
| 15 | No clean "AI is inert" answer | self | `SCHEDULER_INSIGHT_REPLICAS`, AC103, setup doc |
| 16 | "Builds for either" overstated | self | Open decisions, spec's carried line |

**Security**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Signed deploy body can be replayed | code, doc | Basis, Attacks, AC93, Preconditions |
| 2 | Account can stop detection | code | As Architect 5 |
| 3 | No address on the audit trail | self | `provider_host`, audit, column grant, AC72 |
| 4 | Nothing bounds one finding | self | FR51, *Bounds on one finding*, AC94 |
| 5 | Provider text reaches the screen | self | FR19, `CHECK`s, read mapping, AC51, AC65, AC96 |
| 6 | Tokens read model words | code | BR10, A15, A68, AC23 |
| 7 | Exception type holds free text | code | A47, BR16, Privacy, AC95 |
| 8 | "Exactly what was sent" has no record | self | As Sales 10 |
| 9 | Deleting a project does not stop wording | code | FR33, Wording states, `projects` grant, AC39, T5.11 |
| 10 | Compose test is a deny-list | code | AC41, T3.8 |
| 11 | Model and key unvalidated | code | FR23, AC34, T4.2 |
| 12 | Read-again's gate cannot ship | self | T1.10, T1.14 |

**QA**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | 15 ACs unbuilt (blocker) | script | As Developer 4 |
| 2 | Stop-or-go absent (blocker) | self | Step 2b |
| 3 | Regression proof comes late | code | T1.13, T1.7, AC48 |
| 4 | Compares rows, not messages | code | AC78, T2.8, AC48's e2e flow |
| 5 | `explained` reads as an alarm in a batch | code | Design, FR22, AC27 |
| 6 | Cap can send two | code | As Developer 14 |
| 7 | Deploy rules scored circularly | code | AC62, T2.15, *Not proved*, risk row |
| 8 | AC16(b) fails when detection works | self | AC16, T2.16 |
| 9 | Live proofs have nowhere to run | code | T3.11, T3.4, Validation |
| 10 | Requirements with no criterion | self | AC79 to AC85, AC81 |
| 11 | Headline never measured | self | AC97, AC47's limit, NFR1 |
| 12 | The bar has no enforcement | self | As Sales 3 |
| 13 | F28 dependency two ways | self | A71, T2.1, T1.15 |
| 14 | Canary and injection run can pass emptily | code | AC49, AC63, difference row |
| 15 | Step 6 order cannot give held-out results | code, doc | AC62, T2.17 (first T6.4), T2.15, A70 |
| 16 | AC53 contradicts BR13 | self | AC53, check path, T4.3 |
| 17 | AC14 and AC44 do not test their claim | self | AC14, AC44, T1.0 |
| 18 | AC77 cannot be met in that order | self | T4.10, AC77 |
| 19 | *Not proved* list is short | self | Four rows |

## Risks

**Added by Step 4 (2026-11-11):**

- **The address guard's promise widened** (ADR-047): a private address was reachable only in development; now an operator's listed addresses and ports open one for an AI connection. Loopback, link-local and ranges wider than /16 stay refused, and `public_allowed` keeps `http` off the internet.
- **Every probe now refuses a header holding a control character** (a tab still passes), where libcurl sent it before. A check whose saved header held a line break now fails with "A header holds a control character and was not sent", which is the safe answer, but it is a change in what an existing check does.
- **Until a real call is recorded, the settings screen cannot save a connection**, because it offers no format (AC77). The founder's first real call is what opens it.
- **Every field name in the three formats was read from documentation, not from a response.** A provider that differs fails as `invalid` or `bad_request`, never silently, and the real call shows it.

**Added by Step 2 (2026-11-09):**

- **The follow-up changes a path every people-read alert takes** (`_TAKEN`, `compose_batch`, the drop of an unwritable row). AC78's golden fixture holds every earlier kind's bytes on six channel kinds; a later change to an alert's words must rewrite it deliberately (`NEXOVUE_WRITE_OUTBOX_GOLDEN=1`).
- **Any composer that raises now drops its row** instead of failing the delivery task and retrying until the lease ends; the drop is logged with the row and kind, and the delivery log shows "not sent: this message could not be written".
- **Nothing in production shows a cause yet** (FR50), so the card line, the follow-up and the rule counts read empty there until the bar is met; their behaviour is proved only with the non-production override.

**What remains after the reviews.**

- **No real user was asked.** The largest gap. T2.10 now sits in the plan, before the costly work.
- **Nothing was run.** Every window, token, memory, time and cost figure is a guess. Coroot's prompt size, the only measured figure, is someone else's. Whether a column-level grant permits `LOCK TABLE` is from memory, for T3.1.
- **The rule set is untested on anything.** Fixed rules may be silent too often, or wrong. The bed's deploy scores are staged, so real accuracy on real bad deploys stays unknown, and self-hosted installs report nothing back.
- **The AI layer may add little.** With aliases and no free text, the model can only reword code's facts. The facts are useful either way, though evidence is the market's entry fee, not what sets Nexovue apart. A61 decides.
- **Forged spans.** A secret ingest key can always forge, and until F28's F6 change and A63's edge door land, a public browser key can too: it can move a service's numbers, could open a finding that sends its normal alert, and can put a false "likely" on the page. Do not claim "injection-proof". **F6 ships first, and its documents do not mention this yet.** This run may not edit them. The founder approved taking the change before Stage 1 reaches a customer (foundation item 7, 2026-11-02).
- **Where `setup` cannot revoke from `PUBLIC`,** a broken-into AI process could take the beat lock and stop scheduling. It is stated on screen. A broken-into process can always read every organization's provider keys, spend tokens and write words that fit the form.
- **Other documents are now out of step** until approval: F28 (slots, readers, edges), F4 and F5 (row-level policy), the foundation, the catalog, `vision.md`, `phase-2-plan.md`. Each is listed in `design.md`, *Specification impact*. This run did not change them.
- **The floor costs time in a wide outage.** With 4 slots for detection, `releases` and `evidence` together, facts wait longer. AC97 will show how much.
- **The job functions repeat four statements.** A parity test holds them in step.
- **A carried race:** a gather in flight can start one run just after a mute.
- **An owner's stolen account can send facts to an attacker's provider.** Aliases limit what goes, audit rows and an email record it. It is not prevented.
- **Sellers' answers name rivals' current facts,** which change monthly. Re-check before use. Azure OpenAI is untested, and OpenObserve's edition terms are unresolved.
- **A first-week trial may show little** beyond the sample, and it is now stated rather than hidden.
- **No likely cause at launch (2026-11-02, foundation item 9).** With no rented server, the rules are scored only on Oracle after deployment. F3 ships facts only until then, and the claim rows in `spec.md` stay unused.
- **Free tiers have low limits and may train on what is sent** (foundation item 4, changed). Runs take longer, and only synthetic data may go: the demo, the fault bed and the public payloads. Gemini's unpaid terms let Google use and people read inputs.
- **The local model shares the founder's 24 GB Mac** with the dev stack (decision 0047). A81's replay stops the stack first; if that fails, a GPU would be money, which is not approved.
- **"Supported" covers only what was tested.** Free tiers test the OpenAI-style adapter. Claude's own format and Bedrock's have no free API tier, so they stay "not yet tested" on the setup page until someone tests them, though customers use them most.
- **Oracle's free allowance is 2 Arm CPUs and 12 GB,** below the 16 GB minimum, and the bed's demo needs more. Measuring there needs a paid shape, which returns to the founder at deployment.

## Validation

### Step 4, 2026-11-11

Own container `f3s4-pg` (Postgres 18, `pgvector/pgvector:0.8.6-pg18-trixie`, port 25432, network `f3s4-net`), migrated with Alembic from a scratch copy whose 0070 pointed at 0067 (F22's 0068 and 0069 are not on this branch), then 0071. No ClickHouse was needed. Every behaviour test was seen failing first, or failing on a saved copy with its behaviour removed, then passing:

| Test | Seen failing by | Then |
|---|---|---|
| `test_ai_wire_formats.py` (97) | Saved copy without Claude's 200 refusal, the Bedrock model's path encoding, the `..` and query checks, the reported-model pattern: 6 failed | 97 passed |
| `test_ai_private_endpoints.py` (26) | Saved copy without the private and excluded check in `allows` and without the width limit: 3 failed | 26 passed |
| `test_http_check.py` (+4) | Control-character refusal removed: 4 failed | 9 passed |
| `test_insight_check_live.py` (11), as `nexovue_insight` | Saved copy following redirects, without the call-time FR23 check, opening the key for a fixed address, recording a result without the model in its guard: 7 failed | 11 passed |
| `test_ai_settings_live.py` (37) | First run: 4 failed on real faults (aliases read from the escaped body; a viewer's shape; an audit time compared with the pinned clock). Saved copy without key erasure, price clearing, the consent fingerprint check, the opt-out's pending-to-off, the unavailable refusal: 5 failed; the beat-lock flag hard-wired off: failed | 37 passed |
| `test_keys_live.py` (7) | `ai_connections.sealed_key` left out of `SEALED_COLUMNS`: 3 failed | 7 passed |
| `test_insight_grants_live.py` (8) | Grant without `sealed_key`: failed | 8 passed |
| `test_ai_vocabulary.py` (6) | The outcome list checked against 0070's copy: failed | 6 passed |
| `test_policy.py` (102) | `MANAGE_AI` to members, consent to admins: 2 failed | 102 passed |
| `test_authorization_coverage.py` | The seven AI write routes unplaced: failed, naming each | passed |
| `test_compose_contract.py` (92) | The API's `AI_SECRET_KEY` and `SCHEDULER_INSIGHT_REPLICAS` removed: 6 failed | 92 passed |
| `test_settings.py` (+1) | Production requiring `AI_SECRET_KEY`: failed | 33 passed |
| `AISettingsScreen.test.ts` (19) | Saved copy without the member sentence, the erase confirmation, the 60-second words, the owner-only consent: 5 failed | 19 passed |
| `tools/ai-eval/test_real_call.py` (3) | First run: failed on a real fault (a call with estimated counts earned a record) | 3 passed |

Final run, one process: this step's suites with `test_insight_role.py`, `test_insight_account_live.py`, `test_detection_has_no_ai.py`, `test_scheduler_settings.py`, `test_deletion_live.py`, `test_migration_rules.py`, `test_notifications_console.py`, `test_roles.py`, the three tenancy-structure suites, `test_auth_routes.py`, `test_ratelimit_coverage.py`, `test_install_secrets.py`, `test_model_facts.py`: **3,199 passed**. `uv run mypy .`: no issues in 957 files. `ruff check .`, `ruff format --check .`: clean. Biome on the touched web folders: clean. `pnpm -r --if-present run typecheck`: done. Whole web suite: **170 files, 2,078 tests passed** (`status-interpretation.test.ts` among them). `verify-tokens.js`: clean.

**Not run, and why:**

- **No real provider was called.** Each format's dated real call (AC77, T4.10) needs the founder's free-tier key (foundation item 4): not yet run. Claude's and Bedrock's own formats have no free tier and stay hidden until someone with an account runs them.
- **T4.9, Bedrock's AWS signing through `perform`:** not run; needs an AWS account and a scoped key.
- **`test_connect_guard.py`'s module** stops on live DNS (`nip.io` does not resolve here); it was not changed.
- **The Playwright checks at 390 and 1920 pixels (AC90), the customer page `docs/product/setup/ai.md` (T4.8), design QA and every browser or phone check:** deferred to the feature's hardening pass by the founder's rule of 2026-11-11.
- **AC71's "account missing" screen case live:** renaming the role inside a test would disturb a running dev stack's AI container; the words are tested on the screen, and the read is one catalog query.
- `just check`, `just stack`, `just insight-fence` and other features' live suites: not run, by instruction.

### Step 3, 2026-11-11

Own containers `f3s3-pg` (Postgres 18, port 25432) and `f3s3-ch` (ClickHouse 26.3, port 28123, network `f3s3-net`), migrated to 0070 before the renumbering. Every test below was seen failing first, or failing on a saved copy with its behaviour removed, then passing:

| Test | Seen failing by | Then |
|---|---|---|
| `test_insight_account_live.py` (39) | Broken copies: a table `UPDATE` on `jobs`/`finding_explanations`, shared rights handed back to `PUBLIC`, no connection limit; function bodies without the queue filter, `SKIP LOCKED`, lease grace or the lease fence, and a store writing `check_run` and replacing: 17 failed, and the locked-row case blocked until killed | 39 passed |
| `test_insight_grants_live.py` (8) | Broken copy (an extra `aliases` column, a table `UPDATE`, no take-back, limit 50, one timeout dropped, missing password fatal): 5 failed; the over-privilege check removed: 1 failed | 8 passed |
| `test_insight_role.py` (11) | Full preload for the insight set: 2 failed; the worker's queue check removed: 1 failed; the role/tasks check removed: a real worker started (killed at 60 s); the child's limit at 10 MiB: failed | 11 passed; the child kept 20.7 MiB of its own |
| `test_scheduler_settings.py` (+7) | Before the settings change: 4 failed (the misspelt-role cases already held) | 32 passed |
| `test_roles.py` (+1) | Before the role: failed | 7 passed |
| `test_compose_contract.py` (+15) | On the Compose files from `HEAD`: 12 failed; `test_a_restore_stops_every_role_that_writes_to_postgres` failed until `deploy/nexovue` named the role | 92 passed |
| `test_install_secrets.py`, `test_install_script.py` | On `deploy/nexovue` from `HEAD`: 7 failed | passed |
| `test_deletion_live.py` | AI tables left out of the deletion lists: 2 failed, naming all four | passed, with seeded AI rows |
| `test_ai_vocabulary.py` (6) | A list and the call range changed in code only: 2 failed | 6 passed |

Also run and passing: `test_jobs_live.py`, `test_detection_prune_live.py`, `test_slo_prune_live.py`, `test_erasure_inventory.py`, `test_keys_live.py`, `test_apikeys_live.py`, `test_migration_rules.py`, the three tenancy-structure suites, the three no-AI suites (2,715 passed in one run). **`test_setup_schema.py` fails here** with `KeyError: '0069'` after the renumbering (open decision 8). `uv run mypy .`: no issues in 934 files. `ruff check`, `ruff format --check`: clean. `pnpm -r --if-present run typecheck`: done; `verify-tokens.js`: clean (no web code changed). `docker compose -f deploy/compose/docker-compose.yaml config`: parses; Postgres on `ai` and `default`, `scheduler-insight` on `ai` only.

**`just insight-fence`: not yet run here; the coordinator runs it.** Its parts that could run against these containers did (the account suites); the reach test (AC42), the real restore (AC46) and the container's memory need the built images and an installed kit.

### Step 2, 2026-11-09

Every behaviour change has a test seen failing first, or proved by removing the behaviour from a saved copy and restoring it. Live suites ran on this build's own containers (`f3s2-pg` on 25432, `f3s2-ch` on 28123, with its reader, eraser and detection-writer users), migrated with Alembic and the Go setup, with every suite's DSN pointed at them.

| Test | Result | Seen failing |
|---|---|---|
| `test_alert_vocabulary.py` (26) | pass | yes, before 0067 froze `explained` |
| `test_evidence_vocabulary.py` (7; 4 new: ratings' reasons, rules, the API's closed lists) | pass | 2 failed with a word dropped from 0067 |
| `test_migration_rules.py` (43) | pass; 0067's downgrade refuses | — |
| `test_evidence_explained_live.py` (26, new) | pass | under six mutations: the channel switch, the people-read filter, the channel lock (the race made deterministic: one writer holds its transaction open), the settled read with no new version, the stopped rules, the any-sequence check |
| `test_outbox_golden_live.py` (7, new): every pre-F3 kind on six channel kinds, byte for byte against a fixture written from the code before Step 2; a follow-up with no cause dropped alone; a raising composer dropping only its row; F19's AC58 both ways; the newest likely cause read at the claim | pass | the golden failed with one existing line's words changed; AC58 failed with `explained` out of the withhold; the drops failed with the drop removed; the claim's read failed without its strength filter |
| `test_alert_messages.py` (69; 16 new), `test_alert_payloads.py` (16) | pass | 14 failed without the branch, 4 without the batch exclusion, 1 without Slack's unfurl flags, 7 without the closed-finding guard |
| `test_channels_live.py` (23; 1 new) | pass | yes, without the people-only guard |
| `test_findings_explanation_live.py` (20; 12 new) | pass | the reason guard, the carried rating and the fact targets each failed when removed |
| `test_authorization_coverage.py`, `test_ratelimit_coverage.py` | pass | yes: the three new routes unplaced, and `/explanations` on the telemetry tier |
| `test_deletion_live.py` (28) | pass | 23 failed before `explanation_stopped_rules` had its deletion statement and both tables their seed rows |
| `test_evidence_link_traces_live.py` (7, new): AC105's callee shapes run against the real trace search | pass | yes, with one shape's attribute dropped from the shared fixture (the console's test failed too) |
| `test_evidence_gather_live.py`, `test_evidence_store_live.py`, `test_detection_project_job_live.py`, `test_detection_unmoved_live.py`, `test_detection_notify_live.py`, `test_slo_one_message_live.py`, `test_alert_delivery_live.py`, `test_alert_outbox_live.py` | 133 and 55 pass | — (regression) |
| Web: `url-window.test.ts` (20), `evidence-links.test.ts` (22), `app/url-window-routes.test.ts` (11, the app's own router), `FindingsView.test.ts` (6 new), `FindingScreen.test.ts` (5 new), `SampleExplanationScreen.test.ts` (3), `ExplanationSettingsScreen.test.ts` (7), `ChannelsScreen.test.ts` (2 new), `IncidentScreen.test.ts` (2 new) | pass; **the whole web suite: 2,015 in 165 files** | the channel, incident and empty-feed tests were written first and failed; the rest under mutations (a path left out, the screen's window spread removed, the card line, the fact link and rating removed, the sample's gating, the rule's confirmation) |

Also run: `uv run mypy .` (923 files, no issues), `ruff check .`, `ruff format --check .`, biome on all of `apps/web/src` and both JSON fixtures (one warning, in an untouched file), `pnpm -r --if-present run typecheck`, `verify-tokens.js`. The offline API suite: 5,108 pass (four suites needing ClickHouse ran with it as live suites instead).

**One flake seen once:** `test_slo_one_message_live.py::test_ac56_a_burn_and_a_finding_in_two_projects_both_go` failed once in a 15-file batch and passed alone, in its own file (55) and in two later batches. Not investigated further; F19's.

**Said plainly:** before the reader and writer DSNs were set, one batch ran the evidence and project-job suites with their defaults, `localhost:18123`, another worktree's ClickHouse. Each test's tenant id is random, so no other tenant's rows were read or changed, but a few test rows may remain there. Every later run used this build's own containers only.

**Not run (Step 2):** the Slack and email follow-up as received, and following each link from a demo finding (Step 2's *Judgeable*): **not yet run, founder checks after Phase 2.** The Playwright flow for AC21 and the 390 and 1920 pixel checks: no dev stack in this build. Slack's own handling of `unfurl_links`: not tested.

### Step 1, 2026-11-09

Every behaviour change has a test seen failing first, or, where written with the code, proved by removing the behaviour (a saved copy restored afterwards). Live suites ran on this build's own containers (`f3s1-pg` on 25432, `f3s1-ch` on 28123), migrated with Alembic and the Go setup; 0066's `read_again_at` was added to the running database by hand after the renumbering.

| Test | Result | Seen failing |
|---|---|---|
| Go `serviceedges`, `traceassembly` (5 new door and attribute tests, peer attribute, browser edge) | pass; `go vet` clean; golangci-lint 0 issues | build failure before the fields existed |
| `writer_storeprobe_test.go` (door survives a forced merge) | pass on `f3s1-ch` | failed before 000040 (no column) |
| `test_model_facts.py` (19) | pass | 11 failed with the type check removed |
| `test_settings.py`, `test_scheduler_settings.py` (2 each, AC92's override) | pass | yes, before the setting |
| `test_policy.py` | 94 pass | collection error before the action |
| `test_authorization_coverage.py` | pass | yes, on the ungated route |
| `test_deletion_live.py` (27) | pass | 2 failed with the tables left out |
| `test_evidence_vocabulary.py` (3), `test_migration_rules.py` (37) | pass | yes, with a word added |
| `test_evidence_rules.py` (52) | pass | base-rate cases failed first; 21 failed under five mutations |
| `test_evidence_store_live.py` (9) | pass | 2 failed without the server-door preference and the hour's look-back |
| `test_tenant_chokepoint_structure.py` (18) | pass | yes, with an unbound evidence statement |
| `test_evidence_gather_live.py` (14) | pass | 4 failed without the fingerprint, the lock or the deleted-project stop |
| `test_detection_memory_live.py`, the gather at its row bounds | pass, peak 2 MiB under 64 MiB | yes, with the limit lowered to 1 MiB |
| `test_detection_coordinator.py`, `test_compose_contract.py` | 11 and 69 pass | 1 and 5 failed on the old worker |
| `test_detection_project_job_live.py` (37, two new) | pass | yes, with the enqueue removed |
| `test_deploys_live.py` (44, five new) | pass | yes, with the enqueue removed |
| `test_jobs_live.py`'s floor case, now with `evidence` and `releases` | pass | not separately (the floor is F19's) |
| `test_detection_has_no_ai.py` (9) | pass | yes, with AI and probes imports planted |
| `test_scheduler_postgres_tenancy_structure.py` (14) | pass | yes, with an unbound statement planted |
| `test_detection_unmoved_live.py` (2) | pass | yes, with a gather that touched `findings` |
| `test_findings_explanation_live.py` (8) | pass | 3 failed with the hold, the minute or the token scope removed |
| `test_findings_api_live.py` (36), `test_release_facts_live.py` | pass | — |
| `explanation.test.ts` (23), `FindingScreen.test.ts` (11 new; F28's AC40 cases through the folded section) | pass, 593 across the touched web folders | 4 failed under four mutations |
| `test_explain_demo.py` (10) | pass | yes, under a mutation |

Also run: `uv run mypy .` (909 files, no issues), `ruff check .`, `ruff format --check .`, biome on the touched web folders, `pnpm -r --if-present run typecheck`, `verify-tokens.js`. The offline API suite: 5,042 pass; `test_setup_schema.py` fails here only because migration `0065` (the coordinator's) is not on this branch, and passes once 0066 is re-chained. The offline scheduler and platform suites: 5,332 pass; `test_connect_guard.py` needs internet DNS and was not run.

### CI fix 2026-11-09: the gather tests raced the dev stack's own evidence worker

CI failed `test_detection_project_job_live.py::test_an_opening_adds_its_two_reads_and_a_close_its_last_in_its_transaction`: it expected the jobs `[opened, settled, closed]` for the finding and found only `[settled]`. *Why:* the test read the `jobs` table after the run, so it proved "the gathers are still waiting in the queue", which the product never promises. The stack's `scheduler-light` worker, which CI runs beside the tests, claims queue `evidence`, runs the immediate `opened` and `closed` gathers within a second, and deletes each job once it has run (`jobs/queue.py`, `_FINISH`). Only the 5-minute `settled` one was left. The product kept AC2, AC79 and A1. This is the same kind of failure as the prune test (13fd4ad) and AC60's SLO test, above.

*What the tests prove now, whoever runs the job:*
- **The opening and closing test** reads each service transaction of the project job at its last statement (`_advance`), on the transaction's own connection. It reads the findings and gathers whose `xmin` is that transaction's id, then asks Postgres afterwards whether each transaction committed (`pg_xact_status`). It asserts that the transaction that first wrote the finding (`open`) added exactly `opened` (no delay) and `settled` (300 s). It asserts that the transaction that closed it added exactly `closed`. No other transaction added a gather, and both committed. This is stronger than before, because the old test never checked that the job and the finding shared one transaction.
- **The failed-commit test** (`..._fails_to_commit_leaves_no_job_behind`) had the same blind spot the other way: a wrongly committed gather could have been run and deleted before the `== []` check. It now reads the killed transaction before the kill: it had written the open finding and its two gathers. It then asserts that Postgres records that transaction as `aborted`, and keeps the old checks.
- **`test_detection_unmoved_live.py`** (AC48's arms) did not fail, but it was not testing what it says. Measured: in every arm, `unregistered` and `backed_up` included, the stack had already run the outage's `opened` and `closed` gathers with the real store before the arm chose anything. So the outage now adds its gathers to a queue no worker claims (the request is unchanged except `queue`, asserted to be `evidence` first). Each arm asserts that all three gathers wait and that no `finding_explanations` row exists before it runs.
- `test_findings_explanation_live.py` and `test_deploys_live.py` also read `jobs` for gathers. They do not race: each writes inside one outer transaction that is rolled back and never committed, so no worker can see their jobs.

*Seen failing, then passing, against the running dev stack:* the unchanged opening test failed 3 of 3 runs (`[settled]`). The changed pair passed 5 of 5 runs. With the enqueue moved to its own transaction (a temporary mutation of `_read_facts`, restored), both changed tests fail. With the unmoved test's hold switched off, its new check fails (`['settled'] == ['closed', 'opened', 'settled']`). With the hold on, it passed 5 of 5 runs. The F3 live suites (`test_evidence_gather_live`, `test_evidence_store_live`, `test_detection_project_job_live`, `test_detection_unmoved_live`, `test_findings_explanation_live`) passed 70 of 70. `uv run mypy .`, `ruff check .` and `ruff format --check .` are clean. No product code changed.

### Not run

- **The founder's judgement** ("does the first screen tell you where to look?", the abstention rate on the seeded cases): not yet run, founder checks after Phase 2.
- `just explain-demo` against the dev stack, and the Playwright checks at 390 and 1920 pixels (AC17, AC70): no dev stack in this build.
- AC14's receiver arm and T1.3's volume measurement (*Open decisions*, 3 and 4).
- `just check` was not run, by instruction.

### Earlier

None run before Step 1. No test, query or model. The code was read, and each review finding checked. Web facts read on 2026-11-02, through a summarizing tool: Docker Hub on `POSTGRES_USER`; PostgreSQL on `INSERT` and `LOCK` privileges; Coroot's AI overview, AI configuration and AI benchmark pages; OpenObserve's AI SRE page; Grafana's Assistant invoice page; Datadog's Watchdog page; HolmesGPT's evaluations page in its repository. Microsoft's Azure OpenAI v1 page, through a summarizing tool, and its older REST reference through a search summary only. A script confirmed every acceptance criterion in `spec.md`, AC1 to AC106, is cited by a task in `plan.md`, and was re-run after the second review.

## Specification impact

This is the specification. On approval it amends: `phase-2-foundation.md` (the differences listed in `design.md`), `feature-catalog.md` (F2, F3), `vision.md`, `phase-2-plan.md`'s selling reason, the F6, F21, F28, F4 and F5 documents, and `team-and-api-access/spec.md`. None of them has been changed.

## Documentation impact

**Step 4 (2026-11-11):** `docs/operations/ai.md` (allowing a local model, an unreadable key), `docs/operations/key-rotation.md` (`AI_SECRET_KEY`), ADR-047 and the decisions index, `phase-2-tracker.md`. `docs/product/setup/ai.md` is deferred to the hardening pass.

**Step 2 (2026-11-09):** `docs/product/setup/alerts/webhooks.md` (no `explained` event on a webhook), `setup/alerts/channels.md` (the follow-up, its limits and switch, frequent deployers, pages with no finding, held until the bar is met), `setup/deploys.md` (what deploys add to a finding, the 7-day clock), `docs/features/alerts/spec.md` (amended: the `explained` kind), F19's `status.md` (AC58 done), `phase-2-tracker.md`. Before Step 2: none changed. Listed in `design.md`, *Specification impact*, and in the plan's tasks. New customer and operator documents are `docs/product/setup/ai.md` and `docs/operations/ai.md`, written during Steps 4 and 5.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- The scheduler Postgres test: exists, and F28 extends it first (A72, design, T1.9).
- F3 adds `Reader.is_person`, and the explanation route uses it, so F22 and F4's ticket build on one helper (design, T1.10).
- New A80: `ai_connections.off_reason`, which F5 and A53 both need (spec, design).
- `likely_cause.rule` is a stable name, `error_group` stores its window, and the alias function is reachable from the API, as F5 asked (design).
- The page: the last deploy is this section's first row at every width, and from F22 on the section leads the right column (FR14, FR44).
- The event writer is `RECORD_FINDING_EVENT_SQL`, and F22's T2.9 is the known later edit to `gather.py` (spec).
- F4 and F5 add queues to the job functions, and F5 takes the role to 8 slots with `insight` at 3 (design, NFR3).
- Founder items: the bed's US$50 sits inside one shared cap, and the selling line points to the foundation's one wording (spec).
- Not taken: F5's request to link existing error groups whose rate moved. F5's fallback stands.

## Last updated

2026-11-11 (Step 4 built, awaiting commit; Step 3 built, awaiting commit; Step 2 committed 2026-11-09)

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** the go or stop rule on AI wording, 3 of 5 blind readers (item 13); the accuracy bar before "Likely: this deploy": per rule at most one tenth wrong, rounded down, none on the healthy run, and a rule stating fewer than 5 causes stays silent (item 11). Both are now fixed before any result exists.
- **Changed, A: no rented test server.** Step 2c's weeks-long rule scoring, T2.17's report and T2.18's check wait for Oracle after Phase 2 is deployed, and so does any part of Step 6 that needs the demo running for days. **Until then no rule states a likely cause; F3 ships facts only.** A70 and A79 updated.
- **Changed, B: no paid model calls.** T2.11's 20 calls and Step 6's model runs use a free tier (Gemini's is the likely one: Groq's listed 8,000 tokens a minute is below one F3 prompt of about 9,800, someone else's measurement; Gemini's limits are shown only in AI Studio) and the local model on the Mac (A81). Synthetic data only. The report names the models and says paid providers are not yet tested.
- **Open decisions:** none for the founder. The Oracle shape to measure on comes back at deployment.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 4 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.

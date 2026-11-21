# Status: AI chatbot (F4)

## Lifecycle status

**In development.** **Step 7 built 2026-11-21, committed** (worktree `wt/f4s7`, on local commit 4c88ca7 = Step 6 on `feature/phase-2` 96cbd81): case notes and resolved cases in knowledge, migration 0080 (`down_revision` 0078 until re-chained at commit, since F5 Step 3 takes 0079), ADR-088; fake providers only; **T7.3's bed run not yet run: the founder's, after Phase 2, and it waits on Step 4.** **Step 6 built 2026-11-21, committed** (worktree `wt/f4s6`, on `feature/phase-2` 96cbd81): embeddings and hybrid search, migration 0078, ADR-079 and ADR-080; fake providers only; **T6.0's real calls, T6.4's measurement and T6.6's bed re-run not yet run: the founder's, after Phase 2.** **Step 5 built 2026-11-21, committed** (worktree `wt/f4s5`, on `feature/phase-2` 301f450): runbooks, knowledge chunks, keyword search, fact lines, runbook quotes; migration 0076, ADR-067; fake providers only; **T5.7's bed re-run not yet run: it needs the founder's key or local model, and waits on Step 4.** **Step 4 (real models, in the gate) is not built: it needs real models.** Step 1 (a safer MCP server) is on `feature/phase-2` (commit 5988d88, 2026-11-02). **Step 3 built 2026-11-12, committed 2026-11-12,** locally, not pushed, on top of Step 2; no migration, no new ADR; fake providers only, **real-model parts not yet run: they need the founder's key or local model.** **Step 2 built and committed 2026-11-12, committed locally on `wt/f4s2`, not pushed:** one commit on top of F3 Step 5 (`wt/f3s5`, fb9eee3). Against fake providers only; **the one real local-model call is not yet run: it needs the founder's key or local model.** The founder approved the specification, design and plan on 2026-11-02.


## Current task

**Step 7 built 2026-11-21, committed** (worktree `wt/f4s7`, branch `wt/f4s7`, on local commit 4c88ca7, which is Step 6 on `feature/phase-2` 96cbd81 with F4 Steps 1 to 3, 5 and 6, F3 Steps 1 to 5 and F22 Steps 1 to 6; committed). Migration **0080** (`down_revision` "0079", after F5 Step 3). **ADR-088** (ADR-089 and ADR-090 unused). Against fake providers only (foundation item 31).

### Step 7, what was built

- **T7.1** — `apps/api/migrations/versions/0080_knowledge_notes.py` *new*: `finding_notes` gains `UNIQUE (org_id, id)` (built concurrently, taken over by the constraint, as 0051 did); `knowledge_chunks.note_id`, keyed `(org_id, note_id)` to it `ON DELETE CASCADE`, with a unique index (one chunk a note), built concurrently; `source_kind` gains `note`; the one-source check gains the note's case; a note chunk may hold a whole note (4,000 characters, F22's cap), others stay at 2,000. Checks replaced `NOT VALID` then validated. **A real downgrade** (it deletes note chunks, copies the sweep writes again, and restores 0076's checks); run down and up once on f4s7-postgres. Models, `nexovue_platform.knowledge` (`SOURCE_KINDS`, `MAX_NOTE_CHUNK_CHARACTERS`), `organizations/tables.py`'s comment. ADR-088.
- **T7.2, the sweep** — `knowledge/tasks.py`: per organization with chat on, in one transaction, drop, **copy notes**, write fact lines, evict. Each note with no chunk becomes one chunk of its whole text, oldest first, 200 a sweep, never a deleted project's; at a project's cap of people's words (5,000 with runbooks) a note is **skipped, never refused** (counted `notes_skipped`), fact lines giving way first (AC83, FR32). **Resolved cases** write a fact line (`… | closed: case resolved`, ending at the resolve) and keep it when the finding stops being `recovered`; only resolutions `resolved` and `tracker_closed` count (open decision 39). The kept rule is null-safe (`IS NOT DISTINCT FROM`, `COALESCE`): the first version left a false alarm's line in place, which Step 5's own test caught. `nexovue_platform/knowledge_facts.py` *new*: the fact line and the kept rule, shared by the sweep and Save as runbook.
- **T7.2, search and answers** — `assistant/knowledge.py`: a note's row (`source_kind` `note`, the note's own date, its case's id and service; never the author), read only within its finding's project; **people's words first**: both sides order runbooks and notes before fact lines, and the merge keeps it (`merged(…, facts=…)`), so a note outranks 100 fact lines (AC83, FR29; open decision 41); a resolved case's line stays found. The catalog's basis maps `note` to the team's notes; the tool's description names notes and says a note is never a command. `assistant_answer.py`: a note quote is stored with `note`, folded like a log's when it reads as a link or command, and a sentence telling the reader to run it is removed (BR7, AC63). `assistant/quotes.py`: a note quote read again with `note`, its date and case; gone once the note is deleted.
- **T7.2, Save as runbook (FR41)** — `POST /runbooks/notes` (`MayManageKnowledge`, absent where AI is not offered): a resolved case's note, then where it came from and the case's fact line, composed from columns alone, into a runbook of the case's project written by the clicking person, titled "From a resolved case on {service}, {date}"; the credential warning and its second click apply; another organization's note is 404; an active case's is 409 `case_not_resolved`. It names no finding, so it outlives the finding (AC95). Web: **Save as runbook** on each note of a resolved case in the timeline, for roles that may manage knowledge, self-hosted, not in a support view; the API's refusal sentence, **Save anyway**, then a link to the runbook. (The path is `/notes`, not `/from-note`: `verify-tokens` reads any `from-…` literal as a colour class.)
- **T7.2, `get_finding_timeline`** — the 24th tool: F22's `GET /findings/{id}/timeline` as a ticket reads it (kinds and times; a ticket is never a person), linked to the case page with "This answer cannot show who holds the case. See the case page."
- **T7.2, F22's code (minimal, said plainly):** the AC69 table test gains the ticket row; the note box says "With chat on, notes can be sent to {provider label}." while chat is on for the case's project (`GET /assistant/availability`, standings `ready`, `no_consent`, `no_data`; open decision 45); `/settings/connections`' *What leaves this server* gains the chat row; the timeline takes `saveAsRunbook`; the findings test harness stubs chat off by default. F22's note service and routes are unchanged.
- **Settings and consent** — the Chat section counts notes chat cannot search (`EmbeddingState.notes_not_searchable`; open decision 43); progress and the sending sentence name notes; the preview's embedding kind names case notes; `CHAT_DISCLOSURE_VERSION` 2 → 3 (open decision 44).
- **Personal data:** a note chunk is a copy of a note; F22's export already holds a person's own notes, and a removed member's notes stay as *Former member* (F22 BR16), so their chunks stay with them; deleting a note, its case's finding, its project or its organization deletes the chunk by the cascade; retention follows the finding (F22 FR25). No new export category (open decision 49).
- **Not changed:** no new grant: the AI account still reads only `id, org_id, project_id, content, embedded_model, embedding_due` of a chunk, never `note_id`, and nothing of `finding_notes` (held by `test_insight_grants_live.py`).

### Step 7, seen failing first

Every new or changed test was seen failing: the behaviour broken, the test run, the file restored byte-equal by a script keeping its copy outside the repository and comparing bytes after (51 code breaks), plus two in the database. Clocks: `NOW` in the API suites, `DAY` in the scheduler's, `vi.useFakeTimers({ toFake: ["Date"] })` with `vi.setSystemTime` on the web. Live tests own their rows: the API's in rolled-back transactions; the scheduler's and the hostile suite's make their own organizations and sweep only those (`sweep_organization`).

- Gates that caught the change before they were updated: `test_assistant_catalog.py` (the new tool missing from the destination table) 2 failed; `test_authorization_coverage.py` (the new route unplaced) 1 failed; `test_knowledge_records.py` (0076's frozen kinds no longer the code's) 1 failed; `test_runbooks_live.py`'s handler count (6 against 7) 1 failed; `test_assistant_tickets_live.py` (the timeline listed as outside the catalog) 1 failed.
- sch:test_knowledge_writers.py (5 new; 15 in all), each 1 failed unless said: notes never copied (2); the cap ignored; a deleted project's note copied; resolved cases not written; a resolved case's line dropped; any resolution keeping a line; the line saying "recovered"; the end taken from the close; the kept rule null-unsafe on the closed reason (at first PASSED: no line's finding lacked a reason; an open finding whose case ended otherwise was added, then 1 failed) and on the case state (the bug the first version had).
- sch:test_assistant_answer.py (1 new, 1 changed): the note flag not stored; a note acting as a runbook; a note counted, not the team's; the flag left out of the stored quote (2).
- api:test_assistant_knowledge_live.py (5 new): notes not searched (3); text score alone (the 100 lines first); the merge ignoring kinds; the meaning side not people-first; a resolved case's line not kept; a note dated by its chunk; any resolution kept.
- api:test_runbooks_live.py (3 new): Save as runbook on any case; no credential check; the finding's title copied; another organization's note (at first PASSED: the project check after it still bound the organization; removed from both, then 1 failed). In the database: the note key made `NO ACTION` -> 3 failed (runbooks, deletion, knowledge); restored, 8 passed.
- api:test_assistant_quotes_live.py (1 new): the note flag; the note's case.
- api:test_assistant_embedding_live.py (1 new): a deleted project's note counted; chunked notes counted (at first PASSED: one chunked of two made the same count; a third note added, then 1 failed).
- api:test_assistant_catalog.py: the timeline naming a person (`actor_name`); its sentence dropped. api:test_assistant_tools_live.py: the timeline given a person's shape for a ticket -> 1 failed. api:test_findings_cases_live.py: a ticket counted as a real person -> 1 failed. api:test_insight_grants_live.py: `note_id` granted -> 2 failed (a grant made by hand in the database PASSED, because the suite's `setup` takes it back, which is that suite's own test; broken in `insight_account` instead). api:test_assistant_preview_live.py: the kind without notes -> 1 failed. api:test_knowledge_records.py: 0080's note size drifted; a false alarm among the kept resolutions.
- api:test_assistant_hostile_live.py (1 new, run alone; the real loop as the AI account over real uvicorn, the note indexed by the real sweep): a note acting as a runbook -> 1 failed (the "run" sentence kept); the search reading every tenant -> 2 failed (the other organization's note and runbook reached the provider).
- Web: FindingScreen.test.ts (3 new): the note box's sentence always; Save as runbook on an active case; no second click; a viewer offered it. answer.test.ts (3 new): a note not shown as one (2); a note quote unfolded; gone in a runbook's words. ChatSection.test.ts (1 new, 1 changed): the count never shown; zero counted; progress without notes. ConnectionsScreen.test.ts: the row missing. Each 1 failed unless said, then restored.

### Step 7, `/review-diff` (2026-11-21)

Read against the design, plan, ADR-055, ADR-067, ADR-079, ADR-080, ADR-088 and `docs/quality/code-review.md`. Every changed file is explained by T7.1, T7.2 or a gate the plan names; F22's files are the plan's named ones plus the test harness's default stub. Tenancy: every new Postgres statement binds `org_id` (the sweep's statements by `:org_id`; search and quotes by the ticket's organization and project; Save as runbook by the person's organization, then the case's project proven). Authorization: one new write, on `MayManageKnowledge`. Masking: a note goes out only through the existing send-time and embedding masks (held for notes by the hostile suite). Secrets: no note text in an audit row (the runbook audit carries size and counts). Migration: reasoning stated, a real downgrade, concurrent index builds. Web: tokens only, imports `shared → features → app` (the findings feature asks `assistant/availability` itself rather than importing chat's feature), sentences capped. **HIGH:** none. **MEDIUM:** none found. **LOW, fixed:** the sweep's batch comment named findings only. **LOW, recorded:** the meaning side's SQL does not re-check that a note's finding is in the chunk's project, as the keyword side does; the writer sets the project from that finding and a finding never changes project, so it is defence in depth only.

### Step 7, checks (2026-11-21, counts read from the output)

Stores: `f4s7-postgres` (45432, migrated by the real `setup` 0077 → 0078 → 0080, `vector: … ready`; the object store not configured) and `f4s7-clickhouse` (48123, 49000 for the Go migration step, 42 migrations), on their own network `f4s7-net`; every live run through a private wrapper refusing any other store address. No real provider was called.

- `uv run ruff check .` clean; `uv run ruff format --check .` 1533 files formatted; `uv run mypy .` **no issues in 1139 source files.**
- Biome on every touched web path: clean. `pnpm -r --if-present run typecheck`: clean. `verify-tokens`: clean (after the route rename above).
- **Whole web suite: 2628 passed (208 files).**
- **API, gates and chat's and F22's suites, `-n 2`** (cross-service contracts, authorization coverage, read surface structure, spec and live, rate-limit coverage, callers and limiter, both tenancy structures, tenant chokepoint and isolation, migration rules, setup schema and no-vector, body limit, policy, auth routes, deletion, keys, personal data, insight grants, MCP, every findings suite, services, infrastructure, logs, access log, roles, AI settings and vocabulary, every chat suite but the hostile one, runbooks, knowledge records): **4388 passed, 1 failed, 16 errors**: the failure was the tickets suite's stale list above, fixed (41 passed after); the errors are `test_services_live.py` (12, the seeded `acme/production`), `test_tenant_isolation_live.py` (2, the running dev stack), `test_setup_live.py` (2, an object store), as in Step 6; not this change. Compose contract with `POSTGRES_INSIGHT_PASSWORD` unset: **99 passed.**
- **Hostile live suite, alone: 3 passed.**
- **Scheduler (chat, insight, knowledge, beat, detection- and connections-have-no-AI, jobs, expiry, fix suites), `-n 2`: 396 passed. Platform and `tools/ai-eval`, `-n 2`: 1571 passed.**

### Step 7, not built or not run

- **T7.3, the bed's planted note across questions:** not yet run: the founder's, after Phase 2; `tools/ai-eval/injection/` is Step 4's (T4.2) and does not exist. AC40's note half is held against fake providers by the hostile suite.
- **The founder's judging** (resolve a case with a note, ask "how did we fix this last time?", delete the note, ask again) with a real model: not yet run. Each part is held by a test against fake providers (`test_a_note_is_found_with_its_date_and_its_case_and_never_its_author`, `test_deleting_a_note_takes_it_out_of_search_in_the_same_statement`, the quotes suite's note test).
- **Hardening, deferred:** Playwright, browser and phone runs of the note box, Save as runbook and the note quote; performance (the sweep with notes at the cap); customer docs (T8.3); design QA of the new words (open decision 47).

### Step 6

**Step 6 built 2026-11-21, committed** (worktree `wt/f4s6`, branch `wt/f4s6`, on `feature/phase-2` 96cbd81, which holds F4 Steps 1 to 3 and 5 and F3 Steps 1 to 5; not committed, not pushed). Migration **0078** (`down_revision` 0077). **ADR-079 and ADR-080** (ADR-081 unused). Against fake providers only, including a fake embedding provider (foundation item 31).

### Step 6, what was built

- **T6.0** — `nexovue_platform/ai/embed.py` *new*: `embed(texts, model)` as F3-style pure formats, plain HTTP through F3's guarded transport (ADR-047): `openai_embeddings` (`POST {base}/embeddings`, a list of texts, `encoding_format: float`, read by `index`, `usage.prompt_tokens`) for OpenAI-style accounts and Ollama; `bedrock_titan` (`POST {base}/model/{id}/invoke`, `inputText`, one text a call, `embedding`, `inputTextTokenCount`) for Bedrock, not Converse (D81); none for Anthropic. Field names read on 2026-11-21 from OpenAI's generated library types, Ollama's compatibility page source and AWS's InvokeModel reference and Titan V2 example (the providers' own pages could not be reached from this session). Every reply read defensively: finite numbers, one length, at most pgvector's 16,000 dimensions, else `invalid`. `EmbeddingSetting.keyword_only` holds the rules both the API and the AI role read. `ai/testing.py` gains a fake model (`meaning_vector`: "slow" beside "latency"). `ai_runs.purpose` gains `embed` (`ai_records`, `contract.Purpose`, migration 0078). **The one real call per format: not yet run: the founder's, after Phase 2.**
- **T6.1** — `setup.provision_vector()`: `CREATE EXTENSION IF NOT EXISTS vector`, then the three columns, each only if missing, under a 5-second lock timeout; a refusal is one line and `setup` goes on (AC43). `insight_account.VECTOR_GRANTS` (open decision 21): on `knowledge_chunks` SELECT of `id, org_id, project_id, content, embedded_model, embedding_due` and UPDATE of `embedding, embedded_model, embedding_due`; on `assistant_steps` UPDATE of `query_embedding, query_embedding_model`, applied only when the columns exist; and UPDATE of the four embedding-check columns on `assistant_settings`. The account never reads a vector. Migration 0078: `assistant_settings` gains the model, the second connection (format, address, sealed key, key id, owner's agreement and its time) and the last check's result, with three checks; `ai_runs`' purpose check widened `NOT VALID` then validated; `assistant_finish_message` now nulls a question's vectors where the columns exist (dynamic SQL). `keys.SEALED_COLUMNS` gains `assistant_settings.sealed_embedding_key`. `assistant/embedding.py` *new* with `PUT /assistant/settings/embedding` (`MANAGE_AI`), `POST /assistant/settings/embedding/consent` (`CONSENT_AI_DISCLOSURE`), `POST /assistant/settings/embedding/check` (`MANAGE_AI`, a job on the AI container): validated by F3's rules, sealed, audited by field names and host (`assistant.embedding_changed`, `assistant.embedding_disclosure_agreed`, `assistant.embedding_tested`), refused where AI cannot be offered. `GET /assistant/settings` gains `embedding` (model, source, the second connection, check, keyword-only reason, progress). `sch:insight/knowledge.py` *new*, in `TASK_SETS["insight"]`: `embed_chunks` (opted-out vectors nulled first, AC70; due chunks of live, not-opted-out projects, masked by FR40, in the format's batches, each call reserved under chat's share with its `ai_runs` row before the call and settled by `store.settle_run`, so a run F3's sweep ended writes nothing; a failure leaves chunks due) and `check_embedding` (one call with nexovue's own words, its result only on the values tested). `beat.EMBED_SWEEP_ENTRY` every 5 minutes on `insight` while AI is on, with its own time limits. The consent preview counts the project's chunks sent at once when a model is set and lists text and phrases sent for embedding; `CHAT_DISCLOSURE_VERSION` 1 → 2 (open decision 32).
- **T6.2** — `assistant/knowledge.py`: `step_id`; the step must be the ticket's own message's (refused otherwise); exact scan `ORDER BY embedding <=> query_embedding`, filtered by organization, project, the step's own model and equal dimensions, with a false alarm's fact line left out; RRF `1 / (60 + rank)`, top 5, either side empty and the other stands whole (open decision 30); `search` says `keyword` or `hybrid`; no extension, keyword only. `sch:insight/assistant.py`: a `search_knowledge` step embeds its masked phrase from the question's reservation (a run row, purpose `embed`), stores the vector and model on the step, names the step to the route, and records `search:keyword|hybrid` on the step; a failed embedding searches by keyword and never ends the question. The finish nulls both columns. The tool's description says it searches by meaning too; prompt `chat-4`.
- **T6.3** — `/settings/ai` Chat section, *Search*: why search is keyword only (no extension, no model, a provider with no embeddings, no owner's agreement to the second connection, no passing check, chat not ready), or that it searches by meaning; model, connection, check and its date; progress; the model and second connection form; the check, read again every 2 s while awaited; the owner's agreement (owners only). The panel: a knowledge step that searched by keyword says "Searched by keyword only." (`StepView.search`). The spec's *Error cases* row "Install has no `vector` extension" now names its tests.
- **T6.4** — **not yet run** (performance measurement is deferred to hardening): 5,000 chunks at 1,536 dimensions on 1 GB; no index added, so the D84 literal is not needed yet.
- **T6.5** — the spec's provider table now says what each format was built from, and **not yet tested** on every row until the founder's real calls.
- **T6.6** — **not yet run: the founder's, after Phase 2;** waits on Step 4.
- **Design follows the code:** the data and grant tables, the extension paragraph (the repository has no Alembic drift check), the merge rule. `docs/self-hosting/install.md` says meaning search needs the extension; `01-extensions.sql`'s comment corrected.
- **F3 files changed, minimal:** `ai_records.RUN_PURPOSES` and `contract.Purpose` gain `embed`; `ai/jobs.py` gains the embedding check's job and reasons; `ai/service.py` gains a public `refuse_where_unavailable` over its private rule. No F3 behaviour changed.

### Step 6, seen failing first

Every new or changed test was seen failing: the behaviour broken, the test run, the file restored byte-equal by a script that keeps its copy outside the repository and compares bytes afterwards (65 breaks by one script, and one in the database). Clocks: `now` fixture read when each test starts in the live scheduler suite; `NOW` in the API suites; `vi.useFakeTimers({ toFake: ["Date"] })` with `vi.setSystemTime` on the web.

- Gates caught the change before they were updated, each then given its entry: `test_ai_vocabulary.py` (0070's purposes no longer the code's) 1 failed; `test_authorization_coverage.py` (three routes unplaced) 1 failed; `test_scheduler_postgres_tenancy_structure.py` (`_ORGANIZATIONS`) 1 failed; `test_insight_grants_live.py` (the new column rights) 1 failed; `test_assistant_preview_live.py` (the new kinds) 1 failed.
- pkg:test_ai_embed.py (25), each break 1 or 2 failed: the float encoding dropped; vectors not placed by index; mixed lengths accepted; a `true` accepted as a number; infinity accepted; Titan through `/converse`; Anthropic given a format (2); a check of any values accepted; the second connection's agreement ignored; the second connection ignored (2).
- sch:test_knowledge_embed.py (20, live, as the AI account), each break 1 failed unless said: chunks sent unmasked; opted-out vectors not nulled; opted-out project embedded; deleted project embedded; deleted organization embedded; no re-embedding on a new model; chat's readiness ignored (2); AI off ignored; a run the sweep ended still writing vectors; no month share; a reached call not charged; a check landing on changed values; a provider with no embeddings still called; the columns never seen as granted (10); the phrase's vector not stored. The finish's nulling: the database's function put back to 0073's -> 1 failed, then 0078's restored -> 20 passed.
- sch:test_assistant_loop.py (4 new): the step not named to the route; the phrase unmasked; a reached failure not charged; the search not noted (2); embedding with no setting.
- sch:test_beat_schedule.py (1 new): the entry missing; the module out of the insight set.
- api:test_assistant_knowledge_live.py (6 new): meaning search never run (3); the model filter dropped; the step's question not checked; one side dropped when the other is empty (3); a false alarm's line found by meaning (at first PASSED: the row fetch filtered it again; that redundant filter was removed, then 1 failed); no top-five cut.
- api:test_assistant_embedding_live.py (7): model unchecked; a provider with no embeddings allowed; address unchecked; a key kept when the address moves; the audit row naming the model (2); consent to any fingerprint; the container not checked; chat's readiness unsaid; progress counting another model's vectors; the hosted service allowed to save (found in `/review-diff`, below).
- api:test_setup_no_vector.py (3, AC43; a database of its own from `template0`, owned by a non-superuser): a refusal raising (3 errors); vector grants applied without the columns; meaning search without the extension; "no extension" unsaid.
- api:test_insight_grants_live.py (1 new): the vector readable by the account -> 2 failed. api:test_keys_live.py (1 new): the column unknown to the reseal -> 2 failed; sealed under the wrong purpose -> 2 failed. api:test_assistant_preview_live.py (1 new): the count never given; the phrases not listed. api:test_migration_rules.py: 0078 rolling back -> 1 failed. api:test_assistant_error_cases.py: the named web test renamed -> 1 failed.
- web: ChatSection.test.ts (5 new cases, 6 rows): the reason unsaid (6 failed); an empty key sent; an admin offered the agreement; the check not posted; the progress unsaid. step-links.test.ts (1 new): keyword only unsaid.

### Step 6, `/review-diff` (2026-11-21)

Read against the design, plan, ADR-067, ADR-079, ADR-080 and `docs/quality/code-review.md`. Every changed file is explained by T6.0 to T6.3 or a gate the plan names. Tenancy: every new Postgres statement binds `org_id` (the sweep's one cross-tenant list named in the structure test); the search's project and step are the ticket's. Authorization: three new writes gated. Masking: chunk text and phrases masked before they leave. Secrets: the key sealed, never returned or audited; the second connection's address is returned to admins only, as F3's is. Migration: roll forward only, reasoning stated. Web: tokens only, sentences capped, imports `shared → features → app`. **HIGH:** none. **MEDIUM, fixed:** the embedding writes did not refuse where AI cannot be offered (F3 AC40); now they do, through F3's own rule, with a test seen failing. **LOW, recorded:** the Search form's fields start from the settings once and do not follow a later refetch; the batch sizes are guesses (open decision 33).

### Step 6, checks (2026-11-21, counts read from the output)

Stores: `f4s6-postgres` (45432, migrated by the real `setup` 0077 → 0078, `vector: … ready`; the object store not configured) and `f4s6-clickhouse` (48123, 49000 for the Go migration step, 42 migrations), on their own network `f4s6-net`; every live run through a private wrapper that refuses any other store address. The containers were stopped by the host's restart once and started again (only these two). No real provider was called.

- `uv run ruff check .` clean; `uv run ruff format --check .` 1528 files formatted; `uv run mypy .` **no issues in 1137 source files.**
- Biome on every touched web path: clean. `pnpm -r --if-present run typecheck`: clean. `verify-tokens`: clean.
- **Whole web suite: 2621 passed (208 files).**
- **API, gates and chat's suites, `-n 2`** (cross-service contracts, authorization coverage, read surface structure, spec and live, rate-limit coverage, callers and limiter, both tenancy structures, tenant chokepoint and isolation, migration rules, setup schema and the new no-vector suite, body limit, policy, auth routes, Compose contract with `POSTGRES_INSIGHT_PASSWORD` unset, deletion, keys, personal data, insight grants, MCP, services, infrastructure, logs, access log, every chat suite but the hostile one): **4354 passed, 16 errors**: `test_services_live.py` (12) needs the seeded `acme/production`, `test_tenant_isolation_live.py` (2) the running dev stack, `test_setup_live.py` (2) an object store, none started here; not this change. After `/review-diff`'s fix: the embedding, AI settings, chat API, knowledge, authorization and contract suites 270 passed.
- **Hostile live suite, alone: 2 passed.**
- **Scheduler, platform and `tools/ai-eval`, `-n 2`** (chat's and F3's insight suites, knowledge writers and the new embedding suite, beat, detection-has-no-AI, jobs, expiry, general fix, all platform tests, the fake providers): **1945 passed.**

### Step 6, not built or not run

- **T6.0's one real call per format, T6.5's table from it:** not yet run: the founder's, after Phase 2 (foundation item 31). Includes whether a Bedrock API key works on InvokeModel.
- **T6.4, the measurement** (time and memory at 5,000 chunks on 1 GB; AC97): not yet run, deferred to hardening; D84's partial index is added only if it is over 200 ms.
- **T6.6, the bed's knowledge places with embeddings, and a cost run:** not yet run: the founder's, after Phase 2; waits on Step 4.
- **The founder's judging** ("why is it slow?" against a "latency" runbook with embeddings on and off): held with the fake model by `test_meaning_search_finds_the_runbook_keywords_miss_and_says_hybrid`; with a real model, not yet run.
- **Hardening, deferred:** Playwright, browser and phone runs of the Search section; recall; customer docs (`docs/product/setup/`, T8.3); design QA of the new words (open decision 38).

### Step 5 (committed)

**Step 5 built 2026-11-21, committed** (worktree `wt/f4s5`, on `feature/phase-2` 301f450, which holds F4 Steps 1 to 3 and F3 Steps 1 to 5; not committed, not pushed). Migration **0076** (`down_revision` 0075). **ADR-067** (the design's knowledge ADR; ADR-068 and ADR-069 unused). Against fake providers only.

**Step 4 is not built and Step 5 does not need it,** read from the plan and the code: Step 4 builds the bed's chat seed, injection suite and cost runs (T4.1 to T4.4) against real models. Step 5's code needs none of it; only **T5.7** ("re-run the bed") does, so T5.7 is recorded as waiting on Step 4, and **not yet run: it needs the founder's key or local model.**

### Step 5, what was built

- **T5.1** — `apps/api/migrations/versions/0076_knowledge.py` *new*: `runbooks` (title, optional service, text at most 32 KiB checked in the database too, author `SET NULL`, times; `UNIQUE (org_id, project_id, id)`), `knowledge_chunks` (`source_kind` runbook or fact; one nullable cascading key per source, the runbook and finding keys carrying the project, so the database refuses a chunk in another project than its source; a check that exactly the key its kind names is set; generated `content_tsv` with a GIN index; `embedded_model`, `embedding_due`; a fact line's `fact_service`, `fact_signal`, `fact_day` with a unique index, nulls not distinct). No vector column (Step 6's `setup`). Roll forward only. `nexovue_platform/knowledge.py` *new*: source kinds, caps, `chunk_runbook` (closes at a heading once a chunk holds `len/17`, splits at 2,000 at the last whitespace, so at most 18 chunks), the sweep's task name, and `EXPIRED` (a deleted project's runbooks and chunks go at the next expiry sweep). Models, `organizations/tables.py` (both by their project's cascade), `jobs/expiry.py`, the migration rules' roll-forward list. ADR-067.
- **T5.2** — `nexovue_api/runbooks/` *new* (router, schemas, service): list and read for anyone signed in (a support view gets no body), create, upload, edit and delete on `MayManageKnowledge`; chunks written and replaced in the runbook's own transaction; the 32 KiB, 200-a-project and title refusals as sentences with codes; text holding a credential form needs `confirm_credentials` (`assistant_masks.holds_credential`, the exact list FR40 masks before sending); `runbook.created`, `.updated`, `.deleted` audited with project, size and chunk count, never text; absent (404) where `ai_available` is false. Upload: several Markdown files as a JSON body, into each project named as its own copy, refused file by file, at most 50 files an action, with its own body cap (`middleware/body_limit.py`). The personal-data export gains `runbooks_written` and `runbooks_truncated` (AC34); `docs/features/personal-data/spec.md` says so.
- **T5.3** — `features/runbooks/` *new*: `RunbooksScreen` (title, service, author or *Former member*, age with a warning tone past 180 days; loading, failure, empty, cap reached), `RunbookScreen` (read; edit; delete behind the design's sentence; a support view's "no body" sentence), `RunbookForm` (full-width text box, capped sentences, "With chat on, this text may be sent to {provider}. Do not paste secrets.", the second click, the size refusal, unsaved changes), `UploadForm` (files and projects, per-file refusals, a second click for files refused for a credential). `/settings/runbooks` and `/settings/runbooks/$runbookId` behind a new `SelfHostedOnly`; the rail's entry `selfHostedOnly`, its filter moved to `offeredSettings` so it is tested; `console-routes.json`; `/settings/runbooks` reads `?project=` like the other project-only screens. `shared/runbook-age.ts` *new* gives the panel and the screen one age and one 180-day rule.
- **T5.4** — `assistant/knowledge.py` *new*: `GET /assistant/knowledge/search` on `AssistantTicketReader` (`auth/dependencies.get_ticket_reader`: a ticket only; the ticket check is the read surface's own); `websearch_to_tsquery('english', q)`, ranked, 20 at most, the ticket's organization and project, a false alarm's fact line left out at once (AC84), refused at data level *facts only*, and `search: keyword` (AC41). `search_knowledge` in the catalog: the one tool off the read surface (AC14), rows labelled by `source_kind` (a runbook the team's, a fact line counted), quotable by chunk id, linked to `/settings/runbooks` with "The search phrase is not carried." FR42's empty answer: `StepView.cases_kept_days` from the plan's derived retention, said by the panel ("No record of this in what is kept. Cases are kept for {N} days after they end."). The *Error cases* row "Knowledge has no match" now names its tests.
- **T5.5** — `nexovue_scheduler/knowledge/tasks.py` *new*, in `TASK_MODULES`, on the shared queue every 5 minutes (`KNOWLEDGE_SWEEP_ENTRY`, only where AI may be offered): per organization with chat on, in one transaction, drops lines whose finding is no longer `recovered`, writes a line per `recovered` finding from columns alone (`service {name} | signal {signal} | {started} to {closed} UTC | closed: recovered`; strict names or "a service"; a project finding up to 5 strict names), at most one per service, signal and day (the newest of a day), then evicts the oldest past the project's share (1,000 lines, never past 5,000 with everything else counted first). `nexovue_scheduler.knowledge` joins `REPOSITORY_AI_MODULES`.
- **T5.6** — `insight/assistant_answer.py`: a `team` label (telemetry text still wins, a count does not), with the oldest cited age; a runbook quote is never folded and is the one quote a sentence may point at as something to run (a command written in the sentence itself is still refused). `assistant/quotes.py` reads a knowledge quote again from Postgres, with its runbook and age, `gone` once the runbook is deleted, and decides "runbook" from the chunk, not the stored answer. The panel: the notes label with its age, the runbook quote's label, warning tone, Copy (none on a cut quote) and **Open the runbook**. The prompt names runbook quotes (`chat-3`).
- **T5.7** — **not yet run: needs the founder's key or local model;** waits on Step 4 (T4.1 to T4.3), which builds the runs it repeats.
- **Two real faults, found by the new tests and fixed:** the panel folded a quote by the stored answer's word, so a fact line claimed as a runbook's would show unfolded (now by what nexovue read again); and the runbook form showed a refusal's title ("Invalid") rather than the API's sentence (now the problem's own detail).
- **Design follows the code:** the data table's `runbooks` and `knowledge_chunks` rows now name the columns as built.

### Step 5, seen failing first

Every new or changed test was seen failing (behaviour broken, run, restored byte-equal by a script that keeps its copy outside the repository and compares bytes afterwards). Clocks pinned throughout (`NOW` in Python; `vi.useFakeTimers({ toFake: ["Date"] })` with `vi.setSystemTime` in the new web tests). Breaks that first **passed** are said, with how the test was strengthened.

- sch:test_assistant_answer.py (5 new, T5.6, AC21, AC40, AC63), each break then restored byte-equal (69 passed):
  - the team label removed -> 1 failed (a runbook sentence read `checked`);
  - a runbook quote folded like a log's -> 1 failed;
  - runbook-only quotes not recognised -> 2 failed (the act-on-runbook sentence removed);
  - the imperative allowed nowhere -> 1 failed;
  - the newest, not the oldest, age stated -> 1 failed;
  - `team_text` read as telemetry text (Step 3's rule) -> 2 failed (the notes label, and a fact line's count);
  - a command line allowed in a sentence beside a runbook quote -> 1 failed.
- An existing test changed for a real shape change: a stored quote now carries `runbook` (false for a log); test_a_quote_stores_its_reference_and_never_its_text updated.
- test_assistant_catalog.py + test_read_surface_structure.py (T5.4, AC14), each break then restored:
  - before the catalog test knew the exception, adding `search_knowledge` failed 6 (not on the surface, sends `title` and `team_text`, no destination row, no console route, no panel target): the real gap the step closes; the test then learned the named exception;
  - the knowledge route reading `CurrentReader` instead of `AssistantTicketReader` -> 3 failed (the surface list, the new ticket-only test, the catalog's exception test);
  - `team_text` declared on a log body -> 1 failed (only knowledge sends the team's words);
  - the knowledge link's sentence dropped -> 1 failed (the design's destination table);
  - a runbook chunk's `content` declared closed -> 1 failed (free text must be fenced).
- api:test_runbooks_live.py (12 new; T5.1, T5.2; AC38, AC69, AC71, AC90, AC92, AC106) on f4s5-postgres, each break then restored byte-equal (12 passed):
  - chunks never closed at a heading -> 1 failed (split by heading);
  - a chunk closed at every heading (no minimum) -> 1 failed (AC106: more than 18 chunks);
  - the 200-runbook cap removed -> 1 failed (the 201st saved);
  - the 32 KiB check removed -> 2 failed (the database's own check refused it as an integrity error, not a sentence; the upload's per-file refusal);
  - the credential warning removed -> 1 failed;
  - the audit row given the session's titles -> 1 failed (at first a weaker break, the session's new objects, PASSED: their repr holds no text; strengthened to the titles themselves);
  - a rewrite keeping the old text's chunks -> 1 failed;
  - a support view given the body -> 1 failed;
  - an upload copying into the first project only -> 1 failed;
  - runbooks offered on the hosted service -> 1 failed;
  - the organization filter removed from the row read -> at first PASSED: the project check after it still bound the organization; removed from both -> 1 failed;
  - the database's own keys, each dropped inside the test's rolled-back transaction: the finding cascade -> 1 failed; the runbook cascade -> 1 failed; the runbook's composite key -> 1 failed (a chunk of another project accepted).
- api:test_assistant_knowledge_live.py (5 new; T5.4; AC41, AC42, AC68, AC84, FR37), each break then restored (5 passed):
  - the project filter removed from the search -> 1 failed (the sibling project's runbook returned);
  - fact lines of a false alarm not excluded -> 1 failed (AC84);
  - the facts-only refusal removed -> 1 failed (AC68);
  - the ticket-only reader accepting any caller -> 1 failed;
  - `plainto_tsquery` in place of `websearch_to_tsquery` -> 1 failed (the tool's own `OR` advice found nothing).
- api:test_assistant_tools_live.py (AC53, AC8, now over every tool including `search_knowledge`, through real uvicorn with a ticket; it gained keywords for a required phrase):
  - the ticket-only reader refusing every caller -> 1 failed (`search_knowledge` 403);
  - the search proving its project by organization only, not the ticket's scope -> 2 failed (the sibling project read over HTTP, and in the knowledge suite); restored 7 passed.
- api:test_assistant_quotes_live.py (1 new; T5.6, FR15), each break then restored (4 passed):
  - a runbook quote folded as the stored answer said -> 1 failed;
  - knowledge never read back -> 1 failed (read `gone`);
  - the runbook flag taken from the stored answer, not the chunk -> at first PASSED (the deleted runbook read `gone` either way); a fact line's quote stored as a runbook's added to the test -> 1 failed.
- api:test_personal_data_live.py (2 new, AC34), each break then restored (18 passed): every runbook of the organization exported -> 1 failed; the truncation flag forced false -> 1 failed.
- api:test_deletion_live.py (the inventory, 34 passed), each break then restored: `knowledge_chunks` left out of `organizations/tables.py` -> 2 failed; `runbooks` named under `users` -> 1 failed (no such cascade).
- sch:test_knowledge_writers.py (10 new; T5.5; AC39, AC82, AC83, AC84) on f4s5-postgres, each break then restored byte-equal (10 passed):
  - any service name written, not only a strict one -> 3 failed (the planted text reached a line);
  - six services named -> 1 failed;
  - the writer selecting `title` -> 1 failed;
  - "already written today" not in what is due -> 1 failed (a lost race stayed due, to fill every later batch); this assertion was added after the first version passed without it;
  - the insert without `ON CONFLICT DO NOTHING` -> 1 failed (the unique index refused the day's second line and the organization's pass failed);
  - any closed reason written -> 2 failed;
  - a deleted project's findings written -> 1 failed;
  - no drop of a line whose finding became a false alarm -> 1 failed (AC84);
  - the newest line evicted, not the oldest -> 1 failed;
  - runbooks not counted before fact lines -> 2 failed;
  - the pass run on the hosted service -> 1 failed;
  - organizations with chat off swept too -> 1 failed;
  - `nexovue_platform.knowledge.EXPIRED` left out of the expiry sweep -> 1 failed (a deleted project's runbook stayed).
- sch:test_beat_schedule.py and test_detection_has_no_ai.py (1 new each; T5.5, AC82), each break then restored (32 passed): the knowledge entry scheduled on the hosted service too -> 1 failed; `nexovue_scheduler.knowledge` taken off `REPOSITORY_AI_MODULES` -> 1 failed; the task left out of `TASK_MODULES` -> 1 failed.
- api:test_knowledge_records.py (15 new; T5.1, AC69, AC106, D77; offline), each break then restored (15 passed):
  - a hard split mid-word -> at first PASSED (the test looked for the wrong broken word); strengthened to "every piece but the last ends with a whole word" -> 1 failed;
  - the migration's frozen chunk size drifted from the shared record -> 1 failed;
  - the save warning using only part of the send mask's list -> 2 failed;
  - a character lost at a split -> 2 failed (the random runbooks lost text; the heading case).
- **A real fault, found by the new panel test and fixed:** a quote's fold came from the stored answer alone, so a quote stored as a runbook's but read again as a fact line would show unfolded. The panel now folds by what nexovue read again (`read.folded`), as the API decides it from the chunk.
- Web, features/assistant (vitest; 4 new in answer.test.ts, 2 in step-links.test.ts, 4 rows in states.test.ts; clocks pinned with `vi.useFakeTimers({ toFake: ["Date"] })` and `vi.setSystemTime`), each break then restored (75 passed):
  - FR42's sentence not said on an empty knowledge step -> 1 failed;
  - `/settings/runbooks` left out of `STEP_TARGETS` -> 1 failed (and `test_assistant_catalog.py` failed the same way before the panel knew it);
  - the notes label without its age -> 1 failed;
  - a runbook quote shown as a log's -> 2 failed;
  - no warning tone past 180 days -> 1 failed;
  - Copy offered on a cut runbook quote -> 1 failed (a first version matched the "Copy" text every copy control shows, and passed; matched by its own label since);
  - a quote folded as the stored answer said -> 1 failed (the fault above).
- api:test_assistant_error_cases.py: the "Knowledge has no match" row now names its tests; before the web test existed it failed (named test missing) -> 1 failed; and `test_assistant_catalog.py`'s new step-view test: the days on every step -> 1 failed; never given -> 1 failed.
- **A real fault, found by the new screen test and fixed:** the runbook form showed a refusal's title ("Invalid") rather than the API's sentence; the form, upload and delete now show the problem's own detail (`refusalSentence`).
- Web, features/runbooks/runbooks.test.ts (10 new; T5.3; FR32, AC69, AC90, AC92; clock pinned), each break then restored (10 passed): no warning tone past 180 days; writing offered to a viewer; no cap; no "may be sent to" sentence with chat on; no second click for a credential; no local 32 KiB check (the request was sent); a support view shown an empty body; delete with no confirmation; an upload's refusals not listed; a credential resend sending every file to every project -> each 1 failed.
- Web, app/HostedOnly.test.ts (2 new), shared/settings/settings-nav.test.ts (1 new), app/routes/route-manifest.test.ts, each break then restored: `SelfHostedOnly` showing its screen on the hosted service -> 1 failed; runbooks not marked self-hosted only -> 1 failed; the one-runbook route not listed as reached by following something -> 1 failed; `console-routes.json` missing `/settings/runbooks` -> 1 failed.
- api:test_assistant_hostile_live.py (1 new; T5.6; AC40, AC69, AC42; the real loop as the AI account, every read over real HTTP to the real app, run alone), each break then restored (2 passed): the send-time mask off -> 1 failed (the runbook's planted password reached the provider); the notes label removed -> 1 failed (both sentences `unchecked`); a runbook quote not recognised as one -> 1 failed (the act-on-runbook sentence removed); the search reading every tenant -> 1 failed (the other organization's runbook reached the provider).
- Web, shared/settings: the rail's filter moved out of `SettingsLayout` into `offeredSettings` so it can be tested; the self-hosted rule removed -> 1 failed (runbooks offered on the hosted service); restored 4 passed.

- **Found in `/review-diff` and fixed:** a fact line printed its times in whatever zone the database connection handed back, under a "UTC" label. sch:test_knowledge_writers.py (1 new): the zone kept -> 1 failed (19:30 for a 14:00 UTC finding read in Kolkata); restored 11 passed.

### Step 5, `/review-diff` (2026-11-21)

Read against the design, the plan, ADR-055 and ADR-067, and the review criteria (`docs/quality/code-review.md`): every changed file is explained by T5.1 to T5.6 or by a gate the plan names; tenancy (every new Postgres statement binds `org_id`; the knowledge search's project is the ticket's, proven before it scopes the read; the scheduler's one cross-tenant read is named in its structure test); authorization (four new writes on `MayManageKnowledge`, the reads person-only and off the read surface, the search ticket-only); masking (a runbook's text passes FR40's mask before the model reads it, and the save warning is the same list); secrets (no text in an audit row; no credential in a fixture but planted test strings); migration (roll forward only, its reasoning stated); web (imports `shared → features → app`, tokens only, sentences capped and containers not, no window breakpoints, an em dash for an unreadable age). **HIGH:** none. **MEDIUM:** the fact line's zone, fixed above. **LOW, recorded, not changed:** an upload naming 50 projects could write up to 2,500 runbooks in one transaction, bounded by the 200-a-project cap; performance is the hardening pass's (T8.1).

### Step 5, checks (2026-11-21, counts read from the output)

Stores: `f4s5-postgres` (45432, migrated by the real `setup` step 0075 → 0076; the object store skipped, not configured) and `f4s5-clickhouse` (48123, and 49000 for the Go migration step, 42 migrations), started for this worktree on its own network `f4s5-net`; every live run through a private wrapper that refuses any other store address; both containers, their volumes and the network were removed at the end. No real provider was called.

- `uv run ruff check .` clean; `uv run ruff format --check .` 1478 files formatted; `uv run mypy .` **no issues in 1095 source files.**
- Biome on every touched web path: clean. `pnpm -r --if-present run typecheck`: clean. `verify-tokens`: clean.
- **Whole web suite: 2571 passed (205 files).**
- **API, gates and chat's suites, `-n 2`** (cross-service contracts, authorization coverage, read surface structure, spec and live, rate-limit coverage, callers and limiter, both tenancy structures, tenant chokepoint and isolation, migration rules, setup schema, body limit, policy, auth routes, Compose contract, deletion, keys, personal data, insight grants, MCP tools, injection and live, services, infrastructure, logs, access log, every chat suite but the hostile one, runbooks, knowledge, knowledge records): **4074 passed, 14 errors**: `test_services_live.py` (12) needs the seeded `acme/production` organization and `test_tenant_isolation_live.py` (2) the running dev stack, neither made on these stores; not this change (as in Step 3).
- **Hostile live suite, alone: 2 passed.**
- **Scheduler, platform and `tools/ai-eval`, `-n 2`** (chat's and F3's insight suites, knowledge writers, beat schedule, detection-has-no-AI, jobs live, the expiry suites, all platform tests, the fake provider): **1645 passed.** The expiry-related suites (detection and SLO prune, dashboards' expiry, release sync, coordinator): **43 passed serially**; under `-n 2` the dashboards' expiry suite failed twice, because its clock is pinned to 2026-11-12 while another suite's expiry sweep runs on the real clock and purges its 29-day-old dashboard: two suites racing on the install-wide sweep, not this change (open decision 28).
- After `/review-diff`'s fix (2026-11-21): `test_knowledge_writers.py` 11 passed; ruff, format and mypy clean again (1478 files formatted, no issues in 1095 source files).

### Step 5, not built or not run

- **T5.7, the bed re-run** (injection in runbooks and fact lines across later questions, and a cost run): **not yet run: needs the founder's key or local model;** waits on Step 4.
- **The founder's judging** of Step 5 (paste a runbook, ask, delete, ask again, with a provider without embeddings) against a real model: **not yet run: needs the founder's key or local model.** Its parts are each held by a test against fake providers (`test_assistant_hostile_live.py`, `test_assistant_quotes_live.py`, `test_assistant_knowledge_live.py`).
- **Waiting for Step 7 (notes, F22's cases):** note chunks, fact lines of a resolved case and the deletion exception for one, "a note must outrank 100 fact lines" and "the settings screen counts what is not searchable" (AC83's note halves), a poisoned note (AC40's note half, T7.3).
- **Waiting for Step 6:** meaning search, the `nexovue_insight` grant on `knowledge_chunks` (it serves embedding), "keyword only" in the Chat section. Built with Step 6.
- **Not measured (T8.1):** that detection and expiry are not delayed with the knowledge sweep at the cap (AC82's first half).
- **Hardening, deferred:** Playwright, browser and phone runs of the runbooks screen and the runbook quote; performance (search time, the sweep at the cap, an upload of 50 files); customer docs (`docs/product/setup/`, T8.3); design QA of the new words (open decision 23); the founder's judging.

### Step 3 (committed)

**Step 3 built 2026-11-12, committed 2026-11-12** (built in worktree `wt/f4s3` on Step 2's commit; committed locally, not pushed). **No migration:** nothing new is stored but JSON inside `assistant_messages.answer` and a note in `assistant_steps.failure`; the AI account's one new grant (the retention view) is `setup`'s `INSIGHT_GRANTS`, as F3's are. **No new ADR (ADR-056 to ADR-059 unused):** every decision below sits inside the approved design or is an open decision awaiting the founder. Against fake providers only; **the founder's judging with real questions is not yet run: it needs the founder's key or local model.**

### Step 3, what was built

- **T3.1** — `nexovue_platform/assistant_tools.py`: 20 tools added, 23 in all: `get_finding`, `get_finding_explanation`, `get_service`, `get_service_detection`, `list_service_endpoints`, `get_service_trend`, `get_service_map`, `list_releases`, `get_release_comparison`, `list_incidents`, `get_incident`, `list_checks`, `list_error_groups`, `get_error_group`, `list_traces`, `get_trace`, `list_metrics`, `get_metric_histogram`, `list_hosts`, `get_host_trend` (left: `search_knowledge`, Step 5; `get_finding_timeline`, Step 7). The catalog gained path slots with a closed shape per kind (uuid, trace id, group id, service and metric names), row sources (a list, one object, or the whole body; several in order), flattened trends, dotted fields (`this_version.requests`), a basis by row (`from_row`: F3's fact basis, F28's release basis), units (`ms`, `fraction`), `sampled` lists and quote keys. Routes that take no `project_id` compare the ticket's scope with what they load. **Never sent:** titles, deploy metadata, a check's config, an incident's detail and notifications, F3's model wording. **Aged out decided in the loop** (`insight/assistant_reads.py` `kept_since`, `within_retention`): each windowed tool on its own table's clock, read from the new `project_retention` grant in the question's scope; a range wholly past it ends `aged_out` with no read and the model told "not evidence that nothing happened"; one only starting past it is read from the first moment kept (open decision 11). The step records the first day kept as `kept_since:YYYY-MM-DD` in `failure`, which the API turns into a date (`StepView.kept_since`). The internal client fills route slots, refusing a value that could move the route or a slot set that differs from the route's.
- **T2.8's live arm (open decision 6, now built)** — through real uvicorn: every non-closed argument of every tool, with nine path tricks, is refused before any read, the server sees no request, and sent raw, `x%2Fdetection` reaches the detection handler through the service route's slot, so the rule is what closes it.
- **T3.1a** — `services/router.py` `service_trend`, `list_service_endpoints`; `infrastructure/router.py` `list_nodes`, `node_trends`: `CurrentReader`, `require_readable_project`; `THE_SURFACE`; `docs/product/setup/api.md`. Tokens gain them, as the design says.
- **T3.2** — `insight/assistant_answer.py`: quotes by reference (`step`, `tool`, `field`, the row's own keys, `folded`; never the text), only from a field that can be re-read (log `body`/`attributes`, an error group's message), at most 5 with a sixth dropped and counted; the worker runs the way-out and command checks on each value and folds it; a sentence telling the reader to act on a quote (`run`, `execute`, `paste`, "the command below") is removed and counted; **figures** flagged by offset with unit-aware rounding, leaving out dates, times and digits inside a cited name; a sentence citing only sampled lists matches only values with a unit; labels by row and by field (a cited `telemetry_text` basis gives the text label; traces' numbers are telemetry text). The prompt says how to quote (`chat-2`). `logs/search.py` `LOG_LINE_AT` and `log_line_at` (tenant, project, service, severity, `received_at`, the millisecond's range, the row hash); `assistant/quotes.py` *new* and `assistantquotes/router.py` *new*: `GET /assistant-quotes/{message_id}`, the person's own answer only, each quote `shown`, `gone` or `read_failed`, cut at 500 characters; on `TELEMETRY_PATHS` (AC107).
- **T3.3** — `features/assistant/step-links.ts` *new*: every destination of the design's table (`STEP_TARGETS`, held to the catalog by `test_assistant_catalog.py`), route parameters, a step's outcome in nexovue's words (no rows, latest N with no time filter, aged out with its date, refused for now), **Copy steps** (nexovue's words and links; no model word, AC93). `StepRow.tsx` uses it; steps show the person's zone from the question's context. `AnswerSentences.tsx`: the flag beside each unmatched figure, quotes read again (`useQuotes`), folded under the warning with **Show it**, inert `<pre>` with control and direction characters as escapes, "no longer available" apart from "Could not read this now" with Retry. `/logs` `q` (`features/logs/search.ts` `searchPhrase`), read into the box on arrival.
- **T3.3a** — `shared/url-window.ts` `keepUrlProject`, `urlProjectApplies`, `useUrlProject`; joined in `validateSearch` for `/`, `/findings/$findingId`, `/services`, `/service-map`, `/metrics`, `/alerts`, `/alerts/incidents/$incidentId`, `/synthetics`, `/traces/$traceId`, `/releases/$releaseId`; `WindowControl.tsx` selects the address's project on arrival for both window and project-only links (the window's own effect was replaced by this one). No stored window is written.
- **T3.4** — `features/assistant/starters.ts` *new*: the design's four rows by page (finding, service, traces/logs/errors, elsewhere); the service page now names itself to chat (`ServiceDetailScreen.tsx`), so a question there carries the service.
- **T3.5 (part)** — `api:test_assistant_error_cases.py` *new* reads the spec's *Error cases* table and holds every row to a test that produces it and one that shows it, or to the step that builds it; `features/assistant/states.test.ts` *new* holds each sentence word for word. Two rows had nothing behind them and were built: **"No telemetry in this project yet"** (`GET /assistant/availability` answers `no_data` from the ingest meter; the panel says so with a link to set up sending data, and offers no box), and the **delete refusal's sentence**, which now is the spec's "Stop this question first." Keyboard and focus (AC50) and a link in a fresh session (AC22): `tools/e2e/tests/assistant.spec.ts`, **written, not run** (hardening).
- **A real fault, fixed:** the loop read any 404 as "ticket refused" and ended the whole question; with item tools a 404 means "this project holds no such trace, finding or group" (every ticket refusal is 401 or 403). Now a failed step, `not_found`, and the question goes on.
- **A real fault, fixed:** Step 2 put `list_services` on the `derived_capped` clock; its route reads `endpoint_stats_1m`, a `derived` table.
- **Tests and helpers:** `api:test_assistant_tools_live.py`, `test_assistant_quotes_live.py`, `test_assistant_error_cases.py`, `assistant_served.py` *new* (the served API, now shared with the hostile suite, which lost its own copy); `assistant_live.reported`; `features/assistant/answer.test.ts`, `step-links.test.ts`, `states.test.ts`, `app/assistant-step-targets.test.ts`, `features/logs/search.test.ts` *new*; the catalog, loop, answer, client, panel and route tests extended.

### Step 3, seen failing first

Every new or changed test was seen failing (behaviour broken, run, restored byte-equal by a script that compares the file afterwards). Clocks pinned throughout (`NOW` in Python; `vi.useFakeTimers({ toFake: ["Date"] })` with `vi.setSystemTime` in the new web tests). Four breaks first **passed**, and each test was strengthened until it failed: they are said below.

- test_ratelimit_coverage.py (AC107): /assistant-quotes removed from TELEMETRY_PATHS -> 2 failed (the ClickHouse-router rule and the new AC107 test); restored 25 passed.
- test_assistant_tools_live.py, AC53/AC8/T3.1a (1): node_trends proving its project by organization only (`require_project`) instead of the ticket's scope -> at first PASSED (the test asked only another organization's project, which the organization check also refuses); strengthened with a second project in the same organization -> 1 failed; restored 2 passed.
- test_assistant_tools_live.py + test_assistant_api_client.py, AC10 (through real uvicorn): `/` allowed in a path slot -> 2 failed (the live tricks test, the client's slot test); restored 18 passed.
- test_assistant_api_client.py, route slots (5 new cases): the slot-set check removed from build_url -> 4 failed; the per-value path check removed -> 1 failed; restored 22 passed.
- test_assistant_loop.py, AC24 (5 new): no retention clock at all -> 3 failed; the rollup clock reading raw days -> 1 failed (the 10-day rollup read); no narrowing of a straddling range -> 1 failed; restored 21 passed.
- A real fault found while reading for T3.1: Step 2 declared `list_services` on the `derived_capped` clock, but its route reads `endpoint_stats_1m`, a `derived` table; corrected in the catalog, and now held by the new clock test:
- list_services back on `derived_capped` -> 1 failed; restored 190 passed.
- test_assistant_catalog.py, field kinds: get_trace's `status_message` (a sender's free text) declared an `id` -> at first PASSED: an `id` admitted any string, a gap inherited from Step 2's test. Strengthened: an `id` must be an id-named field (`id`, `row_key`, `*_id`); then 1 failed; restored passed.
- test_assistant_catalog.py, destinations (AC22): get_service's link not carrying the range -> 4 failed (the four tools sharing that link); restored passed.
- test_assistant_catalog.py, never sent (AC12): an incident's `detail` added to the incident fields -> 2 failed (list_incidents, get_incident); restored 190 passed.
- test_assistant_answer.py, figures (AC20; 13 new cases), each break then restored (91 passed with loop and hostile): a fraction not readable as a percentage -> 4 failed (with Step 2's counted-label tests); milliseconds not readable as seconds -> 2 failed; dates and times read as figures -> 1 failed; a sampled list's codes matched as counts -> 1 failed.
- test_assistant_answer.py, quotes (AC63, AC87; 12 new cases): quotes never folded -> 1 failed; the act-on-quote removal off -> 4 failed; the 5-quote cap off -> 1 failed; a quote of a field that cannot be re-read accepted -> 2 failed.
- test_assistant_answer.py, labels by row (AC21): `from_row` read as telemetry whatever the row -> 2 failed (a seen release and a basis-less fact).
- test_assistant_quotes_live.py (3, AC19, AC31) on real ClickHouse: the quote's instant bound to one microsecond instead of the wire's millisecond -> 2 failed (the sub-millisecond log line not found); a failed read reported as gone -> 1 failed; the owner condition dropped -> at first PASSED (the other person was in another organization); rewritten with an admin of the same organization -> 1 failed; restored 3 passed.
- Web, features/assistant (vitest), each break then restored (49 passed):
  - Copy steps carrying the step's search phrase -> 1 failed (AC93's planted marker found);
  - figure flags not rendered -> 1 failed (AC20 on screen);
  - a folded quote shown open -> 1 failed (AC63);
  - a quote's failed read shown as gone -> 1 failed (AC19);
  - Traces, Logs and Errors given no starters of their own -> 3 failed (AC49);
  - a step link offered without its route parameter -> 1 failed;
  - control and direction characters not escaped -> 1 failed (AC63);
  - the finding's id not sent with the question -> 1 failed (AC49).
- app/url-window-routes.test.ts, T3.3a and `/logs` `q` (12 new): the shell not selecting the address's project -> 10 failed; `/service-map` left off the project-only screens -> at first PASSED (the project selected by an earlier test stayed in the module store); fixed by choosing another project first in each test, then 1 failed; `q` not read into the box -> 1 failed; any string accepted as a project -> 1 failed; restored 23 passed.
- features/logs/search.test.ts (2 new): the length cap removed -> 1 failed; restored 2 passed.
- test_assistant_catalog.py, panel destinations (1 new): `/service-map` left out of the panel's STEP_TARGETS -> 1 failed; restored passed.
- "No telemetry in this project yet" (an *Error cases* row Step 2 had not built): test_assistant_api_live.py, the never-reported check removed from availability -> 1 failed; restored 17 passed. AssistantPanel.test.ts, the setup link not offered for `no_data` -> 1 failed; restored passed.
- test_assistant_loop.py, a read refused for now (2 new): a 429 not told apart from a store down -> 1 failed; restored 23 passed.
- test_assistant_error_cases.py (23, AC30): a listed test that does not exist -> 1 failed; a row added to the spec's table with no entry -> 1 failed; restored 23 passed.
- The delete refusal's sentence did not match the spec's *Error cases* ("Stop this question first"); changed to the spec's, and test_assistant_api_live.py now holds it: Step 2's sentence put back -> 1 failed; restored 17 passed.
- states.test.ts (15, AC30): the no-telemetry sentence changed -> 1 failed; restored 15 passed.
- A real fault found while writing up: the loop read any 404 as "ticket refused" and ended the whole question, but a Step 3 item tool answers 404 for a trace, finding or group the project does not hold (every ticket refusal is 401 or 403). Fixed: a 404 is a failed step, `not_found`, and the question goes on. test_assistant_loop.py: 404 put back among the refusals -> 1 failed; restored 24 passed.
- Found in /review-diff: the API's step-link building had no API-side test. test_assistant_catalog.py (2 new): a link built without its route parameter -> 1 failed; a malformed kept-since note shown as text -> 1 failed; restored 193 passed.

### Step 3, checks (2026-11-12, counts read from the output)

Stores: `f4s3-postgres` (45432, migrated by the real `setup` step to 0073; its last step, the object-store bucket, failed as no object store was started, and nothing here needs it) and `f4s3-clickhouse` (48123, 49000 for the Go migration step, 40 migrations), started for this worktree on a network of its own; every live run through a private wrapper that refuses any other store address. No real provider was called.

- `uv run ruff check .` clean; `uv run ruff format --check .` 1416 files formatted; `uv run mypy .` **no issues in 1039 source files** (on a cold cache: a warm one reported a stale `nexovue_api.auth` error that a cold run does not).
- Biome on every touched web and e2e file: clean. `pnpm -r --if-present run typecheck`: clean. `verify-tokens` (`packages/ui/scripts/verify-tokens.js`): clean.
- **Whole web suite: 2231 passed (183 files).**
- **API, gates and chat's suites, `-n 2`:** cross-service contracts, authorization coverage, read surface (structure, spec, live), rate-limit (coverage, callers, limiter), both tenancy structures, tenant chokepoint and isolation, MCP tools, injection and live, services, infrastructure, logs, deletion, keys, personal data, access log, insight grants and every chat suite: **3636 passed, 12 errors**: the 12 are `test_services_live.py`, which needs the seeded `acme/production` organization (`just seed`), never made on these stores; not this change. The hostile live suite diffs every table, so it cannot share the database with a parallel suite; **serially it passed** (as in Step 2).
- **Scheduler, platform and `tools/ai-eval`, `-n 2`** (chat's and F3's insight suites, detection-has-no-AI, beat schedule, jobs live, all platform tests, the fake provider): **888 passed.**
- Final run after `/review-diff`'s fixes: chat's changed suites 674 passed (`-n 2`), hostile live 1 passed serially, panel and app web tests 162 passed.

### Step 3, not built or not run

- **The founder's judging with real questions** (research section 2 against a real model): **not yet run: needs the founder's key or local model.** AC23 is the bed's (T4.1).
- **Error cases waiting for later steps:** "Service still learning, or stopped reporting" (the bed, T4.1), "No `vector` extension" (Step 6), "Knowledge has no match" (Step 5); the delete flow's render test is the e2e spec's.
- **The project's zone beside a step's range** (design, *A step*): the step shows the person's zone only; the message carries no project zone yet (open decision 15).
- **`HostileProvider`'s calls are Step 2's tools only**; path tricks on every Step 3 tool are held by the tools suite instead (open decision 16).
- **Written, not run (hardening):** the two new `assistant.spec.ts` cases (a link in a fresh session on its project and range; keyboard and focus, AC50), the screen-reader pass. Also deferred: performance (quote reads, 23 tool descriptions in each call's tokens), customer docs, design QA.

### Step 2 (committed)

**Step 2 built and committed 2026-11-12 (local commit on `wt/f4s2`, not pushed).** Rebased onto F3 Step 5. Migration **0073** (`down_revision` 0072). ADR **055**; no new ADR. Next: the founder's one real local-model call, then the founder's judging, then Step 3.

### Step 2, rebase onto F3 Step 5 (2026-11-12)

- **Conflicts and how they were resolved:** `insight_account.py` (F3's comment kept, F4's wider grant: `projects (id, org_id, name, timezone, deleted_at)`, `organizations (id, deleted_at)`); `test_insight_grants_live.py` (F4's expected grants under F3's comment); `beat.py` (F3's `SWEEP_INSIGHT_TASK` import kept); `jobs/registry.py` (`TASK_SETS["insight"]` holds F3's check, word and sweep, and F4's loop and sweep).
- **What F4 had built that F3 Step 5 now owns, replaced (open decision 1):**
  - *The fence.* F4's own `Untrusted` and fence token in `ai/chat.py` are deleted; chat sends F3's `untrusted.Untrusted` (hidden characters stripped, one JSON string, a tag drawn per part). F3's class gained one field, `max_characters` (default 4,000, F3's "a guess F4 tunes"): tool results pass `2 × CHAT_MAX_CHARACTERS`, since `bounded` has already cut them to whole rows and 4,000 would cut that JSON mid-row. Step labels became `step s1 result` (F3 allows letters, digits and spaces).
  - *The reservation.* F4's statements in `insight/assistant_store.py` are deleted. F3's `insight/store.py` gained `reserve_month()`, the one statement wording and chat both reserve through; chat's share moved from `assistant_records` to F3's `ai/limits.py` (`CHAT_SHARE`, `chat_ceiling()`), and `month_of` is F3's.
  - *The word lists.* F3's `ai/validate.py` names its lists publicly (`CAUSAL_PHRASES`, `ORIGIN_WORDS`, `COMMAND_WORDS`, `URL`, `EMAIL`, `has_any`, `opens_with_command(words=…)`); F4's `assistant_answer.py` uses them and keeps only chat's additions (a few command and cause words, Markdown, HTML, `data:`, host names with a port, line separators). Its hidden-character check is F3's `strip_hidden`. Commands are now caught at every clause start, not only the first: stricter.
- **Migration** `0080_assistant.py` became `0073_assistant.py`, `down_revision` 0072; every reference followed (migration rules, models, grants, queue, store, records, tests, ADR-055).

### Step 2, what was built

**Earlier, 2026-11-11 (the backend):**

- **T2.7a** — `nexovue_platform/ai/chat.py` *new*: `ChatRequest` (tools, `Untrusted` parts), `ToolSpec`, `ToolCall`, `ChatResponse`, `ChatProvider`, `provider_label`; the **fence**: each untrusted part JSON-encoded with every non-ASCII character escaped, between markers holding a random token per request. `ai/chat_formats.py` *new*: tool offering and tool-call parsing for `openai_chat`, `anthropic_messages`, `bedrock_converse`, on F3's endpoints, headers and helpers; a call and text together uses the call; a provider ignoring the tools returns its text (AC94). **A separate request type, not `tools` on F3's `AIRequest`**: F3's type refuses non-trusted parts by design and its canary test holds that (design, *Differences* 12) — open decision 2. Anthropic's tool shape read from its *Define tools* page 2026-11-11; OpenAI's and AWS's pages were unreachable from this session, so those two shapes are **from memory and unverified until a real call** (said in the module).
- **T2.0 (part)** — `nexovue_platform/ai/testing.py` *new*: `ScriptedProvider`, `HostileProvider` (calls each tool with each trick, then answers with every way out: links, images, Markdown, HTML, commands, emails, uncited causes, a citation to a step that never ran, an unknown abstain word, a copy of a fenced result). The container came 2026-11-12 (below).
- **T2.0a** — the spike, **passed**: `sch:test_assistant_account_live.py` *new* runs the worker's real store as `nexovue_insight` under migration 0073's row-level security: take, recheck, reserve, record a call, a step, a read and a count, spend, finish; then the account sees neither the question nor its steps; it cannot read `ticket_hash`, `answer` or `wrong_reason`; its own direct final write is refused by the policy; a second finish rewrites nothing; the sweep, as the account, ends a dead question `unavailable` and charges what it held. **Run on both owners:** the superuser-owned dev database, and a database owned by a non-superuser (`f4owner`, `NOSUPERUSER CREATEROLE`) — 9 passed each. The fallback (functions only) was not needed.
- **T2.1** — `assistant/tickets.py` *new* (`nvat_` + 32 characters, SHA-256 on the message, sealed under `AI_SECRET_KEY` for its own message, `valid_for` with each refusal of AC11 in one sentence); migration `0073_assistant.py` *new* (built as 0080, re-chained 2026-11-12): seven tables (`assistant_settings` with `mask_emails`, `assistant_conversations`, `assistant_messages`, `assistant_steps`, `assistant_question_counts`, `assistant_monthly_counts`, `assistant_read_log`), no vector column, row-level security on messages and steps, `assistant_finish_message` and `assistant_record_spend` (`SECURITY DEFINER`, `EXECUTE` revoked from `PUBLIC`), and F3's three job functions widened to `assistant` with the claim taking a queue list; models; `organizations/tables.py` and the delete map; `keys.SEALED_COLUMNS` gains `assistant_messages.sealed_ticket`. Closed lists in `nexovue_platform/assistant_records.py` *new*, held to the migration by `sch:test_assistant_records.py` *new*.
- **T2.2** — `auth/dependencies.py`: the ticket branch of `get_current_reader`, `Reader.message_id`, allowing by the **matched handler** (`TICKET_HANDLERS`); a ticket reads as neither person nor token. **The check is registered on the app (`app.state.ticket_check`, `main.py`), not imported,** because importing it made every reliability-target route reach AI code (`test_detection_has_no_ai.py` caught it). `_refuse_a_token_here` and `get_current_session` refuse a ticket with their own sentence. Every `not reader.is_token` in findings and SLO routes became `reader.is_person` (trap 2; F3 had only added `is_person`).
- **T2.3 (part)** — `roles.py`: `scheduler-insight` claims `assistant,insight` on 6 slots, the API runs `--no-access-log` (AC65); `QUEUE_CAPS` gains `assistant: 4` and `insight: 4`; `queue.INSIGHT_STATEMENTS` claims both through the widened function; both Compose files: the API joins `ai`, `INTERNAL_API_URL`, 576 MB, the dev worker command; `INSIGHT_GRANTS` gains F4's column grants and the two functions (`projects` widened to `org_id, name, timezone`; `organizations (id, deleted_at)`); F3's grant, parity and role tests follow. The live arms came 2026-11-12 (below).
- **T2.4** — `ratelimit/ticket_owners.py` *new*, the middleware's ticket branch (the person's and organization's allowance, on the path's tier), `ratelimit/policy.tier_for` (AC13).
- **T2.5 (part)** — `ASK_ASSISTANT`, `MANAGE_KNOWLEDGE` in all five copies (`test_policy.py`, `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, team-and-api-access FR1) and `test_auth_routes.py`; `members.scrub` settles a question in flight (`charge`), then deletes the person's conversations and counts and unnames the read record (`assistant/privacy.py` *new*, C1). Expiry and the export came 2026-11-12 (below).
- **T2.6 (part)** — `assistant/router.py`, `service.py`, `schemas.py`, `standing.py` *new*: availability, conversations, ask (one transaction: standing, the day's count atomically, the person's own stale row marked, the message with ticket and clocks under the one-in-flight index, the job with a wake), poll, conversation page, stop (`keep`), wrong and undo, delete (409 while held), settings, consent bound to F3's fingerprint, chat's disclosure version and the data level, the read record. Each refusal carries its own `code`. A reader past the clock sees `unavailable` before the sweep. Two small additions to F3's `ai/service.py`: `connection_in_use`, `consent_fingerprint_of`, and `availability_of` moved there from `ai/router.py`. The preview came 2026-11-12 (below); the context probe is open decision 9.
- **T2.7** — `nexovue_platform/assistant_tools.py` *new*: the catalog (`list_findings`, `list_services`, `search_logs`), field kinds and bases, arguments, link table, `TICKET_HANDLERS`; `bounded` moved here from the API, caps as arguments (`assistant/bounds.py` deleted). `nexovue_platform.assistant_tools` joins `REPOSITORY_AI_MODULES`. **`search_logs` sends no `severity` text**: the catalog test found it is the sender's own string (open decision 4).
- **T2.8 (part)** — `insight/api_client.py` *new*: fixed base URL from `INTERNAL_API_URL`, no redirects, values encoded whole, the ticket in a header, the path-slot rule. **The live arm through real uvicorn is not built:** no Step 2 tool has a path slot, so it waits for Step 3's tools (open decision 6).
- **T2.9 (part)** — `insight/assistant.py`, `assistant_prompt.py`, `assistant_answer.py`, `assistant_reads.py`, `assistant_store.py`, `assistant_sweep.py` *new*; `TASK_SETS["insight"]`, not `TASK_MODULES`; `beat.py`'s `sweep:assistant` every minute on `insight`. One clock; the reservation (F3's statement, chat's 0.8 share); the check before each call; spend through the function; the recheck before every call; steps, reads and counts; one send-back; the final call with no tools; FR40's send-time masks; checks of design section 4 (shape, citations, ways out, commands, copying, labels, arguments). The budget suite and the real kill came 2026-11-12 (below).
- **T2.10 (part)** — `sch:test_assistant_hostile.py` *new* (AC15a, offline). The live and web halves came 2026-11-12 (below).
- **T2.10a** — `sch:test_assistant_structure.py` *new* (AC81).
- **Tests and helpers:** `packages/py-platform/tests/test_ai_tool_calls.py`, `sch:test_assistant_loop.py`, `test_assistant_answer.py`, `test_assistant_api_client.py`, `test_assistant_records.py`, `api:test_assistant_catalog.py`, `test_assistant_tickets_live.py`, `test_assistant_api_live.py`, `test_assistant_privacy_live.py` *new*; `tests/helpers/assistant_fakes.py`, `apps/api/tests/assistant_live.py` *new*. Live API suites run inside a rolled-back transaction; the account suite makes and deletes its own organizations, with clocks far ahead, so a stack's sweeps never meet its rows.

**Then, 2026-11-12:**

- **A real fault, found by the new live hostile suite and fixed at the root:** the worker sent no `project_id`, so every catalog read through the real API was refused (422); the offline tests used a recording client and could not see it. The loop now adds the question's own project to every read (`PROJECT_QUERY`), never the model's.
- **A second real fault, found by the panel's typecheck:** the catalog linked `list_findings` to `/findings`, which is not a console route (F20 moved the feed to `/`). Fixed in the catalog; `test_assistant_catalog.py` now holds every step link to `console-routes.json`.
- **T2.0** — `tools/ai-eval/fake_provider/server.py` *new*: a fake OpenAI-compatible provider, standard library only (FD3, ADR-047), `scripted` (reads services, then answers citing the read) and `hostile` (`HostileProvider`) modes; the dev Compose service `fake-provider` (profile `fake-provider`) at 172.30.250.10 on a fixed dev-only `ai` subnet (`compose.infra.dev.yaml`), which `scheduler-insight`'s default `AI_PRIVATE_ENDPOINTS` names. `tools/ai-eval/test_fake_provider.py` *new*; `test_compose_contract.py` holds address, subnet and endpoint together. **Reaching it from the running role (`sch:test_jobs_live.py`) is not run**: it needs the dev stack, which this build did not start.
- **T2.3** — `api:test_api_access_log_live.py` *new* (AC65): a real uvicorn server, configured from the `api` role's own command, serves the app; a planted search phrase is in none of its output. AC66's arm is added to F3's `test_insight_network_live.py` (the API open by name from `scheduler-insight`): **written, not run** (needs `just insight-fence`'s installed kit). AC29: `sch:test_jobs_live.py` fills each AI queue in turn at the role's real slots and caps; a job on the other queue is claimed before it expires.
- **T2.5** — chat's rows in the expiry sweep (`assistant_records.EXPIRED`): questions past their project's derived retention (never one in flight or holding tokens), conversations with no messages left or whose project or organization is deleted (once nothing in them is held), the read record and question counts at 13 months. `jobs/expiry.py` logs a failing table and runs the rest. `sch:test_assistant_expiry_live.py` *new* (AC35). The personal-data export gains `chat_questions` (question, outcome, stored answer, steps' arguments as stored, wrong mark) with `chat_questions_truncated`; `test_personal_data_live.py` (AC34) and `personal-data/spec.md` follow. Runbooks join the export when they exist (Step 5). `assistant_monthly_counts` has no expiry: the design names none; it holds no text.
- **T2.6** — `GET /ai/preview?purpose=chat&project_id=` (`assistant/preview.py` *new*), on F3's route, which moved to a router of its own (`ai/preview/router.py`) on the telemetry rate tier, since chat's preview reads ClickHouse; the rest of `/ai` stays Postgres-only on the default tier (`test_ratelimit_coverage.py` found the gap): every kind chat can send, generated from the catalog, with its data level; one sample per free-text kind as the provider would see it (FR40's masks, `bounded`, F3's fence), real where the project has a log line and labelled otherwise; the project's switched-off masking rules from `masking_config`; 0 sent for embedding; the consent fingerprint. FR40's masks moved to `nexovue_platform/assistant_masks.py` *new*, so the worker and the preview share one copy. `api:test_assistant_preview_live.py` *new* (AC2). **The context-length probe is not built** (open decision 9).
- **T2.9** — `sch:test_assistant_budget_live.py` *new* (AC25, AC57): eight questions reserving at once at a nearly full month take exactly three reservations; BR13's one overshoot is reported (35,000 tokens in the run) and the next question is refused; below 60,000 left a question is refused while a critical explanation reserves; at 100 million and 79.9% a question reserves. `sch:test_jobs_live.py`: a worker child really killed between two calls, and one killed at the hard limit inside a call (AC27), leave the question running; past its clock chat's sweep, as the AI account, ends it `unavailable`, moves what it held to used, flagged estimated, returns `ai_usage_monthly.reserved_tokens` to its earlier value, and the person can ask again; with no worker, a queued question ends `unavailable` by its queue clock (AC54). `sch:assistant_rows.py` *new* shares the tenant-and-question set-up with the account suite.
- **T2.10** — `api:test_assistant_hostile_live.py` *new* (AC15b, AC9): the real loop as the AI account, every read over real HTTP to the real app under uvicorn, a provider that plays AC9's cross-tenant tricks then all of `HostileProvider`'s; every Postgres table hashed before and after, only AC15b's tables changed (in the run: `ai_runs`, `ai_usage_monthly`, `assistant_messages`, `assistant_monthly_counts`, `assistant_read_log`, `assistant_steps`), the question's row only in named columns, ClickHouse row counts unchanged, nothing of the other organization reached the model. `features/assistant/hostile.test.ts` *new* (AC15c): the hostile answer rendered has no link, image, markup or run script; its fixture is held equal to `HostileProvider`'s output by `sch:test_assistant_hostile.py`.
- **T2.10b** — `main` is an `@container`, `TopBar` has its own; every `lg:` and `xl:` layout variant inside them became a container variant, in 22 files (the design's 17 and `FindingScreen`, `FindingsScreen`, `DashboardEditScreen`, `SloForm`, `SloScreen`, which the panel narrows as much). Thresholds from the installed Tailwind 4.3.3's `theme.css` (`@3xl` 48rem, `@5xl` 64rem; tailwindcss.com was unreachable): `lg:` fired at a 1024 px window, where main is about 816 px, so `@3xl:` (768 px); `xl:` at 1280 px, about 1072 px, so `@5xl:` (1024 px). `SideSheet` stays window-based: it is a fixed dialog over the viewport. On integration with F20 Steps 5–9 (2026-11-12) the dashboard grid followed the same rule: `grid-spans.ts` and `layout-rows.ts` lay panels out at `@3xl` and `@5xl` of `main`, not `lg` and `xl` of the window. `tools/e2e/tests/layout-widths.spec.ts` *new*: **written, not run** (hardening).
- **T2.11** — `features/assistant/` *new*: `AssistantPanel.tsx`, `Conversation.tsx`, `StepRow.tsx`, `AnswerSentences.tsx`, `api.ts`, `states.ts`; `shared/assistant-context.ts` *new* (subject, open state, the button's words); `shared/hooks/use-media-query.ts` *new*; `shared/api-client.ts` gains `isForbidden` and `problemCode`; `AppShell.tsx` (the panel beside main at `lg` with its own `ErrorBoundary`, filling the window below it with what is behind `inert`); `TopBar.tsx` (Ask, Answering…, Answer ready, Back to answer; hidden without `ask_assistant`, for the operator and in a support view); `RefreshControl.tsx` leaves assistant queries out; `index.html`'s viewport gains `interactive-widget=resizes-content` (**from memory, unverified**: browser documentation was unreachable; checked on real phones in hardening); `FindingScreen.tsx`'s **Ask about this finding**. Tests: `AssistantPanel.test.ts` *new* (every state, AC17, AC48's states, AC73, AC77, AC78, AC79, AC103), `app/layout/AppShell.test.ts` *new* (AC73), `RefreshControl.test.ts` (AC75). `tools/e2e/tests/assistant.spec.ts` *new*, against the fake provider, with the phone flow: **written, not run**.
- **T2.12** — `features/aisettings/ChatSection.tsx`, `chat-api.ts` *new*, in F3's screen after Usage: on or off, consent with who and when, "Waiting for an owner to agree: {names}", the preview, the data level, the daily limit, outcome, rejection and wrong-mark counts and per-person counts with no text, a warning at 80% of chat's ceiling, the read record (filterable), tested or not tested, the MCP sentence, F3's selling line. `ChatSection.test.ts` *new*.
- **T2.13** — `tools/e2e/tests/chat-broken.spec.ts` *new*: every route with chat's API failing: **written, not run**. **AC4's detection-and-alerts arm is not built:** it was to extend F3's stopped-role test, `test_ai_off_changes_nothing_live.py` (F3 T5.9), which F3 has not built.

### Step 2, not built or not run

- **The one real local-model call** of the *Judgeable*: **not yet run: needs the founder's key or local model.** No real provider was called.
- **The context-length probe (D87):** open decision 9.
- **T2.8's live arm** (AC10 through uvicorn): no Step 2 tool has a path slot (open decision 6).
- **AC4's detection-and-alerts arm:** F3's T5.9 test it extends does not exist.
- **Written, not run (hardening, deferred):** `layout-widths.spec.ts`, `assistant.spec.ts`, `chat-broken.spec.ts`, AC66's arm in `test_insight_network_live.py`, the fake provider reached from the running role. Also deferred: phone and real-keyboard checks (AC48's manual half), performance (576 MB, call times), customer docs, design QA, the founder's judging.

### Step 1, what was built (2026-11-02)

- **T1.0** — `tools/gateseed`: `--log-body "text"` sends one chosen log record through the receiver with the dev key and waits for it to be readable. New `logbody_test.go` (4 tests); flag parsing refuses anything else.
- **T1.1** — `assistant/bounds.py`: `bounded(..., untrusted=...)` adds `"untrusted": [paths]` and a one-sentence `"untrusted_note"` to the envelope, kept on cut answers too.
- **T1.2** — Every tool passes its entry of `UNTRUSTED_FIELDS` (one registry in `assistant/tools.py`); `list_checks` stops dumping `CheckConfig` and sends a `CheckRow` with type and URL hosts only (`check_row()`); `SERVER_INSTRUCTIONS` gains the untrusted paragraph.
- **T1.3** — `window(hours, from_time, to_time)`: a fixed ISO 8601 range (read as UTC) or hours, both at once refused, at most 7 days; on every windowed tool, held by an AST test that any tool taking `hours` takes the range.
- **T1.4** — A malformed `project_id` (and `finding_id`, `incident_id`) raises `InvalidRequestError` with a sentence naming where ids come from, instead of a bare `ValueError` the tool wrapper hides.
- **T1.5** — `servicemap/router.py` takes `CurrentReader` and `require_readable_project`; `THE_SURFACE` and `docs/product/setup/api.md` gain `GET /service-map`; the published read spec follows from the routes.
- **T1.6** — Seven new tools: `list_findings`, `get_finding` (`for_member=False`, `project_scope` bound to the proven project), `get_trace`, `get_error_group`, `get_incident`, `get_service_map`, `get_service_detection`. `THE_TOOLS` and `READS` grew with them.
- **T1.7** — `apps/api/tests/test_mcp_injection.py` *new*: walks every tool's response model; every `str`, `list[str]` or `dict` field is either in `UNTRUSTED_FIELDS` or on a closed allow-list of ids and closed vocabularies with the reason beside each; hostile text planted in every untrusted field round-trips as JSON string data under the label. The walk found three fields a hand list missed (`preceding_deploy.*`, span `links`).
- **T1.8** — `docs/product/setup/api.md`: the new tools, the labels, fixed ranges, the `/service-map` row, and the plain statement that nexovue cannot control a customer's own AI on the far end. `team-and-api-access/spec.md` FR17 and FR32 updated.

**The design's 2 proposed ADRs are deliberately not filed yet.** Both concern later steps only — the ticket/loop ADR is Step 2's mechanism, the knowledge/pgvector ADR is Steps 5-6's — and Step 1 made no new expensive-to-reverse decision beyond what the approved spec already records (FR33-FR36). They are filed when the step that builds them starts.


## Completed

- **Research**: [`research.md`](research.md), 2026-11-02. It was input, and approves nothing.
- **Specification**: [`spec.md`](spec.md): 107 acceptance criteria, with a table from requirement to criterion; D1 to D24 carried from the research, D25 to D79 new, D80 to D86 answered on 2026-11-02.
- **Design**: [`design.md`](design.md): the ticket, tool catalog, loop, checks, knowledge, screens with wireframes, failures, security, cost, 19 numbered differences from the foundation and F3, 2 proposed ADRs.
- **Plan**: [`plan.md`](plan.md): 8 steps. Step 1 (a safer MCP server) needs only F6, and the founder can judge it with Claude Code. Step 2 starts only after the founder's conversations.

**Corrections to the research, found by reading the code:**

- Deleting a person does not delete their conversations. `scrub` keeps the user row (`members/service.py:188-204`), so it must do so on purpose.
- Most screens cannot take an exact time range from the address. A citation link carries F3's `?from=&to=&project=` where the screen reads it, and says the range is not applied where it does not. A click never moves the person's window (D33).

**Aligned with F3's specification and design** in review: the reservation, chat's 80% share, the fingerprint, `TASK_SETS`, `INSIGHT_GRANTS`, the `ai` network, the window in the address, and F3's bases and word lists. An earlier claim that this covered the queue was false, because F3 had dropped row-level security on `jobs`. Corrected in the second review: F4 adds `assistant` to F3's three job functions. **Nobody has re-checked the other points since.**

## Next action

**Step 7 is built (2026-11-21) and awaits the founder's review of its diff and a commit** on `wt/f4s7`, with 0080 re-chained after F5 Step 3's 0079; then T7.3 (the founder's, after Phase 2, after Step 4), the open decisions 39 to 49, and Step 8. Earlier: **Step 6 is built (2026-11-21) and awaits the founder's review of its diff and a commit** on `wt/f4s6`; then T6.0's real calls, T6.4's measurement and T6.6's bed (the founder's, after Phase 2), the open decisions 29 to 38, and Step 7. Earlier: **Step 5 is built (2026-11-21) and awaits the founder's review of its diff and a commit** on `wt/f4s5`. Then: the founder's judging of Steps 2, 3 and 5 with one real local model; Step 4 (real models, in the gate), after which T5.7 re-runs the bed; the open decisions 19 to 28; Step 6. Earlier:

Step 3 is committed (2026-11-12, locally, not pushed). Next: the founder's judging of Steps 2 and 3 with one real local model (`gpt-oss:20b`, D80, D87) on research section 2's questions; the hardening pass runs the e2e specs; then Step 4. Earlier:

Step 2 is committed locally on `wt/f4s2` (not pushed), on top of F3 Step 5. Next: the coordinator merges it after F3 Step 5; then the founder's one real local-model call (`gpt-oss:20b` with `OLLAMA_CONTEXT_LENGTH` set, D80, D87) and the founder's judging of Step 2; the hardening pass runs the three Playwright specs, AC66 under `just insight-fence`, and the phone checks. Then Step 3.


## Open decisions

The three founder decisions of 2026-11-02 stand answered. Raised while building Step 2, each with a recommendation; none blocks the commit:

1. ~~F3 Step 5 was not built, and F4 built its own fence, reservation and word lists.~~ **Resolved 2026-11-12:** F3 Step 5 is built; F4's copies are deleted and chat uses F3's (*Step 2, rebase*). F3's `Untrusted` gained a per-part cap, `reserve_month()` and public word lists: small changes to F3's modules, each for one owner.
2. **T2.7a's wording: `tools` on `AIRequest`.** Built as a separate `ChatRequest`, as the design's difference 12 says ("F3's type and its canary test stay F3's alone"). **Changed since:** F3 Step 5's `AIRequest` now accepts `Untrusted` parts, so the first reason recorded here (that it refused them) no longer holds; the design's reason does, and the request carries F3's `Untrusted` now. **Kept as the design says. Recommended:** accept, and change T2.7a's wording.
3. **The job carries the organization id beside the message id** (the design says the message id only), so every statement the worker runs binds `org_id` and the scheduler tenancy test holds it. Ids only, as the foundation asks. **Recommended:** accept.
4. **`search_logs` sends no `severity` text**, only `severity_number`: the text is the sender's own string (`LogRecord.severity: str`), so it is attacker-reachable, and the catalog test refused it declared `closed`. **Recommended:** accept; the design's field list follows.
5. **"`assistant` first" is not strict.** The worker rotates which capped queue it serves first on each pass (`worker._capped_in_turn`, F3 A56), so chat is first every other pass; each queue keeps at least 2 of 6 slots while the other is full, which is what the design's numbers rest on. **Recommended:** accept, and drop "first" from the design.
6. ~~AC10's live arm waits for Step 3.~~ **Resolved 2026-11-12:** built with Step 3's path tools (`test_assistant_tools_live.py`).
7. **A non-superuser install account cannot alter a role it did not create** (Postgres 16 and later): the non-superuser spike needed `GRANT nexovue_insight TO f4owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE` by hand, because `nexovue_insight` already existed. Not F4's code, but F3's `setup` step meets it on such an install. **Recommended:** F3 says so in `docs/operations/ai.md`.
8. **The chat sweep is scheduled whether or not an AI container runs;** with none, each minute's job expires unclaimed. **Recommended:** accept (no work, no reach).
9. **Who builds the context-length probe (D87): F3 or F4?** Plan T2.6 lists it under F4, but spec D87 and F3's A86 say it is F3's connection check ("F4's D87 uses this same check for chat rather than adding its own"), and F3's plan has no task for it. Not built here. **Recommended: F3,** in its connection check, as A86 decided: one extra call of known size when a connection is tested, refusing "ready" when the reported input tokens come back smaller; F4's availability then reads it. T2.6's wording changes to point there.
10. **Container thresholds chosen by one rule** (T2.10b): `lg:` became `@3xl:` and `xl:` became `@5xl:`, the main width at the old window threshold, rather than a threshold judged per screen. **Recommended:** accept; design QA in hardening adjusts any screen that reads badly.

11. **A range only starting past retention is read from the first moment kept,** not ended aged out (the design said "range past the retention clock → aged out"). The step and the model are told the data before that day is no longer kept. A range wholly past it still ends `aged_out` with no read (AC24). **Recommended:** accept; a "last 7 days" question on a 7-day project should not read nothing because its start is seconds too old.
12. **New panel sentences not in the design's *Words* table:** "One sentence was removed because it told you to act on quoted text."; "This error message is no longer available."; "Cut at 500 characters. Open what it read for all of it." (the design's cut line names a runbook); "This project has sent no telemetry yet, so chat has nothing to read." with "Set up sending data"; "Latest N, no time filter"; "The read was refused for now." **Recommended:** accept these words, or replace them, in design QA.
13. **Copy steps carries a `/logs` link with the search phrase `q`** when the phrase passed FR14's checks: the link is nexovue-built, but the phrase was the model's. The step's displayed phrase itself is left out. AC93 says no model word. **Recommended:** drop `q` from copied links (a decision either way, so not changed here).
14. **Only log lines and error messages can be quoted:** they are the records a reference can read again (`LOG_LINE_AT`; an error group within its step's window). A span's `exception_message`, a trace's `status_message` and F3's fact sentences are cited but not quotable. **Recommended:** accept for now; spans need their own reference read if a real question needs it.
15. **The project's zone beside a step's range is not shown:** the panel has the person's zone (from the question's context) but not the project's. **Recommended:** add the project's zone to the message view in the hardening pass.
16. **`HostileProvider` plays Step 2's tools only;** a question has 8 steps, so it cannot also try every Step 3 tool. Path tricks on every tool's every argument are held by `test_assistant_tools_live.py` through real uvicorn. **Recommended:** accept; the bed's injection run (T4.2) covers the tools as a model uses them.
17. **The hostile live suite cannot run beside another suite** (its row diff covers every table). **Recommended:** CI runs it alone, as it does today; say so beside the workflow when Step 3 merges.
18. **The quote route reads an error group within its step's window,** so a quote of a group whose window has aged out reads `gone`, as the log quote does. **Recommended:** accept.

Raised while building Step 5 (2026-11-21), each with a recommendation; none blocks the commit:

19. **A fact line's key is its strict service name, or none.** A project finding and a service whose name fails the strict pattern both key as unnamed, so they share one line a signal and day (the line itself says "services …" or "a service"). Keying by the raw name would put a name that failed the pattern into the table, which AC39 forbids. **Recommended:** accept.
20. **Fact lines of a resolved case wait for Step 7.** The design writes them for findings "closed as `recovered`, or whose case is resolved (F22)", and keeps a resolved case's line when its finding stops being `recovered`; plan T5.5 says recovered findings, and T7.2 names "case resolved". F22 is built, so either step could. **Recommended:** keep it in T7.2 with notes, which carries the "resolved case" reading of FR28 whole.
21. **Resolved 2026-11-21 (Step 6):** granted with T6.1 as recommended, only once the vector columns exist. **No `nexovue_insight` grant on `knowledge_chunks` in Step 5.** The design's grant row (select some columns, update the embedding ones) serves only embedding; search runs in the API on the ticket (D31). **Recommended:** grant it with T6.1.
22. **Upload is a JSON body of files the browser read,** not a multipart form: at most 50 files an action (a guess), each at most 32 KiB, under its own body cap of about 4.1 MiB (twice the files' bytes for JSON escaping, plus 1 MiB). A file of control characters could escape past that and be refused whole by the body limit. **Recommended:** accept; it keeps one JSON client and per-file refusals.
23. **New words not in the design's *Words* table:** the runbooks screen ("Your team's own instructions for handling problems, for this project. Chat can quote them in its answers."; "A support view does not show what a runbook says."; "With chat on, this text may be sent to {provider}. Do not paste secrets."; "{n} runbooks added."; "Add to these projects, each as its own copy"; the form's labels) and the panel ("From a fact nexovue recorded"; "This runbook was deleted, or is no longer kept."; "Open the runbook"; the Copy control named "this runbook text"). **Recommended:** accept or replace in design QA.
24. **The credential warning is exactly FR40's mask list** (Bearer and Basic values, URL credentials, card numbers, `password=`-style pairs), not a wider set (AWS keys, private key blocks). One list keeps the warning, the mask and the consent's sentence true together. **Recommended:** accept; widen all three together if the bed finds a gap.
25. **A knowledge step with rows reads "Latest N, no time filter.",** Step 3's words for a windowless read. **Recommended:** "N found." in design QA.
26. **Knowledge search refuses at data level *facts only*,** besides not being offered there (the design only says not offered). **Recommended:** accept, as defence in depth for FR39.
27. **`websearch_to_tsquery` needs every word to match;** a question's words used as they are may find nothing. The tool tells the model to use a few keywords and `OR`. **Recommended:** measure it in T5.7's bed; a ranked any-word query would be a design change.
28. **Settled (2026-11-21): the expiry suites read their own clock and sweep only their own rows.** `test_dashboards_expiry_live.py` pinned its clock to 2026-11-12, nine days before the real one, so a dashboard it made 29 days old was 38 days old to another suite's sweep on the real clock, which deleted it. Not F4's code. The suite now takes its clock when each test starts, ten minutes ahead of the real one, so nothing it needs is within a real-clock sweep's reach; `test_slo_prune_live.py` and `test_release_sync_live.py` stop making a row lapsed before the rows that depend on it exist; chat's test organizations get unique names. Nothing is run serially and no product code changed. Standard: `docs/engineering/testing.md`.

Raised while building Step 6 (2026-11-21), each with a recommendation; none blocks the commit:

29. **The embedding check reserves under chat's share and writes an `embed` run**, unlike F3's connection test, which reserves nothing; it needs the AI connection's monthly limit set. **Recommended:** accept: every embedding call, the check's too, is spending (ADR-079).
30. **The merge cuts to 5 only when both sides found something;** with one side empty the other stands whole, up to 20, as the design's "the other stands" reads. **Recommended:** measure in T6.6's bed; cutting to 5 always would also cut keyword-only answers, Step 5's behaviour.
31. **The second connection has no preview of its own:** the Chat section says what goes there (every runbook piece and fact line, each phrase, masked) and the preview's bulk count; the design names "its own … preview". **Recommended:** add a sample in the hardening pass, or accept the sentence.
32. **`CHAT_DISCLOSURE_VERSION` raised to 2,** since every chunk and phrase may now go to be embedded (FR25: a release widening what is sent). Every existing chat consent ends; pre-deployment, none exists. **Recommended:** accept.
33. **Batch sizes are guesses:** 64 texts a call (OpenAI-style), Titan one; at most 8 calls and 256 chunks a run every 5 minutes, so a Bedrock install embeds 8 chunks a run. **Recommended:** T6.4 and T6.6 move them; consider more calls for one-text formats.
34. **A sweep run longer than 5 minutes could overlap the next,** and embed a chunk twice: the write is idempotent, the spend doubled for that batch. **Recommended:** accept until measured; a run stops starting calls after 2 minutes.
35. **An embedding run's "what was sent" view reads as expired,** as chat's runs do: `ai_runs` holds no content, and an embedding body is chunk text. **Recommended:** accept.
36. **A model chosen after its check is untested until checked again;** the screen offers the check, it does not run it on save. **Recommended:** accept (a save calling a provider would spend on every edit).
37. **No dated real-call record for embedding formats** (`real_calls.py` is chat's); the screen offers both formats before any real call. **Recommended:** add embedding formats to the real-call record with T6.0's founder runs.
38. **New words not in the design's *Words* table:** the six "Search is keyword only: …" sentences, "Search finds runbooks and fact lines by meaning as well as by keyword.", the sending sentence, the progress line, the form's labels, the agreement sentence, and the panel's "Searched by keyword only." **Recommended:** accept or replace in design QA.

Raised while building Step 7 (2026-11-21), each with a recommendation; none blocks the commit:

39. **Which case resolutions keep a fact line.** Built: `resolved` (a person's resolve) and `tracker_closed` (the issue a person linked, closed). Not: `false_alarm` (AC84), `new_normal`, `no_activity` (the sweep ended an idle case) and `recovered_untouched` (recovery closed a case nobody worked, already a recovered finding's line). "How did we fix this last time?" wants a person's word that it was fixed. **Recommended:** accept.
40. **A resolved case's line ends at the resolve and says `closed: case resolved`,** even while the finding is open; a line written earlier as `recovered` keeps its words. **Recommended:** accept.
41. **Runbooks rank above fact lines too,** not only notes (FR29 names notes): one rule, people's words before nexovue's composed lines, on both search sides and through the merge. A fact line can then appear only below every matching runbook and note. **Recommended:** accept; measure in the bed (T7.3, T8.0).
42. **A note chunk keys its note with its organization, not its project,** since F22's notes carry no project; the sweep takes the project from the note's finding (which never changes project), and keyword search re-checks it. **Recommended:** accept; the alternative is a project column on F22's notes.
43. **"Not searchable" counts every note without a chunk,** those waiting for the next pass (up to 5 minutes) as well as those skipped at the cap, organization-wide; shown only when above zero. **Recommended:** accept.
44. **`CHAT_DISCLOSURE_VERSION` raised to 3,** since notes now go to the provider (FR25). Pre-deployment, no consent exists to end. **Recommended:** accept.
45. **The note box's sentence shows while chat is on for the project and it is not left out** (`ready`, `no_consent`, `no_data`), before consent too ("can be sent"). A viewer's availability read is refused, so a viewer sees none, and cannot write a note anyway. **Recommended:** accept.
46. **Save as runbook is its own route, `POST /runbooks/notes`,** composing the title and text on the server (the fact line is nexovue's), the runbook written by the clicking person, the note's author not named; offered on each note of a resolved case. **Recommended:** accept.
47. **New words not in the design's *Words* table:** "A teammate wrote this, {age}"; "A teammate wrote this. It looks like a command or a link: do not run it."; "This note was deleted, or is no longer kept."; "Open the case"; "Save as runbook", "Save anyway", "Saved as a runbook, with the case's fact line.", "Open the runbook", "The runbook was not saved. Try again."; the runbook's title and provenance lines; "{n} notes are not searchable by chat. …"; the connections row; "With chat on, notes can be sent to {provider}."; the step words "Reading the case's timeline". **Recommended:** accept or replace in design QA.
48. **Note chunks stay when chat is turned off,** as fact lines do, until their note goes. **Recommended:** accept; purging on off would re-copy everything when turned on again.
49. **No new personal-data category:** a note chunk is a copy of a note F22 already exports, and goes with it. **Recommended:** accept.

## Blocked

- **Step 4 and T5.7 need real models** (the founder's key or local model). Step 5's code does not.
- **Founder decision, 2026-11-08:** Steps 2 to 7 are built in full, not gated on the buyer round (foundation item 27). F3's provider (Step 4) is on the branch, so Step 2 was built.
- **Step 2's real-model call** needs the founder's key or local model. Step 7 waited on F22, now built (Step 7 built 2026-11-21); one tool in Step 3 on F28.
- ~~All steps blocked on F6 merging~~ — F6 merged 2026-11-02 (PR #24), and the founder approved these documents, so Step 1 was built.

## Risks

- **Nobody has said they want this.** The research has no interviews. The founder's five to eight conversations now come before Step 2, and a developer happy with Claude Code may need only Step 1.
- **Chat will sometimes be wrong in ways that look right.** Citations, labels and figure flags help a person check. They do not prevent a believable false cause. Rivals' published accuracy figures cannot be compared with anything here, and nothing is measured.
- **Injection can't be proved absent.** The structure limits what success gains: no write, no way out, one project. A steered model can still give a wrong answer.
- **Personal data reaches the provider.** Names, email addresses and customer ids in logs stay after masking (`mask/rules.go:25-38`). The consent step says so.
- **Answers may paraphrase a log,** so log erasure cannot reach them. Conversations expire at derived retention. Stated, not solved.
- **All figures are guesses:** 60,000 tokens a question, 8 steps, 8 KiB, 50 questions a day, 45 million tokens a month, 576 MB, 5 minutes, 40 characters, 200 ms. The bed's cost run and T6.4 replace them.
- **Local models may be weak** at the required answer shape and at resisting injection (LogJack, one paper). Which one to document comes from results.
- **Exact-scan search time at 5,000 chunks, on a 1 GB Postgres, is unmeasured** (T6.4).
- **Rivals move quickly.** Elastic is the closest on own model, own server and a knowledge base; its current terms need a further check before any comparison is published.
- **Row-level security on messages and steps is F4's own first use.** A probe showed the mechanism, not the real statements. T2.0a proves them first, with a stated fallback.
- **Tested only on free models (foundation item 4, changed 2026-11-02).** Free tiers have low limits, so Step 4's runs take longer; a question of about 31,000 tokens (a guess) is larger than some free limits. Free tiers may train on inputs and let people read them, so only seeded, synthetic data goes.
- **"Supported" covers only what was tested.** OpenAI, Anthropic and Bedrock have no free API tier. They stay "not yet tested" until someone tests them, though customers use them most, so the setup pages may name few providers at launch.
- **The local model shares the 24 GB Mac with the stack chat reads** (D80; decision 0047). If it spills or swaps, the smaller model replaces it, and the report says so. No GPU money is approved.
- **No server measurement before deployment** (foundation item 9). The 576 MB ceiling and search times are read on the Mac's Docker; the 16 GB server is measured only on Oracle after deployment, and its free allowance (2 Arm CPUs, 12 GB) is below that size.

## Validation

**Step 2, run on `wt/f4s2` after the rebase, 2026-11-12 (counts read from the output).** Stores: `f4s2-postgres` (45432, migrated by the real `setup` step 0071 → 0072 → 0073) and `f4s2-clickhouse` (48123, and 49000 for the ClickHouse migration step, 40 migrations), started for this worktree and removed with their volumes at the end. Every live run went through a wrapper that refuses any store address but these two. No real provider was called.

**Each new test was seen failing first** (behaviour removed or broken, run, restored), and three gates found real faults:

- test_assistant_preview_live.py (3): masks not applied -> 2 failed; labelled flag forced false -> 1 failed; restored 3 passed.
- test_assistant_budget_live.py (3): F3 reservation's ceiling condition dropped -> 2 failed; CHAT_SHARE 0.8 -> 1.0 -> 1 failed; restored 3 passed.
- test_jobs_live.py, chat arms (5): sweep ignoring the hard clock -> 2 failed (both kills); ignoring the queue clock -> 1 failed (no worker); both AI caps raised to 6 slots -> 1 failed (the AC29 arm first passed this break: its burst ended before the other job expired; strengthened to 8 jobs of 8 s, expiry 3 s, then failed); restored 5 passed.
- test_assistant_expiry_live.py (5): chat rows left out of _EXPIRED -> 3 failed; per-table handling removed -> 1 failed; in-flight/held rule removed -> 3 failed; restored 5 passed.
- test_personal_data_live.py, chat (2): steps not exported -> 1 failed; truncation flag forced false -> 1 failed; restored 16 passed.
- test_ai_tool_calls.py + loop + hostile: chat rendering raw text instead of F3's fence -> 3 failed; restored.
- Per-part fence cap: no test caught it at first (83 passed); added test_a_whole_tool_result_reaches_the_model_past_f3_s_default_cap -> failed under the break; restored passed.
- F3 word lists in assistant_answer: F3's URL pattern removed -> no test failed at first (the host-name check catches https://evil.example); added two address-only cases -> 2 failed; F3's command verbs removed -> 3 failed; F3's causal phrases removed -> 1 failed; restored 39 passed.
- test_api_access_log_live.py (1, AC65): '--no-access-log' taken out of the api role -> 1 failed (uvicorn's access line carried the planted phrase); restored 1 passed. First version also failed on the test's own httpx client log line, which is now excluded.
- tools/ai-eval/test_fake_provider.py (3): tool calls written under the wrong key -> 2 failed; scripted mode never answering -> 1 failed; restored 3 passed.
- test_compose_contract.py, fake provider (1): the role's default AI_PRIVATE_ENDPOINTS emptied -> 1 failed; restored passed.
- test_insight_network_live.py AC66 arm: written, NOT run (needs `just insight-fence`'s installed kit).
- test_assistant_hostile_live.py (1, AC15b + AC9): failed on its first runs for real: the worker sent no `project_id`, so every catalog read through the real API got 422 (`list_findings failed: unavailable`); offline tests used a recording client and never saw it. Fixed at the root: the loop adds the question's own project to every read (`PROJECT_QUERY`), never the model's. Break re-applied (project left out) -> hostile live, loop and offline hostile 3 failed; restored 22 passed. The row-diff and other-tenant arms were not separately broken (no single behaviour change makes the loop write another table or the API return another tenant's rows without rewriting two layers); they passed with the reads working.
- test_assistant_catalog.py, step links (1, new): the findings link back at '/findings' (not a console route; F20 moved the feed to '/') -> 1 failed; this was a real fault the panel's typecheck found; restored 17 passed.
- Web, features/assistant + app/layout (vitest), each break then restored:
  - answer sentences set as HTML -> hostile.test.ts and both plain-text tests failed (3);
  - polling not stopped on 403 -> AC73 poll test failed;
  - Lost contact threshold raised -> lost-contact test failed;
  - "Answer ready" never set -> AC78 test failed;
  - RefreshControl counting chat queries -> AC75 test failed;
  - panel's own error boundary removed -> AppShell.test.ts failed;
  - draft cleared on send -> AC79 refusal test failed;
  - "Ask again" not filling the box -> AC103 test failed;
  - withheld's sentence removed -> at first no test failed (the state test compared against the same function); rewritten to the spec's literal sentences, then failed; restored all 30 passed.
- test_assistant_hostile.py fixture drift test (1): written against a fixture generated from HostileProvider; not separately broken (an edit to either side fails it by construction).
- ChatSection.test.ts (7): warning rule returning false -> 1 failed; agreement sending a stale fingerprint -> 1 failed; MCP sentence removed -> 1 failed; agreement offered to any admin -> at first passed (the preview had not loaded when it looked); fixed to wait for the preview, then 1 failed; restored 26 passed with the AI settings suite.
- test_ratelimit_coverage.py caught a real gap: the chat preview put a ClickHouse read on F3's whole `/ai` router, on the default tier. Fixed by giving `/ai/preview` its own router (`ai/preview/router.py`) on the telemetry tier, so the rest of `/ai` stays on Postgres and the default tier. Rule removed -> 1 failed; restored passed.
- assistant-context.test.ts (4, found in review: the Ask button and the finding link showed on the hosted service, where F3 offers no AI): hosted not checked -> 1 failed; support view not checked -> 1 failed; restored 4 passed.

**Checks (2026-11-12):** `uv run ruff check .` clean; `uv run ruff format --check .` clean; `uv run mypy .` no issues in 1030 files; biome on every touched web and e2e path clean (four warnings remain in files this work did not touch); `pnpm -r --if-present run typecheck` clean; `verify-tokens` clean, no `from-`/`to-`/`via-` literal added; **the whole web suite, 2174 passed** (178 files). **API, the F4 suites and the gates touched** (`test_assistant_*`, access log, authorization coverage, cross-service contracts, both tenancy structure tests, tenant chokepoint, roles, insight grants, personal data, Compose contract and dependencies, migration rules, policy, auth routes, rate-limit callers and coverage, MCP tools and injection, read surface, F3 Step 5's wording and evidence vocabulary suites, AI settings), `-n 2` on this worktree's stores: 3380 passed and the one rate-limit gate above, fixed; after the fix, the gates near it 299 passed serially. Under `-n 2` the live suites' signup fixtures can collide across workers (`ConflictError: That signup collided`); each such suite passed serially. **Scheduler, platform and `tools/ai-eval`, `-n 2`:** 6360 passed; 3 failed and 8 errors, none in code this work touched: `test_alert_telemetry_live.py` needs the seeded `acme/production` organization, and `test_detection_failure_live`, `test_detection_settle_live`, `test_erasure_live` and `test_connect_guard` need Kafka, the object store or internet DNS, which this build never started (each says so).

**Incident, 2026-11-12, said plainly:** the session's scratchpad is shared with other builders. A file of store addresses written there (`env.sh`) was overwritten by the f20s7 builder within a minute, so one live batch of this work ran against **f20s7's Postgres (port 55432)**, not this worktree's: 37 failed, 21 passed and 129 errors, most because F4's tables do not exist there. What may have touched that database: F3's `insight_account.provision` (its grant step most likely failed, as there is no 0073 there); the suites that passed made and deleted their own organizations; the API suites ran in rolled-back transactions. Nothing in that container was inspected or cleaned from here; the f20s7 builder should check it. The reverse is possible too: their runs may briefly have read this file while it pointed at this worktree's stores. From then on this work used a private, read-only file and the refusing wrapper. Separately, the Docker daemon stopped mid-session (the session's worker restart killed it); the coordinator restarted it, and only this worktree's two containers were started again.

**Step 2, run on `wt/f4s2`, 2026-11-11 (counts read from the output).** Stores: a Postgres started for this worktree (`f4s2-postgres`, port 45432, migrated by the real `setup` step to 0080), and a second database in it owned by a non-superuser for the spike's second arm. No ClickHouse was needed: no live test here reads telemetry. No real provider was called.

**Each test was seen failing first** (behaviour removed, run, restored):

- test_ai_tool_calls.py: tools not offered and `_call` returning None -> 7 failed, 11 passed; fence without ASCII escaping -> 1 failed (test_untrusted_text_is_fenced_and_escaped); restored 18 passed.
- test_roles.py (2 new/changed): against HEAD's roles.py -> failed; restored passed. test_compose_contract.py: against HEAD's nexovue.yaml -> failed (network neighbours, variables, memory); restored 100 passed.
- test_insight_grants_live.py: F4's table grants removed from INSIGHT_GRANTS -> failed; restored passed. F3's parity suite (test_insight_account_live.py) passes on the widened functions.
- test_detection_has_no_ai.py caught a real fault on first run: the ticket branch imported into `auth/dependencies.py` made reliability-target routes reach AI code (1 failed). Fixed at the root by registering the ticket check on the app (`app.state.ticket_check`) instead of importing it; 9 passed.
- test_beat_schedule.py (new chat sweep case): entry removed from beat -> 1 failed; restored 19 passed.
- test_assistant_loop.py (15): the per-call recheck, the time-and-budget check and the one send-back removed -> 4 failed, 11 passed; restored 15 passed.
- test_assistant_answer.py + test_assistant_hostile.py (42): way_out() returning None -> 13 failed; send-time masking off and the path-slot rule off -> hostile 2 failed (masked credentials and email reached the model; '../../admin' reached the API); restored 42 passed.
- test_assistant_api_client.py (16): following redirects -> 1 failed; values not encoded -> failed; restored 16 passed.
- test_assistant_account_live.py (9, the T2.0a spike): row-level security disabled on both tables -> 2 failed (a direct final write went through; a finished question's steps were visible); re-enabled 9 passed. It also caught a real fault on its first run: the reservation's `:limit * :share` had no type (asyncpg 'operator is not unique'), fixed with casts. Run on the superuser-owned dev database and on a database owned by a non-superuser (`f4owner`, NOSUPERUSER CREATEROLE): 9 passed each.
- test_assistant_tickets_live.py (19): handler allow-list and the standing recheck removed from valid_for -> 6 failed (3 non-catalog routes read; chat off, data level moved and opted out still read); restored 19 passed.
- Structure gates caught three real gaps on first run, each failing before its fix: test_authorization_coverage.py (8 new mutating routes ungated in the list), test_postgres_tenancy_structure.py (`_history` did not bind org_id), test_scheduler_postgres_tenancy_structure.py (the sweep's cross-tenant read unnamed). After: 2595 passed (with the read-surface, chokepoint, contracts, rate-limit coverage, MCP, policy and auth-route suites).
- test_assistant_api_live.py (16): the daily limit's WHERE and the stale-row marking removed -> 2 failed; restored 16 passed. On its first run it caught two real faults: the job's kwargs lacked the org_id the task takes, and `dict(result)` read a SQLAlchemy result as a mapping of column names.
- test_assistant_records.py (10): the job without org_id -> 1 failed; restored 10 passed.
- test_assistant_catalog.py (16): caught a real fault on first run: search_logs sent `severity`, a sender-written string, declared closed (1 failed). Removed from the catalog; 16 passed.
- test_ratelimit_callers.py (2 new): the middleware's ticket branch removed -> 1 failed (the ticket spent the address, not its person); restored all passed.
- test_assistant_privacy_live.py (1): `forget_person` taken out of scrub -> 1 failed; restored 1 passed.
- test_deletion_live.py: failed before chat's tables were in the deletion map (KeyError assistant_settings), and its cascade check refused `assistant_question_counts` listed as deleted directly while users' cascade already takes it; moved to REMOVED_BY_CASCADE. test_keys_live.py: the new sealed column had no row, so its reseal check failed (resealed 0); seeded a sealed ticket. Both suites: 41 passed.
- test_assistant_structure.py (2): a probe file in findings/ naming AssistantMessage.answer -> 2 failed; removed, 2 passed.

**Incident, said plainly (twice):** a run without `NEXOVUE_POSTGRES_DSN` exported reaches the shared dev stack's Postgres on 15432, which `require_stack` finds reachable, so `NEXOVUE_SKIP_LIVE_TESTS=1` does not skip it. That happened once for `test_assistant_account_live.py`, and once in the serial offline run for it, `test_insight_account_live.py` and `test_insight_check_live.py` (57 errors). Each time F3's `insight_account.provision` ran there: its first transaction set `nexovue_insight`'s password and defaults to the same development values they already had; its grant transaction failed (that database is not at 0080: the new claim signature does not exist) and was rolled back. No row was written. **Consequence for whoever commits:** once 0073 (built as 0080) is applied to the shared dev stack, F3's account suites there take F4's grants, as intended. The three suites pass against this worktree's store (67 passed with F3's grant suite).

**Other checks (2026-11-11):** `uv run mypy .`: no issues in 1005 source files. `uv run ruff check .`: clean. `uv run ruff format --check .`: clean. Biome on the three touched web files: clean. `pnpm run typecheck` (web, `tsc -b`): clean. `MembersScreen.test.ts`: 6 passed. `verify-tokens`: clean; no `from-`/`to-`/`via-` literal added. Platform, scheduler and `tools/ai-eval` suites, serially: 6187 passed after `test_detection_coordinator.py`'s cap list gained F4's two queues (it failed until then; 11 passed after), 1 skipped; the 57 errors are the shared-stack runs above. A parallel run (`-n 6`, then `-n 3`) lost workers to the machine's memory, shared with another worktree's suite, and was stopped. **API suite, serially** (live suites pointed at this worktree's Postgres, ClickHouse pointed at nothing): 6450 passed; `test_compose_dependencies.py` then failed on the AI role's undeclared reach to the API, recorded on purpose with its reason (15 passed after). The remaining failures and errors are live suites that need what this worktree never started — ClickHouse, the object store, the seeded dev owner, `pg_stat_statements`, or the full stack on its published ports (`test_metrics_live`, `test_synthetics_live`, `test_servicemap_live`, `test_releases_live`, `test_sourcemaps_live`, `test_llm_observability_live`, `test_dashboard_panels_*`, `test_seed_live`, `test_first_run_seed_live`, `test_setup_live`, `test_dbstats_live`, `test_read_settings_live`, `test_tenant_isolation_live`); each read error names its missing store or seed. **Not compared against the base commit**, so "unchanged by this work" is reasoned, not run. Every F4 live suite and every suite F4 changed passed against this worktree's store.

**Step 1, run on `wt/f4`, 2026-11-02 (counts read from the output):**

- Offline, the touched suites: `test_mcp_tools.py`, `test_mcp_injection.py` (new), `test_read_surface_structure.py`, `test_read_surface_spec.py` — **176 passed** after implementation. Before it, the same run showed the import failure and 2 read-surface failures recorded below.
- Live, against a Postgres + ClickHouse pair started for this worktree (migrated with `nexovue_api.setup` and the ClickHouse setup command): `test_mcp_live.py` (2 new tests) and `test_read_surface_live.py` — **19 passed**. All six suites together on the final code: **195 passed**.
- Whole API suite offline (`NEXOVUE_SKIP_LIVE_TESTS=1 uv run pytest apps/api/tests`): **3781 passed, 65 skipped** (the live suites, skipped on purpose).
- `uv run ruff check apps/api packages/py-platform`: All checks passed. `uv run mypy .`: Success, no issues in 728 source files.
- Go: `go test -count=1 ./...` in `tools/gateseed`: ok, 15 tests (counted with `-v`); `go vet` and `golangci-lint run` clean (one goconst finding fixed).
- **Each behaviour change was seen failing first:** the label/window/tool tests failed with `ImportError: cannot import name 'UNTRUSTED_NOTE'`; the read-surface tests failed with `servicemap/router.py:get_service_map is on the surface but takes no CurrentReader` (2 failed, 49 passed); the malformed-project-id live test, run against the stashed old `tools.py`, failed with a bare `ValueError` from `uuid.py:181`; the Go tests failed to build with `undefined: LogBody` / `undefined: logBodyArgument`. The injection suite then failed on 3 real unlabelled fields before the lists were completed.
- **Not run:** `just check` (the coordinator runs it, per the worktree rules); `--log-body` against a live receiver end to end (only the stores were started on this 16 GB machine — the OTLP payload shape and flag handling are unit-tested, and the receiver's `/v1/logs` door is the one `just dev` serves); the Step 1 Claude Code demonstration (the founder's, by design — a demonstration, not a score).

**Earlier, for the documents — two small probes ran; nothing else:** no test of the product, no model, no build. In a throwaway `pgvector/pgvector:0.8.6-pg18-trixie` container with no network, under a role like `nexovue_insight`: a final write with reservation 0 aborted under a USING-only policy, under `WITH CHECK (true)`, and under separate `SELECT` and `UPDATE` policies; a `SECURITY DEFINER` function did it, and a second call changed nothing; finished rows and their steps were hidden; an upsert with no `SELECT` grant, and a view filter on an ungranted column, were each refused, and passed once granted. On FastAPI 0.162.0, in a scratch app with nested routers, a dependency saw `/search` for `/api/v1/logs/search`, and `abc%2Fdetection` reached `/services/{service}/detection`. The code was read on `feature/anomaly-detection`, with uncommitted work present, and line numbers may drift. Vendor pages and pgvector's README were read through a summarizing tool on 2026-11-02. Other outside facts come from the research.

## Specification impact

This is the first specification for F4. It needs changes to others: `personal-data/spec.md` (**done in Step 2**: the export's chat questions), `team-and-api-access/spec.md` (FR1's actions, done in Step 2; FR17, FR32 in Step 1), and differences in `phase-2-foundation.md` (design, *Specification impact*). Open decisions 2, 9 and 10 change plan wording (T2.7a, T2.6) once the founder answers.

## Documentation impact

`docs/product/feature-catalog.md` still says F4 is to be specified, and links nowhere. `docs/product/setup/` (turning chat on, `OLLAMA_CONTEXT_LENGTH`, the fake provider for development) and the architecture documents follow in T8.3. The fake provider's use is written beside its Compose entry.

## Reviews

**Step 3 `/review-diff`, 2026-11-12:** every changed file is explained by T3.1 to T3.5, T2.8's live arm, or a gate those moved (the outside-catalog list in the tickets suite lost `get_finding`, now a tool; the hostile suite's served-API set-up became shared). F3 files changed: `insight_account.py` (one grant, the retention view). Shared files changed, each named by the plan: `services/router.py`, `infrastructure/router.py`, `logs/search.py`, `ratelimit/policy.py`, `main.py`, `app/router.tsx`, `shared/url-window.ts`, `features/logs/`; plus `WindowControl.tsx` (the arrival selection, generalized) and `ServiceDetailScreen.tsx` (names itself to chat). Tenancy: `LOG_LINE_AT` is a module constant binding the tenant (the chokepoint test passed); the quote route reads only the person's own message, binding `org_id` and `user_id`, and the project through `require_project`; every ticket read is scoped to the question's project, proved against another project of the same organization. Authorization: no new mutating route; the quote route refuses a support view. Secrets: none; quote values reach only their owner. Web: imports flow shared → features → app (a feature test reading the app's route list moved to `app/`); tokens only. **Found and fixed in this review:** the API's link building had no API-side test (added); a namespace `React.ReactNode` (imported). **Found and fixed while building:** the 404 read as a refused ticket; `list_services`' clock; the delete sentence; the missing `no_data` state. **Left open:** open decisions 11 to 18; *Step 3, not built or not run*.

**Step 2 `/review-diff`, 2026-11-12 (after the rebase and the rest of the step):** every changed file is explained by T2.0 to T2.13, the rebase, or a gate those moved. F3 files changed, each for one owner: `ai/untrusted.py` (a per-part cap), `ai/limits.py` (chat's share), `ai/validate.py` (public word lists), `insight/store.py` (`reserve_month`), `ai/router.py` (the preview moved out; back to F3's own imports otherwise), `ai/preview/router.py` (the preview, with chat's purpose, on the telemetry tier), `ratelimit/policy.py` (`/ai/preview` on the telemetry tier), `insight_account.py`, `jobs/queue.py`, `jobs/expiry.py`. Tenancy: the preview proves the project is the organization's before its ClickHouse read, through the tenant repository; every new Postgres statement binds `org_id`; the worker's reads carry the question's own project, never the model's. Authorization: the preview is `MayManageAI`; no new mutating route. Secrets: test sealing keys are generated per run; the fake provider holds no key. Web: imports flow shared → features → app; the generic test harness moved to `shared/` so two features' tests do not import each other; design tokens only; a missing reading is an em dash. **Found and fixed in this review:** the Ask button and the finding page's link were offered on the hosted service, where F3 offers no AI (now one shared rule, `useOffersAssistant`, tested); a feature's test importing another feature's harness; `rate_limit_windows` allowed in the hostile row diff though no read writes it (removed). **Found by the gates and fixed:** the worker's missing `project_id`, the findings link to a route that does not exist, the preview's ClickHouse read on a default-tier router, a test harness naming HTTP status codes. **Left open:** open decisions 2 to 10; the parts under *Step 2, not built or not run*.

**Step 2 `/review-diff`, 2026-11-11:** every changed file is explained by tasks T2.0 to T2.10a or by a gate those tasks moved (F3's grant, parity, role and Compose tests; the five action copies; the `is_token` call sites, trap 2; the deletion and keys suites). F3 files changed, each minimal: `ai/service.py` (`connection_in_use`, `consent_fingerprint_of`, `availability_of` moved from `ai/router.py`), `insight_account.py` (F4's grants), `jobs/queue.py` and `worker.py` (the queue list and caps), the migration's replacement of F3's three job functions. Tenancy: every new Postgres statement binds `org_id`; the sweep's cross-tenant read is named in the scheduler tenancy test. Authorization: every new mutating route is gated (`MayAskAssistant`, `MayManageAI`, `MayConsentAIDisclosure`), the one exemption (`delete_conversation`) is a person's own rows, with its reason. Secrets: no ticket, question or result is logged; the tests' sealing keys are generated per run (a fixed test key first copied from `environment.py` was replaced). Found and fixed in review and by the gates: the AI import through `auth/dependencies.py`, `_history` without `org_id`, the unnamed sweep, the job's missing `org_id`, `dict(result)`, the reservation's untyped parameters, the sender-written `severity`, the coordinator's cap list. **Left open:** the eight open decisions above; the parts listed under *Step 2, not built*.

**Step 1 `/review-diff`, 2026-11-02:** every changed file is explained by plan tasks T1.0-T1.8; no tenancy, authorization or durability invariant touched beyond moving `/service-map` to `require_readable_project` (the byte-identical 404 held by the existing live tests); no credential in code, fixtures use obvious placeholders that the test proves never leave; found and fixed before handoff: a `CheckType` re-export mypy refused, a goconst lint finding in gateseed, and three response-model fields the hand-written untrusted lists had missed, caught by the new model walk. Nothing is left unfixed from this review.


Eight personas reviewed these documents on 2026-11-02, with 162 findings. **Each was checked against the code or a source before acting on it.** All 162 described a real problem, so none was rejected whole. 15 proposed fixes, or parts of them, were rejected or replaced, and are listed below with why. Nothing below is approved; all of it awaits the founder.

### Verdict per persona

| Persona | Verdict as received | Findings | IDs below |
|---|---|---|---|
| Architect | ready with fixes | 20 | A1-A20 |
| Developer | not ready | 21 | D1-D21 |
| UX | not ready | 28 | U1-U28 |
| Sales | not ready | 19 | S1-S19 |
| Marketing | ready with fixes | 14 | M1-M14 |
| Customer | not ready | 21 | C1-C21 |
| Security | not ready | 21 | X1-X21 |
| QA | not ready | 18 | Q1-Q18 |

These are the verdicts on the documents as they were. Nobody has re-reviewed them since these changes.

### What changed, in short

- **The ticket now works.** It allows by handler, lives on the message, and ends with the question.
- **Money.** The question holds the reservation, a check runs before each call, history is capped, and a dedicated sweep settles a dead question. The cost table now counts the tool list, so a typical question is about 31,000 tokens, not 15,000, and the suggested limit is about 45 million.
- **One clock,** `deadline_at`, from `call_seconds`, and compare-and-set on every state write.
- **Safety.** A citation alone earns no label; every model-controlled string that reaches the screen is checked; log quotes are folded; "no way out" is gone from product text; the AI role sees only rows in flight.
- **F3 and F28 reconciled:** network, task sets, grants, fingerprint, tool calling, window in the address, release routes.
- **`vector` no longer threatens alerts.** A `setup` step creates it, and failure means keyword search.
- **Added:** data levels, a read record, availability route, wireframes, wording table, 52 acceptance criteria, a requirement-to-criterion table, *What these tests cannot prove*, packaging, panel versus MCP, launch brief.

### Conflicts between personas, and the decision

| Conflict | Decision |
|---|---|
| A1 (handler) against D2 and Q13 (prefix-free path) | **A1.** The probe saw `/search`, which names no one route |
| A2 (message holds it; F3's sweep ignores chat) against D9 (opening `ai_runs` row) | **Message holds it, call rows hold 0.** F3's sweep then needs no change, and no fake call row exists |
| A11 (hard requirement, or out of the chain) against AC43 | **Out of the chain, by a `setup` step.** BR17 is firm, and F3 AC71 is the precedent |
| U1 (a failing argument ends the step) against X3 (hide, keep running) | **X3.** "Which URL failed?" must stay answerable |
| C11 (Copy answer) against S14 (Copy steps) and U5 | **Copy steps.** U5 is the founder's |
| D12 (grant retention) against Q8 (API marker) | **D12,** with a grant on `project_retention`. Q8's claim that the worker cannot read it holds only without a grant |
| U8 (browser zone) against C14 (`users.timezone`, else browser) | **C14,** which includes U8 |
| A12 (evict facts) against C8 (facts outside the cap) against D19 (runbook refused at cap) | **Limits that fit, plus eviction.** Runbooks always fit, facts have their own ceiling, notes always save |
| X12 and D19 (delete in flight) against A15 (treat as gone) | **409 to a person;** removal settles in its transaction, and the worker treats a gone row as a normal stop |
| X1 (taint) against S1 and Q1 (match rule) | **The match rule,** at least as strict |
| S4 (about 25 million) against the corrected table | **About 45 million.** S4's arithmetic left out the tool list |

### Rejected: fixes or parts of fixes

The problem each one named was still fixed by other means, unless it says otherwise.

| Finding | Rejected | Why |
|---|---|---|
| D2 | Compare `removeprefix(API_PREFIX)` paths | `/search` names no one route. Checked by probe |
| A2 | Have F3's sweep ignore `purpose='chat'` | Not needed: chat's rows hold 0, so F3 stays unchanged |
| D9 | One opening `ai_runs` row carrying the reservation | A row for a call that never happened, in a table of calls |
| A11 | pgvector as a hard requirement, excepted from BR17 | An exception to the rule that protects alerts, when an alternative exists |
| U1 | End the step on failure | See conflicts. X3's form is used |
| U3 | `<main>` as a *size* container | Size containment on a scrolling element would break its height. Inline size does the job |
| D19 | A runbook save refused at the chunk cap | Limits now fit, so the case cannot arise; only the 200 and 32 KiB limits refuse |
| C3, S7 | A send-time attribute drop list; excluding one service | The project is the unit, and masking settings are the place. Data level and email masking cover the larger objection. **Remains a risk:** a sales objection |
| C11 | **Copy answer** | U5: model words do not leave the console by nexovue's own hand. A person can still select text |
| C15 | **Send this answer to my admins** | D19, BR14: widens who reads poisoned text, and undoes privacy |
| C13, S19 | One runbook on several projects; a CLI import | A shared row breaks the project cascade and search's binding, so upload makes separate copies. Tokens are read-only. **Remains a risk:** stale copies |
| C17 | Emails at 50% and 70% | F3 A31 chose the screen; this role holds no email key. An on-screen warning is added. **Remains a risk:** nobody looks at the screen |
| S6 | Keep notes past the telemetry's clock | F22 will not keep cases past what they name (`retention.py:6-9`), and its founder decision 2 owns that. Save as runbook is added instead |
| X17 | Worker or `valid_for` confirming the route matches the tool | The slot rule and handler list close it, and a second check adds no protection |
| M5 | Recording Agent Builder as needing an Enterprise licence | The page read says only "appropriate subscription". Recorded as not confirmed |

**Also not done as asked:** U7's panel line "Screens now show…" is not needed, because a click no longer moves the person's window. C2's new tools are not added to MCP in this version, though tokens gain the routes; D45 keeps MCP to seven new tools. A17's "a browser key can set the name" stays true for names passing the pattern, which are fenced and inert.

### What remains as a risk

- **A believable false cause.** A matched sentence can still mislead. The label says what matched.
- **The command and URL checks are heuristics,** and a false refusal or a miss are both possible. The bed reports rates.
- **Paraphrase of a log** stays in stored answers. **Log erasure cannot reach it.**
- **A broken AI process still reads all chunks,** questions in flight and every connection key. Acceptable on a single-tenant install; not on a hosted one.
- **The API now shares a network with model-driven code.**
- **FR40's masks and intake masking have holes.** Names and customer ids still go at *facts and text*.
- **Row-level security, tool calling on real providers, embedding on Bedrock and Ollama, and exact-scan time are all unproved.**
- **Phone keyboards and real screen readers** are manual checks, and `interactive-widget` was not run.
- **A stopped question's last call may still be running** while a new question starts, bounded by its reservation and slot.
- **Expensive:** the bed's cost rose, and T2.10b touches 18 finished files. Self-hosted only for now, so **this feature earns no money yet.**
- **Slack stays out, by U5,** and rivals answer there.
- **Rival facts** are unusable in public until T8.5.
- **Memory is short on a self-hosted install: 30 days by default.** Foundation decision 6 makes it an operator setting; the default rises only after the bed measures disk use.
- **The repository is public,** confirmed on 2026-11-02 by GitHub's API without a login (visibility public, AGPL-3.0). Results stay outside it until the founder has read them (foundation decision 5).

### Second review, 2026-11-02

Fifteen proven problems. Each was checked against the code or F3, F22 and F28's documents before changing anything, and all fifteen held. All are fixed in these documents; none is implemented, and nobody has re-reviewed the result.

| Severity | Problem | Result | Where fixed |
|---|---|---|---|
| Blocker | F4 built on F3's dropped row-level security on `jobs` | Fixed. Also found: the claim function needs a queue list, or the two queues' caps fail | Design 3, grants, *A plain limit*, difference 11; T2.0a, T2.3, C4 |
| Blocker | The row-level policy stopped every final write | Fixed, **but not as the review proposed.** A separate `WITH CHECK` still aborts, as a probe showed, because an `UPDATE` with a `WHERE` checks the `SELECT` policy too. One function makes every final write (D73) | Design *Settling*, grants, AC67 |
| Major | Aged-out check used raw retention for rollup tools | Fixed. Also found: `service_stats_1m` and `detection_readings` keep the lesser of derived and 35 days, so there are three clocks (D74) | Design *The aged-out check*, spec error table, AC24, T3.1 |
| Major | Grant table broke its own rule | Fixed. `ticket_hash` needs no grant, as the function nulls it | Grants |
| Major | Removing `is_token` broke F3 and F22 | Fixed. `is_token` kept; F3 and F22 read `is_person` (D75) | Trap 2, T2.2, T7.2, AC105 |
| Major | Quotes had no working rate tier | Fixed (D76) | Routes, T3.2, AC107 |
| Major | Rejected link and limit decisions survived | Fixed. C2, reuse table, difference 4, D44, duplicate ADR bullet, this file | As named |
| Major | Link table against F3's helper and router; AC22 | Fixed (D79) | Link table, D33, AC22, T3.3a |
| Major | F22 said to need no change | Fixed. F22's note service stays as it is | Specification impact, D58, T7.2 |
| Major | Runbooks did not always fit the cap | Fixed, by a ceiling of 18 chunks (D77). Merging at a fixed minimum would not fit: at 1,000 characters it allows 6,800 chunks | Design 6, FR32, AC106 |
| Major | Gate criteria that could not be scored | Fixed. AC51 at 90%, a guess (D78); AC80 at 60 seconds, a guess; AC54 | Spec, design, plan |
| Minor | AC57 depended on an unstated limit | Fixed | AC57 |
| Minor | Undeclared foundation differences | Fixed | Differences 18, 19 |
| Minor | Stale code claims | Fixed. Also found: `@lg:` is not the same width as `lg:`, from memory of Tailwind's defaults, which T2.10b checks | Design, T2.10b, citations, memory |
| Minor | Unsourced competitor statements | "Few" sentence dropped. The Grafana claim is marked unsourced, as no URL exists | Spec *Problem*, research |

F3's and F22's documents were reconciled with these changes on 2026-11-02 (*Cross-feature reconciliation*).

### Every finding

**Fixed** means the documents now hold the fix named; the location is the main one. All 162 are fixed in this sense, none implemented, and *part* marks those with a rejected part above.

| ID | Problem | Where fixed |
|---|---|---|
| A1 | Allow-list compares wrong value | D55, design 1, AC53, T2.2 |
| A2 | Dead question leaks reservation; timeout returned | D53, design *Settling*, AC27, AC58 (part) |
| A3 | 60,000 does not limit spending | FR19, FR6, cost table, AC25, AC56 |
| A4 | Clocks disagree | FR22, D67, design *One clock*, AC54, AC55 |
| A5 | Waiting outcomes wrong | States, design *The job*, AC29, AC54 |
| A6 | Sweep sends excluded text; deleted projects | Design *Embedding*, inventories, grants, AC35, AC70 |
| A7 | No embedding model column | FR27, FR31, data table, AC85, T6.1 |
| A8 | Grants break worker | Grants, AC67, T2.3 |
| A9 | Consent weaker than F3 | FR25, D41, AC3 |
| A10 | Not reconciled with F3 | Design status, network, T2.3, T2.9, differences |
| A11 | `vector` can stop the product | D62, design *Extension*, AC43, T6.1 (part) |
| A12 | Knowledge cap unenforceable | FR32, FR28, design *The caps*, AC83 |
| A13 | F4 inside F22's transaction | D58, T7.2, specification impact |
| A14 | Delete resets daily limit | D56, AC37 |
| A15 | Ticket outlives conversation | D54, design 1, AC11 |
| A16 | Deploy tools on dead routes | FR7, C9, T3.1 |
| A17 | Stale fact; BR10 names | BR10, FR28, AC39, AC84 |
| A18 | Short memory horizon unstated | Goal, FR41, FR42, AC95 |
| A19 | One basis per tool | Design 2, check 7, AC21 |
| A20 | Poll returns whole conversation | Routes, T8.1, AC98 |
| D1 | Nobody builds tool calling | T2.7a, T6.0, AC94 |
| D2 | Every ticket read refused | A1's fix (part) |
| D3 | No network path | Design *The network*, T2.3, AC66 |
| D4 | Grants fail | As A8 |
| D5 | Log quotes read as gone | Design *Quote display*, AC19, T3.2 |
| D6 | Encoding claim false | FR8, design client, AC10, T2.8 |
| D7 | Task registration wrong | Design 3, T2.3, T2.9, T5.5, T6.1 |
| D8 | Sweep, AC27, counters cannot work | `assistant_sweep`, AC27, T8.2, columns |
| D9 | Dead reservation | As A2 (part) |
| D10 | Attacker text labelled measured | Design 2, FR7, AC12, T1.7 |
| D11 | False-alarm line; project finding | FR28, AC83, AC84 |
| D12 | AC24 unbuildable | Grants, loop, AC24, T3.1 |
| D13 | Five-minute lockout; late write | As A4 and A5 |
| D14 | Gate before what it tests | T5.7, T6.6, T8.0 |
| D15 | AC15 has no file | AC15, T2.0, T2.10 |
| D16 | Vector work too early | D62, T2.1, `query_embedding_model` |
| D17 | Ticket purpose; `ticket.id` | Design 1 |
| D18 | Viewers and runbooks | D48, AC92 |
| D19 | Cap and in-flight delete | FR32, routes, AC59, AC83 (part) |
| D20 | Step 1's demo has no command | T1.0, T1.1 |
| D21 | Stale plan; tests unnamed | Plan, all tasks |
| U1 | Step arguments unchecked | FR14, check text, AC62 (part) |
| U2 | Panel failure takes the console | Design *Placement*, AC73 |
| U3 | Pushing breaks layouts | D68, T2.10b, AC48 (part) |
| U4 | No availability route | Routes, AC74 |
| U5 | Five-minute wait; silences | States, phase lines, AC54, AC103 |
| U6 | RefreshControl falsified | Design *Polling*, AC75 |
| U7 | Citation moves window | D33, FR3, AC22 |
| U8 | Wrong clock | FR3, FR9, AC76 |
| U9 | Check step fails on phones | Flow A, AC48 |
| U10 | Stale context | FR2, design *Context*, AC49 |
| U11 | Answer below steps | D70, wireframes |
| U12 | Refusals unmapped | Design, *Error cases*, AC79 |
| U13 | Answer shape lacks flags | Design 4, AC16, AC77 |
| U14 | Unchecked reason text | FR12, `refused`, AC77 |
| U15 | Chip wording false | FR17, *Words* |
| U16 | No link table | FR18, *Where each step leads*, AC22 |
| U17 | Accessibility vague | Design, AC50 |
| U18 | Closed panel, reload | Design, UR19, AC78 |
| U19 | Too little data | States, *Error cases*, AC24 |
| U20 | No wireframes | Design *Wireframes* |
| U21 | Phone asserted | Design composer, AC48 |
| U22 | Quotes unsafe | FR15, *Words*, AC63 |
| U23 | Settings conflicts F3 | FR24, FR26, routes, AC2 |
| U24 | Correlation id; failures | States, AC103 |
| U25 | Panel width fixed | D69 |
| U26 | Runbooks under-decided | D20, D48, design, AC92 |
| U27 | Starters undefined | *Starters*, AC49 |
| U28 | Wrong and delete results | D47, *Words*, AC91, AC102 |
| S1 | Label laundering | BR6, check 7, AC61 |
| S2 | "No way out" untrue | BR18, FR14, FR15, AC62, AC63 |
| S3 | Selling line false | Founder decision 2, FR24 |
| S4 | 20 million fails | D26, cost table, `over_limit`, AC101 (corrected) |
| S5 | Limit oversold | FR19, AC56, AC58 |
| S6 | 30-day memory | Goal, dependencies, FR41, FR42 (part) |
| S7 | Log text all or nothing | D63, FR39, AC68 (part) |
| S8 | "Exactly" untrue | UR13, FR24, AC2 |
| S9 | Meaning search for few | Provider table, D61 |
| S10 | No sellable model answer | FR26, AC96, Step 4 report |
| S11 | MCP objection | Spec, *Chat versus MCP* |
| S12 | No hosted statement | Spec, *Who gets what*, AC92 |
| S13 | Metrics omitted | FR7, D59, T3.1 |
| S14 | Evidence unshareable | D33, FR38 |
| S15 | Owner cannot answer | `assistant_read_log`, AC72 |
| S16 | BR15 false | BR15, D60, AC35 |
| S17 | Gate meanings differ | AC15, AC51, research |
| S18 | Counts mislabelled | FR7, design 2 |
| S19 | Runbook setup | FR, upload, AC92 (part) |
| M1 | BR18 over-claims | BR18, AC99 |
| M2 | Sentry missing | Research table, spec line 104 |
| M3 | Claude answer undone | Research 8.4, spec |
| M4 | No name or positioning | D52, BR18, T8.3 |
| M5 | Elastic stale | Research table (part) |
| M6 | "Few choices" unsupported | Spec, research rows |
| M7 | Price message misleading | Research 4.4, 8.6 |
| M8 | No launch work | T8.5, gate |
| M9 | Reports published by merge | Founder decision 4, Step 4 |
| M10 | Market risk untested | Preconditions, founder decision 5, T8.6 |
| M11 | Persona over-promises | Spec target users |
| M12 | Soft competitor facts | Research |
| M13 | Not a difference | Research 8.3, 4.5 |
| M14 | Meaning search promise | BR18, T6.5 |
| C1 | Measured on invented cause | As S1 |
| C2 | Tools cannot answer | D59, T3.1, T3.1a, T4.1 |
| C3 | Consent all or nothing | FR39, FR40, FR24 (part) |
| C4 | What went where | As S15 |
| C5 | Commands through quotes | As S2, FR15 |
| C6 | Third-party outage withheld | D66, FR14, AC18 |
| C7 | 30-day memory | As S6, FR41 |
| C8 | Flapping fills index | FR28, design *The caps*, AC83 |
| C9 | Spending unenforced | As A3 |
| C10 | Caveats removed silently | BR5, FR12, FR13, AC16 |
| C11 | Cannot hand over | D33, FR38 (part) |
| C12 | Meaning search for few | D61, T6.0 |
| C13 | Runbooks too much work | FR, upload, AC92 (part) |
| C14 | Wrong clock | As U8 |
| C15 | No real signal | D47, `assistant_monthly_counts`, AC101 (part) |
| C16 | Secrets in runbooks | FR40, FR32, AC69 |
| C17 | 80% surprise | FR19, `over_limit`, AC101 (part) |
| C18 | No handover read | FR7, T7.2 |
| C19 | Silent waits | States, phases, `call_seconds` |
| C20 | Private route | AC96, T8.3 |
| C21 | Consent edges | FR25, FR24, D48 |
| X1 | Label chosen by model | As S1 |
| X2 | Log quotes | Check 5, AC63 |
| X3 | Step records | Check text, AC62 |
| X4 | Consent weak, late | FR25, loop, AC3, AC60 |
| X5 | Opted-out embedding | As A6 |
| X6 | Names in index | BR10, FR28, AC39 |
| X7 | Measured tools return text | Design 2, FR7, AC12 |
| X8 | Masking overstated | Design preview, FR24, AC2 |
| X9 | Runbook secrets | FR32, FR40, D48, AC69, AC92 |
| X10 | AC32 false | Copy check, AC32, *cannot prove* |
| X11 | BR15 expiry | D60, AC35 |
| X12 | Limits in deletable rows | D56, routes, AC37, AC59 |
| X13 | Role reaches too far | D57, grants, *A plain limit*, AC67 |
| X14 | Audit incomplete | AC71, AC72 |
| X15 | Query strings in logs | Design *Secrets*, AC65, T2.3 |
| X16 | Network missing | As D3 |
| X17 | Encoding claim | As D6 (part) |
| X18 | `valid_for` gaps | Design 1, AC7, AC11 |
| X19 | Question's trust class | FR6, AC64 |
| X20 | AC15 undefined | AC15, T2.10 |
| X21 | Personal-data inventory | AC34, inventories |
| Q1 | Laundering untested | AC61 |
| Q2 | AC15 unprovable | AC15 |
| Q3 | Other model text | BR7, AC15c, AC62, AC77 |
| Q4 | Regression promise thin | AC4, AC80, T2.13, T8.1 |
| Q5 | Requirements without criteria | AC86-AC104, table |
| Q6 | Gate weaker than foundation | AC51, founder decision 1, T4.1, T4.2 |
| Q7 | Gate runs too early | T5.7, T6.6, T8.0 |
| Q8 | AC30, AC24 hollow | AC24, AC30, T3.1, T3.5 |
| Q9 | Names under measured | Design 2, T1.7 |
| Q10 | AC32 false | As X10 |
| Q11 | Budget mechanism | AC25, AC56, AC57 |
| Q12 | Stop, death, sweep | States, FR21, AC27, AC28, T2.9 |
| Q13 | Ticket refusal only | AC53, T2.2 (part) |
| Q14 | Ranges for deploys | FR7, FR9, AC22, T3.1 |
| Q15 | No reachable provider, seed | T2.0, T4.1 |
| Q16 | Isolation proof hollow | AC82, T5.5, design inventories |
| Q17 | NFRs without targets | AC97, AC104, T2.3, AC50 |
| Q18 | No limits statement | Spec, *What these tests cannot prove* |

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- `is_person` comes from F3, not T2.2, and F22's helper no longer needs a change at T7.2. A ticket could otherwise have failed on F22's form between T2.2 and T7.2 (design, plan, D75).
- F5 takes `scheduler-insight` to 8 slots later (D39, differences row 9).
- The findings link follows F20's move of the feed (design).
- Founder item 2 points to the foundation's one selling line (spec).
- F4's differences from the foundation were written into the foundation: tables, routes, `embed(texts, model)`, the 45 million suggestion, no `assistant_tickets`, and the MCP catalog sentence.

## Last updated

2026-11-21 (Step 6 built on wt/f4s6, committed. Step 5 built on wt/f4s5, committed. Step 3 committed locally, not pushed; built on wt/f4s3. Step 2 committed locally, not pushed)

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in the five documents was read against the code and, where it rested on outside facts, checked on the web. Recorded in `spec.md`, *Own questions answered*, with sources.

**Decided by the authors (technical, so not the founder's):**

- **D80, the local model for the first report:** `gpt-oss:20b` on Ollama, on the founder's Apple M4 Pro with 24 GB, with `nomic-embed-text`. Native tool calling, Apache 2.0, fits 16 GB. No new cost. The report names the hardware and claims no speed elsewhere. (Plan question 1.)
- **D81, embeddings:** Bedrock through `InvokeModel` on Titan Text V2, not Converse; OpenAI-style accounts and Ollama through `/v1/embeddings`. T6.0's real calls still come first. (Plan question 3.)
- **D82, Elastic's licence:** a paid self-managed subscription, most likely Enterprise; T8.5 rechecks before any public text. (Plan question 2.)
- **D83, the reader-step trigger:** fixed before any run, at more than 10% of log-text trials followed, or any hard-gate failure traced to log text. (T4.4 and the design no longer send it to the founder.)
- **D84, slow knowledge search:** over 200 ms, a partial HNSW index per embedding model, with iterative scans, on the bundled pgvector 0.8.6. (AC97 and T6.4 no longer send it to the founder.)
- **D85, if the row-security fallback fails:** the authors redesign; Step 2 stays stopped until the new design passes the spike.
- **D86, accepting differences from the foundation:** no separate owner; each is written into the foundation, and approving the spec accepts them.

**Settled by the foundation's approval, removed from the open lists:** evaluation money, the selling line, AI on the hosted service, publishing results, and self-hosted retention (foundation items 4, 2, 1, 5 and 6). Pointers updated in spec, design, plan and research.

**Kept for the founder, rewritten with a recommendation:** the rule for building past Step 1 (scope and what is sold), the accuracy bar for "supported" (a public promise), and competitor research in the public repository (public exposure about named companies).

**Not checked:** the exact Elastic tier, as the subscriptions page's tier marks did not survive text extraction; whether a Bedrock API key works on `InvokeModel` (AWS's page shows only Converse); independent tool-calling scores for small local models, as the rankings found were vendor lists.

### Review, 2026-11-02

A second panel tried to break each answer above against the code, the foundation and outside sources.

**Held as written:** D81 (AWS's API-key page still shows only a Converse example, so T6.0's real call stays), D82, D83, D85, D86, and the five items settled by the foundation. The repository is public: an unauthenticated GitHub API call again returned `"visibility": "public"`, AGPL-3.0.

**Changed:**

- **D80, the local model, now fits the machine.** F3 runs its local model with the stack stopped (F3 A81); chat cannot, because its tools read the live API. A 14 GB model beside Docker's 11.7 GiB (`docker info`) on 24 GB would swap. The run now starts only what chat reads, lowers Docker's memory, checks `ollama ps`, and falls back to `qwen3:14b` (9.3 GB, 40K context).
- **D87, new: context length on a local model.** Ollama defaults to 4K context below 24 GiB of GPU memory, and its OpenAI-style endpoint cannot change it ([context length](https://docs.ollama.com/context-length), [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)). Chat's prompts pass 4K, so the bed and every customer's local model would lose text. Set to 32K for the bed; a connection probe refuses "ready for chat" if a known-size prompt comes back counted short; the setup page names `OLLAMA_CONTEXT_LENGTH`.
- **D84, the slow-search index, now gets used.** Postgres matches a partial index at planning, so a bound parameter never uses it ([Postgres docs](https://www.postgresql.org/docs/current/indexes-partial.html)), and the API's driver is asyncpg (`apps/api/pyproject.toml:13`). The model name goes in as a literal from the stored setting. A model chosen after `setup` is searched exactly until the next `setup`.
- **D88, new: Step 1 is built straight after F6.** Founder item 8 holds one round before F28's Step 1b, and founder item 1 asks people after seeing Step 1, which in the foundation's order would not exist yet. Design differences row 20; it must be written into the foundation before merge (D86).
- **Founder item 2, the accuracy bar:** now set after a tune run and before the report run, as F3 does (F3 AC62, A83), not from the report. The old wording contradicted D83's own reasoning that a bar set after the numbers can be bent. AC51 and plan Step 4 follow.
- **Founder item 3, competitor research:** widened. The foundation itself names rivals in the public repository (`phase-2-foundation.md:626-640`), as do other specs, so it needs one answer for the whole repository. Still the founder's.
- **Design, differences table:** rows 1, 2 and 19 marked as history, since the foundation now reads as this design does.

**Classification:** all three founder items stay the founder's (scope that changes what is sold, a public promise, public exposure about named companies). No agent decision was found to be a founder matter.

**Not checked:** whether Ollama truncates an over-long prompt silently or with an error (T2.0's first real call); whether `gpt-oss:20b` with a reduced stack actually fits (Step 4's first run).

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** the continue rule past Step 1, at least half yes to both questions (3 of 5, 3 of 6, 4 of 7, 4 of 8) (item 21); the "supported" bar, 80% right numbers, 90% right time ranges, every hard gate passed (item 12); dated competitor research allowed in the public repository with the "internal research, not a comparison or a claim" header, never in sales or marketing (item 15). The header must be added before merge.
- **Changed, B: no paid model calls.** Step 4 runs on a free-tier cloud model and `gpt-oss:20b` on the Mac. "Supported" names only what passed; OpenAI, Anthropic and Bedrock stay "not yet tested".
- **Changed, A: no rented test server.** Memory and search time are read on the Mac's Docker; the 16 GB server waits for Oracle after deployment.
- **Open decisions:** none for the founder.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 2 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.

## Founder decision 2026-11-21: the hostile suite's narrowed comparison

`test_assistant_hostile_live.py` (T2.10, AC15b) keeps its narrowed row comparison: a table with an `org_id` is hashed for the test's two organizations only, and the three tables the running stack writes on its own clock with no organization (`STACK_CLOCKS`: `alert_evaluation_status`, `detection_sweep`, `usage_ingest_offsets`) are left out, so the suite can run beside the dev stack's demo traffic. **The gap, accepted:** a write the question caused to a third organization's rows, or to one of the three `STACK_CLOCKS` tables, would not be seen. AC9's half, nothing of the other organization reaching the provider, is unchanged.

# Readiness: Finding explanation, with optional AI wording (F3)

> **Assessed 2026-11-22, as one feature (plan T7.2), on `feature/phase-2` at `4ad5d47` plus this step's changes.**
> **Verdict: NOT READY for a production rollout that states a cause or offers AI wording.** A rollout of the
> facts alone (Steps 1 and 2, with AI off, no likely cause stated) has no blocker of F3's own beyond the
> platform's (`docs/operations/production-readiness.md` is kept outside the repository, founder 2026-10-29; this
> page does not update it). Nothing here approves a rollout: that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass.

## The recorded bar, read

The bar is recorded in `status.md` (*Founder answers, 2026-11-02*; foundation item 11) and fixed before any result:
per rule, wrong causes at most one tenth of those it states, rounded down; none on the healthy run; a rule stating
fewer than 5 causes stays silent. A83 required it before the report rounds, and it was.

**It has not been met, and nothing has been measured against it.** `CAUSE_BAR_RECORDED` is `False` and
`RELEASED_RULES` is empty (`nexovue_platform/evidence/__init__.py:258,262`). `docs/research/f3-rules-report-2026-11.md`
and `f3-bed-report-2026-11.md` are skeletons with no number in them. Production refuses the development override that
shows held causes, and starts no wording run while causes are held (`evidence/wording.py:120`; AC92). So F3 as built
**states no likely cause in production**: that is the gate working, and it is not a pass of the bar.

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | The accuracy bar (T2.17, T2.18) | **open: the fault bed's run, then the founder** | The rules-only run is about 11.5 days on a machine that stays on, with the 7-day deploy scheduler (foundation item 9: after deploy on Oracle). Its tooling is built and tested on fakes (status, *Steps 2c and 6*). The founder then reads the report against the bar; a reviewed change sets `CAUSE_BAR_RECORDED` |
| 2 | Any real model (T2.10, T2.11, T4.10, T6.3, T6.5; go or stop, foundation items 30, 31) | **open: the founder, after Phase 2** | No provider was called, ever. `real_calls.py` holds no dated call, so `/settings/ai` offers no format. Injection, cost and wording results on real models, and the supported list, do not exist |
| 3 | Injection, with no model (AC76: 42 payloads through the real code from facts onward) | **pass, with a limit** | `tools/ai-eval` `test_fixtures.py`, run in this step's batch. The 32 attacks are stand-ins, not LogJack's text (open decision 17), and the hop from telemetry text to facts is `test_insight_canary.py`'s. A clean result on no model proves the code removes them, not what a model does |
| 4 | Facts work with AI off or the role stopped (AC24, AC48 Step 1) | **pass for AI off and a broken gather; open for the stopped role on the install layout** | Step 1's arms pass (`test_detection_evidence.py`, `test_detection_has_no_ai.py`, in the batch). **The install-layout arm (T5.9, AC48 Step 5) is not built:** it needs `just insight-fence`'s installed kit, which this host cannot run (below), and a test may not be written unseen-failing. Buildable in CI by whoever runs that job |
| 5 | The AI container's fence (AC42, AC46, AC41) | **pass, with what it records** | CI run 44996057046 on `b4d7c87`, job `insight-fence`: install, AC42 `5 passed`, backup, an emptied Postgres volume, restore, `48 passed` after it. AC41 (who holds which key) passes in `test_compose_contract.py`, in this step's batch. **The same run records `169.254.169.254:80` as open from the container** (see Security, S1). AC46's older-snapshot arm is not built. Not re-run here: `fence.sh` demands 40 GiB free (this host: 11) and 12 GiB of memory (shared) |
| 6 | Security review | **pass for what the code does; two risks open, one defect fixed** | See *Security review* below |
| 7 | Browser checks at 390 and 1920 pixels (T4.7, T5.8) | **pass for AC17, AC85, AC90, AC103; fail for AC70** | Playwright, first run, 2026-11-22 (below). AC70 does not hold as built (open decision 22). Design QA against the approved design, and a real phone, are the founder's |
| 8 | Migrations (0066, 0070 to 0072; later ones are F4's and F5's) | **pass** | `test_migration_rules.py`, `test_setup_schema.py` (up, down, up at Step 5), and `test_ai_vocabulary.py` in the batch. 0070 is roll-forward only, as it says: `ai_runs` and consent exist nowhere else |
| 9 | Configuration and secrets | **pass in code; rotation not rehearsed** | `AI_SECRET_KEY` is required by the role in production and optional elsewhere (`test_scheduler_settings.py`); `test_keys_live.py` passes. A rotation under live use was never executed on an install (`docs/operations/key-rotation.md` is a claim until it is) |
| 10 | Capacity and memory | **open: deploy** | The 100-findings-at-once measure (all explained, queue cleared in 7.0 s) was one run on a shared build machine with an empty store. The AI container's 768 MiB, the evidence read at volume (open decision 4) and every `sizing.md` row are guesses until Oracle (foundation item 9) |
| 11 | Health, monitoring, alerts | **pass in rules; not seen firing** | Four alerts, scrape job and `promtool` cases passed at Step 5; the container has the scheduler health check. Not re-run here. No alert has fired on a real install |
| 12 | Rollback | **pass for the AI layer; open for data** | The switch, or `SCHEDULER_INSIGHT_REPLICAS=0`, leaves the facts (AC103 screen passes in Playwright). Migrations are roll-forward only |
| 13 | Customer and operator documents | **pass, unreviewed** | `docs/product/setup/ai.md` (written this step, T4.8) and `docs/operations/ai.md`. One sentence in the first is marked unverified (Ollama's `OLLAMA_HOST`: the site was unreachable) |
| 14 | Deploy | **open: the founder** | Nothing is deployed. TLS, the public edge and operational ownership are the platform's |

## Security review (T7.2)

Read, not assumed: `compose/nexovue.yaml` (`scheduler-insight`, `ai` network), `settings.py` (role keys),
`insight_account.py` and migrations 0070, 0073 (grants and the four job functions), `probes/addresses.py`,
`insight/transport.py`, `check.py`, `word.py`, `sweep.py`, `ai/connection.py`, `untrusted.py`, `validate.py`,
`limits.py`, `explain_prompt.py`, `ai/router.py` and its gates.

**Held, with where:**

- *Container and keys.* The role gets its own settings anchor: no Kafka, ClickHouse, check, channel, connection or
  mail key; a test reads the rendered Compose file for every layout (`test_compose_contract.py`). A provider key is
  sealed under `AI_SECRET_KEY` for its own address, so a sealed key moved to another row or address does not open.
  No log line in `insight/` carries a key, a body or a URL; failures are closed words.
- *Network and transport.* Every call leaves through one function. The address is judged when the socket is opened,
  so a name that resolves differently later is refused; redirects are never followed; plain `http` reaches only an
  address the operator listed, with a port; loopback, link-local, reserved and unspecified stay refused even if listed;
  an answer over 1 MiB is a failed call. IPv4-mapped IPv6 forms were checked against Python 3.13: refused or
  public-equivalent. The API makes no provider call at all; a test is a job.
- *Prompt and evidence.* The request holds code-written facts and stand-in names only (BR8), so no attacker text is
  sent; the fence tag and hidden-character stripping exist for F4 and F5. The answer is checked in a fixed order
  (printable ASCII only, aliases that belong to the cited facts, numbers that match, no causal or origin words, no
  command opening, no link or markup) when written and again when the API reads it, because the AI account can write
  wording for any organization. These test form, not truth, and the page's label says so.
- *Account.* Column grants only; no right on `jobs` or `job_results`; the claim function lists AI queues itself, so
  a caller cannot name another queue; `SECURITY DEFINER` with a fixed `search_path`; roles with a right no grant can
  fence are refused; the account's defaults are reset on every setup.
- *Authorization.* Settings routes need `manage_ai`, consent needs an owner, the support view reads on or off only,
  and the coverage test passes.
- *Account limits.* A required monthly limit, a run cap of 3 per finding per day, an input cap, a reservation before
  the call that a dead run keeps, 80% for warnings.

**Findings:**

| # | Severity | Finding | State |
|---|---|---|---|
| S1 | MEDIUM | **The AI container's network is not an outbound firewall.** The fence's own run records the cloud metadata address as open from it. Application code cannot be made to reach it; a process that has been broken into can, and holds every organization's provider key besides | **Open, known.** Operator host rule documented in `setup/ai.md` (written this step); the optional outbound proxy (F5 T4.11) is the founder's, built before the first deploy (item 36c). Foundation now says so. **Approved by the founder, 2026-11-27 (item 37a): blocking `169.254.169.254` is a requirement before the first deploy** (`docs/operations/first-deploy.md`, step 5) |
| S2 | MEDIUM | **Two Postgres rights no grant takes away:** `NOTIFY`, and a session's own timeouts. A broken-into process can fill the notification queue or hold a transaction open, stopping Run now and migrations until ended | **Open: decision 7, the founder's.** Recommendation unchanged: a watchdog on beat's leader (not built) |
| S3 | LOW | **The account's connection limit was 20 and the role can need 21** (8 children at up to 2, a claim pool of 4, a listener); F5 raised the children from four without raising it. A 21st connection is refused in the middle of a run (seen as `TooManyConnectionsError` under parallel tests) | **Fixed.** `CONNECTION_LIMIT` is now worked out from the role's own `--concurrency` (24); tests seen failing for both the arithmetic and each pool |
| S4 | LOW | A saved `http://0.0.0.0:port` passes the save check and then fails every test as "refused" | Open, harmless: the guard refuses it. A one-line save-time refusal would say why |
| S5 | accepted | The AI container holds every organization's sealed provider key by design, and can read any organization's `model_input` (stand-in names only) | By design (ADR-040); the blast radius of S1 and S2 |
| S6 | accepted | A model's words can be wrong and still pass: the checks test form | Labelled on the page; wording is off in production until gate 1 |

No tenancy hole, authorization gap, secret in a log, or way for telemetry text to reach a provider was found.

## `/review-diff`

F3's code was read as it stands (no diff exists: it is committed). This step's own diff: the connection-limit
constants and three tests; two Playwright specs and `just e2e-insight-stopped`; the customer page; documents.
No unintended file. Findings fixed: S3. Findings recorded: S1, S2, S4, AC70, and decisions 22 to 26 in `status.md`.

## Playwright, 2026-11-22

Own stack: Postgres and ClickHouse in containers of this step's own; the API and the console's dev server on the host;
**a stub on the receiver's port that only answers 401** so the suite's liveness check passes. No Kafka, worker or real
receiver, so no spec that needs telemetry ran. Chromium 1243 from `/opt/pw-browsers` through a local config; traces off.

| Spec | Result |
|---|---|
| `ai-insights.spec.ts`: AC17 at 390 (500-character service name, 30 facts, no sideways scroll, 44 px links, an `ol`, hidden "Basis:", focus on the first newly shown fact) | pass |
| AC17 at 1920 (no capped container around the facts; the cap finder was seen finding a seeded cap); a citation opens the list and moves focus | pass |
| AC90 at 390 and 1920; AC103; AC85 | pass |
| AC70 at 390 by 664 | **fail, kept as `test.fail`** pointing to decision 22 |
| `non-ai-screens.spec.ts`: every sidebar and settings path but `/admin` and `/settings/ai`: no failure block, no empty page, no 5xx | pass (seen failing on an injected 500) |
| `just e2e-insight-stopped`'s other twelve specs | **not run**: they need the receiver, Kafka and the workers |

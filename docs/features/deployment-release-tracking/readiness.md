# Readiness: Deployment and release tracking (F28)

> **Assessed 2026-11-28, as one feature (plan gate *Production-readiness assessment*), on `feature/phase-2` at `c22a3698`.**
> **Verdict: NOT READY for a production rollout.** The code holds what it claims under test, and the signed webhook
> worked end to end once on the founder's Mac (live test B4). What is not observed is everything that only a deployed
> server, a real CI runner and the founder can show: the webhook through the public edge, a recipe on a hosted runner,
> the plan's review and Design QA gates, and the founder's own checks. The platform is not ready either
> (`docs/operations/production-readiness.md` is kept outside the repository, founder 2026-10-29; this page does not
> update it). Nothing here approves a rollout: that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass. The founder's Mac and the dev stack are
> evidence about the code, never about a deployed server.

## What F28 is, in one paragraph

Releases come into nexovue two ways. **Seen in traffic:** the worker counts spans by `service.version` into the
ClickHouse rollup `service_versions_1m` (migration `000038`), and the scheduler's release sync (`releases/sync.py`, every
60 seconds, `beat.py:166`) turns new versions into releases and periods. **Reported by CI:** a customer's pipeline
sends a batch to `POST /api/v1/deploys/webhook/{project_id}`, the control plane's only unauthenticated write, signed
with a per-project deploy secret (HMAC-SHA256 over the raw body, GitHub's `X-Hub-Signature-256` scheme). Both feed the
release list and page, the markers on charts, *Release before this* on a finding, and a stored reading and summary 30
minutes into a release (migrations `0074`, `0075`).

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | The signed webhook, end to end | **pass on the Mac; open on a server** | Live test **B4, 2026-11-28** (`docs/changes/phase-2-live-test/checklist.md`): a deploy secret minted for Production, a signed deploy of `ad-new` sent through the real webhook, and the finding named it: "Possibly: the deploy of ad-new demo-090463, 4 minutes before this", with *Release before this* naming it. Limits of that pass: the finding was opened by the demo tool, not by detection (the row says so), and the request went to the local API, not through Caddy, TLS or a public address. `releases.spec.ts` (Playwright, 2026-11-22, status *Step 6*) also sends a signed deploy as the setup page's recipe does and reads "Last deploy received" |
| 2 | Signature and secret handling (code) | **pass** | Read: `deploys/signatures.py` (constant-time `compare_digest` over every live secret, no short-circuit; SHA-1 header refused; malformed header never compared), `deploys/router.py` (body streamed and cut at 512 KiB before buffering; raw bytes, not a parsed model, so a forged body is refused before validation), `deploys/service.py` (secrets opened, signature checked, only then parsed; one refusal sentence for unknown project, no secret, no signature and wrong signature; org taken from the matched secret, never the request; replay bounded by signed `delivery_id` and a 5-minute-ahead, 7-day-behind clock window; at most 5 live secrets). Secrets sealed under `CREDENTIAL_ENCRYPTION_KEY`, purpose `deploy-webhook`, shown once; the API refuses to start in production without that key (`settings.py:430`). Tests: below |
| 3 | Feature tests, run for this assessment | **pass** | Run on the founder's Mac against the dev stack, 2026-11-28: the API's `test_deploys.py`, `test_deploys_live.py`, `test_deploy_recipes.py`, `test_releases.py`, `test_releases_live.py`, `test_release_words.py`, `test_keys_live.py`, `test_ratelimit.py`: **348 passed** (5 min 21 s, `-n 4 --dist loadfile`). The scheduler's `test_release_facts*.py`, `test_release_store.py`, `test_release_summary_live.py`, `test_release_sync*.py`, `test_detection_release_neutrality_live.py`: **80 passed**. These prove the code, not a server |
| 4 | Rate limits on the public door | **pass in code; open behind the real proxy** | Per address before verification: 60 requests a minute (`ratelimit/policy.py:127`, `WEBHOOK_TIER`); per project after the signature, 1,000 new deploys an hour, charged only for new items so neither an outsider nor a replay spends it (`DEPLOYS_ACCEPTED`). The per-address count depends on `TRUSTED_PROXY_COUNT`: the production Compose file sets it to 1 behind Caddy (`deploy/compose/nexovue.yaml:333`). **Not observed on a server** (see blocker B1) |
| 5 | Migrations (Postgres `0074`, `0075` and Step 2's deploy tables; ClickHouse `000038`) | **pass in tests; roll-forward only** | `000038`'s down is one `DROP TABLE` of a derived table (read). The Postgres changes are listed roll-forward only in `test_migration_rules.py` (status, *Step 2*); existing secrets and id-less deploys were deleted on the way up, which is safe only because nothing is deployed. Not re-run here beyond what the live suites set up |
| 6 | Retention | **pass in code** | `scheduler/releases/retention.py`: deploys expire at the project's derived retention from arrival time (our clock, not the sender's), releases from last activity, both for deleted projects; the rollup has a TTL of at most 35 days (`000038`). Audit rows for deploy batches do not expire (status, *What remains as a risk*) |
| 7 | Capacity | **open: deploy** | Step 1a's cost gate passed on demo data on the Mac's Docker (2026-11-07). Step 6's reads were measured on a shared 4-vCPU container at up to 10 M spans for one service (errors by version, 24 hours: 1.6 s cold). Nothing was measured on the Oracle server, with many services, or beside the other tables' load (foundation item 9) |
| 8 | Health, monitoring, alerts | **partial: open** | A stalled releases queue is covered by the general `NexovueJobsWaiting` (oldest ready job over 120 s, any queue; `deploy/monitoring/rules/nexovue.yml:156`). There is **no alert of F28's own**: a sync that runs but keeps failing, a cursor (`synced_through`) that stops moving, or a summary job that fails three times and leaves a period with no reading (status, *Open decisions*, Step 5) is only a log line (`release_sync_bounded`, the `NO_STORE` error). No alert has fired on a real install |
| 9 | Security review | **pass for the code; two risks carried** | See *Security* below |
| 10 | Rollback | **pass for the feature; open for data** | Pre-deployment rollback is "drop the migrations and revert" (`plan.md`, *Rollout and rollback*); the ClickHouse table is derived and recounted. The Postgres migrations are roll-forward only. A platform rollback between two releases has never been rehearsed (the platform's) |
| 11 | Customer documents | **pass, unreviewed by a customer** | `docs/product/setup/deploys.md` (321 lines) was read against the code at Step 6 and needed no change; it covers both ways in, one project per environment, the recipes, the body's rules, rotation and "also remove what it sent since" for a leaked secret. **The recipes have never run on a hosted CI runner** (blocker B2) |
| 12 | Browser checks | **pass for three screens at 375px; open for the finding section** | `releases.spec.ts` 1 passed and two older specs 2 passed (2026-11-22); 375px read as screenshots for the list, the release page and Deploys. The finding section at 375px, and hover on touch, were not checked |
| 13 | Lifecycle gates in `plan.md` | **open** | Unticked: *F6 merged*, *Review*, *Design QA*, *Production-readiness assessment* (this page), *Claims check*, *Documentation and status update*. Status is **Implemented**, not *Verified* (`status.md`, 2026-11-27) |
| 14 | Deploy, TLS, public edge, ownership | **open: the founder** | Nothing is deployed. The webhook is reached under Caddy's `handle /api/*` (`deploy/caddy/Caddyfile:119`), which is read, not observed. These are the platform's |

## Blockers

Each names the failure it prevents.

- **B1. The webhook has not been reached through the deployed edge.** Every signed request so far went to the API on
  localhost. On a server, requests arrive through Caddy, and the per-address limit trusts exactly one proxy hop
  (`TRUSTED_PROXY_COUNT=1`). If that count is wrong for the real edge (another hop, such as a cloud load balancer, or
  none), either every customer's CI shares one 60-a-minute bucket keyed on the proxy's address, so one busy pipeline
  makes everyone else's deploys answer 429, or a caller can choose its own address and the limit is no limit. Needs:
  one signed deploy and one deliberately unsigned burst sent from outside to the deployed server, with the 401s and
  429s read in the API's log. Waits on the first deploy.
- **B2. No recipe has run on a real hosted CI runner.** The contract test covers signing (`test_deploy_recipes.py`),
  not GitHub's or GitLab's environment (`plan.md`, *Not provable here*; status, *What is the founder's*). A customer
  following the setup page whose runner differs (no `python3`, a different shell quoting of the body) gets a 401 on
  every deploy with nothing on our side to tell them why beyond "not signed". Needs: each recipe run once from a hosted
  runner against a reachable address. Waits on a public address.
- **B3. The plan's own gates before rollout are open.** *Review* and *Design QA* are unticked; the founder's checks
  owed are the demo recording, T2.10's timed new person, and the finding section with a real finding. A rollout before
  them ships screens nobody but their author has judged against the approved design. Waits on the founder.
- **B4. The platform's blockers.** F28 cannot be safer than the install it runs on; the private gap list (last
  re-assessed 2026-10-10 in the maintainer's copy, and not updated here) has the platform at NOT READY.

## Recommended

- **An F28 alert:** the release sync's oldest unsynced minute, or the age of `synced_through`, as a metric with a rule
  beside `NexovueJobsWaiting`; and a count of periods past due with no reading. Without them a broken sync shows only
  as releases that quietly stop appearing.
- **A real send of the release summary** through Slack, Teams, Discord and email before any channel is named in
  launch text (foundation section 12 already forbids the claim until then).
- **Rehearse a `CREDENTIAL_ENCRYPTION_KEY` rotation with live deploy secrets** on an install (`just keys-reseal`;
  `test_keys_live.py` covers the reseal in code; `docs/operations/key-rotation.md` says it has never run on a deployed
  install).
- **An expiry for deploy audit rows** (`deploy.batch_recorded`), which at the hourly limit reach about 8.76 million a
  year for one project (status, arithmetic).
- **Refresh the competitor rows** within two weeks before announcing (Claims check gate; the 2026-11-22 reading lapses
  for that purpose on 2026-12-06).
- Step 4's `aria-live` on sections moving from skeleton to numbers, for Design QA to judge.

## Security

Read for this page: `deploys/signatures.py`, `deploys/router.py`, `deploys/service.py` (the webhook half),
`deploys/queries.py` (`live_webhook_secrets`), `ratelimit/policy.py`, `settings.py` (keys in production, proxy count),
`deploy/compose/nexovue.yaml`, `deploy/caddy/Caddyfile`.

**Held:** order of operations (no write before the signature holds); one refusal sentence, so the door does not answer
"is this project real?"; constant-time comparison over every live secret; the org read off the matched secret, and
the one deliberately unscoped query (`live_webhook_secrets`) documented as such; a body bound enforced on bytes
received, not on `Content-Length`; replay ended by signed delivery ids and a 7-day window; secrets sealed and shown
once; minting and withdrawal need `manage_credentials`; metadata masked by the worker's rules.

**Carried, not fixed here:**

| # | Severity | Risk | State |
|---|---|---|---|
| R1 | MEDIUM | **A stolen deploy secret can plant a believable deploy** on a version never seen, and F3 would name it as a possible cause | Known since review (status, *What remains as a risk*). Mitigations: the basis is labelled "signed deploy record", and withdrawal with "also remove what it sent since" exists. Detection is after the fact |
| R2 | LOW | **Before the signature, each request opens up to 5 sealed secrets and computes 5 HMACs over up to 512 KiB** | Measured at 1.82 ms median (status, *Step 2*); bounded by the 60-a-minute per-address limit, which rests on B1 |

The browser-door exposure F28 inherited from F6 was closed by `docs/changes/browser-door` (plan gate ticked 2026-11-07,
checked in code 2026-11-12). No tenancy hole, authorization gap or secret in a log was found.

## The founder's open decisions (from `status.md`)

None blocks a rollout; each was built as recommended and is open to overrule.

- **Step 6, still open:** 1, where the changelog entry lives (recommended: keep it in `status.md` until release notes
  are generated); 5, two builds on one disk (not F28's); 6, the traffic floor counts requests over the whole stretch
  (recommended: leave). Decisions 2, 3 and 4 are done (foundation items 36a, 36b, 37h).
- **Steps 1b to 5, overrulable readings:** the summary's words read by delivery (ADR-066); by-time readings stored but
  never flagged; the webhook adding the summary job; a job failing three times leaving no reading (logged only; see
  *Recommended*); the list's flag from the 30-minute reading; "none reported" for a versions-only service; a failed or
  rolled-back-only release never "the release before this"; the per-run cap never splitting a minute.

## What could not be checked here

- Anything on a deployed server: the public edge, TLS, the real proxy count, memory and disk on the Oracle shape,
  alerts firing.
- GitHub's own delivery payload and webhooks from GitHub (no public address; the live test says so).
- A recipe on a hosted runner; a real channel send of the release summary.
- The founder's judgements: the demo recording, Design QA, the timed new person.
- Playwright, the web unit suites and the Go worker's rollup tests were not re-run for this page; the Playwright
  results above are from status (2026-11-22) and are cited as such.

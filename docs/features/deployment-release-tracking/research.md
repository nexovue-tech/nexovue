# F28 Deployment and release tracking: research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Research input for F28's specification and design. It approves nothing, and no
> code is written from it.
>
> **How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present.
> **Nothing was run:** no test, query, or request. Line numbers are for that working tree and will drift. "Expected"
> means read from the code and not reproduced. Competitor facts and prices were read on 2026-11-02 through a
> summarizing tool, from the vendors' own pages unless marked, and must be checked again before any public claim.
> No customer was interviewed for this document.
>
> **Revised 2026-11-02 after the persona reviews** (`status.md`, *Reviews*): competitor rows, what they say, and the claim in
> section 5, which was false in several parts. The decisions in section 7 are the research's starting point. Where the
> specification differs, the specification stands, and "rule" on a chart is now "marker" (D44).

## The short version

1. **The plumbing is good and the product is missing.** The signed webhook, replay protection and the "order, not
   cause" discipline are sound. But a customer cannot find them: the console has no screen for the deploy secret, and
   the customer documentation never says how to send a deploy.
2. **The markers on the charts cannot be read.** Each dashed line has no version, service or hover text. The version is
   computed and then thrown away.
3. **Every span already says which version produced it, before sampling.** That one fact changes the design. Today
   the value is dropped from the exact rollups. Keeping it gives an exact comparison by version, a real picture of a
   rolling deploy, and a release even for a customer who never set up a webhook.
4. **This disagrees with the foundation, openly.** The foundation chooses a comparison by time because rollups hold
   no version. That is today's state and no real constraint, since nexovue is pre-deployment. This document
   recommends adding the version rollup, with a measurement gate on its cost (D1).
5. **Do not build a second detector.** F6 already judges whether a service got worse. F28 links its findings to
   releases, and never claims that a deploy caused anything.

## Words used here

| Term | Meaning |
|---|---|
| **Deploy** | A statement sent to us that a version reached a service, stored in `deploy_events` |
| **Release** | One version of one service, whether we learned of it from a deploy, from telemetry, or from both |
| **Rollup** | A table of per-minute totals, written before sampling, so its counts are exact |
| **Sampling** | Keeping only some traces to save storage. Here: all errors, all slow traces, one healthy trace in ten |
| **Rolling deploy** | Replacing instances a few at a time, so old and new versions serve traffic together for a while |
| **Canary** | Sending a small share of traffic to the new version first, and comparing it to the old |
| **Control** | The old version running at the same moment, which is what makes a comparison fair |
| **Cardinality** | How many different values a column holds. More values mean more rows and more memory |
| **HMAC** | A signature made with a shared secret, proving who sent a message and that it was not changed |

## 1. What exists

### Sound, and to keep

| What | Where | Note |
|---|---|---|
| Public webhook reading raw bytes, capped at 64 KiB while streaming | `apps/api/src/nexovue_api/deploys/router.py:38,43-62,127-140` | Cap applies to what arrives, not to what the sender claims |
| Signature checked before parsing and before any write; all refusals read alike | `deploys/service.py:50-55,86-90` | Does not reveal whether a project exists |
| GitHub's signature scheme, SHA-256 only, constant-time comparison | `deploys/signatures.py:27,56-76` | |
| Replay refused by delivery id, including racing duplicates | `deploys/service.py:99-107,120-134`; index at `data/postgres/models.py:816-822` | The scheme has no timestamp, so this is the only replay defence |
| Overlapping secret rotation, all audited, viewing included | `deploys/service.py:252-306`; `audit/service.py:70-71,113-114` | |
| "Order is a fact, cause is not" | `deploys/service.py:162-175`; `errorgroups/schemas.py:15-26`; `features/errors/error-groups.ts:65` | The honest wording to carry everywhere |
| Exact preceding-deploy reading without a guessed lookback, with an honest cap | `deploys/service.py:144,193-236`; `deploys/queries.py:119,148` | |
| Error list and detail show "Deploy before" | `errorgroups/service.py:185,189-205`; `ErrorsScreen.tsx:278,364`; `ErrorGroupDetail.tsx:136-143` | |
| Window-aware, service-aware marker logic | `apps/web/src/shared/deploy-markers.ts:37-58` | |
| A separate rate tier, 60 a minute | `ratelimit/policy.py:126-127` | See defect 8 |
| Deletion with the project | `organizations/tables.py:72-73` | |
| Span detail shows the build | `features/traces/SpanDetail.tsx:104-105` | |

### Defects and gaps found by reading

| # | Finding | Evidence | What breaks |
|---|---|---|---|
| 1 | **No screen for the deploy secret.** Only the deletion summary mentions webhook secrets | Search of `apps/web/src` finds `features/projects/ProjectsScreen.tsx:178` and `api.ts:81` only | Only someone calling the API by hand can set it up |
| 2 | **No customer instructions.** `docs/product/setup/` never mentions the webhook path or delivery header; `setup/api.md:74` lists only `GET /deploys` | Search of `docs`, `tools`, `deploy`; hits are `docs/changes/remaining-recommendations/design.md`, `docs/features/team-and-api-access/plan.md`, `tools/otel-demo/doors.py` | The only working example is our demo script (`doors.py:218-262`) |
| 3 | **Markers carry no words.** `drawDeploys` draws lines and never reads `marker.version`; "version" does not appear in `TrendChart.tsx` | `TrendChart.tsx:251-272`; computed at `deploy-markers.ts:55` | A line says that something shipped, not what. On Overview, not even which service |
| 4 | **Old windows lose their markers silently.** The console passes no limit or window, so it gets the newest 50 of the whole project, then filters | `overview/api.ts:219-230`, `servicedetail/api.ts:281-292`, `deploys/schemas.py:17`, `deploys/router.py:65-76` | A busy project sees no markers last week, and this reads as "no deploys". `truncated` is dropped by `select` |
| 5 | **Any time is accepted.** `deployed_at` has no bounds | `deploys/schemas.py:42`, `deploys/service.py:115` | A far-future deploy holds the top of the list forever. A CI clock error or a stolen secret can place a marker just before an incident |
| 6 | **One moment only.** No started, finished, failed or rolled-back state | `deploys/schemas.py:38-44` | A failed deploy draws the same marker as a good one. A 40-minute rollout is one instant |
| 7 | **The delivery id is not length-checked, and its column size disagrees.** Migration says 255, model says 200; only `IntegrityError` is handled | `0007_deploy_secrets_and_replay.py:72`, `models.py:806`, `service.py:99,128` | Expected: an over-long id from a signed sender gives a 500, not a clear refusal. Low severity. NUL characters in `metadata` should be tested too |
| 8 | **Batching is impossible and the limit is per address.** One service per request; 60 a minute per IP | `deploys/schemas.py:39`, `ratelimit/policy.py:127`, `middleware/ratelimit.py:389` | Arithmetic: a repository releasing 100 services from one runner is refused from the 61st, and the comment "no CI system pushes sixty a minute" is wrong for it |
| 9 | **Secrets are stored in plain form and can be re-read.** The model explains they cannot be hashed, which is true; they could still be sealed. The four `SEALED_COLUMNS` do not include them | `models.py:837-850`, `keys.py:93-135`, `service.py:252-266` | A database dump or read-only leak yields every signing secret, and so the power to forge deploys |
| 10 | **Names are never checked.** A deploy of `check-out` for telemetry named `checkout` is stored and matches nothing | `deploys/service.py:110-119`, exact comparison at `deploy-markers.ts:52` | Silently no markers and no links; nobody is told |
| 11 | **"No deploy" and "we are never told" look the same.** A null link is shown as an empty cell, and `error-groups.ts:72-77` says nothing on purpose | `errorgroups/schemas.py:25-26`, `ErrorsScreen.tsx:364` | The customer research's correctness rule is not met (`phase-2-customers-2026-11.md:165-167`) |
| 12 | **Reads by time do not exist,** though the query does | `deploys/queries.py:119` is reached only through `history_for_window` | Blocks defect 4's fix and F20 |
| 13 | **The deploy reader lives in the API,** which the scheduler cannot import. The scheduler has no deploy reference at all | Search of `apps/scheduler/src` and `packages/py-platform/src` finds none | F3 cannot use it until it moves (foundation, section 8) |
| 14 | **The finding page has no markers,** nor do profiles or metrics | `FindingScreen.tsx:535-544`; catalog promises "all charts" at `feature-catalog.md:372` | The screen where the question matters most cannot answer it |
| 15 | **No retention rule.** `deploy_events` lasts as long as its project | Code map; `organizations/tables.py:72` | Fine in itself: it is small (a guess at scale, unmeasured). But it outlives the numbers it would be compared with (section 4) |
| 16 | **MCP drops the "partial" flag and returns raw metadata** | `assistant/tools.py:280-281` | Up to 64 KiB of sender-chosen text reaches a customer's AI unlabelled. F4 owns labels (foundation U7), but F28 should limit what it stores |

**Two corrections to earlier documents.**

- The code map left open whether `ErrorGroupSeen.first_seen_at` is the true first sighting. It is filled only for
  projects with a new-error rule (`alerts/new_errors.py:3-7,84-89`), so it cannot serve as a general "first seen".
- The code map says a version exists "on spans and logs". True, but differently: a column on spans
  (`000001_spans.up.sql:31`), and a JSON path on logs (`errorgroups/queries.py:44,103`), which has no column and costs
  more to filter. Metrics rows were not found to carry it as a column.

### The fact that matters most

The worker holds the version on every span **before sampling**, and then discards it from the exact counts.

- `spanRow.DeploymentVersion` is filled from `service.version` (`traceassembly/flatten.go:28,70,195`).
- `countable()` builds what the rollups see, and does not copy it (`traceassembly/stages.go:39-59`;
  `derive/counters.go:52` onward).
- `service_stats_1m` and `endpoint_stats_1m` have no version column (`000035`, `000008`).
- Sampled spans are skewed on purpose: every error, every slow trace, one healthy trace in ten
  (`sample/policy.go:39-46`). An error rate computed from them is badly overstated, and the architecture document
  records an earlier design being measured wrong by about an order of magnitude
  (`current-system-reference.md:85`). They cannot be used for rate or latency.

Also: our own setup documents already tell customers to set `service.version` and promise it "lets nexovue separate
'the service is slow' from 'the release we shipped at 14:00 is slow'" (`setup/platforms/kubernetes.md:83-84`). Today
nothing delivers that promise.

### Retention limits a comparison

| Source | Kept | Consequence |
|---|---|---|
| `service_stats_1m`, exact histogram | Lesser of derived retention and 35 days (`000035:50`) | Exact p95 for a release is available about 35 days at most |
| `endpoint_stats_1m`, approximate quantiles | Derived retention, 30 days on Free (`0005_plans_table.py:34`) | Endpoint table may last longer than service numbers on paid plans |
| `detection_readings` | Lesser of derived retention and 35 days (`docs/architecture/database.md:21`) | F6's band exists for the same period |
| `deploy_events` | With the project | Old releases must say "the numbers have expired", never show blanks or zeros |

## 2. What users need

Evidence here is weak: vendor guides and practitioner writing, graded in
[`phase-2-customers-2026-11.md`](../../research/phase-2-customers-2026-11.md), and no interviews. That document
recommends five to eight conversations before this specification is approved. This one is not blocked on them.

| Person | What they ask | What today gives them | What they need |
|---|---|---|---|
| **On-call engineer at 3 a.m.** | "Did something ship just before this? What, and where?" | An unlabelled line, if the deploy is among the newest 50 | A labelled marker on the finding's own chart; "v2.1 of checkout, 6 minutes before this started"; a plain "we receive no deploy information for this service" when true. The comparison in one glance, without hunting for times |
| **Developer who just shipped** | "Is my release fine?" | Nothing, except the CI script at `setup/api.md:157`, which checks only a threshold | A release page: old against new, on requests, failures, p95; new errors; honest "too little traffic yet" |
| **New developer on the customer's team** | "How do I set this up?" | No page exists | A copy-and-paste step for their CI, a test button, and a visible "last received" |
| **Admin** | "Who can post? What if the secret leaks?" | An API; a good rotation model, unreachable from the console | A settings screen, sealed secrets, rotation in the console, a record of refusals' count and last arrival |
| **Buyer (founder or CTO of a small team)** | "Does this save time, and do I have to wire up more things?" | Nothing to show | It works from what they already send, if their build already puts a build id in `service.version`. A value that changes per build normally means one change to the build or deploy step, once per service, so "no CI work" would be false |
| **Attacker** | See section 6 | | |

The customer research's most agreed advice: without markers you are guessing whether a change came from a deploy or
traffic; group errors by version; offer "what changed since the last deploy" (`phase-2-customers-2026-11.md:162-164`,
vendor guides, no complaint data).

**Downstream needs.** F3 needs facts with ids and a `coverage` value. F20 needs markers for a real window. F22 and F5
need `commit_sha` (foundation, sections 8 and 9).

## 3. How competitors do it

| Product | How it learns of a change | What it does with it | Limits it states | Price seen |
|---|---|---|---|---|
| **Datadog** | **Both.** The reserved `version` tag on telemetry, and a declared signal: the DORA Metrics API or `datadog-ci dora deployment` | Version section on the service page with first and last seen; compares any two versions over the past 30 days; errors new, gone, or in both; per-endpoint requests, latency, errors. Faulty-deployment detection on errors, alerted through an event monitor. Rollbacks detected when a previously deployed version is redeployed. Trace metrics "calculated based on 100% of the application's traffic, regardless of any trace ingestion sampling configuration" | Warns that a listed error "doesn't mean" the version introduced it, and a rare error may be listed gone because it has "not been seen *yet*". May not flag: few or transient errors, too little history, errors common during deploys. With sampling at the OpenTelemetry SDK, its metrics come from the sampled data | APM $31 per host a month annually, $36 monthly. The price page does not say which tier holds deployment tracking |
| **Sentry** | **Both.** "Sentry will automatically create a release entity in the system the first time it sees an event with that release ID", or by CLI or API; deploys are a separate record with `dateStarted`, `dateFinished`, environment | Commits and authors per release; suspect commits; regression when a resolved issue returns; crash-free rates, adoption; deploy emails | Commit association is for error issues only. Suspect commits need `patch_set`. Repository integration optional | Developer free; Team $29 ($26 annual); Business $89 ($80 annual). Release Health on all plans |
| **New Relic** | Change events through API, CLI, GitHub Actions, Jenkins; optional `commit`, `deepLink`, `deploymentType` (BASIC, BLUE_GREEN, CANARY, OTHER, ROLLING, SHADOW) | Error trends, anomaly detection, before and after metrics; events for flags and operations too | Curated UI only for full platform users | Not read |
| **Honeycomb** | Markers by API or CLI, with `type`, `message`, `url`, `start_time`, optional `end_time` | A marked line or range on graphs | No comparison described in what was read | Free; Pro from $150 a month. Page does not mention markers |
| **Grafana** | `POST /api/annotations`; not deploy-specific: `text`, `time`, `timeEnd`, `tags`. Application Observability (Cloud) reads `service.version` as a filter attribute | Marks graphs. Application Observability compares a service's RED metrics with an earlier period, by time window | General annotation; CI recipe left to the user. A comparison of one version against another was not confirmed (from search results, not read in full) | Not read |
| **ClickStack (ClickHouse)** | **No event at all: a marker where a version first appears in the tile's data**, from `service.version` by default, with a configurable expression | Dashed line with a hover list; nearby ones collapse to "N releases" | States "release markers rather than deployment markers": same-version redeploys draw nothing; an idle service draws one on resuming; 500 versions a window; log and trace tiles only | Not read |
| **Coroot** | Kubernetes metadata through kube-state-metrics. "Requires no integration with your CI/CD pipeline" | "Each release is automatically compared with the previous one." Audits each rollout 30 minutes after, against the previous version: SLOs, log errors, restarts, CPU, memory leaks. "Stuck" after 3 minutes (adjustable). Slack notice | Kubernetes Deployments only. **Edition unsettled:** the Apache-2.0 repository's README advertises this, and the editions page lists it under neither edition | Enterprise $1 per monitored CPU core a month. The page does not say which edition holds this |
| **Dynatrace** | Versions from environment variables or pod labels; SDLC events separately from pipelines | Release dashboard with versions, rollout status, problems, vulnerabilities; release validation | OpenTelemetry resource attributes do **not** populate its release list | Not read |
| **SigNoz** | `service.version`. "Set `service.version` to a per-build value, not a static string." "SigNoz detects a deployment each time this value changes." Sources named: git SHA, CI commit variables, a Helm image tag | Deployment Markers. **Screens and edition not confirmed:** the page read is an instrumentation guide. Earlier note: SigNoz's Noz compares around a release, Cloud only (`phase-2-competitors-2026-11.md:252`, not rechecked) | Tells customers to change their build | Not read |
| **Observe** | `service.version`: "When we see a span with a new value of `service.version`, we create an event." "Use your build system to set the `service.version` resource attribute" | Markers on RED charts and a table of deployments | Needs the build to set it | Not read |
| **OpenObserve** | No first-party page found in these searches, which is no proof of absence | | | Not read |

**What this says.**

- **Releases with no webhook are common, not a differentiator.** Sentry and Datadog each take both telemetry and a
  declared signal. ClickStack, SigNoz and Observe read `service.version`. Coroot reads the orchestrator. Honeycomb,
  Grafana's annotations and New Relic need a signal from CI.
- **Vendors that read a version say the build must set it.** SigNoz and Observe both do. That is the true cost of this
  route, and our own setup pages have not stated it.
- **No page read here reports where a declared deploy and an observed version disagree.** That is not proof nobody does
  it, so it must not be claimed as unique.
- **Counts before sampling are parity with the leader at best.** Datadog documents its trace metrics on all traffic.
- **Prices:** "not read" stays as written. No cost comparison may be made from this table.
- Comparison by version is what the leader does. F28 will be measured against Datadog and Sentry, as the competitor
  research warned.
- Datadog's published caveats are the honest model, and worth repeating in our own words.

## 4. Methods compared

### How to learn that a change happened

| Method | Setup for customer | Strength | Weakness | View |
|---|---|---|---|---|
| **A. Signed deploy from CI** (exists) | One CI step | States intent; can carry commit, and same-version redeploys appear | Needs setup; can be wrong or missing | **Keep, finish** |
| **B. Version first seen in rollup** | None if `service.version` is set | Works on any platform; says when it truly started serving | Needs the version set; config-only changes are invisible; `latest` as a version says nothing | **Add** |
| **C. Native GitHub delivery** (`deployment_status`) | Paste a URL and secret | No CI edit for those using GitHub deployments | Payload does not name a service (`schemas.py:31-35`). One webhook sees every environment, so staging would mark production unless filtered. `ping` must be answered, or set-up looks broken. Fields and payload size were not confirmed: the status schema has no SHA (GitHub REST), and our cap is 64 KiB against GitHub's 25 MB maximum | **After A and B** (D5) |
| **D. Native GitLab** | Paste | | Its HMAC signs `{id}.{timestamp}.{body}`, base64, in other headers (GitLab docs): a second verifier. It does have a timestamp, which ours lacks | **Later** |
| **E. Kubernetes watcher** | An agent or cluster access | No CI, sees rollout status | A new component and cluster permission; Kubernetes only. Not worth it while B exists | **No** |

### How to compare before and after

| Method | Exact? | Handles rolling deploys | Handles time of day | New writes | View |
|---|---|---|---|---|---|
| **1. Fixed time windows around the deploy,** from `service_stats_1m` (foundation's choice) | Yes for counts | **No.** Minutes mix versions | **No,** unless compared with last week as well | None | Fallback when no version is set |
| **2. Window against F6's normal,** from `detection_readings` | Yes | No | **Yes,** it is what the normal is for | None | **Use with 1 for context.** Only for judged services |
| **3. Version split from stored spans** | **No,** skewed by the sampler | Yes | Yes when concurrent | None | **Never for rate or latency.** Fine for "which errors, in which version" |
| **4. Version split from a new pre-sampling rollup** | Yes | **Yes,** old and new run together, which is a concurrent control | **Yes during a rollout;** partly after it | One table and worker change | **Recommended** |
| **5. Statistical canary judge,** such as Kayenta's Mann-Whitney test with a fixed 98% confidence interval (Spinnaker docs) | Needs samples per instance group | Yes | Yes | More, and customers' control groups | **No.** It is for gating a rollout, which we do not run. Note the idea: Google's published canary service asks "is the canary meaningfully worse?" against a concurrent control (via a search extract; the full article returned 403) |

**Why method 4 is the correct design, and what could be wrong with it.**

- **Sketch:** `service_versions_1m` keyed by tenant, project, service, version, minute, holding request count, error
  count and the same 64 latency buckets as `service_stats_1m`, so the arithmetic is shared. `derive.Span` gains the
  version, and the existing cap pattern applies (`derive/services.go:55,205-237`).
- **It yields four features from one table:** exact comparison, rollout share over time ("v2.1 at 40% of traffic"),
  version first and last seen (method B), and a version-free fallback.
- **Cost is not measured.** As a guess, a service mostly runs one version, so rows would be near `service_stats_1m`'s,
  and higher during rollouts. A customer putting a build id per instance would multiply it, hence a per-service cap
  with an "other" bucket. The worker's memory for the extra key is also unmeasured.
- **Gate:** before the design is approved as final, measure rows and worker memory on the OpenTelemetry Demo with
  forced churn. If a cap cannot hold it, method 1 with 2 is the recorded fallback.
- **When it cannot help:** no `service.version` set; same-version redeploys; low traffic. The screen must say which.

### Traffic floors

F6's floors exist: 200 requests to judge slower, 20 for failing (`scheduler/detection/method.py:50,56`). A release
comparison should use the same idea and say "too little traffic to compare" rather than show a percentage from 5
requests. Exact numbers belong to the design, and are guesses until tested.

## 5. What we should bring, and what we should not attempt

### Bring

1. **Setup a person can finish in five minutes (a target, unmeasured):** console screen, CI recipes for GitHub Actions, GitLab CI and plain
   `curl`, a "send test" check, and last-received time per service.
2. **Markers that can be read,** on every chart that has a window, the finding page first, fed by a real window, with
   version and service on hover, and collapsed when close together.
3. **Releases screen and release detail,** per the foundation's routes, with `/releases/{id}/impact` renamed `/releases/{id}/comparison` (D44), since "impact" implies cause.
4. **Exact comparison by version,** falling back to time with a visible label stating which method produced the
   numbers. Against the same time last week as well.
5. **Releases with no webhook,** from version first seen, once the build sets `service.version`, plainly labelled "seen in telemetry" and distinct from
   "reported by your CI".
6. **Coverage, stated:** reported and seen; reported, never seen (the rollout failed, or the names differ); seen,
   never reported; none. This is what lets F3 say "no deploy information for this service".
7. **Links both ways with F6:** a finding shows the deploy before it; a release shows findings that started after it.
   Same "order, not cause" wording. F6's own "new normal" stays the only way a baseline moves.
8. **Errors by version:** new in this version, gone, in both, carrying Datadog-style caution about rare errors.
9. **Deploy states:** started, finished, failed, rolled back, so a rollout is a range and a failure is not read as a
   release. Batch delivery for many services.
10. **Facts for F3 through one shared reader,** in `nexovue_platform`, with ids, and no AI code at all.

**What this lets us say, once built and true:** "When your spans carry a build id in `service.version`, Nexovue compares
old and new from counts taken before its own sampling, and every comparison says how it was made. Request and error counts
are exact for the spans Nexovue receives; p95 is an estimate from latency buckets." Exactness before our sampling is a real
architecture property (`current-system-reference.md:85`), and Datadog documents the same for its trace metrics, so **it is
not a reason to choose us, and we must not claim to be more accurate than anyone.**

The earlier sentence here ("every release is compared on exact, pre-sampling numbers, on your own server, with no extra
charge and no CI work") is withdrawn. Not every release can be compared; p95 is an estimate; counts are short by any sampling
done before sending; hosted buyers are not on their own server; disk and memory are unmeasured; and a per-build value
normally means a build change. See `status.md`, *What may be said*, and the specification's *Positioning*.

### Do not attempt

| Not this | Why |
|---|---|
| **The words "caused by", or a verdict of "bad release"** | The existing discipline is right, and on-call engineers follow a confident wrong answer. A comparison may say "failure share rose from 0.2% to 3%" |
| **A second detector on deploys** | F6 owns judging. Two opinions on one service is the double record F6 D1 refuses |
| **Suspect commits by file blame** | Sentry's needs per-commit file lists and an error-to-file path. Foundation says F28 uses no connection. Store `commit_sha`, link out, and let F22 and F5 build on it. Expect to lose this comparison, and do not hide it |
| **Blocking or rolling back deploys** | nexovue observes; the only write out in Phase 2 is a person's click. A customer can script from the API (`setup/api.md:157`) |
| **Team metrics such as DORA's four** | DORA itself warns against making them targets or comparing teams. Change fail rate needs a definition of failure we do not hold. Not asked for by the chain |
| **Mobile-style crash-free adoption** | Sessions are not our model here. Rollout share by traffic covers server services |
| **Kubernetes watcher** | Section 4 |
| **Feature flag events** | Would be useful evidence, but widens scope. Generic `deploy_events` must not become a general change log in version 1 |

## 6. Thinking as an attacker

**Trust levels differ, and must be displayed.**

| Attacker holds | Can do | Answer |
|---|---|---|
| Nothing | Hit the public route; use up the 60 per address | Refused before parsing, already. Fine |
| A captured signed request | Replay | Same delivery id answered with the stored row, already. But the id is unsigned and outside the body: **the same body with a new id stores a new deploy.** The comment at `service.py:96-98` covers "same id", not this. Expected from reading; needs a test. A signed timestamp or an id inside the body would close it (D6) |
| The secret | Forge a deploy before an incident to shift blame or steer an AI; flood `deploy_events` at 60 a minute with 64 KiB of metadata each, about 3.75 MiB a minute by arithmetic | Bound the time; shrink metadata; per-project cap; seal secrets; show source and arrival time, not just the claimed time |
| A browser key | Possibly send spans naming any version, making a false "seen" release | **Not checked:** whether the browser door can produce SERVER or CONSUMER spans, the rollup's only input (`derive/counters.go:61`; `receiver/internal/otlp/browserdoor_test.go` is the place to start). The design must check. Either way, "seen in telemetry" is never presented as a deploy, and a cap limits row growth |
| Database read access | All signing secrets | Seal them (D7) |
| An AI reader | Reads version strings, service names, metadata planted by any of the above | Foundation section 3: version and service names are **names, replaced by alias**; metadata is **free text, not sent**; order and times are facts. F28 uses no AI, but it must store metadata as something F3 can leave out. Links must be built by us: a `commit_sha` validated as hex, never a sender's URL rendered as a link without a scheme check |

Text from any sender renders through React's escaping today. A clickable link would be new risk, and is covered above.

## 7. Decisions taken

Each is recommended by the authors, and the founder may overrule. Details belong to the specification and design.

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| D1 | How to compare | Time only (foundation); new version rollup; both | **Both: a version rollup first, time as labelled fallback.** Differs from foundation section 9, which says time, on the grounds that rollups hold no version. They could, cheaply in code, and pre-deployment there is no data to migrate. Time alone is wrong during rolling deploys. Gated on a cost measurement. Recommended by the authors, founder may overrule |
| D2 | What a release is | Row in a new table; a view over deploys and rollup | **A derived view for the reading,** keyed by service and version, unless the design shows a stored row is needed for stable ids in `/releases/{id}`. Recommended by the authors, founder may overrule |
| D3 | Regression as a finding (catalog wording) | New finding kind; link to F6 | **Link only.** Catalog sentence is satisfied by F6's finding plus a link. Recommended by the authors, founder may overrule |
| D4 | Commit data | None; `commit_sha`; full commit lists | **`commit_sha`, validated, with optional repository link from `service_repositories` when F22 has built it.** Matches the foundation. Recommended by the authors, founder may overrule |
| D5 | Native GitHub receipts | Now; after; never | **After recipes and telemetry.** The foundation allows it, not requires it, and environment and service mapping need settings of their own. GitHub Actions users are served by a recipe from day one. Recommended by the authors, founder may overrule |
| D6 | Replay of a signed body with a new id | Accept; signed time in the body; refuse repeated body | **Require a signed `delivery_id` or timestamp in the envelope, and refuse old ones.** Pre-deployment, so the envelope may change. Native GitHub delivery, when added, keeps today's id check as it has no timestamp. Recommended by the authors, founder may overrule |
| D7 | Secret storage | Plain; sealed | **Sealed, re-readable by admins only,** using the existing `SEALED_COLUMNS` rotation, a purpose of its own, and the API's key. The webhook runs in the API, so no new container needs it. Recommended by the authors, founder may overrule |
| D8 | Metadata | Keep 64 KiB free form; shrink; remove | **Named fields (`commit_sha`, `url` with https check, `state`), with `metadata` cut to a few KiB and never displayed as links.** A guess at size. Recommended by the authors, founder may overrule |
| D9 | Environment | Column; project | **Project.** As the foundation states; each environment gets its own secret and URL. Recommended by the authors, founder may overrule |
| D10 | Rate tier | 60 per IP; per project; batch | **Add batch delivery of up to a set number of services in one signed request,** and keep an address limit. Recommended by the authors, founder may overrule |
| D11 | Plan limits | Gate on paid; free | **Free for all,** per foundation's short version, point 7. Recommended by the authors, founder may overrule |

## 8. Founder decisions needed

See the specification. The persona reviews moved the customer conversations from `phase-2-customers-2026-11.md:327-328`
from "not blocking" to the one founder decision: whether they come before Step 1b. Settled on 2026-11-02 by the foundation's approved item 8: one round,
before Step 1b.

## 9. Not established

- Rows, disk and worker memory of the version rollup: unmeasured; the gate in D1.
- Real GitHub `deployment_status` payload size and exact fields.
- Whether a browser key can yield rollup-counted spans.
- How many customers set `service.version`, and what they put in it. If few do, B and method 4 help less, and setup
  documentation matters more.
- Whether customers want any of this on a screen rather than in the finding. No interview data.
- Prices marked "not read", and all competitor facts beyond the pages named.
- Which SigNoz screens show its markers, and which edition holds them. Which Coroot edition holds deployment tracking. Plan
  T6.1 settles both by installing them.
- A Datadog `PATCH` route marking a change failure, named in the marketing review. Neither DORA setup page read here
  describes one, so it is not stated in the table.

## Sources

**Competitors**

- [Datadog deployment tracking](https://docs.datadoghq.com/tracing/services/deployment_tracking/), [faulty deployment detection](https://docs.datadoghq.com/watchdog/faulty_deployment_detection/), [price list](https://www.datadoghq.com/pricing/list/), [DORA setup](https://docs.datadoghq.com/dora_metrics/setup/), [trace metrics](https://docs.datadoghq.com/tracing/metrics/metrics_namespace/)
- [Sentry releases](https://docs.sentry.io/product/releases/), [Go release configuration](https://docs.sentry.io/platforms/go/configuration/releases/), [associate commits](https://docs.sentry.io/product/releases/associate-commits/), [create a deploy](https://docs.sentry.io/api/releases/create-a-deploy/), [pricing](https://sentry.io/pricing/)
- [New Relic change tracking](https://docs.newrelic.com/docs/change-tracking/change-tracking-introduction/), [GraphQL](https://docs.newrelic.com/docs/change-tracking/change-tracking-graphql), [CLI](https://docs.newrelic.com/docs/change-tracking/change-tracking-cli)
- [Honeycomb markers API](https://docs.honeycomb.io/api/markers), [pricing](https://www.honeycomb.io/pricing)
- [Grafana annotations API](https://grafana.com/docs/grafana/latest/developers/http_api/annotations/), [Application Observability service overview](https://grafana.com/docs/grafana-cloud/monitor-applications/application-observability/manual/service/), [resource attributes](https://grafana.com/docs/grafana-cloud/observe-and-act/monitor-applications/application-observability/setup/resource-attributes/)
- [ClickStack release markers](https://clickhouse.com/docs/clickstack/features/dashboards/release-markers.md)
- [Coroot deployment tracking](https://docs.coroot.com/inspections/deployment-tracking/), [README](https://raw.githubusercontent.com/coroot/coroot/main/README.md), [editions](https://coroot.com/editions)
- [SigNoz Node.js guide](https://signoz.io/docs/instrumentation/javascript/opentelemetry-nodejs/)
- [Observe deployment tracking](https://docs.observeinc.com/en/latest/content/apm/deployment-tracking.html)
- [Dynatrace release monitoring](https://docs.dynatrace.com/docs/deliver/release-monitoring/release-monitoring-latest), [version detection](https://docs.dynatrace.com/docs/deliver/release-monitoring/version-detection-strategies-latest)

**Senders**

- [GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads), [deployment statuses](https://docs.github.com/en/rest/deployments/statuses)
- [GitLab webhook events](https://docs.gitlab.com/user/project/integrations/webhook_events/), [GitLab webhooks](https://docs.gitlab.com/user/project/integrations/webhooks/)
- [OpenTelemetry CI/CD resource conventions](https://opentelemetry.io/docs/specs/semconv/resource/cicd/), marked in development; `vcs.ref.head.revision` is the commit attribute. No standard deployment event was confirmed.

**Method**

- [Spinnaker: how canary judgment works](https://spinnaker.io/docs/guides/user/canary/judge)
- [Argo Rollouts with Kayenta](https://argo-rollouts.readthedocs.io/en/latest/analysis/kayenta/)
- [Google SRE Workbook: canarying releases](https://sre.google/workbook/canarying-releases/)
- [Canary Analysis Service, CACM](https://cacm.acm.org/practice/canary-analysis-service) (search extract only; full text refused)
- [DORA metrics guide](https://dora.dev/guides/dora-metrics/)

**Repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [`feature-catalog.md`](../../product/feature-catalog.md), [`phase-2-code-map-2026-11.md`](../../research/phase-2-code-map-2026-11.md), [`phase-2-competitors-2026-11.md`](../../research/phase-2-competitors-2026-11.md), [`phase-2-customers-2026-11.md`](../../research/phase-2-customers-2026-11.md), [`integrations-deploy-events/spec.md`](../integrations-deploy-events/spec.md)

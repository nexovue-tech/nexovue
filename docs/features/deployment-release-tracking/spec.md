# Feature: Deployment and release tracking (F28)

## Status

**Approved** by the founder, 2026-11-02. Written and revised the same day after eight persona reviews (see [`status.md`](status.md), *Reviews*).
No code is written from it yet. Stage 2 of
[`phase-2-plan.md`](../../product/phase-2-plan.md). Input: [`research.md`](research.md). Next: [`design.md`](design.md),
[`plan.md`](plan.md), [`status.md`](status.md).

**How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present. Nothing
was run. Line numbers are for that working tree and will drift. Every limit marked *starting guess* is a guess until
tested. No customer was interviewed; [`phase-2-customers-2026-11.md`](../../research/phase-2-customers-2026-11.md)
grades its evidence as vendor guides and practitioner writing.

**Where this differed from the foundation, and now agrees.** [`phase-2-foundation.md`](../../product/phase-2-foundation.md),
section 9, first said the comparison is "by time around a deploy", because rollups hold no version. That described today's
code, not a real limit, and nexovue is pre-deployment. This specification compares by version first and keeps time as
a labelled fallback (D1). The foundation's section 9 was changed to match on 2026-11-02 (its F28 row now reads "by version
first", `phase-2-foundation.md:547`), so D1 is no longer a difference, and it was a technical choice, not a founder one. It also renames the foundation's `/releases/{id}/impact` and `deploy_impact` (D44), is the first feature after F6 to extend
the existing scheduler Postgres tenancy test (D48), and applies F3's three bases, so a seen version is never counted as measured (D54). The foundation's other F28 promises hold: `DeployHistory` in
`nexovue_platform`, a windowed read, a `coverage` value, `deploy_events.commit_sha`, no AI and no connection.

## Words used here

- **Deploy**: a statement sent to us that a version reached a service. One row of `deploy_events`.
- **Release**: one version of one service, whether we learned of it from a deploy, from telemetry, or from both.
- **Release period**: one unbroken stretch in which a release was the version in service. A version that comes back has
  a second period. History is never rewritten.
- **Reported**: told to us by a signed request from the customer's CI (the system that builds and ships their code).
- **Seen**: counted by us in the customer's server traffic, through the `service.version` label, if they send it.
- **Basis**: how we know of a release or marker: reported, seen, or both. It reads "Reported", "Seen in traffic" or
  "Reported, seen in traffic". It is not the sender's free-text `source` field, which we never show as fact.
- **Rollup**: a table of per-minute totals, written before our sampling, so its counts are exact for what reaches us.
- **Sampling**: keeping only some traces to save storage. Here: all errors, all slow traces, one healthy trace in ten.
- **Rolling deploy**: replacing instances a few at a time, so old and new versions serve traffic together for a while.
- **p95**: the time within which the fastest 95% of requests finish. It shows what the slow end feels like. We estimate
  it from latency buckets about 19% wide, so it is never exact.
- **Warm-up**: the first minutes of a new instance, when caches and connections are cold and requests run slower.
- **Failure share**: the share of requests that failed.
- **Coverage**: what we are able to know about a service's changes (see *States*).
- **Release marker**: the dashed vertical line drawn on a chart at a deploy or a release period's start.

## Problem

A person looking at a bad chart asks one question first: "did something ship just before this?" Nexovue has good
plumbing for that question and almost no product around it.

- **A customer cannot set it up.** The signed webhook works, but the console has no screen for its secret. The search
  of `apps/web/src` finds only the project-deletion summary (`features/projects/ProjectsScreen.tsx:178`). The setup
  documents never say how to send a deploy; `setup/api.md:74` lists only `GET /deploys`. The only working example is
  our own demo script (`tools/otel-demo/doors.py:218-262`).
- **The markers say nothing.** `drawDeploys` draws a line and never reads the version (`TrendChart.tsx:251-272`), though
  `deploy-markers.ts:55` computes it. A line says that something shipped, not what. On Overview it does not even say
  which service.
- **Markers disappear silently.** The console asks for the newest 50 deploys of the whole project and then filters
  (`overview/api.ts:219-230`, `servicedetail/api.ts:281-292`, `deploys/schemas.py:17`). A busy project sees no markers
  last week, and this looks like "no deploys". `select` throws away `truncated`.
- **The screen where it matters most has no markers.** The finding page draws none (`FindingScreen.tsx:535-544`).
- **"Is my release fine?" has no answer.** There is no release list and no comparison. Our setup document promises one:
  `service.version` "lets nexovue separate 'the service is slow' from 'the release we shipped at 14:00 is slow'"
  (`setup/platforms/kubernetes.md:83-84`). Nothing delivers it, because the worker reads the version on every span
  (`traceassembly/flatten.go:143,195`) and then leaves it out of what the exact counts see
  (`traceassembly/stages.go:39-59`). That same page, and the EC2 one, teach a fixed `service.version=1.4.0`
  (`kubernetes.md:65`, `aws-ec2-and-bare-metal.md:49`), which would make one release forever.
- **"No deploy" and "we are never told" look the same.** Both show the `UNKNOWN` mark in the errors list
  (`ErrorsScreen.tsx:364`). The customer research names this a correctness rule
  (`phase-2-customers-2026-11.md:165-167`), and F3 cannot honestly explain a finding without it.
- **The door is weaker than it looks.** Secrets are stored readable (`models.py:837-850`). A captured signed body can
  be sent again with a new id and is stored as a new deploy, because the id sits outside what is signed
  (`deploys/service.py:99-107`; expected from reading, not reproduced). Any time is accepted (`schemas.py:42`). A
  misspelled service name is stored and matches nothing, and nobody is told (`deploy-markers.ts:52`).

## Goal

A customer gets deploys into nexovue with a copied CI step (*target: five minutes, unmeasured until Step 2*), or passes
a build id into `service.version` once per service and needs no deploy webhook. From then on:

- every chart with a time window shows which version of which service changed, in words;
- a finding, and the notice that opens it, say what shipped before it, and say plainly when we do not know;
- a release page compares old against new, and says how it compared them and what that cannot show;
- nothing anywhere says a release caused anything.

Comparisons start from the next rollout. On day one a customer sees versions, and has nothing yet to compare them with.

## Positioning

For internal use until tested in customer conversations and re-checked (plan Step 6). Foundation rule: say what Nexovue
does, never what others lack, and do not name rivals in public.

- **Table stakes, not reasons to buy:** comparing versions, releases with no webhook, and pre-sampling counts. Sentry,
  SigNoz, Observe and Coroot create or mark releases from telemetry or the orchestrator, and Datadog documents its trace
  metrics as computed on all traffic ([`research.md`](research.md), section 3).
- **The candidate story:** on a free, self-hosted, any-platform product, a finding states what shipped before it and how
  we know. "Reported" and "seen" are kept apart, and disagreement between them is shown. Every comparison names its
  method. An absence of information is stated rather than left blank.
- **Not yet proven wanted.** No buyer has said any of this matters to them.

## Target users

| Person | What they do here | Surfaces |
|---|---|---|
| **On-call engineer at 3 a.m.** | Reads "v2.1 of checkout, 6 minutes before this" on the page or notice, then opens the comparison | Alert notice, finding page, charts, release page |
| **Developer who just shipped** | Asks "is my release fine?", on screen or from their pipeline | Release list, release page, read API, optional summary notice |
| **New developer on the customer's team** | Follows a copy-and-paste step for their CI and checks it worked | Project settings, setup documents |
| **Admin** | Mints and withdraws secrets, sees when one was last used, removes forged records | Project settings |
| **Buyer: founder or CTO of a small team** | Wants value without wiring up more things | Release list once `service.version` carries a build id |
| **F3, F20, F22, F5** (other features) | Read deploy facts with ids and a coverage value | `nexovue_platform`, API routes |

**Not served.**

- Release managers wanting approval flows, or teams wanting nexovue to stop a rollout. Nexovue observes.
- Managers wanting team performance scores. DORA itself warns against making its metrics targets or comparing teams.
- Mobile teams wanting crash-free adoption. Sessions are not our model here.
- Teams whose only code is a browser app. Their deploys appear and their errors group by version, but the comparison
  of requests needs server traffic (FR27).
- **Teams with little or no tracing,** such as those on Prometheus metrics and Loki logs. We count requests from SERVER
  and CONSUMER spans only (`derive/counters.go:59-62`). Their deploys get markers, and they get no comparison. Background
  jobs and cron with neither span kind are the same. The page says so (`no_request_traffic`).

## Scope

1. Console setup for the deploy secret, CI recipes that cannot harm a deploy, a check that sends nothing to storage, and
   "last received".
2. A signed deploy envelope with named fields and states, batch delivery, time limits and sealed secrets.
3. A new exact rollup by version, written before sampling.
4. A stored `releases` record with its `release_periods`, combining reported and seen.
5. A release list and a release page, with a comparison that names its method.
6. Readable markers from a real time window on every chart that has a window today, and on the finding page.
7. Coverage, stated, on findings, errors and releases.
8. Links both ways with F6's findings, always worded as order and never as cause, plus one line of fact in the finding's
   opening notice.
9. Errors by version: new, gone, in both, with a plain caution.
10. Facts for F3 and F20 through one shared reader, with no AI code.
11. An optional release summary notice, and a stored reading shown on the list.
12. Corrected setup pages for `service.version`, and a claims check before anything is announced.

## Out of scope

| Left out | Why |
|---|---|
| **The words "caused by", or a verdict of "bad release"** | On-call engineers follow a confident wrong answer. The existing rule is right (`errorgroups/schemas.py:15-26`) |
| **A second detector on deploys, or a new finding kind** | F6 owns judging. Two opinions on one service is the double record F6 D1 refuses. The catalog's "regression surfaced as a finding" is **only partly met**: only where F6 opens a finding (D3) |
| **Suspect commits by file blame** | Needs per-commit file lists and a repository connection, and the foundation says F28 uses none. We store `commit_sha`; F22 and F5 can build on it. **We expect to lose this comparison to Sentry, and we should not hide that** |
| **Commit links built by us on the release page** | They need F22's `service_repositories`, which comes after F28. Recipes fill `url` with the commit page, so a click-through exists on day one under "Link from your CI" (D4) |
| **Native GitHub or GitLab webhook delivery** | The payload names no service, one webhook sees every environment, and GitLab needs a second verifier. A GitHub Actions user is served by a recipe from day one (D5) |
| **Kubernetes watcher** | A new component and cluster permission, and the seen-in-telemetry path covers what it would add |
| **Comparison per endpoint** | `endpoint_stats_1m` holds no version, and adding it multiplies a table's rows by a dimension only this reads. Datadog has this; we will lag it, and the page says what this hides (D18) |
| **Comparing any two chosen versions** | Only the automatic previous version in version 1. Datadog offers any two (D43) |
| **Automatic detection of a faulty release** | That is a detector (D3). A pipeline may read the comparison and decide for itself (FR14). Datadog and Coroot do this; we lag them |
| **A person's name on a release** | Stores personal data for a convenience the commit link already gives (D42) |
| **Blocking or rolling back deploys** | Nexovue observes. A customer's own pipeline can read `GET /releases/{id}/comparison` and decide (FR14) |
| **DORA metrics, crash-free adoption, feature-flag events** | Research, section 5. `deploy_events` must not become a general change log in version 1 |
| **AI of any kind** | F28 calls no model. F3 words what F28 states |

## User requirements

- **UR1.** An admin can get a deploy secret in the console, copy a working CI step, and see that it worked, without
  reading API documentation.
- **UR2.** A customer whose `service.version` carries a build id, and who sends no deploys, still sees releases.
- **UR3.** Anyone reading a chart can see which version of which service changed at a marker, by hovering, by touch, or
  by keyboard or screen reader.
- **UR4.** Someone looking at last week sees last week's markers, even if a version has since come back.
- **UR5.** An on-call engineer on a finding can see what shipped before it, how long before, and how we know, on this
  service and elsewhere in the project.
- **UR6.** An on-call engineer is told plainly when we receive no deploy information for a service, and how old our last
  report is.
- **UR7.** A developer can open their release and see old against new on requests, failures and p95, with a plain
  statement of how the numbers were compared, both during the rollout and since it finished.
- **UR8.** A developer is told "too little traffic to compare" rather than shown a percentage from five requests.
- **UR9.** A developer can see which errors appear in this version and not the previous one, and the reverse.
- **UR10.** Someone can see a failed or rolled-back deploy as such, and a rollback as a version coming back, compared
  against the version it replaced.
- **UR11.** A monorepo can report 100 services from one CI step.
- **UR12.** An admin can rotate a secret with no gap, withdraw a leaked one, and remove what it wrote.
- **UR13.** A person can tell what CI told us from what we measured, and when a report arrived versus what time it
  claims.
- **UR14.** A team can use this without a deploy pipeline going red because of nexovue.

## Business rules

- **BR1. Order, never cause.** Every screen, API description and document states order. No text may say or imply that
  a release caused anything. Numbers may say "failure share rose from 0.2% to 3%".
- **BR2. Measured beats told.** Where we have both, a period's start is when we counted it, not when a sender says. A
  webhook never moves a start that was measured.
- **BR3. Reported and seen stay distinct.** Every release shows its basis. "Seen" is never called a deploy.
- **BR4. A real absence and an unknown never look alike** (`docs/design/principles.md`, rule 2). "No deploy" is only
  ever said when coverage lets us say it, and at the time being asked about.
- **BR5. Every comparison names its method.** A number never appears without the method that produced it.
- **BR6. Rate and latency are never read from sampled spans.** Sampling is skewed on purpose
  (`sample/policy.go:39-46`), and an earlier design measured wrong by about an order of magnitude
  (`current-system-reference.md:85`). Stored spans may answer only "which errors, in which version" and "which
  environment labels appear".
- **BR7. A browser key cannot make a release, or a release's errors.** Telemetry through the browser door (`DOOR_RUM`)
  never produces a seen version, and never lists as "new here" on a server release.
- **BR8. Tenancy.** A release, deploy and comparison belong to one project of one organization. Another tenant's row,
  another project's row for a project-scoped token, and a deleted project's row all answer as if they do not exist.
- **BR9. No feature is gated by plan; how far back numbers go follows plan retention,** per the foundation's section 10.
  Limits otherwise exist only to protect the system, and are stated as such. Current seed values
  (`0005_plans_table.py:33-38`, and no later migration changes them), to be re-read at build:

  | Plan | Comparison numbers | Errors by version | Release record |
  |---|---|---|---|
  | Free | 30 days | 7 days | 30 days after last activity |
  | Pro | 35 days | 30 days | 395 days |
  | Scale, Enterprise | 35 days | 90 days | 395 days |

  *Why 35 days on a 395-day plan:* the per-minute version counts are kept for the same period as detection's
  `service_stats_1m` (`000035:50`), since a release comparison is about recent rollouts. The screen states the project's
  actual limit. A self-hosted install has no plan allowance (`deployment.py`, per the foundation), and its values are
  read the same way.
- **BR10. Sender text is data.** `source`, `metadata`, `url`, `version` and `service_name` are shown as plain text. Only
  a checked `url` becomes a link, labelled as coming from the customer's CI.
- **BR11. A project is an environment** (`models.py`, `Project`). Each environment has its own secret and URL. There is
  no environment column. Nothing stops a team sending two environments to one project, so we warn when we can see it
  (FR45), and a laptop with no environment label stays invisible to that check.
- **BR12. F6 stays unchanged in meaning.** A release never opens, closes, mutes or moves a finding or its normal, and
  never delays or suppresses a notification. The only effect on any alert is one added line of fact (FR44).
- **BR13. Nexovue must not be able to break a customer's deploy.** Every recipe we publish exits 0 and warns.

## States

### A deploy's state, as told by the sender

| State | Meaning | Marker on a chart |
|---|---|---|
| `started` | A rollout began | Marker, no word. A later `finished` draws its own |
| `finished` | The default, and the only value a plain sender needs | Marker, no word |
| `failed` | The sender says it did not complete | Marker with the word "failed" |
| `rolled_back` | The sender says it was undone | Marker with the word "rolled back" |

### A release's activity, worked out from the record

| Activity | Means | How it reads |
|---|---|---|
| `not_seen` | Reported, no traffic counted | Under 30 minutes (*starting guess*): "Waiting for traffic". After: "Reported, never seen in traffic. The rollout may have failed, or the service name or version text may differ" |
| `serving` | Counted in the last 10 minutes (*starting guess*) | "Serving" |
| `replaced` | Its latest period has ended | "Replaced by v2.2" |
| `idle` | Not counted lately, and nothing else was either | "No traffic since 14:30" |
| *flag:* `already_running` | Its first period is the first version we ever counted for that service, with no report before it | "Running when we started counting". No marker, and no "started" sentence |

`failed` and `rolled_back` show beside the activity. CI may say failed while traffic flows, and both are facts. When a
service keeps showing reported-never-seen versions beside seen-never-reported ones, the list replaces the `not_seen`
words: "Your CI and your traffic name versions differently: CI sent X, traffic sends Y" (FR46).

### A service's coverage

Coverage is computed **as of a moment**: now, or a finding's start. "Reported" means any report within the release
record's retention, and "counted" any non-empty version within the same period. There is no silent 30-day flip: the
last report's date always shows with it.

| Value | True when | Words |
|---|---|---|
| `deploys_and_versions` | Reported and versions counted | "Deploys reported by your CI, versions seen in traffic. Last report: 12 days ago" |
| `deploys_only` | Reported, no version in traffic | "Deploys reported by your CI. Last report: 12 days ago. No version in traffic, so we cannot split old from new" |
| `versions_only` | Versions counted, none reported | "Versions seen in traffic. Your CI reports no deploys, so same-version redeploys and config changes do not appear" |
| `none` | Neither | "We receive no deploy information for this service" |

If the first report or version is later than the moment asked about: "We first heard about deploys on this service on
{date}, after this started."

### A comparison's method

A page may show **two blocks**: *during the rollout* and *since the rollout finished* (D34).

| Method | When | Fair because | Words |
|---|---|---|---|
| `side_by_side` | Old and new both served during the same minutes, above the failure floor | Same moments. The two versions may not receive the same mix of requests | "Side by side: both versions over the same 42 minutes. Traffic may be routed differently to each, and a new version starts cold" |
| `version_before_after` | One version replaced the other, or the rollout has finished | Exact per version, but different times | "Before and after, by version. Different times of day can move these numbers" |
| `time_before_after` | A deploy was reported and every row in the window has no version | Weakest: every version in those minutes is mixed | "Before and after, by time. No version in your traffic, so a rolling deploy mixes old and new here" |
| `none` | See FR21's reasons | | Says which reason |

## Functional requirements

### Getting deploys in

- **FR1.** Project settings must have a *Deploys* section, shown to those holding `MANAGE_CREDENTIALS` and hidden from
  others (`ProjectSettings.tsx:55-71` convention), holding: the URL, secret list, mint and withdraw, numbered rotation
  steps, CI recipes, when a deploy or check was last received, the last signed refusal, and each service's current
  version with when we first saw it.
- **FR2.** A new secret must be shown once, at creation. Afterward only its ending, creation time and last use are
  shown. A person who lost one mints another (D7). Leaving while a just-minted secret is on screen must warn.
- **FR3.** Secrets must be sealed at rest, and included in key rotation.
- **FR4.** A project may hold at most 5 live secrets (*starting guess*). Each request opens all of them, before any
  signature has proved anything.
- **FR5.** The webhook must keep today's order: bound the body, verify the signature over raw bytes, and only then
  parse and write. All signature refusals read alike.
- **FR6.** The signed body must be `{"deploys": [ ... ]}` with 1 to 100 items (*starting guess*), and every item must
  carry `delivery_id`, `service_name`, `version` and `deployed_at`, and may carry `state`, `commit_sha`, `url`,
  `source` and `metadata`. There is one shape, for one service or a hundred. The `X-GitHub-Delivery` header is no longer
  read. A body may be at most 512 KiB (*a guess*), and any batch whose fields are within FR9's limits in plain ASCII
  must fit. The refusal must say "at most 512 KiB; send fewer items".
- **FR7.** A repeated `delivery_id` for the project must answer with the stored deploy and change nothing, including in
  a race, per item. A repeat must not touch `releases`, and must not count against the hourly limit. The same id twice
  in one body must be refused with 422. A batch mixing new and repeated items stores the new ones and answers each.
- **FR8.** `deployed_at` must be refused when more than 5 minutes ahead of our clock or more than 7 days behind it
  (*starting guesses*). This also ends any replay of an old capture, should its row ever have been deleted. It means past
  deploys cannot be imported beyond 7 days, and the setup page states this.
- **FR9.** `commit_sha` must be 40 or 64 hexadecimal characters, stored in lower case. `url` must be `https`, carry no
  username, password, query or fragment, and be at most 2,048 characters. `metadata` must be flat string keys and string
  values, at most 1 KiB (*a guess*) when encoded, and the default credential rules must mask it before storing.
  `delivery_id` must be 1 to 200 characters. `version` and `service_name` must pass one shared name check, refusing C0
  and C1 control characters, bidirectional overrides, Unicode tag characters, and the reserved value `__other__`. NUL
  characters must be refused, not left to cause a server error.
- **FR10.** One bad item must refuse the whole request, naming the item's position and field. Nothing is stored. A
  signature-proven refusal must be recorded (time, position, field, which secret) and shown in the Deploys section,
  because the sender proved the secret (D22).
- **FR11.** A body with `"dry_run": true` must be checked in every way, store no deploy or release, record when it was
  received, and answer "valid, not stored". It must not count against the hourly limit. This is how a customer tests CI
  (D21).
- **FR12.** The answer must say, per item, `release_id` (absent on a dry run), whether the service is known to this
  project (from `service_last_seen`), `version_seen`, and the service's most recent seen versions, so a misspelling
  shows up in CI's own output.
- **FR13.** After a valid signature, a project is limited to 1,000 newly stored deploys an hour (*starting guess*),
  answered with 429 and `Retry-After`. A batch that would cross the limit is refused whole, and uses none of the allowance. The limit is counted only
  after the signature holds, and only for new items, so neither an outsider nor a replay of a captured body can use up a
  victim's allowance. If Postgres cannot count, the request is refused. The per-address limit of 60 a minute stays
  (`ratelimit/policy.py:127`).
- **FR14.** Setup documents must gain a page with recipes for GitHub Actions, GitLab CI and plain `curl`. Every recipe
  must:
  - never fail the customer's deploy: bounded time, bounded retries, a visible warning, exit 0 (BR13);
  - read the secret from the CI's secret store through an environment variable, and never pass it as a process argument;
  - build `delivery_id` from the service and the attempt number, so it is stable across a step's retries and new on a
    re-run (GitHub's `run_id` alone would treat a re-run as a repeat);
  - set the reported `version` and `service.version` from one variable, and say why;
  - fill `url` with the commit page from CI's own variables, and send no actor or CI context fields.

  The page must also give per-platform ways to pass a build id into `service.version` (Kubernetes with Helm or Kustomize,
  Docker build argument, systemd), say plainly that this is one change per service, never `latest`; state one project and
  one key per environment, with the reason; say what must be reachable from CI and the routes for a private install (a
  self-hosted runner, or telemetry only); warn against posting CI context objects into `metadata`, which is stored and
  shown; state that counts assume no sampling before sending; and give a post-deploy recipe that reads
  `GET /releases/{id}/comparison` so a customer's own pipeline can decide.
- **FR41.** The Kubernetes and EC2 pages must stop teaching a fixed literal version. They are the only setup pages that
  mention `service.version` today, so the language pages and onboarding must start showing it. All must use a build
  variable such as an image tag or commit, and the EC2 example must explain its environment label (BR11).

### Knowing about versions

- **FR15.** The worker must keep exact per-minute totals by service and version, before sampling, from the same inbound
  spans and with the same 64 latency buckets as `service_stats_1m`.
- **FR16.** For one service and minute, the rows of the new rollup must sum to that minute's `service_stats_1m` row.
  Spans with no version count under an empty version. Browser-door SERVER and CONSUMER spans must count in neither table
  once the F6 change below lands. Until then they count under the empty version, so the sum holds at both times (D55).
- **FR17.** At most 20 versions per service **per drain** are written (*starting guess*), by most requests, and the rest
  fold into one row named `__other__`, so totals stay right. A minute written in several drains can therefore hold more
  than 20 version rows. As spans arrive, 64 distinct versions per partition, service and minute (*starting guess*) and
  512 per tenant, partition and minute (*starting guess*) are kept first come, first served, and later ones count under
  `__other__`. A burst of junk names early in a minute can therefore absorb a real version's count. A version longer
  than 200 characters, or failing the shared name check, is folded too. Whenever `__other__` holds requests in compared
  minutes, the comparison must say "Versions were combined" and mark itself inexact.
- **FR18.** A stored release must be created when a deploy is accepted or a version is first counted, unique per
  project, service and version, and must keep its first-seen time even after the rollup rows age out. Rows that Postgres
  cannot store (an over-long or NUL service name) must be skipped and counted per row, and never stop the sync.
- **FR19.** A release must keep one `release_periods` row per period in service, and past periods must never change
  except to gain an end. A version that returns gets a new period, and the page must say it came back. A period ends when
  another version holds over 90% of the service's versioned requests for 10 minutes in a row, each with traffic, or when
  this version has had no requests for 60 minutes while another has had at least 20, so a quiet service's swap still ends
  a period. A version with an ended period returns when it holds at least 20% of them, and at least 5 requests, over a
  10-minute stretch, so one stray request is not a return. Its new period starts at that stretch's first minute with
  traffic (*all starting guesses*). A service sending no version in traffic gets a new period when a
  later `deployed_at` is reported for a version another version has replaced. That is how a rollback appears with or
  without CI (D15).
- **FR42.** A period's start must follow one rule. It is the first counted minute once that is known. Before any traffic
  it is the reported time. A webhook must never change a period whose start was counted, whatever time it claims.
- **FR43.** The first version ever counted for a service with no earlier report must be flagged `already_running`. It
  must draw no marker and read "Running when we started counting", and must not be named as "the release before this". It
  may still serve as a later release's previous version.

### Comparing

- **FR20.** The comparison must read a chosen period, and choose its method by the ordered list in the design, reading
  only settled minutes (`detection_sweep.settled_minute`). The previous version is the other non-empty, non-`__other__`
  version with the most requests in the hour before this period started; failing that, the version of the period before
  this one on the service. After a rollback, that is the version it replaced.
- **FR21.** With no method available, the answer must give one reason: `no_previous_version`, `too_little_traffic`,
  `too_recent`, `numbers_expired`, `no_version_no_deploy`, `new_version_not_seen` (this service sends other versions, and
  never this one), or `no_request_traffic` (this service has no server spans to count).
- **FR22.** The window must default to one hour from the period's start, and offer 1, 6 and 24 hours (*starting guess*).
  Opened from a finding, it must instead pick the smallest offered window that reaches the finding's start and say so:
  "Compared over 6 hours, to include the finding that started at 14:02." If none does, it must say "This comparison ends
  before that finding started, so it says nothing about it."
- **FR23.** Each side must have at least 200 requests to read p95, and 20 to read failure share (*starting guesses*,
  the same values as F6's floors at `scheduler/detection/method.py:50,56`). The failure floor selects the method; p95
  falls back per cell. Below that, the cell says "too little traffic", and the request count still shows.
- **FR24.** Each number must show its counts ("6 of 600"), and one of four readings. `higher` and `lower` on failure
  share need a two-proportion test reaching z = 3. `about_same` on failure share is allowed only when there is enough
  traffic to rule out a change of 1 percentage point; otherwise it reads `not_clearly_different`. p95 reads `higher` or
  `lower` when the ratio is above 1.2, because one latency bucket is about 19% wide (`derive/services.go:16-18`), and
  `about_same` within it when both sides reach the floor. p95 values always appear in the sentence. All are *starting
  guesses*. `higher` on failure share or p95 uses critical text with the word. `lower`, `about_same` and
  `not_clearly_different` use dim ink. Traffic share has no colour, and green is never used. These words open nothing.
- **FR25.** `version_before_after` and `time_before_after` must also show the same window one week earlier, while those
  numbers exist. Requests per minute show in these two methods only; in `side_by_side`, share of traffic shows instead,
  since share is what a rollout changes.
- **FR26.** The page must show each version's share of the service's traffic over time, top four versions plus "Other
  versions", with a table of the same shares for touch and screen readers. The empty version reads "No version", and
  `__other__` "Combined versions".
- **FR27.** A reported release with no server traffic reads `no_request_traffic`, with a sentence saying this is expected
  when a service only runs in the browser or sends no traces. Its errors by version still show, labelled as coming from
  the browser where they do.
- **FR28.** The comparison must compare per minute up to the settled minute. If any compared minute's request sum differs
  from `service_stats_1m`, it must say "incomplete: some counts were not stored for N minutes". A dropped write must never
  look like healthy numbers.
- **FR47.** Where a rollout overlap exists, the page must show two blocks: "During the rollout" (side by side) and "Since
  the rollout finished" (the new version against the previous version's equal stretch before the rollout, plus last
  week). The opening sentence must use the most recent settled block, and the window chooser must apply to the second. A
  rollout has finished when the previous version holds under 1% of requests for 5 minutes in a row (*starting guess*).
- **FR48.** Each version's first 5 minutes (*starting guess*) must be left out of p95 by default for warm-up, and the page
  must say so. Failure share keeps them.
- **FR49.** Beneath any `about_same` the page must say "This covers the whole service. A problem on one endpoint can be
  hidden here", and link to errors by version and the service's endpoint view for the same window.
- **FR50.** The comparison must say "Counts assume you do not sample before sending."

### Errors by version

- **FR29.** The release page must list error groups as *new here*, *gone here*, or *in both*, comparing this version
  with the previous over the same durations, capped at 24 hours and 100 groups. It must state: "Not seen in the previous
  version during this window. That is not proof this version introduced it. A rare error can look gone only because it
  has not recurred yet." A release with server traffic reads only non-browser-door rows.
- **FR30.** Where raw data has aged out, the section must say so with the date, and show no blanks or zeros.
- **FR31.** If the project's sampling keeps errors off (`sample/policy.go:71`), the section must say the list may be
  incomplete.
- **FR51.** If the read times out, the section must say so in words, never as an empty list.

### Findings

- **FR32.** The finding page must show, for a service finding, the release period before `started_at`, its basis,
  version and gap, by the wording table in the design. Under 24 hours it is stated plainly; older, it is dimmer with no
  comparison prompt. A project finding must list the latest release per affected service within 24 hours, newest first, up
  to 5, then "and 3 more". Every row that names a release that came before, including that list, must carry beside it: "It
  came first. That is not evidence that it caused this." A row naming no earlier release must not: nothing before, already
  running, coverage `none`, first report after. The markers go on each chart with their text list open.
- **FR33.** With coverage `none`, the finding page must state "We receive no deploy information for this service" and
  link to setup for those who can act on it. Others read "An owner or admin can set this up in project settings."
- **FR34.** A release page must list findings on that service which started during the chosen period, worded "Started
  after this release".
- **FR52.** The finding page must also list every release in the project in the 2 hours before the finding (*starting
  guess*), the same service first, and mark those this service calls, from `service_edges_1m`, all worded as order. With
  nothing on this service: "No release on this service. 3 elsewhere in this project in the last 2 hours."
- **FR44.** A finding's opening notice must carry one template line from stored facts, on every channel including
  PagerDuty: "Most recent release on this service: v2.1, 6 minutes before this. Order only, not cause", or coverage
  `none`'s words. It must never delay the alert beyond one bounded read per message, must run outside the claim's transaction so its
  failure cannot fail the claim, and must be left out if the read fails. Names must pass `one_line` and the channel's
  escaping (D51).

### Markers on charts

- **FR35.** Markers must be read for the window on screen, from a route that takes `from` and `to`, and F20's dashboards
  must call the function behind that route (D30). If more exist than the cap of 500 (*starting guess*), the screen must say "showing the newest
  500 of this window". If the read fails, the chart must say "Releases could not be loaded for this chart" with a retry,
  never an unmarked chart.
- **FR36.** Overview's markers must name the service. Markers close together must collapse into one marked "3 releases".
  Hover shows facts, and a click pins them with a link and a close control. Every chart group must have one text list for
  touch and screen readers, and one always-visible line saying what the marks mean and how many.
- **FR37.** Markers must distinguish `failed`, `rolled_back` and seen-only releases by a word, without relying on colour
  or dash alone.

### Coverage and other features

- **FR38.** A coverage value must be available per service as of a moment, from the API and to the scheduler, and must need
  Postgres only. The list of
  services with coverage `none` comes from `service_last_seen`. The errors list must show short words in the cell, "none
  before" or "not told", with the `UNKNOWN` mark only for an unread value. The error's detail panel must state the deploy
  line for every coverage value (D23).
- **FR39.** Each deploy fact given to F3 or F20 must carry a stable id, `basis` (`reported` or `seen`) and arrival time. It
  must leave out the sender's free-text `source`, `metadata`, `url` and `delivery_id`, which are free text under the
  foundation's section 3.
- **FR53.** `deploy_events` must record which secret signed each row. An admin must be able to delete one deploy, and to
  withdraw a secret and remove everything it sent since a chosen time. Removal must recalculate or delete the affected
  release and periods, and be audited.
- **FR54.** `metadata`, `url` and `source` must be served only from `GET /releases/{id}/sender-details`, to a signed-in
  person, with `metadata` only for members and above. Tokens and F4's ticket must be refused. `GET /deploys`,
  `GET /releases/{id}` and MCP must leave them out.
- **FR45.** The release page and Deploys section must warn "Two environments appear to share this project, so these
  comparisons mix them" when one service's stored spans in the compared window carry more than one
  `deployment.environment.name` value.
- **FR46.** A service with at least two reported-never-seen versions beside at least one seen-never-reported version in 24
  hours (*starting guess*) must read the "name versions differently" words.
- **FR55.** A job must store each period's latest reading 30 minutes after it starts (*starting guess*), read from
  `side_by_side` or `version_before_after`. The list must show a "failure share higher" or "p95 higher" flag with counts
  from it, and nothing for other readings. A project may opt in to a release summary, sent to a people-read channel and
  never paging, carrying the counts and plain words, with no verdict and no finding. The opt-in is off by default, names one
  channel, and only an owner or admin may change it (D53).

### Retention

- **FR40.** `deploy_events` and `releases` must be deleted past the project's own derived retention, after last activity,
  measured by arrival time and never the sender's claim. A deleted project's rows become unreadable at once and are
  removed once its deletion is older than the shortest plan's derived retention. Rollup rows age out, as `ProjectDeleted`
  states. Old releases must show that their numbers expired, with the project's actual limit, never blanks or zeros.
- **FR56.** A project over 50,000 releases (*starting guess*) must evict before refusing: first never-seen releases of
  unknown services, then the oldest inactive. A seen release must always be creatable, and a period in service is never
  evicted. The same cap applies to both doors. The Deploys section must say "Older releases are being removed early:
  this project has over 50,000".

## Non-functional requirements

- **Exactness.** Rollup counts must equal what `service_stats_1m` counts, on replay and rebalance too. This is exact for
  what reaches us; sampling before sending shortens counts by that factor, with nothing to show it.
- **Cost gate.** Rows, disk and worker memory must be measured before later steps proceed (*Plan*, Step 1a). The
  thresholds are in the design.
- **F6 protected.** A problem with the new rollup, transient or permanent, must not hold detection back or end the worker.
- **Alerts protected.** Release jobs must not delay alert evaluation.
- **Read speed.** The release page's first numbers should show within 2 seconds on the demo (*a target, measured in
  Step 4*).
- **No AI.** No F28 module may import an AI module, enforced by the same test as detection.
- **Accessibility.** Every marker is reachable without a mouse, and color is never the only signal. The opening sentence is
  announced when it changes, a section moving from skeleton to numbers is announced, charts carry a description naming the
  versions and their latest shares.
- **Phone width.** Every F28 screen must work at 375px, with the words and flags visible without sideways scrolling.
- **Times.** Every time uses the project's zone, named once per screen, as the finding page does.

## Error cases

| What happens | What the user sees | Recover? | Can we tell it from a normal empty? |
|---|---|---|---|
| Wrong or missing signature | CI gets 401 with the same words every time. The console says: "A wrong signature is not recorded here: your CI's output shows a 401" | Fix the secret | Yes. Not recorded for the customer, since an outsider could fill the record (D22) |
| Bad field in a signed body | CI gets 422 naming item and field. Deploys section shows the last refusal | Fix the step | Yes |
| Time out of bounds | 422 stating the bounds | Fix the CI clock | Yes |
| Hourly limit reached | 429 with `Retry-After` | Wait. Recipes retry, then warn | Yes |
| Service name matches nothing | Accepted, with `known: false`. Release later reads "Reported, never seen" | Fix the name | Yes |
| Version text differs between CI and traffic | Accepted, with `version_seen: false` and the versions we do see. List says they name versions differently | Use one variable | Yes |
| CI cannot reach the server | The recipe warns and exits 0. Setup page explains the two routes | Self-hosted runner, or telemetry | Partly: coverage shows the last report's age |
| CI stopped reporting | Coverage shows "Last report: 12 days ago", and the finding's sentence carries it | Fix CI | Yes |
| Stolen secret, false deploy | Marker shows basis and arrival time. Where the sender's time and arrival differ by over 15 minutes, both show (*starting guess*) | Withdraw the secret and remove what it sent | Partly. This is why measured beats told |
| No `service.version`, no deploys | Empty release list explaining two ways to start. Coverage `none` | Set it up | Yes |
| `latest` or a fixed value as a version | One release that never changes. Docs warn; repeat reports show as a count; the Deploys section lists each service's version and its age | Use a build id | Partly |
| Two environments in one project | Warning on the page and Deploys section | One project per environment | Partly: only where the label is sent |
| Sampling before sending | Page says counts assume none | Turn it off | No. Nothing in the data shows it |
| Service sends no server or consumer spans | `no_request_traffic` words. Markers still draw | Add tracing | Yes |
| Over 20 versions | "Versions were combined" on that comparison, marked inexact | Stop putting instance ids in the version | Yes |
| Over 50,000 releases | Stated in the Deploys section; oldest removed early | Fix the version text | Yes |
| Too little traffic | "Too little traffic to compare", with counts | Wait, or widen the window | Yes |
| Not enough traffic to rule out a change | "Not clearly different", with counts | Wait, or widen the window | Yes |
| Numbers expired | "Numbers for this release have expired (kept 30 days on this project)" | None. Stated | Yes |
| Rollup write refused or failing | "Incomplete" label on affected comparisons, with the minute count | None for those minutes | Yes |
| Pipeline delayed | Comparison stops at the last settled minute and says "as of 14:32" | Waits | Yes |
| A section's read failed | That section shows its failure with retry and correlation id; the rest of the page stands | Retry | Yes |
| Sync job stopped | New seen releases are late. Comparisons still work for existing ones | Automatic | Operators see a log and metric |

## Acceptance criteria

**Setup**

- **AC1.** An admin with no prior knowledge must be able to mint a secret, copy a recipe, run it with `dry_run`, and see
  "last check received" change, using only the console and setup page. At least one real new person must be timed on
  GitHub Actions, with the result recorded as an observation against the five-minute target. If nobody can be found, the
  result must be recorded as "not timed", and no document or screen may claim five minutes.
- **AC2.** A member or viewer must not see the *Deploys* section, and the routes must refuse them (an API test, not only a
  web test).
- **AC3.** A database read of `project_webhook_secrets` must show no usable secret, and key rotation must re-seal it. A
  test must fail if the column is missing from `SEALED_COLUMNS`.
- **AC4.** A secret must not be readable by any route after the creation response.
- **AC5.** A project with 5 live secrets must refuse a sixth with a clear message.
- **AC48.** No recipe or snippet in `docs/product/setup/` or onboarding may contain a fixed literal version as the value
  of `service.version`, checked by a test.
- **AC49.** Each recipe's signing step, extracted from `setup/deploys.md`, must be accepted by the API test client. A test
  must also prove each recipe reads the secret from an environment variable and not as an argument, sends no actor or
  context fields, exits 0 with the endpoint down and on a bad item, and sends a new `delivery_id` on a re-run.
- **AC50.** Mint, send on the new secret, withdraw the old: no delivery may be refused in between (UR12).

**Webhook**

- **AC6.** Every existing refusal must hold: wrong signature, altered body, another project's secret, unknown project,
  and all in the same words, writing nothing. The missing-header refusal is removed on purpose; a missing or empty signed
  `delivery_id` is refused instead.
- **AC7.** A captured signed body sent again with any header values must store nothing new, whatever id the header
  carries.
- **AC8.** A batch of 100 services must store 100 deploys and one `deploy.batch_recorded` audit row, with the secret id,
  count and first deploy id, in one transaction. A 100-item body with every field at its limit must be accepted, and a
  body one byte over 512 KiB refused with the stated message.
- **AC9.** A batch with one bad item must store nothing, name the item and field, and appear as the last refusal.
- **AC10.** Sending the same batch twice must store nothing the second time, answer each item as repeated, and leave
  `report_count` and `last_reported_at` unchanged. Two racing requests must store each item once. A batch mixing new and
  repeated items stores only the new. The same id twice in one body answers 422.
- **AC11.** `deployed_at` six minutes ahead or eight days behind must be refused; a time inside the bounds accepted.
- **AC12.** A short or non-hex `commit_sha`, a non-https `url`, a `url` with a username, query or fragment, over-large or
  nested `metadata`, an over-long `delivery_id`, `__other__` as a name, a bidirectional override or tag character in a
  name, and a NUL in any text must each answer 422 and never 500.
- **AC13.** `dry_run` must store no deploy and no release, must update "last check received", must not change "last deploy
  received", and must not count against the hourly limit.
- **AC14.** The 1,001st new deploy within an hour must answer 429. A batch of 100 with 950 used must be refused whole, with
  `Retry-After`, and the 50 left must still be usable. An unsigned flood must not use up that allowance. Replaying one captured body 2,000 times must not either,
  and a genuine new deploy must still be accepted.
- **AC51.** Two batches updating the same services in opposite order, racing the sync, must not end in a server error.
- **AC52.** FR12's answer must carry `release_id`, `known` and `version_seen` per item, and `known: false` for a misspelled
  service.
- **AC53.** A token-shaped value in `metadata` must not be stored in the clear, against fixtures shared with the Go masking
  rules.

**Rollup**

- **AC15.** For any span mix, the new rollup's rows for a service-minute must sum exactly to `service_stats_1m` for
  requests, errors and every bucket.
- **AC16.** Replay after restart, and rebalance with `Disown`, must not double-count. The drain must be byte-identical
  for the same offsets.
- **AC17.** A SERVER span through `DOOR_RUM` naming a new version must create no release and no row for that version. Before
  the F6 change it must count under the empty version. After it, the span must appear in neither `service_versions_1m` nor
  `service_stats_1m`, AC15's sum must hold, and FR28's check must read complete.
- **AC18.** 25 versions in one drain must yield 20 rows and one `__other__`, totals unchanged. The same version split
  across two drains that fold differently must keep totals exact, and the comparison must say "Versions were combined".
  64 junk names before a dominant real version, on one partition, service and minute, must put the real version's whole count
  for that minute into `__other__`, keep totals exact, and show "Versions were combined". A version over 200 characters or holding
  control characters must fold. Many services with many versions on every partition must keep the worker under
  `GOMEMLIMIT`.
- **AC19.** A refused rollup insert must not hold `span_derived_progress`, and must increase the refused-rows count.
- **AC54.** With inserts to `service_versions_1m` failing transiently for longer than its retry budget, the worker must stay
  up, `service_stats_1m` must keep being written, `span_derived_progress` and the settled minute must keep advancing, and
  the affected comparison must read incomplete.
- **AC20.** Erasure of an organization must remove the rollup's rows, and the inventory test must include the table.
- **AC21.** The cost measurement must exist as a committed report before Step 1b begins, with its thresholds met or a
  founder decision recorded.

**Releases**

- **AC22.** A service sending `service.version` with no webhook must show a release, basis "Seen in traffic", within 3
  minutes of its first settled span. This is an end-to-end timed check with `release-demo`, reported as a demo measurement.
- **AC23.** A deploy with no traffic must read `not_seen`, and later `serving` with basis "Reported, seen in traffic" when
  traffic matches.
- **AC24.** When rollup rows expire, `first_seen_at` and the release id must not change.
- **AC25.** v2.0, v2.1, v2.0 again must give one release row for v2.0 with two periods, and read "came back". With a
  finding between the first two: the finding must still name v2.0 with its original gap, last week's marker must still
  draw, findings since must not change for the first period, and the returned v2.0 must compare against v2.1.
- **AC26.** Another organization's release id must answer 404, from person and token alike. A token scoped to project A must
  get 404 for a release of project B, and everyone must get 404 for a deleted project's release, on every `/releases` route. `sender-details` answers a token with 403 whichever release it names (AC76).
- **AC27.** The release list must paginate, filter by service and version, show a version serving since yesterday in
  today's window, and distinguish the empty state, a filter that matched nothing, and "No releases in the last hour. The
  latest was v2.1 of checkout, 3 days ago."
- **AC55.** A sparse two-version service with a request per version every 15 minutes must open no return. A return that
  happens during a sync outage (v1, v2, v1 inside one read) must be seen. A version that appears only in rollup rows
  arriving after a sync ran, within the overlap, must become a release with its true first minute. A quiet service whose old
  version stops getting requests must end that period after 60 minutes. One stray request from an ended version must open no
  return.
- **AC56.** A rollup batch with an over-long and a NUL service name beside a good one must create the good release, count
  the two skipped, and advance `synced_through`.
- **AC57.** A report at 14:00 with first count at 14:02 must give a start of 14:02. A seen-then-reported version must keep
  its start. A back-dated report after seen must not move it. `rolled_back` on a seen version sets its state only.
- **AC58.** The first version counted on a service must read "Running when we started counting", draw no marker, and not be
  named by a finding as the release before it.
- **AC59.** A service with no version in traffic, reported v1, v2, then v1 again must get a new period for v1.
- **AC60.** 50,000 junk releases must not stop a new real version from appearing, and the Deploys section must say so.
- **AC61.** Removing a deploy, and withdrawing a secret with removal since a time, must recalculate or delete the affected
  release and periods, and be audited.

**Comparison**

- **AC28.** During a simulated rolling deploy where the new version fails more, the page must show `side_by_side` and the
  right exact counts, while a figure computed from `service_stats_1m` for the same minutes shows a smaller, mixed change.
  This is the test that proves why the method exists. It must also run the F6 project job over that scenario and record, as
  a fact in the report, whether F6 opens a finding.
- **AC29.** A replacement with no overlap must show `version_before_after`, with the caution, and last week's window.
- **AC30.** Traffic with no version and a reported deploy must show `time_before_after`, with its caution. A reported v2.1
  with traffic still on a named v2.0 must show no time comparison and read `new_version_not_seen`.
- **AC31.** 5 requests on one side must show "too little traffic" and no percentage.
- **AC32.** Each reason in FR21 must be reachable by a test and show its own words, including a reported release older than
  the project's limit reading `numbers_expired`.
- **AC33.** p95s within a ratio of 1.2 must read "about the same". The two-proportion rule must have tests on both sides
  of z = 3. 0 of 20 against 5 of 20 must read "not clearly different" (arithmetic: z is about 2.39). 0 of 20 against 0 of 20
  must not read "about the same".
- **AC34.** No comparison may read `spans` for a rate or latency. A structure test must fail otherwise, with
  `ERRORS_BY_VERSION_SQL` and the environment check named as the only allowed statements.
- **AC35.** A minute after the settled minute must not be counted.
- **AC36.** One missing minute in a 24-hour window must read "incomplete", with the minute count.
- **AC37.** No response, screen or setup document from F28 may contain "caused by", "because of this release", "regression from",
  "bad release", "due to", "introduced by", "triggered", "responsible for", "after deploying" or "impact". A test must search
  its strings. A search cannot prove there is no implied cause, so review checks that too.
- **AC62.** A new version healthy during the overlap and failing 3% afterward must read "higher" in the opening sentence.
- **AC63.** A healthy version with a slow first 3 minutes must read `about_same` on p95, and the page must state the warm-up
  rule.
- **AC64.** FR22: a release 5 hours before a finding, opened from it, must compare over 6 hours and say so. A release 30 hours
  before must say the comparison ends before the finding. Without a finding, 1, 6 and 24 hours must each read their window.
- **AC65.** FR25: last week's column must show in the two before-and-after methods and traffic share in `side_by_side`.
  FR26: five versions must draw the top four plus "Other versions", with a table of shares. FR27: a browser-only service must
  read `no_request_traffic`, and its errors must show. FR20: an `''` version must never be chosen as previous.
- **AC66.** Every method and reason, including the mixed-floor case, must assert its exact opening sentence.
- **AC67.** A service's rollup holding `__other__` in compared minutes must mark the comparison inexact. Two environment
  labels in one service's stored spans must show the warning. A 2-second first-numbers target must be measured once on the demo
  and reported.
- **AC68.** A failed comparison read must leave the facts visible and show the failure in that section. A timed-out errors
  read must say so in words.

**Errors by version**

- **AC69.** New here, gone here and in both must each appear from a seeded error, with FR29's caution. Aged-out raw data must
  show its date, with no zeros (FR30). Sampling with errors off must show "may be incomplete" (FR31). A browser key's exception
  naming a server version must not list as "new here" on that release.

**Markers and findings**

- **AC38.** With 60 deploys, a window a week ago must show that window's markers, in overview and service charts. Over 500
  must show the cap sentence, and a failed marker read must show its failure line.
- **AC39.** A marker must show version, service and basis on hover, and never the sender's `source`, and in a text list usable by keyboard and screen
  reader. Its link must be reachable by mouse (a click pins the box), touch and keyboard, the list must appear once on
  Overview, and markers within 8 pixels must collapse with the count. Failed, rolled-back and seen-only markers must each show
  a word.
- **AC40.** The finding page must show markers and the release before it, and coverage `none` words when true. A finding on a
  service with a version reported 6 minutes before must read so. Each row of the wording table must have a test, including 41
  days earlier, failed, rolled back, came back, `deploys_and_versions` with nothing before, a finding older than our first
  report, and a project finding with a null `service_name`. A test must assert "It came first" appears on the project-finding
  list and every row naming an earlier release, and is absent from nothing before, already running, coverage `none` and first
  report after.
- **AC41.** A release page must list later findings, and must never list one started before it. A finding in the same minute as
  the period's start counts as after it, and the words stay "started after".
- **AC42.** Findings must be unchanged by F28: a scheduler live test must run the project job over the same seeded minutes with
  and without `releases` and `deploy_events` rows and compare findings, `detection_readings` and outbox rows. They must be equal,
  since FR44's line is composed at delivery and never stored. A structure test must fail if detection or an alert evaluation
  sweep reaches `nexovue_scheduler.releases`, directly or transitively, at module level or inside a function, or reads
  `service_versions_1m` or `releases`.
- **AC70.** With AI off, a finding's opening notice must carry FR44's line on every channel. A grouped message (`compose_batch`) carries one line per notice, so it leaves FR44's line out, and that must be tested as such. With the read failing or
  timing out, the alert must go out without it, within the read's limit, and the claim must stand. A statement that fails must not
  abort the claim's transaction. At most one read may run per message. A hostile version must pass through `one_line` and the
  channel's escaping.
- **AC71.** `payments` shipping before a `checkout` finding must appear on the finding, marked if `checkout` calls it, and worded as
  order.
- **AC72.** Release jobs all blocked must not stop alert sweeps from running. Two jobs for one project must not overlap, and
  `synced_through` must only move forward. A first run must read no more than its per-run bound.

- **AC78.** FR46: a service with two reported-never-seen versions and one seen-never-reported version inside 24 hours must read
  "Your CI and your traffic name versions differently: CI sent X, traffic sends Y", with the real names, in place of the `not_seen`
  words. One reported-never-seen version must not. Versions outside the 24 hours must not count.
- **AC79.** FR49: any block with `about_same` must show "This covers the whole service. A problem on one endpoint can be hidden
  here", with links to errors by version and to the service's endpoint view, each for the same window. A block with no `about_same`
  must not show it.
- **AC80.** FR50: every comparison that shows numbers must show "Counts assume you do not sample before sending."
- **AC81.** FR55, stored reading: 30 minutes after a period starts, a job must write `last_reading` from `side_by_side` or
  `version_before_after`, holding method, readings and counts and no names. `/releases` must show "failure share higher" or "p95 higher"
  with counts when that reading is `higher`, show no flag for any other reading or no reading, and read no ClickHouse. A test must
  fail if the list route calls ClickHouse.
- **AC82.** FR55, summary: with the opt-in off, which must be the default, no notification may be written. With it on, each period
  except an `already_running` one must produce exactly one `release_summary` notification on the chosen channel, whatever its reading,
  including a second release on the same channel and a retried job. The message must carry the counts and plain words, or the
  reason's words, no verdict, no word from AC37, and no finding must open. A hostile version must pass through `one_line` and the
  channel's escaping. A row of that kind with no `release_period_id` must be refused by the database. Deleting the channel must turn
  the opt-in off.
- **AC83.** FR55, permission: `PUT /projects/{project_id}/release-summary` must refuse a member, a viewer and a token without the
  action, must answer 422 for another organization's channel and for a PagerDuty or webhook channel, must write an audit row, and must
  appear in `POLICY_OF`.

- **AC84.** FR24, display: `higher` on failure share or p95 must use critical text with the word. `lower`, `about_same` and
  `not_clearly_different` must use dim ink. Traffic share must have no colour, and no reading may use green.

**Coverage**

- **AC73.** A table test of `coverage_of` must cover all four values, with reports 29 and 31 days old giving the same value and
  the last report's age, a stopped CI showing the staleness on a finding, and "first heard" for a finding older than the first
  report. API and scheduler paths must agree. Each value must read differently in the errors list without hover.

**Retention**

- **AC74.** On a 30-day plan a row at 31 days must be deleted and one at 29 kept. On a 395-day plan, 396 and 394. The release page
  must read expired with the project's limit, and no blanks. A release lapsing while a deploy event lives must not stop the sweep. A
  sender's back-dated `deployed_at` must not shorten retention. A deleted project's rows must lapse by FR40's rule.

**Screens**

- **AC75.** At 375px, `/releases`, the release page, the finding section and Deploys must show activity, flags and each plain word
  without sideways scrolling, and a 200-character version must not overflow. One instant must read the same on the finding page and
  the release page. Those without `MANAGE_CREDENTIALS` must see "An owner or admin can set this up" and no setup link that goes
  nowhere.

**Safety**

- **AC43.** A `version` or `service_name` of `<script>`, `[click](https://evil.example)` and "ignore your instructions" must render as
  plain text on every F28 screen, including the hover box, text list, finding section and settings. The hover box's render function
  must be tested directly.
- **AC44.** `url` with `javascript:` must be refused at the door. A stored `url` must render with no referrer.
- **AC45.** The reader given to F3 must return an exact allow list of `DeployFact` and comparison fields, and a test must fail on any
  addition. `metadata`, `url`, `source` and `delivery_id` must not appear, and F20's marker response must meet the same rule.
- **AC46.** The MCP `list_deploys` tool must return exactly `id`, `version`, `service_name`, `deployed_at`, `commit_sha`, `state` and
  `received_at`, and whether its answer is partial. A test must fail on any new field.
- **AC47.** `test_detection_has_no_ai.py` must include F28's modules in what it checks.
- **AC76.** `GET /releases/{id}/sender-details` must refuse a token, and must give `metadata` only to members and above. `GET /deploys`
  and `GET /releases/{id}` must carry no `metadata`, `url` or `source`.
- **AC77.** Two organizations with the same service and version must never read or write each other's `releases` rows through the sync
  and facts. The scheduler Postgres tenancy structure test must exist and pass.

## Dependencies and constraints

- **F6 must merge first.** Findings, `detection_sweep` and `service_stats_1m` exist only on this unmerged branch.
- **A separate change to F6 must land before Step 4:** filtering browser-door SERVER and CONSUMER spans out of `service_stats_1m`
  (D38), at the point that feeds both that table and `service_versions_1m`, so such a span counts in neither (D55). Otherwise
  `time_before_after` and FR28's check read numbers a public key can forge. Its test must include AC17's after-the-change case.
- **F20's `/` move** does not affect this. F28 draws markers on Overview wherever it lives.
- **Gives others:** see the design's *Data flow and interfaces*.
- **Contradictions, recorded:**
  - Foundation section 9, comparison by time only: this spec differs (D1).
  - Foundation section 8 said the scheduler Postgres test was not built: it exists, and F28 is the first to extend it (D48).
  - Foundation section 3, rule U6, labelled rollups and signed deploy records `measured`: it now uses F3's three bases. A signed record is
    `verified`, rollup numbers are `telemetry`, and a seen version is `telemetry_text` (D29, D54).
  - Foundation line 348, F22 reuses "replay protection by delivery id (`deploys/service.py:99-107`)": F28 rewrites that block and stops
    reading the header. F22 keeps `signatures.py` as a plain verifier taking a list of secrets, and the delivery unique index pattern,
    and must write its own header-id check with a compensating dedupe on the signed payload's own event id, since a header id is weaker
    on replay.
  - Foundation line 527, F28 "may accept GitHub's own signed delivery on the existing webhook": D5 and the signed-id ADR rule that path
    out, since a GitHub payload has no signed id.
  - Foundation line 613 gives "It needs a notification" as the reason not to claim automatic deploy detection. The seen path removes that
    reason. "Seen is not a deploy" stays, and the wording changes in Step 6.
  - F20 D14, dashboards call `deploys_in_window` in their context route, and read coverage as "a deploy in 30 days": D29 makes
    that read signed deploys only, so dashboards would miss seen releases. F20 calls the marker function instead (D30), and
    coverage needs only Postgres, since `releases` stores the times.
  - Console redesign IA3, "Deploys gains no entry" (`console-redesign/design.md:202-203`): a release now has its own comparison, so it has
    a subject of its own and earns an entry (D25).
  - Research D2, a derived view: the design shows a stored row is needed, which is the exception D2 itself named.
  - Research D7, re-readable secrets: changed to shown once (D7).
  - Research defect 11 said an empty cell. The code shows the `UNKNOWN` mark (`ErrorsScreen.tsx:364`,
    `shared/format/number.ts:14`).
  - `000035`'s comment, "nothing else reads this table", stops being true (for `service_stats_1m`). The plan corrects it
    in documentation; the migration itself stays as applied.

## Decisions taken

Each is *recommended by the authors, founder may overrule*. D1 to D11 start from the research, section 7. "Revised" means changed after
the persona reviews.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | How to compare | Time only (foundation); version rollup; both | **Both: version rollup first, time as a labelled fallback.** Time alone is wrong during rolling deploys. First differed from the foundation, whose section 9 now matches (2026-11-02); pre-deployment, there is no data to migrate. Gated on measured cost, and the design lists what falls away if the gate fails |
| D2 | What a release is | Derived view; stored row | **Stored row.** Changes the research's first preference, under its own stated exception. A view's "first seen" would drift as rollup rows age out at 35 days (`000035:50`), and `/releases/{id}` and F3's evidence need stable ids |
| D3 | Regression as a finding | New kind; link to F6 | **Link only.** One detected problem is one record (ADR-022). *Revised:* the catalog's promise is only partly met, only where F6 opens a finding. In the showcase case the mixed rate is 0.76% and whether F6 fires is not established. AC28 records what it does. FR14's recipe and FR55's optional summary are the answer where it does not |
| D4 | Commit data | None; `commit_sha`; commit lists | **`commit_sha`, full length only.** F5 needs an exact commit, and short ones can be ambiguous. *Revised:* recipes also fill `url`, so a click-through exists until F22 adds a repository link |
| D5 | Native GitHub delivery | Now; after; never | **After recipes and telemetry.** It needs service and environment mapping settings |
| D6 | Replay of a signed body with a new id | Accept; add a timestamp; sign the id | **Sign the id, inside each item, and require a signed `deployed_at` with bounds.** A signed id closes the hole. The time bound covers a deleted row. A separate timestamp adds nothing further. Pre-deployment, the envelope may change |
| D7 | Secret storage | Plain; sealed and re-readable; sealed and shown once | **Sealed, shown once.** Stronger than the research's re-readable choice, since a stolen admin session can then only mint, which is visible and audited. Matches notification channels (`setup/alerts/webhooks.md`). Cost: a lost secret means minting another, and rotation already handles that |
| D8 | Metadata | Keep 64 KiB; shrink; remove | **Named fields, with `metadata` cut to 1 KiB, flat strings, masked at the door, never sent to AI.** *Revised* from 4 KiB, so a full batch fits the request cap. Removing it would break a superset post from CI. The size is a guess |
| D9 | Environment | Column; project | **Project,** as the foundation says |
| D10 | Many services | Raise rate limit; batch; two shapes | **One shape, a batch of up to 100.** One code path. Also a per-project limit counted after the signature, since one on the public route would let an outsider use up a victim's allowance |
| D11 | Plan limits | Gate; free | **Free for all,** per the foundation. Retention still differs by plan (BR9) |
| D12 | A browser key naming versions | Label only; exclude; refuse at door | **Exclude `DOOR_RUM` spans from versions.** The envelope already says which door (`envelope.proto:40`). Nothing in the receiver or worker reads span kind by door, so expected from reading: a browser key can send SERVER spans (see *Risks* in status) |
| D13 | When the version rollup fails | Hold F6's progress; do not | **Do not hold, and do not end the worker.** *Revised:* a transient failure gets its own 30-second budget (*a guess*), then steps over like a refusal, and the comparison shows "incomplete". Detection never reads this table, so it must not stop detection (`pipeline.go:703-725`, `consume/retry.go:19`) |
| D14 | Too many versions | Drop; fold | **Fold into `__other__`.** Totals stay exact, and the screen says so. *Revised:* the earlier claim that junk names cannot crowd out real ones was false. Arrival is first come, first served, and the drain cap is per drain, so the label fires whenever `__other__` has requests in compared minutes |
| D15 | Rollback | New row; same row with moved start; periods | **Revised: one release row, one `release_periods` row per period.** A moved start erased history, and broke "the release before this", last week's markers and findings since. The return rule also needs share, not just recency, or sparse traffic flaps |
| D16 | Comparison window and floors | Fixed; choice | **1, 6, 24 hours; default 1. F6's floors.** All guesses until tested. The failure floor selects the method |
| D17 | Words for a difference | Numbers only; verdict; plain words | **Revised: four plain words from a stated rule, with counts.** "About the same" must mean we can rule out a change. "Not clearly different" says we cannot |
| D18 | Comparison per endpoint | Now; later | **Later,** with a page sentence that says what this hides (FR49), and an arithmetic estimate in the cost report (from `endpoint_stats_1m`'s rows), not a built table. *Revised:* F6's finding names the worst endpoint only if it opens |
| D19 | Time bounds | None; future only; both | **Both: 5 minutes ahead, 7 days behind.** Guesses. Allows a late report, refuses a planted one |
| D20 | Retention | With project; fixed; per project | **Revised: the project's own derived retention, by arrival time.** The earlier flat 395 days broke detection's rule that a stored record "must not outlive the telemetry it names" (`detection/retention.py:5-8`), by about 365 days on Free. On Free, "numbers expired" hardly appears. A longer life would be a retention promise |
| D21 | Testing setup | Button in console; dry run from CI | **Dry run from CI.** A shown-once secret means the console cannot sign. A dry run also tests the customer's real path, which is what can fail |
| D22 | Showing refused requests to customers | Record; do not | **Do not record unsigned refusals.** Otherwise anyone on the internet can write to the record. *Revised:* do record signature-proven refusals, since that sender proved the secret, or a broken hookup looks healthy |
| D23 | Coverage on the errors list | New sentence each row; hover; short words; section | **Revised: short words in the cell, and the detail panel always states it.** Hover gives nothing on touch, and the column is hidden below `xl` (`ErrorsScreen.tsx:38`) |
| D24 | Alert text naming the release | Now; F3 | **Revised: now, one template line (FR44).** F3's `explained` notice goes out only with a `likely_cause`, once, and never to PagerDuty (foundation lines 414-415), so a page would never say what shipped. It is a stored fact needing no AI, and foundation U5 allows that. F3 keeps `explained` |
| D25 | Where the screen sits | Home; Signals; no entry (IA3) | **Signals, after Errors.** It is evidence, and the feed stays home. Changes IA3 for the reason recorded above |
| D26 | Shared reader | One module with database access; pure shared code plus per-process statements | **Pure shared code.** `test_cross_service_contracts.py:265` forbids database access in `nexovue_platform` |
| D27 | When to measure cost | Before design approval; in plan Step 1 | **Plan Step 1a, right after the rollup and demo tool, before tables, API or screens.** Measuring needs the rollup, and the rollup needs an approved plan. Differs from the research's wording. Step 1 stops if the numbers fail |
| D28 | MCP's `list_deploys` | Leave for F4; change now | **Revised: an explicit field list, adding `partial`.** Dumping the model would leak `source` and `url`. F4 still owns untrusted labels |
| D29 | What F3 reads | One mixed list; two reads | **Two reads.** `deploys_in_window` returns signed deploy events only, with a stable `deploy_id`. `releases_seen_in_window` returns telemetry facts labelled `telemetry`. A seen version can be planted by a secret ingest key, so mixing them would rank a forged version as signed. Removes the blanket "measured" mapping |
| D30 | F20's markers | F20 reads `deploys_in_window`; F20 calls the route over HTTP; both call one function | **One function,** `markers_in_window` in the API's `releases/service.py`, Postgres only, which `GET /releases/markers` also calls. Otherwise one install draws different markers on one window, and FR35 is false on dashboards. F20's reasons against HTTP stand: one request, its own tier, and an answer through a ClickHouse outage. *Revised on checking:* the review cited F20's older `deploy_events_between` with a cap of 200, and F20's D14 now calls `deploys_in_window`. Changes F20's D14, and that document must be changed at approval |
| D31 | Where the sync runs | `light` queue; its own | **Its own `releases` queue, capped at 2 slots (*a guess*),** with a 60-second expiry, a per-project lock and a per-run bound, as F6 does. On `light` it could fill all eight slots and delay alerts |
| D32 | Request size | Keep 64 KiB; raise; cut items | **512 KiB and 1 KiB `metadata`.** The old cap refused valid batches. Worst plain case is about 395 KiB (arithmetic, 100 × about 3.95 KiB). Cost: up to 5 HMACs over 512 KiB per unsigned request, bounded by 60 a minute per address. Measured in Step 2 |
| D33 | Completeness check | 99% of total; per minute | **Per minute.** 14 of 1,440 minutes could go missing under 99%, and a lost burst may be what the comparison exists to show. Reads already stop at the settled minute |
| D34 | A release that degrades after its overlap | Overlap only; two blocks | **Two blocks, with warm-up left out of p95.** Overlap-only would report a 3-minute rollout forever, and a release failing 20 minutes later would keep reading "about the same" |
| D35 | Telemetry-only rollbacks with no version | None; by report | **By report (FR19).** Otherwise customers with no version in traffic never see one |
| D36 | Release cap | Stop creating; evict | **Evict in a stated order, one cap on both doors.** Stopping is a silent dead end, and a stolen secret could lock out real versions for a year |
| D37 | Recovering from a stolen secret | Withdraw only; remove too | **Remove too, with `webhook_secret_id` on each row.** Otherwise a forged deploy stays on charts and goes to F3 until it expires |
| D38 | Browser key reaching F28 by other paths | Accept; label; split | **Split.** Add a door column to `spans` and `logs` (`000039`), so server releases read server-door errors only, and require the separate F6 change before Step 4 |
| D39 | Two environments in one project | Stated limit only; counter in the worker; read stored spans | **Read stored spans, once per comparison window.** This asks which labels appear, not a rate, so sampling does not matter, and it adds nothing to the worker's hot path. Differs from the review's counter. Misses a laptop with no label |
| D40 | Telling people about a release | Nothing; opt-in summary | **Opt-in summary plus a stored reading on the list.** Three reviewers said a page nobody remembers to open fails, and rivals push this. Every release when on, with no filter, so it stays a report and not a detector. If overruled, it is a stated loss against Coroot and Sentry |
| D41 | Other services' releases on a finding | This service only; project with a lookback | **Project, within 2 hours, callees marked.** "No release of this service" reads as "not a deploy" when `payments` shipped and `checkout` broke. Stated as order |
| D42 | Who shipped it | `deployed_by`; none | **None.** It adds personal data to a long-lived record, when the commit link already names the author |
| D43 | Any two versions | Picker; automatic | **Automatic previous in version 1.** Simpler, and untested demand. A stated lag |
| D44 | Names | Rule, impact; marker, comparison | **Release marker and comparison.** "Rule" already means an alert rule here, and "impact" implies cause, against BR1. Renames the foundation's route, which was not a founder decision |
| D45 | Sender text exposure | Leave in reads; one route | **One route, for people only (FR54).** F4's ticket is a third kind of `Reader` (foundation section 5), so `CurrentReader` would let its model read this text |
| D46 | Who sees a `url` | Everyone reading; signed-in people; members | **Signed-in people for `url` and `source`, members and above for `metadata`.** Weighs security's narrower gate against a viewer's need to reach the commit at 3 a.m. A `url` holds no query or fragment now, and `metadata` is the free text |
| D47 | A version already running when counting began | Treat as a release; flag | **Flag it.** Otherwise "started 6 minutes before this" is false on first connection, and Overview fills with fake markers |
| D48 | Scheduler Postgres tenancy test | F3 builds it; F28 builds it; extend the one F6 built | **Extend the one F6 built, in Step 1b** (`apps/api/tests/test_scheduler_postgres_tenancy_structure.py`, read 2026-11-02). F28 adds cross-tenant upserts before F3 exists, so it extends it first. A second test of the same name would stop collection |
| D49 | Marker differences | Dash patterns; words | **One neutral dash, with words in an HTML strip above the plot.** A 1px dash difference is unreadable, finding charts already use dash for their three lines (`finding-page.ts:152`), and canvas text sits under the series |
| D50 | Words versus the console's shared change indicator | One threshold; separate | **Separate.** The console's `Delta` flags 0.5 of a point (`delta.ts:40`) between two periods, with no counts. Here a word needs evidence from counts, so one change can be flagged on the services list and silent here. This differs from the redesign's one-threshold rule, on purpose |
| D51 | Where FR44's read runs, and what "imports nothing from releases" means | A read in `outbox.py`, loaded lazily; move `ANOMALY_FINDING` out of `outbox.py` and read inside the claim under a savepoint; a read in `delivery.py` after the claim commits | **In `delivery.py`, on its own connection, one per message, with a 500 ms limit (*a guess*).** It answers both problems: its failure cannot touch the claim's transaction, and `delivery.py` is outside what detection imports, so no lazy import is needed. AC42's test checks **transitive** imports, since a direct-only test would pass while the claim was false |
| D52 | What a release summary's notification names | Project kind, with counts added to `detail`; a new payload column; a nullable `notifications.release_period_id` | **`release_period_id`.** A project-subject row has no way to reach the counts, and a payload column would freeze words at write time. This keeps the existing rule that a row names its subject, and counts are read at send. Costs a column, and edits to one index, one check and the order rule |
| D53 | Where the opt-in lives, and who may change it | A new table with many channels; `projects.release_summary_channel_id`; under `MANAGE_CHANNELS`, or `CONFIGURE_PROJECTS` | **One nullable column on `projects`, under `CONFIGURE_PROJECTS`.** It is a setting of a project, and it changes nothing about the channel. One channel is enough until someone asks for more. Owners and admins hold both actions today, so nobody's access differs |
| D54 | How `reported` and `seen` map to the shared bases | Call all of it `measured`, as U6's first wording would; use F3's three bases | **F3's bases: a signed record is `verified`, rollup numbers are `telemetry`, and a seen version is `telemetry_text`.** A seen fact's only claim is a string a secret ingest key can write, so the rule that free text alone cannot be a likely cause applies. Foundation U6 was changed to match on 2026-11-02 |
| D55 | Where a browser-door server span counts after the F6 change | Neither table; only the version table's empty row | **Neither.** Keeping it only in the version table would break FR16's sum, and FR28 would read "incomplete" for ever on such minutes. Until the change lands, the empty version keeps the sum true |
| D56 | Who builds the scheduler reads for FR32 and FR52 (raised by F3, `ai-insights/design.md:300`, `ai-insights/plan.md:125`) | F3's T1.4 adds them to F28's file; F28 builds them | **F28 builds both, in `releases/facts.py`, in T3.8: `release_before` (the last period before a time, at any age, which FR44's line already needs) and `releases_near` (FR52's 2-hour project list).** One owner per module, F28 ships first (foundation section 9, order), and FR44 needs the first read anyway. F3's T1.4 then calls them and writes none. Decided 2026-11-02 |
| D57 | Keep or cut the opt-in summary (D40, FR55) and the project release list (FR52), which the status named as cuttable after scope grew | Keep both; cut one; cut both | **Keep both, as written.** Both are free, inside F28, and sell nothing new, so this is a technical scope call. Rivals push a release notice: Coroot audits each application 30 minutes after a new version is deployed and sends notable changes to Slack, and Sentry sends a deploy notice to the people who committed to that release (both re-read 2026-11-02, *Sources* below). Coroot's goes only on notable changes; D40's every-release summary is deliberately not that. F3's evidence job already relies on FR52 (`ai-insights/plan.md:125`). The summary stays last, in Step 5 (T5.6), so it can still be dropped late with no rework if Step 1a or the conversations say so. Decided 2026-11-02 |
| D58 | If the browser-door change is late, may Step 4 ship with the exposure labelled | Wait; ship labelled | **Wait.** The founder approved taking the change before Stage 1 reaches a customer (foundation, *Founder decisions*, 7), and section 9 says F28's Step 4 waits for it. The "or the founder accepts the exposure" path is withdrawn. Decided 2026-11-02 |

**Sources read for D57, 2026-11-02:** Coroot, deployment tracking, <https://docs.coroot.com/inspections/deployment-tracking>;
Sentry, notifications, <https://docs.sentry.io/product/notifications> and <https://blog.sentry.io/release-deploys/>. Read through a
search tool's summary; check before quoting in public.

## Founder decisions needed

**None is open. Founder answers, 2026-11-02:** the browser-door owner and the later gate below are approved as recommended (foundation, *Founder decisions*, 22 and 24).

**Settled by the foundation, not asked again** (`phase-2-foundation.md`, *Founder decisions*, approved 2026-11-02):

- **Customer conversations before Step 1b.** Item 8: one round of five to eight conversations with on-call engineers and CTOs,
  held before F28's Step 1b, using every feature's questions. F28's questions and what the answers change stay below, for that round.
  - **Ask:** what is in `service.version` today, how a release is checked now, would a pull page be used without an alert, and do
    they sample before sending?
  - **What would change the plan:** if most want only the fact on the finding, Step 3 moves ahead of Step 1b, and Step 4 waits. If
    few set `service.version`, setup pages and recipes come first. If most sample before sending, the comparison's exactness cannot
    be a selling point.
- **The browser-door change before Step 4.** Item 7 (D58). **Approved 2026-11-02: F28 owns that change** (foundation item 22).
- **D1, comparing by version.** No longer differs from the foundation (section 9 now matches), and it was a technical choice.

**Approved in advance 2026-11-02, as recommended (foundation item 24). Applies only if the Step 1a cost gate fails (design, *Performance and scale*).** In plain words: counting versions may cost too much disk or
memory. The plan tightens the limits once. If it still fails, someone must choose between paying that cost (every self-hoster's
disk and memory, and later nexovue's own hosting bill) or dropping version comparison. Dropping it also drops "releases appear with
no new webhook". That is table stakes, not a reason to buy (*Table stakes* above): Sentry, SigNoz and Coroot have it, so a buyer expects it and
notices its absence.
**Recommendation then, by which check failed:**
- **Only a cost check** (rows, memory share, disk share, all marked guesses): keep version comparison and pay, if the absolute
  numbers fit the smallest supported server (16 GB; worker 1.25 GB, ClickHouse 6 GB, `docs/self-hosting/sizing.md`). Update
  `sizing.md` with the real figures. Drop it only if it does not fit there.
- **A correctness check** (worker over `GOMEMLIMIT`, totals not exact, alerts starting more than 10 seconds late): ship time
  comparison only. Money cannot fix these, and a late or wrong alert costs more trust than a missing comparison.

**Why it was yours:** it trades customers' money, and later nexovue's, against what is sold. **Where "fits" is checked (2026-11-02):** no test server is rented (foundation item 9). Step 1a runs on the Mac's Docker, which holds each container to its ceiling, so the worker's 1.25 GB and ClickHouse's 6 GB can be checked there. Whether the whole 16 GB server fits is confirmed only on Oracle after deployment; until then `sizing.md`'s new figures say "measured on a development machine, not on a 16 GB server".

## Open decisions

None. D56 to D58 were decided on 2026-11-02 (*Decisions taken*); all decisions may still be overruled at approval.

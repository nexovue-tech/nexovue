# Phase 2 competitors: what they do, what they cost, what Nexovue may honestly say (2026-11)

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Type:** Research snapshot for [`phase-2-plan.md`](../product/phase-2-plan.md), open question 3.
> **Status:** Proposed. Nothing here is approved until the founder approves it.
> **All sources accessed:** 2026-11-02.

## The short answer

- **"All on your own server, so your data stays with you" does not separate Nexovue from everyone.** It does
  separate it from Datadog, New Relic, Dynatrace (its AI), Sentry (its AI), SigNoz's assistant, and Grafana's
  assistant. It does not separate it from OpenObserve Enterprise, Aurora, HolmesGPT, or OpenSRE.
- **The narrower claim that may hold, once it is built:** one product under an open-source licence that stores the
  telemetry, detects the problem, explains it and drafts the fix, with the AI running in the customer's own account
  or on their own server. Of the products checked, none was confirmed to do all of that in its open-source edition.
  That is a statement about what was checked, not proof that nothing else exists.
- **Today Nexovue has no AI explanation, no chat, no fix, no SLOs and no dashboard builder.** Every Phase 2 claim
  below must stay in the future tense until the feature ships.
- **Draft pull requests with human review are now common.** Datadog, Sentry, Rootly, incident.io, HolmesGPT, Aurora
  and OpenSRE all describe a form of it. It is expected, not a differentiator.
- **The best unclaimed ground is safety evidence.** No vendor found publishes test results for injection through
  telemetry. Nexovue's plan already requires a test suite in the gate. Publishing the results would be unusual.

## How to read this document

**Words used here**

| Term | Meaning |
|---|---|
| **AI SRE** | An AI agent that investigates production problems. SRE means site reliability engineer, the person who keeps systems running |
| **MCP** | Model Context Protocol: a standard way for an AI tool to call another product's functions |
| **BYO model** | Bring your own: the customer supplies the AI account or key |
| **Local model** | An AI model running on the customer's own machines, for example through Ollama |
| **SLO** | Service level objective: a reliability target, such as 99.9% of requests succeeding over 30 days |
| **Error budget / burn rate** | The failure the target still allows, and how fast it is being used up |
| **On-call** | The rota that decides who gets woken up. The founder has decided this stays with PagerDuty |
| **Injection** | Text planted in logs or traces that tricks an AI reading them into following it |

**How far to trust each fact**

- **[V]** read on the vendor's own page or repository. **[V-s]** the vendor's page, seen only as a search result
  excerpt. **[3P]** a third party only, which may have a stake in the answer.
- Vendor pages were read through a tool that summarizes them. **Check any quote against the live page before
  publishing it.**
- A vendor's claim about itself is a claim, not a measurement. Nothing here was installed or tried.
- "Not found" means these searches did not find it, not that it does not exist. "Not checked" means it was not looked
  for in this pass.
- Dollar sums marked *arithmetic* are this document's, not a vendor's quote.
- Nobody was interviewed. What buyers actually refuse a tool over is still unknown
  ([earlier research](phase-2-scope-2026-10-30.md) reached the same point).

## Open question 3: competitors' AI, from their own documentation

| Product | Where the AI runs | Which edition | Customer's model? | Plan's belief | Verdict |
|---|---|---|---|---|---|
| **OpenObserve** | An agent service inside the customer's install [V] | **Enterprise licence, not the AGPL edition** [V] | Own key: yes [V]. Local: the product page says any OpenAI-compatible endpoint, and that a self-hosted model keeps data in-house [V]. The setup guide lists only Anthropic, OpenAI and Gemini [V] | "Claims a self-hosted AI SRE with bring-your-own and self-hosted models" | **Confirmed, with two limits the plan missed:** it is under a commercial licence (free to 50 GB/day), and the local-model claim is on the product page but not in the setup guide |
| **SigNoz** | Noz, its assistant: "Available to SigNoz Cloud users" [V]. Its MCP server can run self-hosted, and an outside AI tool then reads SigNoz through it [V] | Noz is listed under Teams on the pricing page [V] | Through whichever AI tool the customer connects to MCP | "Appears to keep its AI in the cloud" | **Confirmed for Noz, incomplete overall.** A self-hosted SigNoz plus Claude or Cursor through MCP is a real, cheap alternative. The plan's source (a fork's README) can be replaced by SigNoz's own docs |
| **Grafana** | Hybrid. The interface runs in the customer's Grafana; "the backend, usage limits, and billing stay in the Grafana Cloud stack". Prompts and query context go to Grafana Cloud; data sources stay put [V] | Open-source and Enterprise can connect, since 2026-04-21 [V] | No such option described [V] | "Appears to keep its AI in the cloud" | **Confirmed, with detail.** Investigations, investigation memory, Slack and automations are hidden when self-managed [V] |
| **Dynatrace** | SaaS only. "Not available for Dynatrace Managed customers" [V] | SaaS | Not addressed [V] | Not in plan | Cloud only |
| **Sentry** | Seer is unavailable on self-hosted Sentry: "currently closed source" [V] | Paid cloud plans | Not checked | Not in plan | Cloud only |
| **Datadog, New Relic, incident.io, Rootly** | Vendor's cloud. No self-hosted option found | Paid | Rootly: its pricing page lists own keys on Enterprise; its AI SRE guide says it does not accept them yet [V, conflicting]. Others not found | Not in plan | Cloud only, as far as found |
| **Honeycomb** | Vendor's cloud. "Honeycomb Private Cloud" is listed for Enterprise with no details [V] | All tiers include Canvas and MCP [V] | Not found | Not in plan | Cloud, apart from an undetailed Enterprise option |
| **Aurora** | Customer's own install; air-gapped bundle offered [V] | Apache 2.0 [V] | Yes, including Ollama for air-gapped use [V] | Not in plan | **Directly overlaps the selling reason** |
| **HolmesGPT** | Customer's own install [V] | Apache 2.0, CNCF sandbox [V] | Yes. Ollama is "experimental", with limited tool calling [V] | Not in plan | **Overlaps**, though it reads other tools' data rather than storing telemetry |
| **OpenSRE** | Customer's own machine [V] | Apache 2.0, public alpha [V] | Yes, including Ollama [V] | Not in plan | **Overlaps in words**; its focus is failed CI runs and it is still alpha |
| **Coroot** | Sources disagree. Docs: Enterprise includes AI root cause; Community reaches AI through Coroot Cloud, 10 free investigations a month [V]. Editions page: not in Community [V] | Enterprise from $1 per CPU core a month [V] | Docs name OpenAI and Anthropic [V] | Not in plan | **Closest in shape**: stores telemetry, finds cause. AI not in the free self-hosted form unless through its cloud |
| **Elastic** | Self-managed possible, with an appropriate licence [V-s] | Tier unclear [V-s] | Old docs (8.19) say no private model; newer docs mention self-managed models [V-s, conflicting] | Not in plan | Unresolved. Check before naming it |

**What remains unresolved:** whether OpenObserve's local-model path works in practice, the Coroot conflict, the
Elastic tier, and Rootly's own-key status. Each needs a direct test or a question to the vendor before any public
comparison names it.

## Capability 1: AI root-cause explanation

| Product | What it does | Cost | Gaps and cautions |
|---|---|---|---|
| **Datadog Bits Investigation** (formerly Bits AI SRE) | "Autonomous AI agent that investigates production issues end to end" [V] | AI Credits: $500 per 500 a month annual, $600 monthly, $650 on demand [V]. About 6.5 credits per investigation, Datadog's own estimate [V-s]. *Arithmetic:* about $6.50 to $8.45 each | Not supported on Datadog's government sites [V]. Older third-party pages still show the replaced $500 per 20 investigations |
| **Grafana Assistant Investigations** | Investigations in Cloud; hidden when self-managed [V] | $20 per active AI user with 40M tokens, then $2 per 1M tokens; no fee per investigation [V]. Pro's $19 platform fee includes three AI users. Metered since 2026-10-01 [V] | Cost depends on tokens, so it is hard to predict. Free plan blocks when the allowance ends rather than billing [V] |
| **New Relic Autopilot** (formerly SRE Agent) | Triage and root cause; "recommends actions but does not take them" [V] | Pro or Enterprise, plus Advanced Compute [V]. $0.60 per compute unit [V-s, may be old]. No Autopilot price listed [V] | 100 requests an hour per organization; not on FedRAMP [V] |
| **Dynatrace Assist and SRE agents** | Generative and agentic AI on its platform [V] | UI use: no licence charge, though queries it runs consume licence. Workflows, API and MCP use: rate card, $0.10 per AI unit [V]. No SRE-agent price found | SaaS only. Tool results are shared with the companies hosting its models [V] |
| **Honeycomb Canvas** | Investigation workspace with an AI agent using the same query engine as the user [V-s] | Included on Free and on Pro, from $150 a month [V]. Pro events rose to $3.00 per million from 2026-07-01 [V-s] | Cloud; event-based pricing |
| **Sentry Seer** | Root cause from issue details, traces, logs, code and profiles [V] | $40 per active contributor a month, unlimited use [V]. A contributor makes 2 or more pull requests in a connected repository [V] | Cloud only. Its base is errors and code, not whole-service anomaly finding |
| **OpenObserve** | Starts when an alert fires. Report with summary, findings and recommended actions; re-runs when a new alert type joins [V] | Self-hosted Enterprise free to 50 GB/day, above that by sales. Cloud: $0.50 per credit per the product page, 20 free [V]. Its own FAQ also says "priced on ingestion" [V, unreconciled] | Not in AGPL edition. **Not read-only:** its chat can create dashboards and alerts [V]. Shared chats can be public, visible to anyone with the link, unless tool inputs and outputs are hidden [V] |
| **SigNoz Noz** | Answers questions on logs, traces, metrics; creates dashboards, alerts, views [V] | Cloud Teams, shown at $49 a month with $49 of usage [V] | Cloud only |
| **incident.io Investigations** | Starts on a declared incident. Hypotheses with confidence score and linked sources [V] | Add-on on Pro and Enterprise; no price shown [V] | Hosted; needs telemetry elsewhere. It says it backtests daily but gives no accuracy figure [V] |
| **Rootly AI SRE** | Tests competing explanations; names a cause only when evidence supports it, otherwise "Inconclusive" [V-s] | "Let's talk"; requires Incident Response at $20 per user a month [V] | Hosted. Custom MCP endpoints must be public [V-s] |
| **HolmesGPT** | Agent that investigates using many existing data sources; "dozens" of integrations [V] | Free; pay own model | About 3,700 GitHub stars when read. Needs a telemetry product beside it |
| **Aurora** | Agentic root cause and incident management across clouds and Kubernetes [V] | "Free forever, no per-seat or per-incident pricing" [V] | About 450 stars when read, so young. Runs commands in isolated Kubernetes pods, which a small non-Kubernetes team lacks |
| **Coroot** | AI root cause on its own telemetry [V] | From $1 per CPU core a month [V] | See the conflict above |

**The bar this sets.** Linked evidence and "inconclusive" as a real answer are already sold (incident.io, Rootly). The
plan's "every claim links to its data; causes are labelled as likely" meets the market. It does not beat it.

**What Nexovue could bring, once built, that is true**

- Correlation from stored facts first, AI wording second. Nexovue already has the discipline in code: the deploy
  shown beside an error is labelled "before, not because"
  (`apps/api/src/nexovue_api/errorgroups/schemas.py:15-26`).
- A finding that stands with AI off. Detection imports no AI code, and a test enforces it
  (`apps/scheduler/tests/test_detection_has_no_ai.py:1-16`).
- Published scores: each injected fault's stated cause scored on the OpenTelemetry Demo. No vendor checked gives an
  accuracy figure. Such a number would be from a demo, and must say so.

**False to claim**

- "The only self-hosted AI root cause" (Aurora, HolmesGPT, OpenObserve Enterprise).
- "More accurate than X": nothing has been measured.
- "Your data never leaves your server" as a blanket line. The founder-approved own-account option sends data to the
  customer's AI provider. It is true only of the local-model option.
- "Works as well on a local model": unproven. The plan already flags this risk.

## Capability 2: Incident and case management

| Product | What it does | Cost | Gaps and cautions |
|---|---|---|---|
| **Datadog Incident Management** | Incidents, with AI triage actions into Jira and paging [V] | $30 per seat a month annual; On-Call $20 more [V] | Priced per seat |
| **Grafana IRM** | Cloud incident and on-call app | $20 per active user; three included [V] | **Grafana OnCall open-source was archived on 2026-03-24**; development continues in Cloud [V-s]. A real gap for self-hosters |
| **OpenObserve Incidents** | Open, Acknowledged, Resolved, reopen. Groups alerts by shared dimensions; activity timeline [V] | Enterprise and Cloud only [V] | Jira and GitHub not mentioned on that page [V]. On Cloud, running out of AI credits stops new incidents for unpaid organizations; alerts keep firing [V] |
| **incident.io** | Full incident product plus call transcription | Free Basic. Team $15 annual or $19 monthly, Pro $25, per user a month; on-call $10 or $20 more [V] | Hosted; no self-hosting mentioned [V] |
| **Rootly** | Incident response and status pages | $20 per user a month, on-call $20 more; "Prices are additive" [V] | Hosted; no self-hosting mentioned [V] |
| **Keep** | Open-source alert grouping and workflows; on-premises and air-gapped [V] | Free self-hosted | Whether its AI is limited to a paid tier is unresolved. Licence type not read. Not an observability product |
| **Aurora** | Incident management with post-mortems [V] | Free | Young |
| **SigNoz, New Relic, Dynatrace, Honeycomb, Sentry** | Not checked in depth | | |

**What Nexovue has today:** alert incidents, one per breach, with flap handling and links to open findings
(`apps/scheduler/src/nexovue_scheduler/alerts/incidents.py:1-25`), and findings with open, acknowledged and closed
states (`apps/api/src/nexovue_api/findings/schemas.py:18-19`). No case lifecycle, timeline, GitHub or Jira.

**What Nexovue could bring, once built, that is true**

- Case management in the free self-hosted product. OpenObserve keeps its version out of AGPL; Grafana has withdrawn
  its self-hosted on-call.
- One record, not two, because a finding grows into a case (F6 D1).
- No per-seat charge, against $20 to $30 a seat elsewhere. It rests on the "never per seat" promise
  (`docs/product/vision.md:152`).

**False to claim**

- "Replaces incident.io or Rootly." The founder has kept on-call out. Nexovue has no Slack-centred response, call
  transcription or status pages planned in the chain.
- "Replaces PagerDuty." It sends alerts to PagerDuty (`packages/py-platform/src/nexovue_platform/alerts/channels.py:35`).

## Capability 3: Chat over telemetry

| Product | What it does | Cost | Gaps and cautions |
|---|---|---|---|
| **Datadog Bits Chat** | Searches and analyzes telemetry, creates dashboards and notebooks, inspects monitors [V] | AI Credits [V] | Can trigger actions, so it is not read-only [V] |
| **Grafana Assistant** | Chat, dashboards, questions, blast radius analysis [V] | $20 per active AI user [V] | Prompts and query context go to Grafana Cloud even when self-managed [V] |
| **SigNoz** | Noz in Cloud; MCP for outside AI tools on either [V] | MCP: no separate fee found. Alert tools need v0.120.0 or newer [V-s] | Only Admins can create the API keys it uses [V-s] |
| **OpenObserve** | Writes SQL, VRL and PromQL; can create dashboards and alerts [V] | Enterprise or credits | Not read-only |
| **Honeycomb** | Canvas and hosted MCP. MCP includes write tools such as `create_slo` [V-s] | Included [V] | Cloud |
| **New Relic** | Natural-language to NRQL; MCP [V] | Advanced Compute [V] | A partner connection gets the same access as the user who signed in [V] |
| **Dynatrace Assist** | Respects user permissions; logs interactions [V] | See above | SaaS only |

**A correction to the plan.** `phase-2-plan.md` lists the Chatbot (F4) as having only pgvector. **Nexovue already has a
read-only MCP server** at `/api/mcp` (`apps/api/src/nexovue_api/assistant/server.py:34,46`). It offers nine tools
(`assistant/tools.py:153-284`), authorizes with the same API tokens as the read API (`assistant/auth.py:1-10`), and caps
results (`assistant/bounds.py:1-22`).

Two things follow, and both matter to Stage 4's design:

- **Log bodies reach the AI as they are.** A search of `assistant/*.py` finds no sanitizing, masking or injection
  handling, and `search_logs` returns the records it reads (`assistant/tools.py:201-225`). The plan's rule, "untrusted
  fields are sanitized or replaced before AI reads them", is not met on this surface today. This is an inference from
  that code; no injection test was run. Whether masking at intake covers this was not checked.
- **It has no findings tool.** None of the nine reads findings (`assistant/tools.py`).

**What Nexovue could bring, once built, that is true**

- A chat that cannot write, by construction, where several rivals' assistants can create things. That is a real
  difference, and a trade-off: users may want it to build a dashboard.
- Answers limited to what the asking user may see, with citations. Dynatrace also says it respects permissions, so
  this is expected rather than unique.

**False to claim**

- "Chat with your telemetry, unlike the others." This is common.
- "Injection-proof." Published work reports read-only access limits the damage without stopping the injection.

## Capability 4: Suggested fixes and draft pull requests

| Product | What it does | Who approves | Cost | Cautions |
|---|---|---|---|---|
| **Datadog** | Generates fixes by default for code-related causes; Bits Code iterates on pull requests and never auto-merges [V, V-s] | Person creates and merges [V]. Ask or Deny guardrails [V] | AI Credits [V-s] | Guardrails, one-click and Kubernetes actions are in Preview [V] |
| **Sentry Seer Autofix** | Root cause, plan, code, then a draft pull request [V] | Stop after Root Cause, Plan or PR Drafted; a person reviews before merging [V] | $40 per contributor [V] | **GitHub.com and GitLab.com only**; no self-hosted source control [V] |
| **Rootly** | Remediation steps and draft pull requests [V] | "Every change requires explicit human sign-off" [V-s] | By quote | **Its docs warn it does not filter write-capable MCP tools** [V-s] |
| **incident.io** | "The only change Investigations can make to your systems is a pull request you review and merge yourself" [V] | Person | Add-on | Sandboxed containers, zero-retention agreements: vendor claims [V] |
| **New Relic Autopilot** | Recommends only. GitHub link (preview) looks up pull requests; no evidence found of it opening one [V-s] | Person | Compute | |
| **OpenObserve** | "Actionable remediation plans"; no pull requests mentioned on that page [V] | | | |
| **HolmesGPT** | "Read-only access" by design, yet "can even open PRs", plus a remediation toolset for scaling and rollbacks [V] | Not clear from the README | Free | The README does not reconcile these |
| **Aurora** | Opens fix pull requests [V] | Org-level command policies [V] | Free | Which commands can change resources is not clear [V] |
| **OpenSRE** | Pushes fixes to a pull request branch; optionally executes remediation [V] | Tool approvals in headless mode [V] | Free | Alpha |
| **Splunk** | Named in the plan's sources; not re-checked here | | | |

**What Nexovue could bring, once built, that is true**

- A strict split: the part reading telemetry holds no write permission, and repository access is narrow and audited.
  Rivals describe human approval. The pages read here did not describe this separation, though they may have it. Say what
  Nexovue does; do not claim others lack it.
- Works with a self-hosted model, unlike Seer. Self-hosted GitHub or GitLab support is a possible gap to fill, but
  the plan's connection is GitHub; do not claim more without a design decision.

**False to claim:** "the only tool where a person approves the fix" (this is the norm), or "safe" without qualification.

## Capability 5: SLOs

| Product | What it does | Cost | Gaps |
|---|---|---|---|
| **Datadog** | Metric-based, monitor-based and time-slice. Burn-rate status over 2 hours [V] | Price not found on list page | Cloud |
| **Grafana SLO** | SLIs, SLOs, error budget, burn rate, maintenance windows [V] | Not listed [V] | Documented under Cloud. Whether it runs self-managed is **unverified** |
| **SigNoz** | **No native SLO found.** Issue [#658](https://github.com/SigNoz/signoz/issues/658), open since 2022-01-30, labelled customer demand, last comment 2026-10-23, no maintainer plan stated [V] | | **The clearest gap found.** Settles the plan's earlier unknown |
| **OpenObserve** | Count, time-slice and alert-based SLOs; multi-window burn-rate alerts [V] | Edition not stated on the page read | |
| **Honeycomb** | SLOs: none on Free, 2 on Pro, 100 and up on Enterprise [V] | Tied to plan | Tight limit below Enterprise |
| **Coroot** | SLO tracking in the free Community Edition [V] | Free | |
| **Pyrra, Sloth** | Open-source SLO tools for Prometheus. Pyrra has its own interface; Sloth generates rules [V-s] | Free | Separate tools, need Prometheus |
| **New Relic, Dynatrace, Sentry** | Not checked | | |

**What Nexovue could bring, once built, that is true:** SLOs included on the free self-hosted product, fed by exact
pre-sampling rollups, with budget burn appearing in the findings feed (F19 in `feature-catalog.md`). Unlike Honeycomb
there would be no small count limit, if the design holds to that.

**False to claim:** "Others do not have SLOs." Only SigNoz's lack is supported, and only as of this date.

**Nexovue today:** a search of `apps/api/src`, `apps/web/src` and `apps/scheduler/src` finds no SLO or error-budget code.

## Capability 6: Dashboards

This area was checked least, because the conclusion does not depend on details.

| Product | Fact | Cost |
|---|---|---|
| **Grafana** | The reference point; dashboards are what it is known for. Assistant can build them [V] | Three free visualization users on Cloud free tier [V]; open-source self-hosted version exists |
| **SigNoz** | Unlimited dashboards on all tiers, including Community [V] | Free when self-hosted |
| **Datadog, Noz, OpenObserve** | AI can create dashboards by chat [V] | AI usage |
| **Others** | Not checked in depth | |

**What Nexovue could bring, once built, that is true:** a composed overview that needs no building
(`apps/web/src/features/overview/OverviewScreen.tsx:28`), with a builder for the rest. This is parity, not a selling point.

**False to claim:** anything suggesting its builder matches Grafana's. Dashboards are Grafana's core product, and a first
version will not match it. The design should say which narrow set of panels it covers.

## Capability 7: Deployment tracking

| Product | How it knows about a deploy | What it does with it | Limits |
|---|---|---|---|
| **Datadog** | Reserved `version` tag on telemetry [V] | Compares versions; detects faulty deployments automatically [V] | Needs tagging |
| **Sentry** | Releases, with commits attached [V] | Suspect commits, regressions, release health, deploy emails [V] | Strongest on commit attribution; suspect commits need commit data [V-s] |
| **Honeycomb** | Markers sent by API or CLI, type `deploy` [V] | Line on graphs [V] | No dedicated deploy feature [V] |
| **Grafana** | Annotations posted to an API, usually from CI [V-s] | Marks graphs | No first-party deploy integration found |
| **New Relic** | Change Tracking events; commit SHA leads to the pull request [V-s] | Autopilot uses them [V-s] | GitHub link in preview |
| **Coroot** | Reads Kubernetes metadata, so no CI setup [V] | Compares each version about 30 minutes after rollout [V] | Kubernetes Deployments only [V] |
| **SigNoz** | No native feature found. Noz can compare before and after a release [V-s] | | Noz is Cloud only |
| **OpenObserve, Dynatrace** | Not checked | | |

**What Nexovue has today:** signed deploy notifications naming service and version
(`apps/api/src/nexovue_api/deploys/schemas.py:33-39`, `deploys/signatures.py:1-15`) and the deploy preceding an error
group, labelled as order and not cause (`errorgroups/schemas.py:15-26`).

**What Nexovue could bring, once built, that is true:** deploy facts feeding the finding directly, with honest wording.
Sentry and Datadog are ahead on commit attribution and version comparison, and F28 should expect to be measured
against them. Coroot's no-setup approach only helps on Kubernetes, but it shows a rival removing the CI step; whether buyers care is unknown.

**False to claim:** "automatic deploy detection" (it needs a notification), or that a deploy "caused" a finding.

## Injection through telemetry: where everyone stands

- **No vendor-published injection results were found** for any product here. Absence from these searches is not
  proof that none exists.
- **Stated defences:** Aurora lists a NeMo input rail and SigmaHQ command signatures [V]. Rootly and incident.io
  describe redacting sensitive data before it reaches the model, which is about privacy, not injection [V, V-s].
  OpenObserve's AI SRE page makes no claim about it [V].
- **The most relevant paper, [LogJack](https://arxiv.org/html/2604.15368)** (a preprint, 42 hand-written payloads in
  cloud logs, 8 models, 5 trials per payload, action tools that intercepted rather than ran commands):
  - When told to act, verbatim execution ran from 0% (Claude Sonnet 4.6) to 86.2% (Llama 3.3 70B).
  - Telling models not to run fixes cut most to 0%, but Llama 3.3 70B still ran them 30% of the time.
  - Claude Sonnet 4.6 still ran dangerous commands in 31 of 160 active trials. The verbatim figure above is not a
    clean bill of health.
  - Input classifiers almost all missed payloads in log format: Azure 1 of 32, GCP 0 of 32, DeBERTa 1 of 32.
  - The author calls the rates upper bounds, since real agents have narrower tools. The dangerous-command detector was
    regex-based and unvalidated.

**What this means for Nexovue**

1. **Input filters should not be the main defence.** That supports the plan's design: no write permission for the
   part reading telemetry.
2. **The local-model option may be the more exposed one.** One model in one paper is thin evidence, but it cuts against
   the instinct that local is the safer choice. The injection suite must run on both AI options, and any local model
   Nexovue documents must pass it.
3. **Publishing the suite and its results would be new ground** among products checked. It should be a design goal of
   Stages 2, 4 and 5.

## What it costs, side by side

*Arithmetic from the list prices above. These are not quotes, and real bills vary with usage.*

| Example | Product | Rough monthly cost |
|---|---|---|
| 100 AI investigations | Datadog | $650 to $845 in credits (6.5 each, on top of hosts) |
| 5 people using AI | Grafana Pro | $19 + 2 × $20 = $59, before extra tokens and telemetry |
| 10 developers with 2 or more pull requests | Sentry Seer | $400, on top of the Sentry plan |
| 10 responders | Rootly, incident response only | $200, AI SRE by quote |
| 10 responders | incident.io Pro | $250, on-call and AI extra |
| 10 responders | Datadog Incident Management | $300 |
| Self-hosted, up to 50 GB/day | OpenObserve Enterprise | $0 licence, plus own servers and model bill |
| Self-hosted | Aurora, HolmesGPT | $0 licence, plus own servers and model bill |
| Self-hosted | Nexovue | $0 licence, plus own servers and model bill (`docs/product/vision.md:233`) |
| 10 services, hosted | Nexovue Pro | $60 (`vision.md:157-163`). AI on hosted is undecided, plan question 1 |

**Nexovue's real AI cost to a customer is unmeasured.** Nobody has run its chain on either AI option. The vision notes
that inference cost "could still break the unit" (`vision.md:199`). That matters only for the hosted service, and the
founder has not decided that yet.

## What Nexovue brings to the table

**True when the chain ships, if the designs hold**

1. The whole chain in one AGPL product, on the customer's server: storage, detection, explanation, case, chat, draft
   fix. OpenObserve keeps AI and incidents in Enterprise. SigNoz keeps its assistant in Cloud and lists anomaly
   detection as absent from Community [V]. Grafana needs a Cloud stack.
2. Detection that stays useful with AI off, enforced by a test.
3. Pricing that does not rise with people.
4. Published fault scores and injection results, if the founder agrees to publish them.

**True today**

- AGPL source, free self-hosting (`docs/product/vision.md:231-233`).
- Read-only MCP server, with the injection gap above still to close before leaning on it.
- Signed deploy records and honest "before, not because" wording.
- Alerts to email, Slack, Teams, Discord, PagerDuty and webhooks (`channels.py:34-35`).

**Where rivals are plainly ahead**

- Maturity, customers and scale. Nexovue is pre-deployment with no customer.
- Grafana on dashboards; Sentry and Datadog on tying problems to code; incident.io and Rootly on running an incident.
- HolmesGPT on data source breadth. Teams already on Prometheus or Datadog may add an agent rather than change
  platforms. **This is the competitor most likely to be missed:** free agents bolted onto an existing stack.
- Security paperwork. incident.io states SOC 2; Nexovue's position was not checked here.

## What this means for the Phase 2 feature documents

| Feature | Implication |
|---|---|
| **F28, F3 Explain** | Treat evidence links and "inconclusive" as the entry fee. Publish scores. Expect comparison with Sentry on commits |
| **F22 Track** | Free self-hosted case management is the gap. Do not drift into on-call or status pages |
| **F4 Ask** | Start from the existing MCP server. Close the unsanitized log path first. Add findings to its tools |
| **F5 Fix** | The write split is the point of difference. State it in docs, with tests |
| **F19 SLOs** | A clear gap against SigNoz. Keep it on the free self-hosted product, with no small count limit |
| **F20 Dashboards** | Aim for parity, and say what is out of scope |

## Decisions taken

*Recommended by the authors, founder may overrule.*

| Decision | Options | Recommendation and why |
|---|---|---|
| Stated answer to plan question 3 | (a) Close it. (b) Answer it, and keep the listed unknowns open | **(b).** The main question is answered from vendors' own pages. Four conflicts need a test or a vendor reply |
| Public selling line | (a) Keep "all on your own server" as a differentiator. (b) Use it only with the narrower claim | **(b).** The founder's selling reason stands as the reason. The wording of any public claim must carry "open-source edition" and "all in one product", and name local-model limits |
| Naming rivals in public | (a) Name them with evidence. (b) No names | **(b) for now.** These facts change monthly, and several are unresolved. Re-check before any comparison page |

## Founder decisions needed

None new from this research. Plan question 1 (AI on the paid hosted service) stays with the founder, and this
document adds one input: hosted rivals charge per seat, per token or per credit, which sits awkwardly with the
"never per seat, bill has a ceiling" promises. **Recommendation:** decide it before hosting opens, as the plan says,
with measured AI costs in hand rather than these list prices.

## Sources

**Datadog** · [Price list](https://www.datadoghq.com/pricing/list/) · [Pricing, AI credits](https://www.datadoghq.com/pricing/?product=ai-credits) · [AI credits docs](https://docs.datadoghq.com/account_management/billing/ai_credits/) · [Bits Investigation](https://docs.datadoghq.com/bits_ai/bits_ai_sre/) · [Bits Remediation](https://docs.datadoghq.com/bits_ai/bits_remediation/) · [Bits Code](https://docs.datadoghq.com/bits_ai/bits_code/) · [SLOs](https://docs.datadoghq.com/service_level_objectives/) · [Deployment tracking](https://docs.datadoghq.com/tracing/services/deployment_tracking/)

**Grafana** · [Assistant self-managed](https://grafana.com/docs/grafana/latest/ai-tools/grafana-assistant/self-managed/) · [On-prem announcement](https://grafana.com/whats-new/2026-04-21-grafana-assistant-becomes-available-on-prem/) · [Assistant billing](https://grafana.com/docs/grafana-cloud/platform/pricing-and-usage/assistant/) · [Pricing](https://grafana.com/pricing/) · [SLO](https://grafana.com/docs/grafana-cloud/alerting-and-irm/slo/) · [OnCall open-source notice](https://grafana.com/docs/oncall/latest/set-up/open-source/) · [Graphite annotations](https://grafana.com/docs/grafana/latest/datasources/graphite/annotations/)

**SigNoz** · [AI overview](https://signoz.io/docs/ai/overview/) · [MCP server](https://signoz.io/docs/ai/signoz-mcp-server/) · [Pricing](https://signoz.io/pricing/) · [Repository](https://github.com/signoz/signoz) · [Issue #658](https://github.com/SigNoz/signoz/issues/658) · [Noz changelog](https://signoz.io/changelog/2026-06-18-noz-signozs-ai-teammate-now-available-to-all-cloud-users-df6noereaben2w8ov498srbe/) · [Noz deploy use case](https://signoz.io/docs/ai/use-cases/noz-incident-triage/)

**OpenObserve** · [AI SRE](https://openobserve.ai/ai-sre.md) · [SRE agent setup](https://openobserve.ai/docs/enterprise-setup/sre-agent/) · [Incidents](https://openobserve.ai/docs/user-guide/analytics/incidents/) · [Alert-based SLOs](https://openobserve.ai/docs/user-guide/analytics/slos/alert-based-slos/) · [Editions](https://openobserve.ai/downloads/) · [Pricing](https://openobserve.ai/pricing/)

**New Relic** · [Autopilot](https://docs.newrelic.com/docs/agentic-ai/sre-agent/overview/) · [Connect to GitHub](https://docs.newrelic.com/docs/agentic-ai/autopilot/connect-to-github/) · [Pricing](https://newrelic.com/pricing) · [Usage plan](https://docs.newrelic.com/docs/licenses/license-information/usage-plans/new-relic-usage-plan/) · [Agentic integrations](https://docs.newrelic.com/docs/agentic-ai/agentic-integration/introduction-agentic-ai/)

**Honeycomb** · [Pricing](https://www.honeycomb.io/pricing) · [2026 Pro changes](https://docs.honeycomb.io/get-started/honeycomb/2026-pro-plan-changes) · [MCP tools](https://docs.honeycomb.io/integrations/mcp/tools) · [Markers](https://docs.honeycomb.io/working-with-data/markers)

**Dynatrace** · [AI FAQ](https://docs.dynatrace.com/docs/dynatrace-intelligence/copilot/copilot-faq) · [Rate card](https://www.dynatrace.com/pricing/rate-card/)

**Sentry** · [Pricing docs](https://docs.sentry.io/pricing/) · [Autofix](https://docs.sentry.io/product/ai-in-sentry/seer/autofix/) · [Self-hosted](https://develop.sentry.dev/self-hosted/) · [Releases](https://docs.sentry.io/product/releases/)

**incident.io** · [Pricing](https://incident.io/pricing) · [AI SRE](https://incident.io/ai-sre)

**Rootly** · [Pricing](https://rootly.com/pricing) · [AI SRE guide](https://rootly.com/ai-sre-guide) · [AI SRE docs](https://docs.rootly.com/ai/ai-sre/overview) · [Custom MCP](https://docs.rootly.com/ai/connectors/custom)

**Self-hosted and open-source** · [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) · [HolmesGPT Ollama docs](https://robusta-dev.github.io/holmesgpt/ai-providers/ollama) · [Aurora](https://github.com/Arvo-AI/aurora) · [OpenSRE](https://github.com/Tracer-Cloud/opensre) · [Keep](https://github.com/keephq/keep) · [Coroot AI](https://docs.coroot.com/ai/overview) · [Coroot editions](https://coroot.com/editions) · [Coroot deployment tracking](https://docs.coroot.com/inspections/deployment-tracking) · [Pyrra](https://github.com/pyrra-dev/pyrra) · [Sloth](https://github.com/slok/sloth) · [Elastic AI Assistant](https://www.elastic.co/docs/solutions/observability/ai/observability-ai-assistant) · [Elastic 8.19 guide](https://www.elastic.co/guide/en/observability/8.19/obs-ai-assistant.html)

**Injection** · [LogJack](https://arxiv.org/html/2604.15368)

**Third party, used only to spot the change in Datadog's pricing** · [PricingSaaS diff](https://pricingsaas.com/companies/datadog.network-monitoring/diffs/2026W24) · [Better Stack comparison](https://betterstack.com/community/comparisons/bits-ai-sre-alternatives/)

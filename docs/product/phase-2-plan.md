# Phase 2 plan: the incident chain

> **Status:** Approved by the founder, 2026-10-30. Each feature still goes through its own specification,
> design and plan gates. Nothing here is built.

## Decided by the founder, 2026-10-30

- **Scope.** Phase 2 is the chain in [`vision.md`](vision.md) §4, built completely, to production level.
- **Timing.** There is no release date. Building proves one path at a time (`ADR-0019`).
  - *Changed 2026-11-01:* **Stage 1, detection with its findings and alerts, ships on its own**, ahead of the
    rest of the chain (F6 spec, D32). Detection and alerts already work with AI off, so Stage 1 can be judged
    by real installs before Stages 2 to 6 are built.
  - Each later stage is still specified and designed before its code is written.
- **Selling reason.** "It finds the problem, explains it and suggests the fix, all on your own server, so your
  data stays with you." See *Risks*: this is not unique.
- **Where AI runs for a self-hosted install.** The customer chooses: their own AI account, or a model on their
  own server. nexovue runs no AI service of its own for them.
- **Test bed.** The OpenTelemetry Demo. See *Risks*: it cannot prove the false-alarm rate on real traffic.
- **Left out.** The AWS role flow and data archival move to Later: neither is part of the chain. Session replay
  stays out, for its privacy and legal cost.
- **Around the chain.** SLOs (F19) and the dashboard builder (F20) are in Phase 2, built after the chain
  ([research](../research/phase-2-scope-2026-10-30.md); it is suggestive, not proof).

## What exists

| Link | Exists today | Missing |
|---|---|---|
| Detection (F6) | Per-minute rollups written before sampling (`endpoint_stats_1m`); the threshold evaluator's waits, low-traffic rule and saved position (`scheduler/alerts/thresholds.py`); decision 0013: statistical | Baselines, detector, sensitivity, feedback |
| Findings (F1, F2) | Screen, ordering and actions, on an empty feed by design (`findings/api.ts`) | Findings table, API, live updates, detail page |
| Explanation (F3, F28) | Deploy markers, error-to-deploy links, the service map; decision 0014: a provider-neutral AI interface; masking before storage | Correlation, AI interface, comparison screen |
| Cases (F22) | Alert incidents: one per breach, flap guard, resolve | Lifecycle, timeline, GitHub and Jira |
| Alerts (F21) | Email, Slack, Teams, Discord, PagerDuty, webhooks | A detection condition. WhatsApp and SMS stay dropped (D6) |
| Chatbot (F4) | pgvector (decision 0004); the recorded split: knowledge is embedded, telemetry is queried live | Everything else |
| Fix (F5) | Nothing | Everything; a GitHub connection shared with F22 |

`docker-compose.yaml` reserves an `insight` service and Ollama, but `apps/insight` does not exist, and `ADR-019`
ships exactly two images. Where the AI code runs is a design decision for Stage 2.

## Order

Progress, the order of work and how lanes run in parallel: [`phase-2-tracker.md`](phase-2-tracker.md).

Each stage's documents (spec, design and plan) were approved by the founder on 2026-11-02 ([shared foundation](phase-2-foundation.md); [features index](../features/README.md)). Each stage depends on the one before it. **Detection and alerts must work with AI turned off.** An alert never
waits for AI: it goes out when detection fires, and the explanation joins it later.

### Stage 1: Detect (F6, F1, F2's evidence)

**F6 documents:** [spec](../features/anomaly-detection/spec.md), [design](../features/anomaly-detection/design.md), [plan](../features/anomaly-detection/plan.md).

- A service getting slower, failing more, or losing traffic, against its own daily and weekly normal.
- *Done when:* new services and low traffic stay quiet and say why; an outage cannot teach a false normal; findings
  update in place and close themselves; users can give false-alarm feedback, set sensitivity and mute; the
  injected faults below are caught, except those that move none of its signals, which are recorded as gaps
  (founder, 2026-10-30; F6 spec, D3).

### Stage 2: Explain (F28, F3)

**F28 documents:** [spec](../features/deployment-release-tracking/spec.md), [design](../features/deployment-release-tracking/design.md), [plan](../features/deployment-release-tracking/plan.md). **F3 documents:** [spec](../features/ai-insights/spec.md), [design](../features/ai-insights/design.md), [plan](../features/ai-insights/plan.md).

- Correlation from stored facts first (deploys, errors, dependencies), then AI wording, through one interface for
  both AI options.
- *Done when:* every claim links to its data; causes are labelled as likely; a missing or failing AI, and its
  cost limits, leave the finding intact; each injected fault's stated cause is scored.

### Stage 3: Track (F22)

**F22 documents:** [spec](../features/case-management/spec.md), [design](../features/case-management/design.md), [plan](../features/case-management/plan.md).

- A finding opens a case by growing the finding itself, not by adding a second record (F6 D1, ADR-022).
- *Done when:* lifecycle and timeline work; GitHub Issues and Jira create and stay in sync; on-call stays with
  PagerDuty (never built here).

### Stage 4: Ask (F4)

**F4 documents:** [spec](../features/ai-chatbot/spec.md), [design](../features/ai-chatbot/design.md), [plan](../features/ai-chatbot/plan.md).

- Live, tool-based queries first, then resolved findings, notes and runbooks through pgvector.
- *Done when:* it reads only what the asking user may see, cites every answer, and passes the injection tests.

### Stage 5: Fix (F5)

**F5 documents:** [spec](../features/ai-solution/spec.md), [design](../features/ai-solution/design.md), [plan](../features/ai-solution/plan.md).

- A suggested fix on the finding, then a draft pull request. Where Nexovue cannot read the code, a general suggestion with example code, from the finding's facts only (rung 2b, founder's decision 2026-11-02).
- *Done when:* a person always approves; the part reading telemetry cannot itself write; repository access is
  narrow and audited.

### Stage 6: Around the chain (F19, F20)

**F19 documents:** [spec](../features/slo-tracking/spec.md), [design](../features/slo-tracking/design.md), [plan](../features/slo-tracking/plan.md). **F20 documents:** [spec](../features/dashboards/spec.md), [design](../features/dashboards/design.md), [plan](../features/dashboards/plan.md).

- SLO targets and the dashboard builder, each through its own specification.
- **One exception to the order, approved 2026-11-02:** F20's Step 1 alone, moving `/` to the Findings feed, ships as soon as F6 merges. It carries no dashboard code, and it is the timing of the recorded front-door decision (FD15), not a new one ([F20 spec](../features/dashboards/spec.md), D58).

## AI safety, across Stages 2, 4 and 5

**Telemetry is attacker-controlled text.** Anyone who can reach a customer's public endpoint can put words into
its logs and traces. Published work shows instructions planted this way steering AI operations agents, including
to running code, and that read-only access limits the damage without stopping the injection.

- Untrusted fields are sanitized or replaced before AI reads them.
- The AI that reads telemetry holds no permission to write or execute.
- Every AI link has an injection test suite in the gate.
- AI is off until the customer turns it on and chooses where it runs, because the first option sends their data to
  their AI provider.

## Test bed

The demo's fault flags give known answers: `paymentFailure`, `paymentUnreachable`, `cartFailure`, `adFailure`,
`productCatalogFailure`, `recommendationCacheFailure`, `adHighCpu`, `emailMemoryLeak`, `kafkaQueueProblems`,
`imageSlowLoad`, `intlShippingSlowdown`.

- **Caught faults:** each flag is injected repeatedly and scored for caught, time to catch and stated cause.
- **False alarms:** the demo runs with no fault, and every finding counts as false.
- **Starting bar:** at most 1 false finding per service per week. It is a guess until measured, and is set by the
  founder from the first results.
- **Where:** on Oracle Cloud, after Phase 2 is deployed (open question 2). AI runs use free tiers or a local model,
  on synthetic data only, with no paid model calls ([foundation, *Founder decisions*, 4](phase-2-foundation.md#founder-decisions)).

## Risks

- **The demo is not real traffic.** Its load is steady and artificial, so a pass says little about a customer's false
  alarms. This is accepted by the founder's decision; expect tuning after the first real customers.
- **Daily and weekly patterns need shaped traffic.** The flat load generator has none, so the test needs traffic
  shaped over time through `loadGeneratorVUs` or `tools/loadgen`.
- **A weeks-long run needs a machine that stays on.** The founder's Mac is not one, and the demo competes for its
  11.7 GiB. The founder decided on 2026-11-02 to rent nothing now and run it on Oracle Cloud after Phase 2 is
  deployed (open question 2). So the false-alarm bar below is not measured before then, and nothing may claim it.
- **The selling reason is not unique.** OpenObserve's own blog claims a self-hosted AI SRE with bring-your-own and
  self-hosted models. SigNoz and Grafana appear to keep their AI in the cloud, by a competitor's account and a
  fork's README. All of this needs checking against their own documentation before anything is published.
- **Local AI may be weaker.** Quality must be proven on both options, and a local model needs hardware the 16 GB
  install sizing does not include. Decision 0047 flags this for the dev machine too.
- **Tested only on free models (2026-11-02).** With no paid model calls, the cloud option is tested on free tiers,
  which have low limits and may train on what is sent, so only synthetic data goes. OpenAI, Anthropic and Bedrock have
  no free API tier, so they stay "not yet tested" until someone tests them.
- **Baselines need history.** Retention must hold enough weeks of rollups on every plan, and storage cost is
  unmeasured.

## Open

| # | Question | Who | When |
|---|---|---|---|
| 1 | AI on the paid hosted service | Founder | Before hosting opens |
| 2 | The test server's price. **Answered 2026-11-02:** no rented test server now. Long runs and measurements happen on Oracle Cloud once the Phase 2 features are built and deployed ([foundation, *Founder decisions*, 9](phase-2-foundation.md#founder-decisions)). So Stage 1's false-alarm run, and every other weeks-long run, waits until then. Oracle's free allowance (2 Arm CPUs, 12 GB) is below the 16 GB minimum, so the shape to measure on comes back to the founder at deployment | Founder | At deployment |
| 3 | Competitors' AI, from their own documentation. **Answered 2026-11-02, Proposed** ([research](../research/phase-2-competitors-2026-11.md), read through summarizing tools). The Risks line above holds, with limits: OpenObserve's AI is in its Enterprise licence, and its setup guide does not list a local model; SigNoz's assistant is Cloud, though its MCP server can run self-hosted; Grafana's backend stays in Grafana Cloud. It also missed overlaps: Aurora, HolmesGPT and OpenSRE run on the customer's machine with their own models. **Still open before naming any of them:** OpenObserve's local path, Elastic's tier and Rootly's own-key status; Coroot's editions were read again by F3, and OneUptime's fix path by F5 | Research, then the founder | Before any public claim |

## Sources

- [LogJack](https://arxiv.org/html/2604.15368)
- [When AIOps become "AI Oops"](https://arxiv.org/html/2508.06394v1)
- [Adversarial telemetry evaluation, NeurIPS 2026](https://neurips.cc/virtual/2026/168153)
- [Straiker: AI SRE agent prompt injection](https://www.straiker.ai/blog/ai-sre-agent-prompt-injection-kubernetes-ransomware)
- [OpenObserve vs SigNoz](https://openobserve.ai/blog/openobserve-vs-signoz/)
- [SigNoz fork README](https://github.com/Misfits09/signoz)
- [Splunk: root cause to pull request](https://www.splunk.com/en_us/blog/observability/ai-sre-in-splunk-observability-cloud-can-remediate-incidents-with-claude-managed-agents.html)
- [Seasonal Hybrid ESD](https://arxiv.org/pdf/1704.07706)

# Phase 2 plan: the incident chain

> **Status:** Awaiting the founder's approval. Each feature still goes through its own specification, design
> and plan gates. Nothing here is built.

## Decided by the founder, 2026-10-30

- **Scope.** Phase 2 is the chain in [`vision.md`](vision.md) §4, built completely, to production level.
- **Timing.** There is no release date. Every link is specified and designed before feature code is written, and
  the chain is released as a whole. Building still proves one path at a time (`ADR-0019`).
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

Each stage depends on the one before it. **Detection and alerts must work with AI turned off.** An alert never
waits for AI: it goes out when detection fires, and the explanation joins it later.

### Stage 1: Detect (F6, F1, F2's evidence)

- A service getting slower, failing more, or losing traffic, against its own daily and weekly normal.
- *Done when:* new services and low traffic stay quiet and say why; an outage cannot teach a false normal; findings
  update in place and close themselves; users can give false-alarm feedback, set sensitivity and mute; the
  injected faults below are caught.

### Stage 2: Explain (F28, F3)

- Correlation from stored facts first (deploys, errors, dependencies), then AI wording, through one interface for
  both AI options.
- *Done when:* every claim links to its data; causes are labelled as likely; a missing or failing AI, and its
  cost limits, leave the finding intact; each injected fault's stated cause is scored.

### Stage 3: Track (F22)

- A finding opens a case by growing the existing incident, not by adding a second record.
- *Done when:* lifecycle and timeline work; GitHub Issues and Jira create and stay in sync; on-call stays with
  PagerDuty (never built here).

### Stage 4: Ask (F4)

- Live, tool-based queries first, then resolved findings, notes and runbooks through pgvector.
- *Done when:* it reads only what the asking user may see, cites every answer, and passes the injection tests.

### Stage 5: Fix (F5)

- A suggested fix on the finding, then a draft pull request.
- *Done when:* a person always approves; the part reading telemetry cannot itself write; repository access is
  narrow and audited.

### Stage 6: Around the chain (F19, F20)

- SLO targets and the dashboard builder, each through its own specification.

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

## Risks

- **The demo is not real traffic.** Its load is steady and artificial, so a pass says little about a customer's false
  alarms. This is accepted by the founder's decision; expect tuning after the first real customers.
- **Daily and weekly patterns need shaped traffic.** The flat load generator has none, so the test needs traffic
  shaped over time through `loadGeneratorVUs` or `tools/loadgen`.
- **A weeks-long run needs a machine that stays on.** The founder's Mac is not one, and the demo competes for its
  11.7 GiB. It needs a small server, at a cost not yet priced.
- **The selling reason is not unique.** OpenObserve's own blog claims a self-hosted AI SRE with bring-your-own and
  self-hosted models. SigNoz and Grafana appear to keep their AI in the cloud, by a competitor's account and a
  fork's README. All of this needs checking against their own documentation before anything is published.
- **Local AI may be weaker.** Quality must be proven on both options, and a local model needs hardware the 16 GB
  install sizing does not include. Decision 0047 flags this for the dev machine too.
- **Baselines need history.** Retention must hold enough weeks of rollups on every plan, and storage cost is
  unmeasured.

## Open

| # | Question | Who | When |
|---|---|---|---|
| 1 | AI on the paid hosted service | Founder | Before hosting opens |
| 2 | The test server's price. The founder accepts the need for one (2026-10-30); the price comes back for approval before anything is rented | Founder | Before Stage 1's false-alarm run |
| 3 | Competitors' AI, from their own documentation | Research | Before any public claim |

## Sources

- [LogJack](https://arxiv.org/html/2604.15368)
- [When AIOps become "AI Oops"](https://arxiv.org/html/2508.06394v1)
- [Adversarial telemetry evaluation, NeurIPS 2026](https://neurips.cc/virtual/2026/168153)
- [Straiker: AI SRE agent prompt injection](https://www.straiker.ai/blog/ai-sre-agent-prompt-injection-kubernetes-ransomware)
- [OpenObserve vs SigNoz](https://openobserve.ai/blog/openobserve-vs-signoz/)
- [SigNoz fork README](https://github.com/Misfits09/signoz)
- [Splunk: root cause to pull request](https://www.splunk.com/en_us/blog/observability/ai-sre-in-splunk-observability-cloud-can-remediate-incidents-with-claude-managed-agents.html)
- [Seasonal Hybrid ESD](https://arxiv.org/pdf/1704.07706)

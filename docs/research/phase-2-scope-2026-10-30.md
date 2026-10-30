# Phase 2 scope: SLOs, the dashboard builder, session replay (2026-10-30)

> **Type:** Research snapshot for [`phase-2-plan.md`](../product/phase-2-plan.md), open question 1. **It does not
> settle the question.** No source measured what buyers refuse a tool over, and nobody was interviewed. Most
> competitor facts come from vendor-written comparisons, not product documentation.

| Feature | Evidence | Strength | Recommendation |
|---|---|---|---|
| **SLOs** | Grafana's 2026 survey (1,363 responses, promoted by Grafana): roughly three-quarters use SLOs "in some capacity", which includes investigating and proofs of concept. In its 2025 survey, 29% of SREs and 18% of developers reported production use. OpenObserve publishes SLO-based alerting; SigNoz's guide does SLOs through dashboards | Medium: wide interest, thinner production use, and no evidence it decides a purchase | Include, after the chain |
| **Dashboard builder** | Every peer named in the comparisons offers dashboards, including Uptrace, the small one. The only direct "mandatory" reading is one rfp.wiki page, already marked unverifiable in the September research | Weak: inferred from peers | Include, after the chain |
| **Session replay** | Better Stack, OpenObserve and Highlight are described as having it; SigNoz is described as not having it, by a competitor. No adoption survey found. Compliance sources agree no tool is compliant out of the box: it needs consent, notice, masking, short retention and restricted access. They also report US wiretap-style claims against sites using it without consent | Weak on demand; clear on legal cost | Leave out of Phase 2 |

## What else the survey says about the chain

- 95% say it matters that AI shows its reasoning (somewhat important through critical).
- 77% see value in AI acting on its own, but 15% do not trust it to yet, and it drew the most skepticism.
- Top concerns: complexity and overhead 38%, signal-to-noise 34%, cost 31%.
- Selection criteria: cost 65%, ease of use 49%.

These back the plan's rules that evidence is shown, that a person approves a fix, and that false alarms are
measured. Grafana sells in this market, so read them as a signal rather than as proof.

## Not answered

- What makes a small team reject a tool. It remains unanswered after three passes, and needs conversations with buyers.
- Whether SigNoz, Uptrace and Better Stack have native SLOs. Their own documentation was not confirmed.

## Sources

- [Grafana Observability Survey 2026](https://grafana.com/observability-survey/2026/)
- [Grafana survey 2025 takeaways](https://grafana.com/blog/observability-survey-takeaways/)
- [SLO-based alerting in OpenObserve](https://openobserve.ai/blog/slo-based-alerting.md)
- [SigNoz: SLO monitoring guide](https://signoz.io/guides/slo-monitoring/)
- [Better Stack vs SigNoz](https://betterstack.com/community/comparisons/better-stack-vs-signoz/)
- [SigNoz vs Uptrace vs Highlight](https://devopsboys.com/blog/signoz-vs-uptrace-vs-highlight-2026)
- [10 best open-source observability platforms](https://www.parseable.com/blog/ten-best-open-source-observability-platforms-2026)
- [Session recording under GDPR](https://logreplay.com/blog/session-recording-gdpr-compliance/)
- [Session replay disclosure](https://captaincompliance.com/education/session-replay-technology-disclosure-is-required/)

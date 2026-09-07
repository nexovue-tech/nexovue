# 0106 — Network telemetry is accepted and never collected, and SIEM is out of scope

- **Status:** Accepted
- **Date:** 2026-09-07
- **Closes:** the two categories [0025](0025-phase-1-signal-and-door-coverage.md) left as
  *"unplaced — open"*.

## Context

`vision-and-scope.md` lists eleven product categories. Nine have a phase. Two never got one:

| | category | the doc's own note |
|---|---|---|
| 10 | Network monitoring | *"eBPF kernel-level agents, not OTel"* |
| 11 | SIEM / security | *"evaluate later, may stay out of scope"* |

ADR 0025 recorded them as open on 2026-08-25 and `phase-1-plan.md` §8 asks the question again:
*"Park it explicitly, or accept the contradiction deliberately?"* Two weeks and a phase of work
later neither has moved, which is how a category with no home quietly becomes a commitment nobody
made.

**The contradiction is real and it is with the founding decision.** `vision-and-scope.md` §1 says
collection is *"All via OpenTelemetry (OTLP protocol, community SDKs — we do **not** build
collection agents)"*, and the 2026-08 decision row repeats it. Network monitoring as Datadog NPM
does it is a kernel agent on every host. There is no version of category 10 that we collect and
that decision survives.

**What changed since 0025 is who else collects it.** OpenTelemetry eBPF Instrumentation (OBI) is
the merged Grafana Beyla line and is working toward a 1.0, and the pattern the project itself
recommends is a **split**: OBI for L7 application traces with `network.enabled: false`, and Cilium
Hubble or Tetragon for network flows and kernel-level events
([OBI 2026 goals](https://opentelemetry.io/blog/2026/obi-goals/),
[OBI docs](https://opentelemetry.io/docs/zero-code/obi/)). All of them export OTLP.

**SIEM's pull is real too, and it is commercial rather than technical.** Roughly 80% of what flows
through a SIEM pipeline is what flows through an observability pipeline — logs, cloud events,
identity signals, infrastructure metrics, traces — and the consolidation is happening at the top of
the market, with Palo Alto Networks closing its acquisition of Chronosphere in February 2026. The
data overlap is genuine. What does not overlap is the product: detection content, threat
intelligence, retention mandates, and the certifications a buyer requires before they will send you
a security log.

## Decision

**Network telemetry is accepted, never collected.** We build no eBPF agent, no kernel module and no
host agent of any kind — the founding decision stands unchanged. A customer who runs OBI, Beyla,
Hubble or Tetragon and points its OTLP exporter at door 1 has their spans and metrics land with no
work from us, because door 1 does not care what produced an OTLP payload. **That is the entire
support commitment**, and it is deliberately smaller than "we support network monitoring": no
screen, no semantic-convention mapping, no claim in marketing.

**SIEM is out of scope for Phase 1 and Phase 2, and is not re-opened as a screen.** The nearest
thing we will ever build is export — getting a customer's telemetry out to the SIEM they already
have. Not detection, not correlation rules, not audit-log analysis as a product.

**Both are struck from the open list rather than deferred again.** "Evaluate later" has now been
the answer twice; a third would be the same as a decision, taken by not deciding.

## Alternatives considered

**Build an eBPF agent.** It contradicts the founding decision, and the operational cost is the
argument that finishes it: a kernel-level agent is a support obligation across kernel versions,
distributions and container runtimes, carried by one developer. The 2026 answer is also simply that
we would be building a worse OBI.

**Defer both again to Phase 2 scoping.** What has happened twice. Deferral is only honest when
something is expected to change the answer, and nothing about Phase 2 does.

**A security screen over the telemetry we already hold** — failed logins, unusual egress, audit
trails. Cheap to build and dishonest to ship: a SIEM without detection content is a filter with a
security-coloured header, and the customers who ask for SIEM are asking for the content.

**Map OBI's network attributes onto the service map now.** Premature. Nobody has run OBI against
this receiver, and this ADR claims only where the data would come from, not that it works. When
somebody runs it, that is a measurement and a plan row, not this decision.

## Consequences

**We cannot answer "which services talk to which" below L7.** The service map is derived from spans
and stays that way, so a dependency that carries no instrumented call is invisible. That is a real
gap in the product and it is now a chosen one.

**A customer asking for SIEM is a lost deal, and we accept it.** Saying so early is worth more than
the option value of an evaluation nobody schedules.

**The OBI route is untested.** No claim is made that a customer's OBI export renders usefully on
any screen we have — only that door 1 accepts OTLP regardless of producer. The first person to try
it will find something, and it will not be nothing.

**`vision-and-scope.md` and `phase-1-plan.md` §8 both have to change**, because both currently
describe these as open. An ADR that leaves the question phrased as open in two other documents has
decided nothing.

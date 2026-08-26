# 0053 — nexovue monitors nexovue, with an out-of-band escape hatch

- **Status:** Accepted
- **Date:** 2026-08-26
- **Relates to:** P1-26 (self-instrumentation, M2)

## Context

Every nexovue service exports its own telemetry into nexovue. P1-26 records this
in one line — "we are customer zero" — with no downside noted, which is the part
worth fixing: the decision is right, and it has a failure mode that bites at the
worst possible moment.

**Why do it.** We find our own bugs first: an unreadable trace waterfall is
noticed while building it, not reported by a customer. It exercises the customer
onboarding path daily — same SDK, same two environment variables — so "value in
ten minutes" is tested continuously rather than once at P1-54. It is the only
realistic data we have before customers exist. And it proves a claim we sell: if
our own logs cannot join our own traces, the feature is not real.

**Why it is dangerous.** When the platform is down, the tool that would explain
why is inside the outage. Worse, an incident amplifies itself: errors produce
telemetry about errors, which is load, which produces more errors. And our own
telemetry sits beside customer data, so the tenancy invariant has to hold for us
too.

## Decision

Self-instrument everything, subject to three rules that make the circular
dependency survivable.

**1. The alert that nexovue is down originates outside nexovue.** Self-monitoring
answers *why*; something external must answer *that*. Until door 2 probes run
from a host that is not the platform, an external uptime service fills the gap.
A platform that can only page itself cannot page you when it is the thing that
failed.

**2. Structured logs go to stdout independently of the OTel pipeline.** This is
why services do both. It looked like duplication; it is the fallback for when
Kafka or ClickHouse is the broken component. Container logs survive a pipeline
that does not.

**3. Self-telemetry is its own organization and project, rate-limited like any
tenant.** A self-inflicted error storm consumes its own quota rather than the
ingest path customers share, and the isolation invariant is enforced against us
on the same code path as everyone else.

## Consequences

- Debugging a platform outage starts from container logs and external checks,
  not from the findings feed. That order should be in the runbook (P1-51), and
  is the opposite of the order used for every other incident.
- The self-telemetry tenant is real data in production tables — it counts toward
  storage, appears in usage metering, and must be excluded from any customer-
  facing aggregate.
- Rate limiting our own telemetry means we can lose our own observability under
  load. That is the correct trade: customer ingest is what is being protected.
- Instrumentation bugs are self-inflicted incidents. A bad span attribute in a
  hot path is a load problem, not just a data problem.

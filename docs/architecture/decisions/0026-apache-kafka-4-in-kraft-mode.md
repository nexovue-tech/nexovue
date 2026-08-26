# 26. Buffer = Apache Kafka 4.x in KRaft mode

- **Status:** Accepted
- **Supersedes:** [0020](0020-redpanda-replaces-kafka.md)
- **Date:** 2026-08-25

## Context

[0020](0020-redpanda-replaces-kafka.md) had switched the buffer to Redpanda for a lighter
single-host footprint. Research into both the licence and current benchmarks contradicted it on two
independent grounds.

## Decision

Apache Kafka 4.x in KRaft mode.

**Licence.** Redpanda Community Edition is BSL 1.1. Its Streaming/Queueing Service clause forbids
commercial offerings that let third parties directly or indirectly cause topic creation — an
unresolved ambiguity for a commercial SaaS, and one that would rule out per-tenant topics
permanently. Kafka is Apache 2.0 with no such question.

**Performance.** The "lighter, no JVM" rationale was ZooKeeper-era. Kafka 4.0 removed ZooKeeper
entirely; an April 2026 benchmark on identical hardware measured Kafka 4.2 in KRaft mode at higher
sustained throughput using 3× less memory than Redpanda, winning every `acks=all` test.

## Consequences

No licence question sits under the core of the pipeline, and per-tenant topics stay possible. Cost
accepted: JVM tuning is ours to own. `franz-go` producer and consumer code is identical either way,
so no application code changed.

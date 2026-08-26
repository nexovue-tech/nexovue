# 20. Redpanda replaces Kafka as the buffer

- **Status:** Superseded by [0026](0026-apache-kafka-4-in-kraft-mode.md) — reversed the same day
- **Supersedes:** [0002](0002-kafka-as-ingestion-buffer.md)
- **Date:** 2026-08-25

## Context

The buffer has to run in an on-premise single-host compose stack
([0015](0015-containers-with-on-premise-docker-compose.md)), alongside ClickHouse, Postgres, Redis
and every service. Kafka in that setting meant a JVM to size and KRaft or ZooKeeper to tune, on a
host that has neither memory nor attention to spare.

## Decision

Use Redpanda instead. It is Kafka API-compatible, so `franz-go` producer and consumer code is
unchanged, but it is a single binary with no JVM, no KRaft/ZooKeeper tuning and far lower memory.
Topic names and the partitioning strategy (by `trace_id`) are unchanged. Moving to real Kafka later
would be a config change, not a rewrite.

## Consequences

Much less memory and operational surface on the dev host, at no cost in application code — which is
what made this look right: the API compatibility meant the decision appeared reversible.

It was reversed the same day. Research turned up two things this reasoning had not weighed: the BSL
licence terms on Redpanda Community Edition, and that the "lighter, no JVM" premise was
ZooKeeper-era and no longer held for Kafka 4.x. See
[0026](0026-apache-kafka-4-in-kraft-mode.md).

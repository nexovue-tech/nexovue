# 8. Production language split — Go on the data plane, Python on the control plane

- **Status:** Accepted
- **Date:** 2026-08

## Context

The hot path (receive, buffer, assemble, write) is throughput- and memory-bound. The intelligent
path (insight engine, RAG, statistics) lives where the AI ecosystem is. One language for both means
losing one of the two.

## Decision

Split by plane.

**Go on the data plane.** Receiver: `net/http` (or chi) plus `google.golang.org/grpc`, serving
OTLP/HTTP (4318) and OTLP/gRPC (4317) from day one; official OTel protobuf/pdata for parsing;
`franz-go` or `confluent-kafka-go` as producer. Workers: `franz-go` consumers as small
single-purpose services — trace-assembler, log-writer, metrics-deriver — each independently
scalable, with in-memory trace buffers evicted on timeout, writing batched inserts to ClickHouse.

**Python on the control plane.** Backend API is FastAPI; Celery runs probes and pollers; the insight
engine is Python (correlation, LLM, stats/ML).

The boundary: Go writes Kafka and ClickHouse, Python reads ClickHouse and Postgres. The languages
never entangle.

## Consequences

5–10x throughput and low memory on the hot path, and Python's AI ecosystem where the intelligence
lives. Cost accepted: Go skill is required to work on the data plane, and two toolchains exist in
one repo.

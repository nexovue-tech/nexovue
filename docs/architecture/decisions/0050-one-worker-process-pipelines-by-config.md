# 0050 — One worker process, pipelines selected by config

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [0008](0008-go-on-the-data-plane-python-on-the-control-plane.md) in part — the "small single-purpose services" clause, not the language split

## Context

[0008](0008-go-on-the-data-plane-python-on-the-control-plane.md) specified the processing side as "small single-purpose services (trace-assembler, log-writer, metrics-deriver) each independently scalable". M0 built that literally: four binaries, four containers.

What it produced:

```
trace-assembler/main.go    11 lines
log-writer/main.go         11 lines   ← identical apart from a comment
metrics-deriver/main.go    11 lines      and a service-name string
edge-aggregator/main.go    11 lines
shared internal/         1,432 lines
```

Four containers reserving 640 MB each — around 2.5 GB of a 24 GB laptop — to run four copies of the same eleven lines. The scaling argument in 0008 is sound and remains true; it is a statement about production under load, and it was applied to a system with no load and no measurements.

Raised by Raj asking why the system needs twelve containers when the design reads as three.

## Decision

**One `worker` binary running every pipeline, with `WORKER_PIPELINES` selecting a subset.**

Pipelines keep their names — `trace-assembly`, `log-writing`, `metric-ingest` — because the names are what make splitting a deployment change rather than a rewrite. An unknown name fails at boot rather than silently not running.

**Amended 2026-08-29.** This record originally listed `metric-derivation` and `service-edges` as names too. Nothing ever started them: they were accepted by `WORKER_PIPELINES`, reported at boot as running, and referenced by no code outside their own constant block. A process asked for `metric-derivation` alone ran nothing and said it had. Both are stages inside `trace-assembly` and cannot be deployed apart from it today, so the names were removed rather than left claiming an affordance that did not exist. `metric-ingest`, added later for customer-sent OTLP metrics, does start a pipeline and is listed above.

One container in development. In production the same image runs as many times as needed with different selections, **when measurement justifies it**.

## Consequences

- Frees roughly 1.9 GB of container caps on the dev machine.
- One image to build and one container to restart when worker code changes, rather than four.
- Independent scaling stays available and stops being pre-paid. 0008's intent is preserved; only the timing changes.
- A pipeline that misbehaves now shares a process with three others, so an OOM takes all four down. Acceptable while Kafka holds the data and the workers are stateless — and it is the signal to split, not a reason not to collapse.
- The receiver stays a separate container regardless. That boundary is not about scaling: Kafka sits between them precisely so ingestion survives a worker restart or crash, which is invariant 2.

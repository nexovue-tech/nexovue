# Platform observability architecture

## Current verified behavior

Go and Python services emit structured logs with consistent fields including timestamp, level, service, environment, correlation ID, trace ID, span ID, organization ID, and project ID. The receiver and worker expose liveness/readiness behavior on internal admin ports.

The product's own telemetry pipeline derives request/error/latency and service-edge metrics from every span before sampling. This is product telemetry, not an implemented production monitoring/alerting platform for nexovue itself.

Every service answers `/healthz`, `/readyz` and `/metrics` on an internal port (ADR-021), and the `monitoring` profile runs Prometheus with nexovue's alert rules (`deploy/monitoring/rules/nexovue.yml`, held by `promtool` tests in `just check`). [`operations/monitoring.md`](../operations/monitoring.md) lists every metric and threshold. No label names a tenant or a project; where an operator needs one, the log names it.

Anomaly detection (F6) adds what an operator needs to see it stall, published by beat's leader from Postgres so a worker restart cannot reset it:

- the settled minute's age, `+Inf` before any;
- how many projects have had no completed detection run for 3 minutes, and the longest any has waited;
- `detection_events` rows of the last 15 minutes, by kind: the install-wide guard engaging, or a hold reaching its 30-minute limit.

Three alerts page on them: `NexovueDetectionStalled`, `NexovueProjectDetectionFailing` and `NexovueDetectionHeldByIntake`. The receiver's refusals now tell Kafka refusing (`broker_unavailable`, `NexovueReceiverKafkaUnavailable`) from the control plane failing (`unavailable`, `NexovueReceiverDependencyUnavailable`). [`operations/detection.md`](../operations/detection.md) is the runbook.

`just chaos` exercises broker/store failure under load and checks the acknowledged-data guarantee. `just load`, the OTel demo, live tests, and E2E tests provide additional verification paths. Alertmanager routes to nowhere until a receiver is named, and dashboards, SLOs, and operational ownership are not yet established.

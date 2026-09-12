# Platform observability architecture

## Current verified behavior

Go and Python services emit structured logs with consistent fields including timestamp, level, service, environment, correlation ID, trace ID, span ID, organization ID, and project ID. The receiver and worker expose liveness/readiness behavior on internal admin ports.

The product's own telemetry pipeline derives request/error/latency and service-edge metrics from every span before sampling. This is product telemetry, not an implemented production monitoring/alerting platform for nexovue itself.

`just chaos` exercises broker/store failure under load and checks the acknowledged-data guarantee. `just load`, the OTel demo, live tests, and E2E tests provide additional verification paths. Production alerts, dashboards, SLOs, and operational ownership are not yet established.

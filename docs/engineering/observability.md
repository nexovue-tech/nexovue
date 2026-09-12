# Observability engineering standard

- Emit structured logs using existing field names and never log credentials or unmasked sensitive telemetry.
- Propagate correlation and trace context through service boundaries where existing utilities support it.
- A material new flow identifies its useful logs, metrics/health signals, failure modes, and operational owner in its design.
- AI features, when implemented, additionally require evaluation, model/prompt versioning, latency/cost monitoring, fallback behavior, and safety/grounding checks.

# Security and tenancy architecture

## Current state

Postgres control-plane access is scoped by organization. ClickHouse access goes through the API repository chokepoint, whose statements must bind a tenant identifier. Tests enforce both patterns.

Server OTLP uses secret API keys; browser RUM uses public site keys restricted by an origin allowlist. Secret keys are prefix-looked-up and hash-verified; public keys are intentionally visible to browser code. Receiver gates execute in this order: authentication, browser-origin validation, allowance, rate limiting, decode, normalize, and Kafka publish.

Telemetry is masked in workers before derive, sample, and storage. The worker masking hash secret is required in production configuration. The API uses typed domain errors and a central RFC 9457 error boundary.

## Future state

No AI tool permissions, RAG data-access model, or production secret-management platform is implemented. Those require separate approved design before implementation.

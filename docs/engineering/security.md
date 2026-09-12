# Security and privacy standard

- Authenticate and authorize before reading tenant data; preserve the API's Postgres and ClickHouse tenancy chokepoints.
- Keep credentials out of logs, responses, commits, fixtures, and defaults. Plaintext API keys are shown only at their intentional one-time creation boundary.
- Validate untrusted input, use typed domain errors, and preserve the API's RFC 9457 response shape.
- Browser telemetry requires public site keys and allowed origins; do not weaken that boundary.
- Mask telemetry before derivation, sampling, storage, or export. Treat masking configuration and hash secrets as security-sensitive.
- Schema changes go through migrations; evaluate tenant erasure, retention, indexes, rollback, and backup/restore implications.
- Security, deployment, provider, and public-endpoint changes require a production-readiness review.

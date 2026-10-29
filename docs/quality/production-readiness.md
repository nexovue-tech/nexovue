# Production-readiness gate

Production readiness is required before a production rollout, not merely when code compiles.

Check final configuration/secrets, migrations and compatibility, capacity, health checks, logging/monitoring/alerting, backup and restore evidence, rollback, TLS/public endpoints, security review, release process, smoke tests, and operational ownership.

Nexovue has not passed this gate. The verified current gap list, `docs/operations/production-readiness.md`, is kept private: it names nexovue's open weaknesses (founder, 2026-10-29). It lives outside the repository's history from that date, in the maintainer's working copy.

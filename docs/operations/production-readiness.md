# Production readiness status

## Current status

**Not ready for production deployment.** The repository supports local development and a production-replica shape. The on-premise Compose target declares itself a non-runnable skeleton.

## Required before approval

- Decide production hosting, image registry, and immutable-release process.
- Finish and verify the web edge, browser-worker image, public ingress, and any gRPC exposure decision.
- Establish external secrets management, capacity sizing, durable storage, backups, restore drills, and rollback.
- Add repository-owned CI/CD and staging smoke/deployment verification.
- Define production monitoring, alerting, incident response, and on-call/runbook ownership.
- Perform a security and release-readiness review against the final target.

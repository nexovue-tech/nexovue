# Infrastructure and operations standard

Current local development uses Docker Compose. The on-premise Compose target is explicitly a deployment skeleton, not a production-ready environment.

- Use `just dev`, `just fresh`, `just migrate`, and `just topics` rather than ad hoc startup steps.
- `just down-hard` is destructive; take a verified backup first where data matters.
- Backup/restore are per-store by design; no combined rollback or restore implies cross-store consistency.
- Production images must be immutable, configurations explicit, secrets externally supplied, and health/readiness checks verified.
- Any deployment change must update `docs/operations/` and assess sizing, TLS, public endpoints, observability, backups, restore drills, rollback, and incident response.

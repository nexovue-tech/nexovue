# Backend engineering standard

- Go receiver/workers changes preserve delivery ordering, envelope compatibility, partition ownership, and masking order.
- FastAPI routers are HTTP-only; business logic belongs in services and SQL remains in approved data-access modules.
- Use async I/O in Python service paths, typed domain errors, `%w` wrapped Go errors, and structured logging.
- Add a migration for every persistent schema change. Evaluate backwards compatibility, indexes, tenant erasure, retention, and rollback.
- Do not add dependencies or cross-service coupling without an approved design when the tradeoff is material.

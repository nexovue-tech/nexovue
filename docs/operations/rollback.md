# Rollback

## Current verified behavior

Postgres and ClickHouse migration rollback commands are separate by design: `just migrate-down-pg` and `just migrate-down-ch`. Backup restores are also per-store. There is deliberately no combined rollback/restore command because the two stores do not have one coherent shared transaction.

## Not yet established

No production release rollback procedure, image version policy, or rollback decision owner exists. Define these with the production deployment target.

# Rollback

## A release

`nexovue rollback` returns an install to the version that ran before its last upgrade
([upgrade](../self-hosting/upgrade.md)).

- **Same schema** — the upgrade added no migration (`nexovue setup schema` and `/setup schema` answer the
  same for both versions' images): the previous version's kit and images run against the same stores.
  Nothing is lost.
- **Schema changed** — refused. `nexovue rollback --restore` restores the backup `upgrade` took before
  switching, then starts the previous version; everything since the upgrade is lost.

Conservative on purpose: an additive migration would usually let the old version run, but nothing
records which migrations are additive, and a wrong guess runs old code against a schema it has never
seen. If rollbacks across additive migrations are needed, the migrations must say so first.

Only one step back is kept. Every version's kit stays in `releases/` until deleted.

## A migration, in development

Postgres and ClickHouse migrations roll back separately: `just migrate-down-pg` and `just migrate-down-ch`.
There is deliberately no combined command because the two stores share no transaction.

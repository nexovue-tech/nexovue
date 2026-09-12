# Backup and restore

## Current verified tooling

`just backup` creates Postgres and ClickHouse snapshots, manifests, revisions, row counts, and checksums under `.backups/` by default. `just backup-verify <snapshot>` verifies a snapshot. Restore is intentionally per store: `just restore-pg <snapshot>` or `just restore-ch <snapshot>`.

## Current limitation

This is tooling, not a production backup program. No production schedule, retention policy, off-host destination, recovery-time objective, recovery-point objective, or restore-drill record exists yet. Those are required before deployment.

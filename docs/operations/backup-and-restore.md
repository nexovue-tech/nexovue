# Backup and restore

The customer-facing guide is [`docs/self-hosting/backup.md`](../self-hosting/backup.md).

## Current verified behavior

`nexovue backup` (daily by `nexovue-backup.timer`, and before every upgrade):

1. `deploy/backup/backup.sh` — `pg_dump` (custom format, zstd), ClickHouse `BACKUP DATABASE`, a manifest
   of row counts, schema versions and ingest watermarks;
2. the bundled object store's bucket as `bucket.tar` (`nexovue backup bucket-export`);
3. `SHA256SUMS` over all of it;
4. off the host when `BACKUP_S3_*` is set (`nexovue backup ship`): each file checked against
   `SHA256SUMS` as it streams, `SHA256SUMS` written last so a cut-off upload is never a snapshot, a
   failed one deleted; then pruned to 7 daily and 4 weekly (`nexovue_api.backups.snapshots_to_keep`);
5. locally, the 2 newest kept (7 when nothing is shipped).

`nexovue restore <snapshot>` fetches it if needed, checks the checksums, stops every service but the
stores, replaces both databases and the bucket, runs `verify.sh` against the manifest, and starts
nexovue. The ingest watermarks are never restored (`restore-clickhouse.sh`, `restore-postgres.sh`).

In development the same scripts run as `just backup`, `just restore-pg`, `just restore-ch` and
`just backup-verify`.

Rehearsed 2026-10-28: [disaster-recovery.md](disaster-recovery.md).

## Not in a backup

`nexovue.env`. Its keys open the secrets sealed in Postgres, so the operator keeps a copy elsewhere;
a snapshot without them restores everything but those secrets.

## Not yet established

Continuous Postgres archiving (point-in-time recovery). The objective accepts losing up to a day; a
customer who needs less is the trigger for it.

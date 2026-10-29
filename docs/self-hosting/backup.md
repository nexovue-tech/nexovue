# Backups and restoring

## What is backed up

`nexovue backup` takes one snapshot of everything nexovue keeps:

| | |
|---|---|
| `postgres.dump` | accounts, organizations, projects, settings, alerts, checks |
| `clickhouse.zip` | the telemetry: traces, logs, metrics, profiles |
| `bucket.tar` | uploaded source maps and check files, when nexovue's bundled object store holds them |
| `manifest.txt` | what was captured, and the row counts a restore is checked against |
| `SHA256SUMS` | so a damaged file is found before it is restored |

**Not in a backup: `nexovue.env`.** It holds the keys that encrypt secrets inside the database, and a
snapshot restored without them cannot open those secrets. Keep a copy in your password manager or vault
when you install, and again after any key rotation.

## When

`nexovue install` schedules a backup every day at 02:30 (the `nexovue-backup.timer` systemd unit). A
backup missed while the server was off runs when it is back. `sudo systemctl list-timers nexovue-backup`
shows the next one; `journalctl -u nexovue-backup` the last ones' output.

`nexovue upgrade` takes one before changing anything. Take one by hand at any time:

```bash
sudo /opt/nexovue/current/nexovue backup
```

## Off the server

A backup on the disk it protects is lost with that disk. Point nexovue at any S3-compatible bucket
that is **somewhere else** — AWS S3, Azure (through an S3-compatible gateway), Google Cloud Storage,
Backblaze, MinIO on another machine — by adding to `/opt/nexovue/nexovue.env`:

```bash
BACKUP_S3_BUCKET='your-bucket'
BACKUP_S3_ACCESS_KEY_ID='…'
BACKUP_S3_SECRET_ACCESS_KEY='…'
BACKUP_S3_ENDPOINT='https://s3.example.com'   # leave out for AWS S3
BACKUP_S3_REGION='eu-west-1'                  # the bucket's region; us-east-1 if unset
BACKUP_S3_PREFIX='nexovue-backups'            # the folder inside the bucket; this is the default
```

The key needs to list, read, write and delete in that bucket and nothing else.

Each file is checked against `SHA256SUMS` as it is uploaded, and `SHA256SUMS` is uploaded last: a
backup cut off halfway is never mistaken for a complete one, and a failed upload removes what it wrote.

**Kept off the server:** the newest backup of each of the last 7 days that have one, and of each of the
last 4 weeks. Counted in days that have a backup, so a server that was off for a fortnight still keeps
its last week. **Kept on the server:** the 2 newest (7 when nothing is shipped).

On a bucket that supports it, turn on versioning or object lock as well, so a mistake — or an attacker
with the server's key — cannot delete every copy.

## Restore

```bash
sudo /opt/nexovue/current/nexovue restore 2026-10-28T023012Z
```

The name is one `nexovue backups` lists: a snapshot on the server, or one off it, which is fetched and
checked first. A restore **replaces everything** on the install with the snapshot, so it asks you to type the
console's address before touching anything. It then stops every service, restores both databases and the
bucket, checks the result against the manifest — every table's rows, the schema version, and that no
ingest position points past what Kafka holds — and starts nexovue again.

### On a new server

When the server itself is lost:

1. Install the **same version** on the new server, with the old `nexovue.env`:

   ```bash
   sudo ./nexovue install --settings /path/to/the/old/nexovue.env
   ```

   Update `NEXOVUE_APP_HOST` and `NEXOVUE_INGEST_HOST` in it first if the names move with the server.
2. Restore the newest backup from off the server: `sudo /opt/nexovue/current/nexovue restore <name>`.
   `sudo /opt/nexovue/current/nexovue backups` lists them.
3. Point the two DNS names at the new server.

Our objective for this is **back within 4 hours, losing at most the last day of telemetry** and no
account data beyond the last backup; the rehearsal that measured it is in
[disaster recovery](../operations/disaster-recovery.md).

## Telemetry since the last backup

Up to a day of telemetry is lost in a restore, by design: telemetry is replaceable in a way accounts
are not, and a smaller loss would need continuous archiving that no customer has asked for. Tell us if
yours needs it.

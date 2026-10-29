# Disaster recovery

## Objectives

Decided with the founder, 2026-10-28 (`docs/changes/ready-to-deploy/design.md` §8):

- **Backups every 24 hours**, kept 7 daily and 4 weekly, off the host.
- **Lose at most a day of telemetry**, and no account data beyond the last backup.
- **Back within 4 hours** of losing a server.

## The procedure

[`docs/self-hosting/backup.md` — On a new server](../self-hosting/backup.md#on-a-new-server): install
the same version with the old `nexovue.env`, `nexovue restore` the newest off-host snapshot, move DNS.

## Restore drills

Repeated at every release that adds a migration. Each records the version, the date, the data, and the
time from an empty machine to a verified install.

| Date | Version | Where | Data | Time | Result |
|---|---|---|---|---|---|
| 2026-10-28 | `dev` at `eb810fc` + this change | The founder's Mac: the quickstart (three brokers), the dev stack's SeaweedFS as the off-host store | 351 spans, 2 ingest keys, 1 bucket object | 72 s: 50 s to start an empty install, 22 s to fetch, restore and verify | **Pass** — `verify.sh` matched every table and schema version; the owner signed in; the bucket object read back byte for byte |

The drill found three defects, each fixed with a test before it passed: a backup whose upload failed
reported itself complete; a Mac's `tar` put metadata entries before `SHA256SUMS`; and the restore
dropped the telemetry database and then tried to connect into it.

**Still owed:** the drill on two separate Linux machines (a Lima VM or a server), at a size that
measures the 4-hour objective rather than seconds.

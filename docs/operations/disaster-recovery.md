# Disaster recovery

## Current verified behavior

The repository has backup, restore, and verification scripts for Postgres and ClickHouse. Backup output includes checksums, schema revisions, and table manifests.

## Not yet established

No recovery objectives, off-host backup destination, retention schedule, disaster scenario runbook, or recorded restore drill exists. Production use requires all of these.

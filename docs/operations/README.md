# Operations index

Operational truth is created only for verified procedures. Each page says what was rehearsed and what is
not yet. The customer's own guide to running nexovue is [`docs/self-hosting/`](../self-hosting/install.md).

- [Deployment](deployment.md) — the install layouts and the `nexovue` command
- [The first deployment](first-deploy.md) — from buying a server to watched and backed up
- [Releasing](release.md) — tagging a version, and what the release publishes

The production-readiness review — the list of nexovue's open weaknesses — is kept private and is not
in this repository.
- [The first accounts](first-accounts.md) — who exists before anyone can sign in, on a self-hosted install and on ours
- [Monitoring](monitoring.md)
- [Alerting](alerting.md)
- [Backup and restore](backup-and-restore.md)
- [Rollback](rollback.md)
- [Key rotation](key-rotation.md) — replacing either encryption key without losing what it sealed
- [Dead letters](dead-letters.md) — listing and replaying telemetry set aside as unprocessable.
- [Detection](detection.md) — when anomaly detection stalls, fails for a project, or is held by nexovue's own intake
- [Disaster recovery](disaster-recovery.md)
- [Incident response](incident-response.md)

Before the first production deployment: the install, offline and upgrade rehearsals on a Linux machine
(`docs/changes/ready-to-deploy/status.md`), and an approved incident-response runbook.

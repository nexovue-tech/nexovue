# Upgrading and rolling back

## Upgrade

An upgrade runs from the **new** version's kit, which carries that version's Compose files:

```bash
tar -xzf nexovue-X.Y.Z.tar.gz
cd nexovue-X.Y.Z
sudo ./nexovue upgrade
```

From an offline bundle, the same with `--offline`.

In order, it:

1. checks the machine, and downloads (or loads) the new images while the old version keeps running;
2. compares the database schema each version carries — read from the images themselves;
3. **takes a backup**, shipped off the server if backups are set up ([backup](backup.md));
4. switches to the new version, which prepares the databases first and then starts every service;
5. waits until every service is healthy.

nexovue is unavailable from step 4 until step 5 finishes, usually a minute or two. Telemetry sent in
that window is refused with a retryable error, and OpenTelemetry exporters send it again.

Read the release notes before upgrading: they say when an upgrade changes the database schema and how
long its migration may take on a large install.

## Roll back

```bash
sudo /opt/nexovue/current/nexovue rollback
```

returns to the version that ran before the last upgrade. How depends on what the upgrade changed:

- **The schema did not change.** The older version runs against the same databases: nothing is lost,
  and the rollback takes a minute.
- **The schema changed.** The older version may not run against the newer schema, so `rollback`
  refuses and says so. Going back means restoring the backup taken just before the upgrade:

  ```bash
  sudo /opt/nexovue/current/nexovue rollback --restore
  ```

  **Everything recorded since the upgrade is lost** — telemetry, and any change made in the console. It
  asks you to type the console's address before it touches anything.

`nexovue status` shows the previous version and whether its schema matches.

Only one step back is kept: after a rollback, `rollback` has nothing further to return to. Every
installed version's kit stays in `/opt/nexovue/releases/` until you delete it; delete only versions that
are neither current nor previous.

## When an upgrade fails

If a service does not become healthy, the upgrade stops and names it, and the services that did start
keep running. `nexovue status` and the service's log ([install](install.md#when-something-fails)) show
why. Either fix the cause and run the upgrade again, or roll back as above.

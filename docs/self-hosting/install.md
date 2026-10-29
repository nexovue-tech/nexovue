# Installing nexovue on your own server

nexovue runs on one Linux server with Docker. One command installs it, and the same command upgrades,
rolls back, backs up and restores it. This guide is for the person who runs that server.

## Try it first, on a laptop

To see nexovue before giving it a server, unpack the kit on any machine with Docker Desktop (8 GB of
memory for Docker) and run:

```bash
./nexovue quickstart
```

After a few minutes the console is at **http://localhost:8080**. The command prints the email and a
generated password to sign in with; the password is shown once. Applications send telemetry to
**http://localhost:4318** (OTLP over HTTP), and the console's setup pages show how for each language.

The quickstart is **not for production**: one Kafka broker, no backups, no TLS, and nothing reachable
from another machine. `./nexovue quickstart down` stops it; `./nexovue quickstart remove` deletes it
and everything it recorded.

## What you need

| | |
|---|---|
| A server | Linux on amd64 or arm64; 16 GB of memory and 4 CPUs at least ([sizing](sizing.md)) |
| Disk | 40 GB free where Docker keeps its data, more as telemetry accumulates ([sizing](sizing.md)) |
| Docker | Docker Engine 24 or newer, with the Compose plugin 2.24 or newer |
| Two DNS names | one for the console (`nexovue.example.com`), one where applications send telemetry (`ingest.nexovue.example.com`), both pointing at the server |
| Ports | 80 and 443 open to your users and applications; nexovue obtains certificates for both names itself |
| A place for backups | any S3-compatible bucket that is **not on this server** ([backup](backup.md)) |

Two names rather than one: a sign-in cookie set for one name must never travel with telemetry sent to
the other.

## Install

Download the kit for the version you want (`nexovue-X.Y.Z.tar.gz`), unpack it, and run the installer as
root:

```bash
tar -xzf nexovue-X.Y.Z.tar.gz
cd nexovue-X.Y.Z
sudo ./nexovue install
```

It checks the machine — Docker's version, memory, disk and the two ports — and refuses with the reason
if any falls short. It then asks for:

| Question | Setting |
|---|---|
| The console's address | `NEXOVUE_APP_HOST` |
| Where applications send telemetry | `NEXOVUE_INGEST_HOST` |
| An email address for certificate notices | `NEXOVUE_TLS_EMAIL` |
| Your organization's name | `FIRST_ORGANIZATION_NAME` |
| The email you will sign in with | `FIRST_OWNER_EMAIL` |

To install without questions, set those variables before running it; `FIRST_OWNER_PASSWORD` may be set
too, at least 12 characters, or one is generated. Every other secret — database passwords, encryption
keys, the object store's keys — is generated for this install and never leaves it.

The install then downloads the images, prepares the databases, starts every service and waits until
each reports healthy. On a first start that takes a few minutes. It ends with the console's address and
the account to sign in as; **a generated password is shown once** — change it after signing in.

### Where things are

Everything lives in `/opt/nexovue` (set `NEXOVUE_HOME` to choose another place):

| | |
|---|---|
| `nexovue.env` | every setting and secret, readable by root only. **Keep a copy somewhere safe**: a backup cannot be restored without the keys in it |
| `releases/X.Y.Z/` | each installed version's kit; `current` points at the running one |
| `backups/` | the most recent local backups |

After installing, run every command from `/opt/nexovue/current/`, for example
`sudo /opt/nexovue/current/nexovue status`.

### Without internet access

For a server that cannot reach the internet, download the offline bundle for its architecture
(`nexovue-X.Y.Z-offline-amd64.tar` or `-arm64.tar`) on a machine that can, carry it across, and run:

```bash
tar -xf nexovue-X.Y.Z-offline-amd64.tar
cd nexovue-X.Y.Z
sudo ./nexovue install --offline
```

The bundle holds every image nexovue runs. The installer checks each file against the bundle's
`SHA256SUMS` before loading anything, and never contacts a registry. Certificates still need a
certificate authority your server can reach; see *Certificates* below.

### Checking a download

Every kit and bundle is signed when it is released. With [cosign](https://docs.sigstore.dev/) installed:

```bash
cosign verify-blob nexovue-X.Y.Z.tar.gz \
  --bundle nexovue-X.Y.Z.tar.gz.sigstore.json \
  --certificate-identity-regexp '^https://github.com/nexovue-tech/nexovue/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## After installing

- **Backups.** Set `BACKUP_S3_*` in `nexovue.env` ([backup](backup.md)). Until then backups stay on the
  same disk as the data, which protects against nothing that loses the disk.
- **Email.** Without a mail provider nexovue cannot send invitations, password resets or alert emails;
  everything else works. Set `EMAIL_PROVIDER`, `EMAIL_API_KEY`, `EMAIL_SENDING_DOMAIN` and
  `EMAIL_FROM_ADDRESS` in `nexovue.env`, then run `sudo ./nexovue install` again to apply them.
- **Teammates** join by invitation from the console. Public sign-up is closed (`SIGNUP_ENABLED=false`).
- **Monitoring.** `NEXOVUE_MONITORING=yes` in `nexovue.env` runs Prometheus and Alertmanager beside
  nexovue with its alert rules; name where alerts go in `monitoring/alertmanager.yml`. Or point your own
  monitor at the same endpoints and rules (`monitoring/`).

Changing a setting in `nexovue.env` and running `sudo ./nexovue install` again applies it. The install
keeps every secret it generated the first time: they are what the databases were created with.

**Your own changes** — a different port, your own certificates, a proxy in front — belong in a Compose
file of your own, named in `NEXOVUE_EXTRA_COMPOSE` (a full path; several separated by spaces). It is
applied after nexovue's files, and an upgrade leaves it alone. Editing nexovue's own files is undone by
the next upgrade.

## Certificates

nexovue's edge (Caddy) obtains a certificate for each name from Let's Encrypt the first time it is
asked for that name, and renews it. That needs both names to resolve to this server and ports 80 and 443
reachable from the internet. A server with no internet access needs your own certificates; that set-up
is not yet automated — ask us.

## Commands

| Command | |
|---|---|
| `nexovue status` | the version, the last backup, and every service's health |
| `nexovue backup` | a backup now ([backup](backup.md)) |
| `nexovue backups` | the backups on the server and off it |
| `nexovue restore <snapshot>` | put a backup back ([backup](backup.md)) |
| `nexovue upgrade` | from a newer kit ([upgrade](upgrade.md)) |
| `nexovue rollback` | back to the version before the last upgrade ([upgrade](upgrade.md)) |
| `nexovue stop` | stop every service; the data stays. `install` starts them again |

## When something fails

`nexovue status` names the service that is not healthy. Its log says why:

```bash
cd /opt/nexovue/current/compose
sudo docker compose --project-name nexovue --env-file /opt/nexovue/nexovue.env -f nexovue.yaml logs --tail 200 <service>
```

The services that prepare the databases (`setup`, `setup-pipeline`) run once at each start and exit;
if either fails, nothing else starts, and its log is the place to look.

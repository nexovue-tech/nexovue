# Design: ready-to-deploy

## Status

Approved 2026-10-11 — the founder took every ⚑ recommendation: release-please; OpenTofu; Strimzi,
Altinity and CloudNativePG with managed Postgres by default; a native Azure adapter; daily backups kept
7 daily and 4 weekly, at most a day lost, back within 4 hours; `self-hosted` as the default mode.

## Context and goal

`change.md` (approved 2026-10-11): nothing is hosted yet; everything a deployment needs is written
and rehearsed, so the day the first customer pays a cloud is chosen, the code is run, and nexovue is
live. Founder decisions already taken: both ADR-006 paths, infrastructure code for AWS, Azure and
Oracle, GitHub Actions with public images on GHCR, self-hosted and try-it-locally included.

Research behind this design, 2026-10-11 (sources in the three research notes summarised in
`status.md`): GitHub runners and supply-chain tooling; Kubernetes operators for Kafka, ClickHouse and
Postgres; Terraform/OpenTofu, Azure and Oracle object storage, Ansible versus cloud-init.

## Proposed design

### 1. Two images (ADR-019, executed)

| Image | Contents | Base |
|---|---|---|
| `ghcr.io/nexovue-tech/nexovue` | One virtualenv with the API and the scheduler, Alembic and its migrations, the built console files at `/srv/web`, the `nexovue` role command | `python:3.13-slim-trixie` |
| `ghcr.io/nexovue-tech/nexovue-pipeline` | `/receiver`, `/worker`, `/dlq`, `/setup` (new, §3) | `distroless/static-debian12:nonroot` |

- **The role is the command.** `nexovue api`, `nexovue scheduler-beat`, `nexovue scheduler-light`,
  `nexovue scheduler-deliveries`, `nexovue setup`, `nexovue web-assets <dir>` (copies the console
  files out, replacing `web-assets-init`'s separate image). The pipeline image has no shell, so the
  command names the binary: `/receiver`, `/worker`, `/setup`.
- **The five Dockerfiles become two** (`deploy/images/nexovue.Dockerfile`,
  `deploy/images/nexovue-pipeline.Dockerfile`); `apps/web/Dockerfile`, `apps/api/Dockerfile`,
  `apps/scheduler/Dockerfile`, `apps/receiver/Dockerfile` and `apps/workers/Dockerfile` are deleted.
- Every image carries `org.opencontainers.image.{source,version,revision}` labels, and the Go binaries
  their commit (already `-X main.gitSHA`).

### 2. Releases (GitHub Actions)

- **`ci.yml`, every push and pull request:** `just check`, with the stores started by the repository's
  own compose file (`docker compose up -d --wait`), so CI runs the topology that ships and the live
  suites run rather than skip. Standard runners: 2 vCPU / 8 GB while the repository is private, 4 / 16
  once public.
- **`release.yml`, on a version tag `vX.Y.Z`:** each image is built **natively** on `ubuntu-24.04` and
  `ubuntu-24.04-arm` (no emulation), pushed by digest, and merged into one multi-architecture tag with
  `docker buildx imagetools create`. Tags: `X.Y.Z`, `X.Y`, and the commit. Every image gets SLSA
  provenance and an SBOM (`build-push-action` attestations) and a keyless cosign signature over its
  digest. The Helm chart is pushed to `oci://ghcr.io/nexovue-tech/charts`. Then the **install kit** and
  the **offline bundles** (§6) are attached to a GitHub Release.
- **Actions are pinned by commit hash** and kept current by Dependabot. `GITHUB_TOKEN` with
  `packages: write`, `id-token: write`, `attestations: write`; no stored credentials.
- **Versions ⚑:** *recommended* — **release-please**: one product version shared by both images, the
  chart and the install kit (`vX.Y.Z`), and the browser SDK on its own track (`sdk-vX.Y.Z`), each with a
  generated changelog. The SDK is published to npm by **trusted publishing** (OIDC, no token, provenance
  automatic). npm provenance needs a public repository, so the SDK's first publish waits for
  `going-public`.
- **GHCR visibility** is switched to public once per package, by hand, in the package's settings; it
  cannot be switched back. That is done in `going-public`, so nothing is public before the repository
  is.

### 3. Every deploy prepares its stores — one `setup` step

Today the Postgres migrations run through `alembic` from the laptop, the ClickHouse migrations through
the `migrate` CLI (golang-migrate) from the laptop, and the topics through `deploy/kafka/topics.sh`
inside the Kafka container. None of the three is in an image.

- **`nexovue setup`** (Python): `alembic upgrade head`, then creates the bucket if missing (moving
  `ensure_bucket` out of the API's startup, where it races between replicas).
- **`/setup`** in the pipeline image (Go): the ClickHouse migrations through golang-migrate's **library**
  — the same tool and the same migration files, so nothing about their format changes — and the Kafka
  topics through franz-go's `kadm`, already a dependency (group 7). The topic list, partitions, sizes
  and `min.insync.replicas` move from `topics.sh` into this one place; `topics.sh` is deleted and
  `just topics` calls the binary.
- **Ordering:** both run to completion before any service starts — Compose
  `depends_on: condition: service_completed_successfully`, Helm `pre-install`/`pre-upgrade` hook Jobs.
  Each takes a lock (Postgres advisory lock; golang-migrate's own lock for ClickHouse) so two installs
  starting at once cannot run migrations twice. Both are idempotent, so a re-run is a no-op.
- **The development stack uses the same code:** `just migrate` and `just topics` call the same entry
  points, so the deploy step is exercised every day, not once a release.

### 4. The deployment mode

One setting, **`NEXOVUE_DEPLOYMENT=hosted|self-hosted`**, read by the API, the scheduler and the
receiver. **Default `self-hosted`** — the images are public, so whoever runs them without saying
otherwise is self-hosting; the hosted service sets `hosted` explicitly.

| | hosted | self-hosted |
|---|---|---|
| Billing screen, Razorpay routes, payment webhooks | yes | absent (routes not mounted; console hides the screen) |
| Monthly allowance on the receiver | enforced | not enforced — the operator's disk is theirs |
| Usage screen | with plan and allowance | volumes only |
| `PAYMENTS_*` settings | required in production | refused if set, so a misconfigured install fails loudly |
| Signup | the hosted setting (closed by default) | closed; the first owner comes from `FIRST_OWNER_*` |
| Operator console (`SUPER_ADMIN_*`) | yes | absent |
| Ingest suspension valve | yes | yes — an operator tool on both |

The receiver learns the mode from its environment, so the allowance check is skipped without a query.

### 5. Three installs, one set of images

All three run the same images with the same environment contract. What differs is who installs them
and what is in front.

**5a. Try it locally.** For someone evaluating nexovue on a laptop.

- From the install kit: `./nexovue quickstart` — starts one of everything with
  `NEXOVUE_DEPLOYMENT=self-hosted`, on `http://localhost:8080` (console) and `localhost:4317/4318`
  (OTLP), no TLS, a memory budget of about 6 GB, a first owner created with a generated password that
  the script prints once, and a page in the console showing how to send data.
- Not for production, and says so on start: no backups, one Kafka broker, no TLS.

**5b. Self-hosted.** A customer's IT team, on their servers.

- `deploy/compose/docker-compose.onprem.yaml` becomes **`deploy/compose/nexovue.yaml`**, with every
  `TODO` answered: images from the registry (or the offline bundle), sizing as variables with a
  documented minimum (the readiness review's laptop budget is not a server's), the bucket on the
  customer's own S3-compatible store or the bundled SeaweedFS, backups scheduled (§8), healthchecks on
  everything (group 7).
- `./nexovue install` checks the machine (Docker and Compose versions, memory, disk, ports 80/443),
  writes `.env` from prompts or flags (generating every secret), runs `setup`, starts the stack, and
  prints the console address. `./nexovue upgrade <version>` and `./nexovue rollback` re-run it with
  another tag (rollback is only to a version whose migrations were additive; `rollback.md` says how to
  tell).
- `docs/self-hosting/` — an install guide written for an IT team, not for us.

**5c. The hosted cloud — infrastructure, then Path A or Path B.**

*Infrastructure ⚑ — OpenTofu or Terraform.* ADR-006 says Terraform. *Recommended* — **OpenTofu**:
MPL-2.0 under the CNCF, the same AWS, Azure and Oracle providers, and native state encryption, which
matters once three clouds' state files exist. Terraform's BSL licence would not bind us as a user, so
this is a preference, not a necessity; the code is kept compatible with both.

- `infra/{aws,azure,oci}/`, one root module per cloud, hand-written resources in small local modules
  (`network`, `compute`, `kubernetes`, `lb`, `dns`, `object_storage`), no cross-cloud abstraction,
  `envs/` per environment, region an input (ADR-009). Community modules are avoided except for the
  managed Kubernetes cluster, where they save real work.
- Each cloud's module creates either VMs (Path A) or a managed cluster (Path B), chosen by a variable.
- **Proven offline:** `tofu validate`, `tofu test` with mocked providers (module wiring, variables,
  counts, conditions), `tflint` with each cloud's ruleset, `checkov`. **Not proven until applied:**
  quotas, instance availability, IAM sufficiency, DNS delegation, certificates, the stack booting.
  `status.md` keeps that line for each cloud.

*Path A — Linux servers.*

- **cloud-init** (from the infrastructure code) only bootstraps a VM: a user, SSH keys, Docker and the
  Compose plugin. **Ansible** deploys: it templates `.env` and the Compose files with an exact image
  digest, runs `setup`, pulls and starts with `community.docker.docker_compose_v2` (`wait: true`).
  Upgrade and rollback are the same playbook with another version. (ADR-006 left Ansible versus
  cloud-init open; cloud-init alone cannot upgrade.)
- **Hosts by role.** The inventory groups hosts as `edge`, `app`, `kafka`, `clickhouse`, `postgres`.
  Each host runs one Compose project holding its groups' services; a small install puts every group on
  one host, a larger one spreads them. Postgres may instead be the cloud's managed service.
- **The Compose files are the self-hosted ones** (§5b), split by role. Path A is the self-hosted
  install automated — ADR-006's own consequence.

*Path B — Kubernetes.*

- One Helm chart, `deploy/helm/nexovue`, Helm 4, chart format v2, published to GHCR. Every role is a
  Deployment from the two images; `setup` is a hook Job; the Caddy edge is a Deployment behind a
  LoadBalancer Service, its console files copied in by an init container — the same edge as Compose, so
  TLS, headers and caching are configured once.
- **Stores ⚑.** Each store is either run inside the cluster by an operator or supplied as an endpoint
  outside it, chosen in the values. *Recommended defaults:*

  | Store | In the cluster | Default on the hosted cloud |
  |---|---|---|
  | Kafka | **Strimzi** 1.2 (CNCF, KRaft-only, Kafka 4.3) | in the cluster |
  | ClickHouse + Keeper | **Altinity clickhouse-operator** 0.27 (Apache-2.0) | in the cluster |
  | Postgres | **CloudNativePG** 1.30 with the Barman Cloud plugin | **the cloud's managed Postgres** (all three have one) |

  ClickHouse Inc.'s own operator (v0.0.8) is re-evaluated at 1.0. **No Bitnami chart or image** — their
  free versioned images were withdrawn in 2025. Confluent's operator needs a paid licence.
- An ADR records the store choice (ADR-022), because changing an operator later means migrating data.

### 6. The install kit and the offline bundle

- **Install kit** (`nexovue-X.Y.Z.tar.gz`, every release): the `nexovue` script, the Compose files, the
  Caddyfile, the ClickHouse configuration, `.env.example`, and the docs. Everything a host needs except
  the images.
- **Offline bundle** (`nexovue-X.Y.Z-offline-{amd64,arm64}.tar`): the kit plus both images as OCI
  archives (`skopeo copy --all`), `SHA256SUMS`, and a cosign signature over it.
  `./nexovue install --offline` loads the images and sets `pull_policy: never`. For customers with no
  internet — banks, government, hospitals.

### 7. Object storage on Azure ⚑

Azure Blob has no S3 API, and MinIO's community edition is archived (2025-26). *Recommended* — **a
native Azure adapter**: one object-store interface in `packages/py-platform`
(`put`/`get`/`delete`/`delete_prefix`/`ensure_bucket`) with two implementations, S3 (boto3, today's
code) and Azure Blob (`azure-storage-blob`), chosen by `OBJECT_STORE=s3|azure`. The API's
`ObjectStore` and the scheduler's probe-file reader move onto it — the whole object-storage surface,
two clients. ClickHouse uses no object storage today, so nothing there changes. Oracle uses its
S3-compatible endpoint, path-style, with boto3's checksums set to `when_required` (Oracle refuses the
newer defaults on some calls).

*Rejected:* SeaweedFS in front of Azure — we would run a storage system with its own durability,
patching and backups on the one cloud that needed none.

### 8. Backups, restore and the recovery objectives ⚑

- **Off the host.** `backup.sh` already writes and checksums a snapshot; it gains an upload to the
  object store (the same interface, §7) and a pruning pass. Scheduled by a systemd timer on Path A and
  self-hosted, a CronJob on Path B. On Path B with CloudNativePG, Postgres is backed up by its plugin
  instead; managed Postgres by the cloud.
- *Recommended objectives:* **backups every 24 hours, kept 7 daily and 4 weekly; lose at most a day of
  telemetry and no account data beyond the last backup; restored within 4 hours.** Telemetry is
  replaceable in a way accounts are not; a later change can add continuous Postgres archiving if a
  customer needs a smaller loss.
- **The rehearsal:** a backup taken on one machine and restored on a fresh one (§11), verified by
  `backup-verify` and by the console reading the data. Recorded in `disaster-recovery.md` with the date,
  the version and the time it took. Repeated every release that adds a migration.

### 9. Three Kafka brokers

- KRaft with three nodes, each both controller and broker, replication 3, `min.insync.replicas` 2 —
  any one may be lost with nothing acknowledged lost. Path A puts each on its own host; Path B is
  Strimzi's; self-hosted offers it as an option (one broker remains the small-install default, and the
  install guide says what that risks).
- The topic settings follow the broker count in `/setup`, so replication can never be set higher than
  the brokers that exist.

### 10. A monitor, ready to connect

- `deploy/monitoring/`: a Prometheus scrape configuration for every `/metrics` group 7 added, alert
  rules for the thresholds `monitoring.md` names (consumer lag, dead letters, a stopped pipeline, beat
  not leading, deliveries waiting, receiver refusals, 5xx rates), and an external uptime check list for
  the public endpoints.
- Validated in CI with `promtool check rules` and rule unit tests. A `monitoring` Compose profile runs
  Prometheus and Alertmanager locally so the rules can be watched firing in the rehearsal.
- Which monitor watches the hosted service (a managed Prometheus, a SaaS) is chosen on the day; the
  rules travel to any of them.

### 11. The rehearsals — proven at no cost

| What | Where | Proves |
|---|---|---|
| CI and a release | GitHub Actions, a `v0.0.0-rc` tag | both images built natively for both architectures, signed, pulled back and run |
| Try it locally | the Mac | `./nexovue quickstart` from the kit, data sent and read |
| Self-hosted | a fresh **Lima** VM (Ubuntu 24.04, free, scriptable) | install from published images, then from the offline bundle with the network cut; upgrade; rollback |
| Path A | Ansible against fresh Lima VMs (one host, then roles spread over three) | install, upgrade, rollback, a host restart |
| Path B | **kind** (free, multi-node) | `helm install` with the three operators, upgrade, rollback, a node removed |
| Restore | backup on one VM, restore on a fresh one | §8 |
| Kafka | three brokers, one stopped under load | nothing acknowledged lost |
| Infrastructure | offline only | §5c's proven/unproven line per cloud |

OrbStack was rejected for the rehearsals: its licence charges for commercial use.

### 12. The first-deploy runbook

`docs/operations/first-deploy.md` — what only a real host can do, in order: create the cloud account
and the state bucket; apply the infrastructure; delegate DNS; let Caddy certify; verify the email
sender's domain (SPF, DKIM, DMARC); set `TRUSTED_PROXY_COUNT` for the load balancer in front; connect
the monitor; take the first backup and restore it; create the operator account. Each step with how to
know it worked.

### 13. Cleared along the way

- **`insight` and `ollama` leave the deploy files.** `apps/insight` does not exist; both files describe
  it behind `TODO(phase-2)`. It comes back with the service (Phase 2), not before.
- `docker-compose.app.yaml` (the production replica) becomes the hosted layout of the same files, not
  a third copy.

## Data and migration impact

No schema change. The deployment mode is configuration. The setup step runs the existing migrations;
it changes when they run, not what they do.

## Failure handling

- `setup` fails → no service starts, the install or upgrade stops with the migration's error; the
  previous version keeps running on an upgrade (Compose and Helm both start new containers only after
  `setup` succeeds).
- A release job fails after some images were pushed → nothing is tagged `X.Y.Z` until the merge job
  succeeds; per-digest pushes are invisible to installs.
- Rollback past a non-additive migration is refused by `./nexovue rollback` (it reads the migration
  revisions) rather than starting old code against a new schema.

## Security, tenancy, and privacy

- No stored registry or cloud credentials in CI: `GITHUB_TOKEN` and OIDC only. Images signed and
  attested; the bundle checksummed and signed.
- The public images contain no secret: every key and password comes from the environment, and
  production refuses to start without them (groups 5-6). A test in CI fails a build whose image holds a
  file matching the repository's secret patterns.
- Self-hosted mode removes the payment surface entirely rather than leaving it unconfigured.
- Infrastructure code holds no secret; state is encrypted (OpenTofu) and kept in the cloud's own
  bucket.

## Observability and operations

Group 7's endpoints are unchanged; §10 connects them. New runbooks: `first-deploy.md`, the self-hosting
guide, `release.md` (cutting a release), and updates to `deployment.md`, `rollback.md`,
`backup-and-restore.md`, `disaster-recovery.md`, `monitoring.md`.

## Alternatives and tradeoffs

- **Hosted-only now, self-hosted later** — rejected by the founder; self-hosted is most of Path A.
- **Kubernetes only** (one path) — rejected by ADR-006: the first customer's budget decides.
- **Emulated arm64 builds** — rejected: many times slower; native arm runners are free once public.
- **Bitnami charts for the stores** — rejected: free versioned images withdrawn in 2025.
- **One cross-cloud infrastructure module** — rejected: the resources differ too much to share.
- **A SeaweedFS gateway on Azure** — rejected in §7.
- **`docker save` tarballs for the bundle** — rejected: one architecture per archive and slower to
  verify; OCI archives carry both and their digests.

## Specification impact

- Billing's specification gains the self-hosted mode (no billing surface).
- ADR-006 amended (three clouds; OpenTofu if chosen; Ansible with cloud-init). ADR-019 marked
  executed. New ADR-022 (stores on Kubernetes).

## Decisions for the founder

1. **Versions:** release-please, one product version plus the SDK's own *(recommended)*, or tags by
   hand.
2. **OpenTofu** *(recommended)* or Terraform.
3. **Stores on Kubernetes:** Strimzi, Altinity, CloudNativePG, with managed Postgres by default on the
   hosted cloud *(recommended)*.
4. **Azure storage:** a native adapter *(recommended)* or a SeaweedFS gateway.
5. **Recovery objectives:** daily backups, 7 daily + 4 weekly kept, a day of telemetry at most, back
   within 4 hours *(recommended)* — or tighter, at the cost of continuous Postgres archiving now.
6. **Default mode `self-hosted`** for the public images *(recommended)*, the hosted service setting
   `hosted` explicitly.

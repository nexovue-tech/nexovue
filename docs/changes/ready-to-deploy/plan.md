# Plan: ready-to-deploy

> **Status:** Approved 2026-10-28 — in progress
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` and `design.md` approved 2026-10-11, every ⚑ recommendation taken.
- `going-public` is a separate change. Until it is done the repository and its GHCR packages stay
  private: CI and releases run, images are pulled with a token, and npm publishing waits (npm
  provenance requires a public repository).

## Current system

Read from the code on 2026-10-11 (paths under `apps/api/src/nexovue_api/` unless given in full).

**Images and commands**
1. Five Dockerfiles: `apps/api/Dockerfile:33` (`uvicorn nexovue_api.main:app`), `apps/scheduler/Dockerfile:37`
   (`python -m nexovue_scheduler worker`), `apps/receiver/Dockerfile`, `apps/workers/Dockerfile` (worker and
   `/dlq`), `apps/web/Dockerfile` (busybox carrying `/srv/web`). The API and scheduler images already copy
   every workspace member's metadata to satisfy `uv sync --frozen`.
2. The scheduler's commands are `beat`, `worker [--queues] [--concurrency]` and `health`
   (`apps/scheduler/src/nexovue_scheduler/__main__.py:42-73`). The API has no command of its own; its
   tools are separate modules: `keys` (`keys.py:250`), `suspend` (`suspend.py:162`), `seed` (`seed.py:183`,
   development only).
3. `alembic.ini` resolves `script_location = %(here)s/migrations` and the image copies all of `apps/api`
   (`Dockerfile:26`), so `alembic -c /app/apps/api/alembic.ini upgrade head` can run inside the image;
   the DSN is `POSTGRES_DSN`, asyncpg form (`data/postgres/engine.py:12,21`).

**Store preparation**
4. ClickHouse: 33 migration pairs (`deploy/clickhouse/migrations/000001…000033`), one statement each, run
   by the `migrate` CLI with `x-multi-statement=false` and credentials in query parameters (`justfile:11-15,
   264-265`). golang-migrate is in no `go.mod`; the workers module uses `ch-go` v0.74.0, not `clickhouse-go`
   v2 (which golang-migrate's ClickHouse driver needs), and already has franz-go `kadm` v1.19.0.
5. Kafka topics: `deploy/kafka/topics.sh` runs `docker exec` in `nexovue-kafka-1` — spans-raw 12
   partitions, logs/metrics/profiles-raw 4 each, four `*-dlq` at 1; 7-day retention, 16 MiB messages, zstd,
   `min.insync.replicas = RF-1`, replication `KAFKA_REPLICATION_FACTOR` (default 1).
6. **Correction to `design.md` §3:** `ensure_bucket` does not race between replicas — it runs only in
   development (`lifespan.py:286-300`); outside development **nothing creates the bucket**, which the
   docstring leaves to "the rest of the infrastructure". `setup` creating it is still right; the reason is
   that nothing does today.

**Deployment mode — what it must switch**
7. No mode exists. Billing is mounted unconditionally (`main.py:141`, routes under `/api/v1/billing`,
   webhook at `/billing/webhook/domestic`); the operator console at `main.py:123` (`/admin`) and admin mode
   at `auth/router.py:135`. Payment validators: `settings.py:333`. Signup is closed by default
   (`settings.py:266`). Both seeds run at API startup (`lifespan.py:155,159`).
8. The receiver's allowance gate is `withinAllowance` (`internal/otlp/gates.go:141`), wired at
   `cmd/receiver/main.go:102-107`; suspension rides the same lookup (`allowance/allowance.go:327,371`), so
   self-hosted must skip the budget and keep the suspension.
9. The console shows Billing and Usage to everyone (`shared/settings/settings-nav.ts:122-131`) and has no
   way to know the mode: `/auth/me` carries no plan or deployment (`auth/schemas.py:85`); `/system/info`
   is build information only (`system/router.py:48`).

**Object storage**
10. The whole surface: the API's `ObjectStore` (`data/objectstore/store.py:63`; `put`/`get` used by
    `sourcemaps/service.py:82,181` and `checkfiles/service.py:94`) and the scheduler's `ObjectStoreFiles`
    (`apps/scheduler/src/nexovue_scheduler/probes/files.py:34`; `read`, `delete`, `delete_prefix`, used by
    `probes/tasks.py` and `erasure/run.py:91`). Two boto3 clients, configured separately
    (`settings.py:279-285`, scheduler `settings.py:86-90`). ClickHouse uses no object storage.

**Compose files**
11. `docker-compose.app.yaml` (project `nexovue-app`) and `docker-compose.onprem.yaml` (project `nexovue`)
    share the `x-` anchors and `include: compose.infra.yaml`. **A defect in the on-prem file:** `x-image-base`
    is `${NEXOVUE_REGISTRY}/nexovue` with no image name or tag, used by every service but two (`:22`). Both
    carry `insight` and `ollama` for a service that does not exist.
12. `test_compose_dependencies.py` and `test_compose_worker_memory.py` already read the compose files.

**Backups**
13. `deploy/backup/common.sh` assumes the containers of project `nexovue` (`nexovue-postgres-1` …) and
    dev credentials; the replica's project is `nexovue-app`. `backup.sh` writes Postgres, ClickHouse, a
    manifest and checksums to a host directory. **Not in the design: the bucket is not backed up** — on a
    self-hosted install with the bundled SeaweedFS, uploaded check files and source maps would be lost
    with the volume (Question 1).

**CI prerequisites**
14. `just check` needs gofmt, golangci-lint, uv (ruff, mypy, pytest), pnpm (biome), buf, and the stack:
    `test-py` runs `gate-seed` against the receiver, ClickHouse and API ports, then the live suites
    (`justfile:133`). `contracts-check` runs `buf breaking` against `.git#branch=main`, so CI must fetch the
    whole history.

## Change required

`change.md` and `design.md` in this directory.

## Impact

| Area | What changes |
|---|---|
| Files | `deploy/images/` (two Dockerfiles), `apps/workers/cmd/setup`, `setup` and `roles` modules in the API, `.github/`, the Compose files reorganised, `deploy/nexovue` (the install script), `deploy/ansible/`, `deploy/helm/nexovue/`, `infra/{aws,azure,oci}/`, `deploy/monitoring/`, `packages/py-platform` object store; five Dockerfiles and `topics.sh` deleted |
| Services | Every service's start command; the receiver, API and console read the mode |
| Schema / migrations | None |
| UI | Billing and the plan hidden in self-hosted mode |
| Tests | New Go, Python, script, chart, infrastructure and rule tests; CI runs everything |

## Reuse

- The ClickHouse migration files and the golang-migrate format — unchanged; only the runner moves into a binary.
- `kadm` (already in the workers module, group 7) for topics.
- The Caddy edge and its Caddyfile (group 6) on every path, Kubernetes included.
- Group 7's `/healthz`, `/readyz`, `/metrics` and the scheduler's `health` command for every healthcheck and probe.
- `backup.sh`, `restore-*.sh` and `verify.sh` (P1-51) — extended, not rewritten.
- The first-owner and operator seeds at API startup — the install script sets their variables.

## Gaps

Everything in *Impact → Files* that does not exist: CI, the two images, the setup step, the mode, the install
script, the Ansible roles, the chart, the infrastructure code, the monitoring rules, the Azure adapter, the
off-host backup.

## Steps and tasks

### Step 0 — The repository is run like production

Added 2026-10-28 at the founder's request. **Found that day:** the repository was **public**
(`visibility: public`, last updated 2026-10-28) before `going-public` had run. A pattern scan of all 430
commits on every branch found no real secret (one `AKIAEXAMPLE…`, AWS's documentation example) and no
committed `.env`, key or certificate file. At the founder's instruction it was **made private the same day**
through the API (`PATCH /repos/nexovue-tech/nexovue {"private": true}`, answered `visibility: private`).

**What the plan allows.** The organization is on GitHub **Free** (`/orgs/nexovue-tech`: `plan: free`).
On Free, a *private* repository gets no branch protection or rulesets — the protection API answered
**HTTP 403** — and no secret scanning, push protection or CodeQL. All of those are free on a *public*
repository. So what Step 0 can do now depends on Question 3.

*Judgeable:* the repository's settings page and `just repo-audit` show every item below as set, and a push
that would break a rule is refused.

- [x] **T0a** — A deep history scan with **gitleaks** (entropy and rules, every commit) before the repository
  is ever public again; findings recorded in `status.md`, a real one rotated and removed from history.
- [x] **T0b** — Organization settings: two-factor required for members; members may not create public
  repositories; base permission read; web commit sign-off required · proved by `just repo-audit`. *Done 2026-10-28 by the founder in the settings page, with two corrections found on the way: GitHub Free cannot limit members to private repositories, so members may create none (owners always can), and members may not change a repository's visibility. The two-factor requirement is deferred until a second member joins (founder's decision); organization web sign-off left off, since the repository requires it.*
- [x] **T0c** — Repository settings: Actions limited to GitHub's own and verified creators plus an allowlist,
  pinned by commit hash; workflow token **read-only by default**; Actions may not approve pull requests;
  fork pull requests need approval to run; wiki and projects off; auto-delete merged branches; squash merge
  only · proved by `just repo-audit`.
- [x] **T0d** — Dependabot alerts, security updates and version updates (`.github/dependabot.yml`, grouped,
  weekly, for Go, Python, npm, Docker and Actions); the stale `dependabot/github_actions/actions-5c183d6ffa`
  branch (based on an old commit) closed · proved by the settings and a first Dependabot run.
- [x] **T0e** — `SECURITY.md` (private vulnerability reporting switched on), `CODEOWNERS`, `CONTRIBUTING.md`,
  issue and pull-request templates · proved by GitHub showing the security policy.
- [x] **T0f** — `tools/repo-audit` and `just repo-audit`: reads every setting above through the API with the
  founder's token and fails on any drift, so the settings are checked like code · proved by its own test
  against recorded API answers.
- [ ] **T0h** — **`going-public`, folded into this step (founder, 2026-10-28, Question 3 (c)):** the
  contributor terms (the CLA recommended 2026-10-11: Apache-ICLA-based, through CLA Assistant, the browser
  SDK on DCO — text approved by the founder before it is switched on); the founder's call on what in `docs/`
  stays public; a README written for visitors; then the founder switches the repository to public — after
  T0a is clean · proved by T0a's report and the repository's visibility.
- [ ] **T0g** — *After T0h publishes.* A ruleset on `main` (no deletion, no force push, linear history,
  changes through pull requests, the CI checks of T7 required, no bypass), a ruleset on `v*` tags (only the
  release workflow creates them), and — public or paid — secret scanning with push protection and CodeQL.
  Required checks are switched on after T7 makes CI exist.

### Step 1 — Two images and the setup step, built by CI

*Judgeable:* a push turns CI green on GitHub; a tag `v0.1.0-rc.1` puts both images in GHCR for amd64 and
arm64; on an empty machine `docker compose up` with them prepares the stores and starts.

- [x] **T1** — `apps/workers/internal/setup/{migrations,clickhouse,lock,topics}.go` and `apps/workers/cmd/setup/main.go`:
  ClickHouse migrations through a runner of our own on `ch-go` *(founder, 2026-10-28: golang-migrate's
  ClickHouse driver is still clickhouse-go v1.4.3, a 2021 client; the runner keeps its `schema_migrations`
  format, and adds the cross-process lock golang-migrate never had, in Postgres)*, topics through `kadm` with replication and
  `min.insync.replicas` derived from the brokers that answer · proved by `topics_test.go` (the topic plan per
  broker count) and `setup_storeprobe_test.go` (against the dev ClickHouse and Kafka: a second run changes
  nothing).
- [x] **T2** — `apps/api/src/nexovue_api/setup.py` (`python -m nexovue_api.setup`): Alembic `upgrade head`
  under a Postgres advisory lock, then the bucket if missing · proved by `test_setup_live.py` (fresh
  database migrated; a second run a no-op; the bucket created once).
- [x] **T3** — `apps/api/src/nexovue_api/roles.py` and the `nexovue` console script: `api`,
  `scheduler-beat|light|deliveries`, `setup`, `web-assets <dir>` → the exact process each runs · proved by
  `test_roles.py`.
- [x] **T4** — `deploy/images/nexovue.Dockerfile` (API and scheduler in one environment, the console built
  into `/srv/web`, OCI labels) and `deploy/images/nexovue-pipeline.Dockerfile` (`/receiver`, `/worker`,
  `/dlq`, `/setup`); the five per-service Dockerfiles deleted · proved by
  `test_images_contract.py` (two Dockerfiles, every role in both images, labels present) and a build.
- [x] **T5** — `justfile`: `migrate-ch` and `topics` run the setup code; `topics.sh` deleted; the `migrate`
  CLI no longer needed · proved by `just fresh` on the dev stack.
- [x] **T6** — `deploy/compose/docker-compose.app.yaml`: the two images, a `setup` service that every
  service waits on (`service_completed_successfully`), `web-assets-init` from `nexovue` · proved by
  `test_compose_dependencies.py` (extended: nothing starts before setup) and the replica brought up from
  empty volumes.
- [x] **T7** — `.github/workflows/ci.yml`: full history checkout, the toolchain, the stores from the
  repository's Compose file, `just check` · proved by a green run.
- [x] **T8** — `.github/workflows/release.yml`: on a version tag `vX.Y.Z` (or `vX.Y.Z-rc.N`), native
  per-architecture builds pushed by digest and merged, provenance and SBOM, a cosign signature, tags
  `X.Y.Z`/`X.Y`/commit, and a GitHub release with generated notes · proved by the `v0.1.0-rc.1` release,
  pulled back and `cosign verify`'d. *Changed 2026-10-28 (founder, option B): versions are tagged by hand
  rather than by release-please — it reads only Conventional-Commit titles, which this repository does
  not use, and it needs Actions allowed to open pull requests, which Step 0 switched off.*

*Scope changed 2026-10-28 (founder): **built now — 2, 3, 5, 6, 7 and 11**, which any customer needs and
which can be rehearsed on a laptop. **Deferred until a customer needs them — 4 (Azure storage), 8
(Ansible), 9 (Helm), 10 (Terraform):** code written today for a cloud nobody has chosen cannot be proven
without an account and drifts as the providers change; the first customer's cloud and size say which one
to write, in days. Until then a server created by hand in any cloud's console and `./nexovue install`
(Step 3) is the deployment. Their sections stay below as the design for that day. Step and task numbers
are kept, so references stay valid.*

### Step 2 — The deployment mode

*Judgeable:* the same image started as self-hosted shows no Billing, takes telemetry past any allowance and
refuses Razorpay settings; started as hosted, it behaves as today.

- [x] **T9** — `settings.py` (API and scheduler): `NEXOVUE_DEPLOYMENT` (`self-hosted` default), payments
  refused when self-hosted, required in hosted production · proved by `test_settings.py`.
- [x] **T10** — `main.py`: billing and the operator console mounted only when hosted; `/system/info` names
  the mode · proved by `test_deployment_mode.py` (routes absent, 404; info answers the mode).
- [x] **T11** — `apps/receiver/internal/config/config.go`, `cmd/receiver/main.go`, `internal/otlp/gates.go`:
  self-hosted skips the budget but keeps the suspension valve · proved by `gates_test.go`.
- [x] **T12** — `apps/web/src/shared/settings/settings-nav.ts`, the usage screen: Billing hidden and the plan
  omitted when self-hosted · proved by `settings-nav.test.ts` and the usage screen's tests.

### Step 3 — Try it locally, and the self-hosted install

*Judgeable:* on the Mac, `./nexovue quickstart` from the release's kit opens a working console; on a fresh
Lima VM, `./nexovue install` from published images, then from the offline bundle with the network cut, then
`upgrade` and `rollback`.

- [x] **T13** — `deploy/compose/nexovue.yaml` (the self-hosted file, replacing `docker-compose.onprem.yaml`,
  every `TODO` answered, per-image names), `nexovue.hosted.yaml` (the hosted overlay, replacing
  `docker-compose.app.yaml`), `nexovue.quickstart.yaml`; `insight` and `ollama` removed · proved by
  `test_compose_contract.py` (both stacks render; no service without a healthcheck; only the edge publishes
  ports; images by version).
- [x] **T14** — `deploy/nexovue` (bash): `quickstart`, `install [--offline]`, `upgrade`, `rollback` (refused
  past a non-additive migration), `status`; preflight checks; secrets generated · proved by
  `test_install_script.py` (the script against a stub `docker` on `PATH`) and `shellcheck` in CI.
- [x] **T15** — `release.yml`: the install kit and the two offline bundles (OCI archives, `SHA256SUMS`,
  cosign `sign-blob`) attached to the release · proved by the rehearsal.
- [x] **T16** — `docs/self-hosting/{install,upgrade,backup,sizing}.md` for a customer's IT team · proved by
  the rehearsal following the text literally.

### Step 5 — Backups off the host, and a restore on a fresh machine

*Judgeable:* a backup taken on one Lima VM appears in the bucket; restored on a second fresh VM, the console
shows the same data and `backup-verify` passes.

- [x] **T19** — `deploy/backup/common.sh`: the project name and credentials from the install's `.env`, not
  dev defaults · proved by a backup of the replica (`nexovue-app`).
- [x] **T20** — `deploy/backup/ship.sh` and `nexovue backup-ship` (in the image, using T17): upload,
  checksum check, prune to 7 daily and 4 weekly · proved by `test_backup_retention.py` (which snapshots are
  kept) and the rehearsal.
- [x] **T21** — `deploy/systemd/nexovue-backup.{service,timer}` (installed by T14 and Ansible); a CronJob
  in the chart · proved by the rehearsal.
- [x] **T22** — the restore rehearsal, recorded in `docs/operations/disaster-recovery.md` with the version,
  the date and the time taken (objective: back within 4 hours).

### Step 6 — Three Kafka brokers

*Judgeable:* three brokers under load, one stopped, every acknowledged record stored.

- [x] **T23** — `deploy/compose/nexovue.kafka3.yaml` (three KRaft nodes, controller and broker each),
  `setup` deriving replication 3 / `min.insync.replicas` 2 · proved by `topics_test.go` (T1) and a chaos
  run recorded in `status.md`.

### Step 7 — A monitor, ready to connect

*Judgeable:* the `monitoring` profile shows every target up, and an alert fires when a worker is stopped.

- [x] **T24** — `deploy/monitoring/{prometheus.yml,rules/*.yml,rules/*_test.yml,uptime.md}` and the
  `monitoring` Compose profile · proved by `promtool check rules` and `promtool test rules` in CI.

### Step 8 — Path A: Ansible on Linux servers

*Judgeable:* the playbook installs nexovue on one fresh Lima VM, then spread over three by role; upgrade,
rollback and a host restart all leave it working.

- [ ] ~~deferred~~ **T25** — `deploy/ansible/{site.yml,upgrade.yml,rollback.yml,roles/{docker,nexovue,backup}}`,
  inventory groups `edge`, `app`, `kafka`, `clickhouse`, `postgres`, rendering T13's files per host ·
  proved by `ansible-lint` in CI and the rehearsal.

### Step 4 — One object-store interface, S3 and Azure

*Judgeable:* the live suites pass with the store pointed at SeaweedFS and at Azurite, Microsoft's Azure
storage emulator.

- [ ] ~~deferred~~ **T17** — `packages/py-platform/src/nexovue_platform/objectstore/{__init__,s3,azure}.py`: `put`, `get`,
  `delete`, `delete_prefix`, `ensure_bucket`; S3 with Oracle's checksum settings, Azure through
  `azure-storage-blob`; `OBJECT_STORE=s3|azure` · proved by `test_objectstore_live.py` run against both.
- [ ] ~~deferred~~ **T18** — the API's `data/objectstore/` and the scheduler's `probes/files.py` move onto it; their
  boto3 clients deleted · proved by the existing source-map, check-file and erasure suites.

### Step 9 — Path B: the Helm chart

*Judgeable:* on a three-node kind cluster, `helm install` with the three operators gives a working console;
upgrade, rollback, and a node removed.

- [ ] ~~deferred~~ **T26** — `deploy/helm/nexovue/` (Helm 4, chart v2): one Deployment per role, `setup` as a
  pre-install/pre-upgrade hook Job, the Caddy edge, stores `operator|external` (Strimzi, Altinity,
  CloudNativePG resources; external endpoints) · proved by `ct lint`, `helm unittest`, `kubeconform` in CI,
  and the rehearsal.
- [ ] ~~deferred~~ **T27** — `release.yml`: the chart pushed to `oci://ghcr.io/nexovue-tech/charts` with the product
  version · proved by the release.

### Step 10 — Infrastructure for AWS, Azure and Oracle

*Judgeable:* `just infra-check` passes for all three clouds; `status.md` lists, per cloud, what is proven
and what waits for an account.

- [ ] ~~deferred~~ **T28** — `infra/aws/` (VPC, security groups, EC2 or EKS, ALB/NLB, Route 53, S3, state bucket)
  · proved by `tofu validate`, `tofu test` with mocked providers, `tflint`, `checkov`.
- [ ] ~~deferred~~ **T29** — `infra/azure/` (VNet, NSGs, VMs or AKS, load balancer, DNS zone, Blob storage) · same checks.
- [ ] ~~deferred~~ **T30** — `infra/oci/` (VCN, security lists, compute or OKE, load balancer, DNS, Object Storage with its
  S3-compatible keys) · same checks.
- [ ] ~~deferred~~ **T31** — `justfile` `infra-check` and its CI job.

### Step 11 — The SDK release, the runbooks and the records

*Judgeable:* the documents read true against what was rehearsed.

- [x] **T32** — `release.yml`: the SDK on `sdk-vX.Y.Z` through npm trusted publishing, enabled by a
  repository variable once `going-public` is done · proved by a dry run (`npm publish --dry-run`).
- [x] **T33** — `docs/operations/{first-deploy,release}.md`; `deployment.md`, `rollback.md`,
  `backup-and-restore.md`, `monitoring.md`, `production-readiness.md`, `current-system-reference.md`
  updated.
- [x] **T34** — ADR-006 amended (three clouds, OpenTofu, cloud-init with Ansible), ADR-019 marked executed,
  ADR-022 (stores on Kubernetes); this plan's Outcome and `status.md`.

## New risks

- **CI minutes while private.** `just check` with the stores takes several minutes, against 2,000 a month
  on 2-core / 8 GB runners; the live suites may not fit 8 GB alongside ClickHouse. If they do not, CI runs
  them on a schedule rather than every push until the repository is public (4 cores / 16 GB, free).
- **golang-migrate brings `clickhouse-go` v2** into a module that uses `ch-go`; two ClickHouse clients in
  one binary only for `/setup`. Accepted: the setup binary is the only importer.
- **The Caddy edge on Kubernetes** keeps its certificates on a volume, so it runs one replica until
  certificates move to shared storage — the same as Compose today.
- **Rehearsals need memory.** Three Lima VMs or a three-node kind cluster with the stores is about 16-20
  GB; the multi-host runs are done one at a time.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Should the bucket (uploaded check files, source maps) be in the backup? *Assumed yes* for self-hosted with the bundled SeaweedFS — the backup copies it; on a cloud the provider's storage is durable and versioning is turned on instead. | No | `design.md` §8 |
| 2 | Whether CI's live suites fit the private runners | No — measured in T7 | `status.md` |
| 3 | How `main` gets protected while private: (a) **GitHub Team**, about $4 per user a month (third-party figures; GitHub's own pricing page to be read before paying), which adds branch protection and rulesets to private repositories; (b) stay on Free and private, with T0b-T0f only and no enforced protection until public; (c) finish `going-public` and publish, which makes T0g free. Secret scanning and CodeQL on a private repository are separate paid products on any plan. | **Answered 2026-10-28: (b) now, then (c)** — Free and private with T0b-T0f; T0g when `going-public` publishes. | `status.md` and ADR-004's publication note |

## Gates

- [x] Specification approval — `change.md`, 2026-10-11
- [x] Design approval — `design.md`, 2026-10-11
- [x] **Plan approval — before the first edited file** — 2026-10-28, Step 0 included
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Design QA — the self-hosted Billing removal against the design
- [x] Production-readiness assessment — NOT READY, blockers A1-A7
- [x] Documentation and status update

## Validation plan

- Every step: `just check` once at the end of the step, counts read.
- Step 1: a real CI run and a real release candidate. Steps 3, 5, 8, 9: the rehearsals in `design.md` §11,
  each recorded in `status.md` with what was run and what was seen. Step 10: offline checks only, stated as
  such.

## Rollout and rollback

Nothing is deployed. The development stack keeps its own Compose file and dev Dockerfiles throughout; the
production replica moves to the two images in Step 1 and is the first thing re-checked. Each step is its
own commit, so any step can be reverted alone. The real risk is the opposite of a rollout: infrastructure
code that is never applied drifts — `status.md` keeps the unproven line per cloud, and `first-deploy.md`
begins by running `tofu plan` against the real account.

## Outcome

Built 2026-10-28: Steps 0-3, 5, 6, 7 and 11. Steps 4, 8, 9 and 10 deferred by the founder until a customer
chooses a cloud (scope note above). What changed from the plan, and why:

- **Compose files (T13).** `nexovue.yaml` is the self-hosted install *and* the base every layout overlays
  (`nexovue.hosted.yaml`, `nexovue.quickstart.yaml`, `nexovue.kafka3.yaml`, and `nexovue.build.yaml` for
  the replica), where the plan had separate files. One definition of every service; overlays change
  settings only, which `test_compose_contract.py` holds. The stores' install settings moved into one
  `compose.infra.deploy.yaml` (the onprem and hosted variants are deleted). `docker-compose.app.yaml` and
  `docker-compose.onprem.yaml` are deleted, with `insight` and `ollama`.
- **The bundled object store** writes its one identity from the install's own S3 keys at start; every
  deployed install used to share the repository's development keys (`deploy/seaweedfs/s3.json`). Not in
  the plan; found reading the files.
- **ClickHouse's memory cap** became a setting (`memory.deploy.xml`, `CLICKHOUSE_MAX_SERVER_MEMORY`); the
  fixed 4 GiB could not follow a server's size.
- **The quickstart** runs `ENV=development` on plain http at `localhost:8080`/`4318` — the only
  environment whose cookies and OTLP address work without TLS — through the same edge with
  `NEXOVUE_SITE_SCHEME=http`.
- **Rollback (T14)** is decided by comparing the schema each version's images carry (`nexovue setup
  schema`, `/setup schema`): same schema, swap back; changed, restore the pre-upgrade backup. The plan
  said "refused past a non-additive migration"; nothing records which migrations are additive, so this
  is the conservative form. **A decision for the founder** (`status.md`).
- **The install keeps its state in `/opt/nexovue`** with each version's kit in `releases/`, so an upgrade
  runs from the new kit and a rollback still has the old one's Compose files.
- **Offline bundles (T15)** are `docker save | gzip` per architecture of every image the install runs,
  not OCI archives through skopeo: one file `docker load` reads with nothing else installed. Each file
  and the kit are signed with `cosign sign-blob`.
- **Backups (T19-T22)** ship through `nexovue backup` in the `nexovue` image (`nexovue_api.backups`, boto3)
  rather than through T17's interface, which was deferred with Step 4. The restore drill ran on the Mac,
  not two Lima VMs.
- **The monitor (T24)** gained `NexovueServiceMissing`: the rehearsal showed a stopped container leaves
  discovery and `up == 0` never fires.
- **The SDK release (T32)** is its own workflow, `release-sdk.yml`, on `sdk-v*` tags; the plan put it in
  `release.yml`, where every job would need a condition. Its proof is `npm pack --dry-run` — `npm publish
  --dry-run` refuses a package still marked `private`, which it stays until going public.
- **ADR-022** (stores on Kubernetes) is not written: it belongs to Path B, deferred.

**Not done, and owed before the first customer:** `nexovue install` on a fresh Linux machine, the offline
bundle with the network cut, an upgrade and rollback between two released versions, and the restore
drill across two machines — Lima or a real server. And the first release carrying the kit.

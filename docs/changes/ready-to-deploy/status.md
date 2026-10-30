# Status: ready-to-deploy

## Lifecycle status

In review — Steps 0-3, 5, 6, 7 and 11 built and rehearsed on the founder's Mac; Steps 4, 8, 9, 10 deferred
(founder, 2026-10-28). Uncommitted: the founder asked for every step's code before one commit.

## Current task

The self-review and the full gate, before asking to commit.

## Completed

- Scope decided by the founder, 2026-10-11: nothing hosted yet, everything ready; both ADR-006 paths;
  Terraform for AWS, Azure and Oracle; GitHub Actions and public images on GHCR; self-hosted and
  try-it-locally included.
- `change.md`, approved 2026-10-11.
- Research, 2026-10-11, behind `design.md`:
  - *Kubernetes stores:* Strimzi 1.2 (KRaft-only, Kafka 4.2-4.3.1), Altinity clickhouse-operator 0.27.4,
    ClickHouse Inc.'s operator at v0.0.8, CloudNativePG 1.30.1 (backups through the Barman Cloud
    plugin), Bitnami's free versioned images withdrawn in 2025, Confluent's operator licensed; kind for
    multi-node rehearsal, OrbStack licensed for commercial use.
  - *Releases:* `ubuntu-24.04-arm` standard runners (free for public repositories, in private ones
    since 2026-01-29), native per-architecture builds merged by digest, provenance/SBOM attestations,
    keyless cosign, OCI archives for offline bundles, npm trusted publishing (needs a public
    repository), Lima for a free scriptable VM.
  - *Infrastructure:* no S3 API for Azure Blob and MinIO's community edition archived; Oracle's
    S3-compatible endpoint path-style with boto3 checksums `when_required`; OpenTofu 1.13 (MPL) and
    Terraform 1.16 (BSL) on the same providers (aws 6.68, azurerm 5.9, oci 9.9); `tofu test` with mocked
    providers proves wiring, not quotas or IAM; Trivy's 2026 supply-chain compromise (prefer checkov);
    `community.docker.docker_compose_v2` for Ansible; Oracle Always Free is 2 OCPU / 12 GB (ADR-006
    confirmed).
- The broken sentence in `production-readiness.md`'s fix order, item 9, restored from `e47280b`.

## Next action

The founder reviews and commits. Then the rehearsals only a Linux machine can give — install, offline,
upgrade and rollback between two releases, the two-machine restore — on a Lima VM or the first server,
and the first `v*` release to carry the kit.

## Open decisions

1. **Rollback across a schema change**: decided by the founder, 2026-10-30. The conservative rule is kept:
   any change in either store's newest migration makes `rollback` a restore of the pre-upgrade backup.
   Marking migrations additive waits until a customer needs it.
2. **The Linux rehearsals** (readiness A1, A3): no Lima and no CI job (founder, 2026-10-29) — a friend
   of the founder installs from the kit on a Linux server and reports back. Needs the first release
   carrying the kit, and read access to the images while the repository and its packages are private.

Plan Question 3 answered 2026-10-28: Free and private now (T0b-T0f), protection rulesets (T0g) when
`going-public` publishes.

## Blocked

Nothing.

## Risks

- **The repository was public until 2026-10-28** with no `going-public` work done. The deep scan (T0a)
  found no live secret.
- **Never run on Linux.** Everything in Steps 3 and 5 ran on Docker Desktop on a Mac (bash 3.2, BSD tools).
  The script is written for both and `shellcheck` passes, but GNU-only paths (`mv -T`, `sha256sum`, systemd
  units) have run only in the tests' stand-in or not at all.
- **Offline bundle size.** About 1.5 GB per architecture by estimate, against GitHub's 2 GiB per file; the
  release refuses a file over 1.9 GB rather than failing at upload. Not measured until the first release.
- **One broker by default.** A broker failure refuses telemetry until it returns (nothing accepted is
  lost); three brokers must be chosen at install.

See `change.md`, *Risks and rollback*.

## Validation

Step 0, 2026-10-28:
- **T0a** — gitleaks 8.30.1 over all 428 commits on every branch: 35 findings, each reviewed — test
  fixtures, the development compose file's dev-only keys, AWS's `AKIAEXAMPLE…` documentation key, the
  fabricated tokens the masking tests must catch, one false positive in `auth/totp.py`. None is a real
  secret. Their fingerprints are in `.gitleaksignore`; the history scan now reports no leaks. The
  working-tree scan's other hits are in files git ignores (`.env`, `.env.replica`, the pnpm store).
- **T0c** — applied through the API and read back: projects off, squash merge only, merged branches
  deleted, branch updates offered, web commit sign-off required; Actions limited to GitHub's own,
  verified creators and 15 named actions, **pinned by commit hash required**; the workflow token read-only
  and unable to approve pull requests; Dependabot security updates on (alerts were already on).
  Before-values kept in the session's scratch copy: projects on, all three merge methods, no branch
  deletion, Actions `all` with no pinning.
- **T0d** — `.github/dependabot.yml` (Go, uv, npm, Docker, Actions; weekly, grouped, five-day cooldown);
  the stale pull request #1 closed with a comment.
- **T0e** — `SECURITY.md`, `.github/CODEOWNERS`, `CONTRIBUTING.md`, issue forms and the pull-request
  template. GitHub's private vulnerability reporting is switched on at publication (T0g).
- **T0f** — `just repo-audit`: "repository settings match", the organization's settings included;
  `tools/repo-audit/test_repo_audit.py` 5 passed (the applied settings pass; public, alerts off, an
  unreadable setting and members able to change visibility are each drift).
- **T0b** — read back through the API: base permission read; members may create no repository and
  may not change a repository's visibility; two-factor required, secure methods only, every member
  enrolled (`Saro006` pending the move off SMS).

Step 1, 2026-10-28:
- **T1** — `go test ./internal/setup` (unit: loading, up, rerun, dirty, down, topic plan per broker count);
  `setup_storeprobe_test.go` against the dev ClickHouse: from empty, again, two down, two up. Against the
  dev database golang-migrate built: "0 migration(s) applied, 33 known". A fresh database migrated by
  the runner is **identical** to the dev one, table by table, engine by engine, column by column.
- **T2** — `test_setup_live.py` 3 passed: from empty to head and again; the bucket created once; **two
  setups started together** — red before the advisory lock (`duplicate key … alembic_version_pkc`),
  green after, three runs in a row. Every migration suite re-run: 81 passed.
- **T3** — `test_roles.py` 4 passed; `nexovue` prints its roles.
- **T4** — both images built locally. The pipeline image carries the four binaries and the 66
  migration files, runs as nonroot, and its worker embeds the commit — the old images set
  `main.gitSHA`, a variable no package declares, so every image reported "unknown". A `.dockerignore`
  now keeps the `.env`, caches and test suites out of every build (the image had held a 37 MB mypy
  cache); `nexovue` is 615 MB (the API's and the scheduler's libraries together, as ADR-019 expected).
  Roles run from the images: `web-assets` copied 102 files, `setup` and `/setup` against the dev stack.
  `test_images_contract.py` 4 passed.
- **T5** — `just migrate` and `just topics` run the new code; `topics.sh` and the `migrate` CLI gone.
- **T6** — the replica, under a scratch project, **from empty volumes**: 33 ClickHouse migrations, 8 topics
  created, Postgres to head, the bucket, 102 console files, then every service healthy and the console
  200 through the edge; a second setup changed nothing. The ordering test fails with an API that does
  not wait for setup (seen) and passes with it.
- **Found on the way:** `test_pollers.py`'s AWS client test read the developer's own `~/.aws/config`,
  which an `aws login` session (2026-10-26) broke; it now runs on an empty configuration.
- `just check` exit 0: Python 4,412 passed, web 1,366, SDK 124; mypy over 638 files clean. `actionlint`
  clean on both workflows.

- **T7, CI on GitHub** — green on the eighth run (`40674598770`, 21 min): Go 40 packages, Python 4,413
  passed, web 1,366, SDK 124, lint, types, contracts and bundle sizes. The seven runs before it each
  found something only a fresh Linux machine shows, all fixed at the cause, none a product bug:
  `setup-just` pulled in an action outside the allowed list (just installed through uv); Compose v2.38
  refuses an included service redefined by its includer (each file's adjustments now sit in a file
  listed with the include — a self-hosted install on older Docker would have hit it too); the stack
  started its services before the setup step (`just stack` runs stores, setup, services — CI and
  `just dev` alike); git refused the bind-mounted tree as "dubious ownership" (the dev Go image trusts
  it); `host.docker.internal` exists on Linux Docker only when asked (mapped for the probe, delivery and
  insight services); five `uv run`s installed into one empty shared environment at once
  (`python-deps` fills it first); suites chose "the busiest project", which is the demo shop only on a
  laptop (each now chooses the project holding its readings; gateseed also sends
  `http.server.request.duration`); the dev SeaweedFS sized its volume count from free disk (fixed at
  200); a trace window bounded by the clock hour missed traces seeded at minute two (it follows the
  data); the runner's disk was 96% full (CI frees unused toolkits first); the web container's
  root-owned `pnpm install` into the shared tree locked the host's tests out (each package's
  node_modules is a volume). Before pushing each fix, `just check` passed locally — which is exactly
  why these were invisible there.

- **T8, releases** — `v0.1.0-rc.1` built all four images and failed to publish: the `nexovue-*` digest
  pattern also matched `nexovue-pipeline-*`, and GitHub stores attestations only for public or paid
  repositories. `v0.1.0-rc.2` published both images for amd64 and arm64, labelled with the tagged
  commit, run as non-root; `cosign verify` confirms each was signed by `release.yml` on a `v` tag. It was
  also published while its commit's CI was still running — so a release now waits for that commit's CI
  result and builds nothing unless it passed; `v0.1.0-rc.3` proved it (check 12:05-12:26, builds from
  12:26).
- **Dependency security batch, 2026-10-28** (founder, option B with an audit): 63 Dependabot alerts.
  gRPC 1.83.2 (not 1.84: 1.84 answers an undecodable request INTERNAL before the receiver's handler can
  answer INVALID_ARGUMENT — two receiver tests caught it); certifi, urllib3, aiohttp, PyJWT, Mako,
  multidict, dnspython, opentelemetry-api; vite 8.3.5, vitest 4.1.12, undici, markdown-it,
  source-map-js; the Go and Node base images. **`just audit`** (govulncheck reachability, pip-audit over
  the runtime dependencies, `pnpm audit --prod`, high and above) now runs in CI, and on its first run
  found what Dependabot did not: **Go 1.27.0 itself**, with twelve standard-library flaws reachable
  from the receiver's HTTP server — the project now requires Go 1.27.3, and x/net 0.60.0.
  golangci-lint 2.15.0 (2.13.1 cannot read Go 1.27.3's export data).
- **Deferred to a follow-up change, none a security fix:** TypeScript 7, Vitest 5, jsonpath-rfc9535 2,
  @types/node 26, Biome 2.6 (now formats Markdown and YAML — 220 files — and adds lint rules), gRPC 1.84
  (needs the receiver to decode requests itself), the Go minors that need gRPC 1.84 (pgx, franz-go,
  client_golang, otlp 1.11.1), and @tanstack/react-virtual 3.14.23, which renders no rows in jsdom
  (pinned at 3.14.10 until it is understood).

- **Step 2, the deployment mode** — `NEXOVUE_DEPLOYMENT` (`nexovue_platform.deployment`; self-hosted unless
  set). Self-hosted: billing, the operator console and support access are not mounted; a payment-processor
  key refuses to start; the scheduler schedules no payment-grace sweep; the receiver enforces no monthly
  allowance but still refuses a deleted organization and a closed stop valve; the console hides Billing
  and Support access and shows the usage screen without cost, rate, plan or allowance. Proved by
  `test_deployment_mode.py` (6), `test_beat_schedule.py`, `TestASelfHostedInstallHasNoMonthlyAllowance`,
  the usage view's self-hosted test and `settings-nav.test.ts`; `/system/info` names the mode on the dev
  stack. **Differences from the design:** support access is hosted-only too (it exists for nexovue's
  staff, who have no access to a customer's install); the hosted service does not *require* a processor
  key in production, because the replica runs as production with none and the routes already refuse by
  variable name; the console learns the mode from `/system/info`, not the sign-in identity, because it is
  a fact about the install. `just check` exit 0: Python 4,424, web 1,368, SDK 124.

- **Steps 3, 5, 6, 7, 11** — built 2026-10-28; what changed from the plan is the plan's *Outcome*.
  Proved by: `test_compose_contract.py` (34 — every layout rendered by Compose itself: only the edge
  publishes, every service restarts and is health-checked, images by version, the mode per layout, no
  development key anywhere, the overlays add no lists), `test_install_script.py` (17, against a stand-in
  Docker), `test_backup_retention.py` (4), `test_backups_live.py` (4, real S3), `test_setup_schema.py`,
  `cmd/setup/main_test.go`, `promtool test rules` (8 cases) and `shellcheck` in `just lint-deploy`.
  **Rehearsed on the Mac**, from images built from this checkout:
  - *Quickstart* — healthy in 23 s; the owner signed in; an ingest key made; a span sent to `:4318`
    through the edge read back as a trace; `/system/info` said `self-hosted`, billing answered 404.
  - *Three brokers (Step 6)* — replication 3, `min.insync.replicas` 2 on every topic. 75 s of load with
    `kafka-2` stopped for 28 s: 2,627 accepted, 0 refused, 2,627 records in Kafka, the worker accounted
    for every one (351 kept, 2,276 sampled out), every partition back to three in-sync replicas.
  - *Backup and restore (Step 5)* — shipped off the host (the dev stack's SeaweedFS standing in), the
    install and its local backups deleted, a fresh start with the saved settings, restored from the
    off-host copy: `verify.sh` passed, 351 spans and 2 keys back, the bucket object byte for byte, the
    owner signed in — 72 s in all (`disaster-recovery.md`).
  - *Monitor (Step 7)* — six jobs up; the worker stopped; `NexovueServiceMissing` fired at two minutes and
    reached Alertmanager.
  **Defects the rehearsals found, each fixed with a test seen red first:** the masking secret generated
  URL-safe, which the worker refuses (3 of 6 runs red); a backup whose upload failed reported success
  (bash 3.2 does not stop inside `$(…)`); a Mac's `tar` wrote `._` entries before `SHA256SUMS`; the
  restore dropped the telemetry database and then connected into it; a stopped service never raised
  `up == 0`; `install` would carry on with an empty answer, and leave half-written settings.
  The self-review added one more: a snapshot's file names, read back from the off-host bucket on a
  restore, are refused unless plain (`../x` would land outside the snapshot).
  **Gate, 2026-10-28:** `just check` — formatting, ruff, mypy, golangci-lint, biome, `lint-deploy`
  (shellcheck, promtool, amtool) and the Go tests clean; Python 4,499 passed (3 skipped), web 1,368,
  SDK 124; contracts and bundle checks pass. The first run failed one test — the monitor starting before
  the setup step — which is deliberate (it must watch a failing setup) and now recorded in
  `test_compose_dependencies.py`.

- **Design QA, 2026-10-29** — the self-hosted console against design §4, on a running quickstart.
  The settings menu, the usage figures, billing and support routes in the API: as designed. **Three
  mismatches, fixed and seen fixed in the console:** the usage screen's sentences still spoke of a bill
  (`describeScope`, `ownPeriodNote`, `describeFamily`; `UsageView.test.ts` red with the old sentence,
  green with the fix); `/settings/billing` and `/settings/support` opened their screens, which failed
  to load, instead of the console's "No screen at this address" (`app/HostedOnly.tsx`,
  `HostedOnly.test.ts`); and a closed self-hosted install's sign-in offered "Start sending traces",
  leading to "Choose a plan at nexovue.com" — now "Ask whoever runs nexovue for your organization to
  invite you" (`LoginScreen.test.ts`, `SignupScreen.test.ts`). Console: biome, `tsc` and 1,374 tests.
- **Production-readiness assessment, 2026-10-29** — **NOT READY** for a first rollout, seven blockers
  (A1-A7), written into `docs/operations/production-readiness.md`. Every one is closed by a run on
  Linux, a real hostname, a release, or a decision at the first deployment — none by more code here.
  Blockers 2 and 5 of the earlier review are marked fixed by this change.

- **Going public (T0h), 2026-10-29** — the lawyer approved `CLA.md` without change; it is in force and
  CLA Assistant is linked (its webhook on the repository fires on every pull request; the gist matches
  `CLA.md` word for word). `CONTRIBUTING.md` names the CLA and the browser SDK's DCO sign-off;
  `README.md` is written for visitors. `docs/operations/production-readiness.md` left the repository
  (its earlier versions stay in the history). The founder made the repository and both image packages
  public; both images pull with no login (HTTP 200 on `0.1.0-rc.3`). CLA Assistant lists only public
  repositories, which is why it was linked after the switch.
- **Protections (T0g), 2026-10-29** — rulesets read back from GitHub: `main` refuses deletion and force
  pushes, keeps history linear, takes changes only through pull requests (squash, no approvals — one
  maintainer) with CI's `check` green and up to date, no bypass; `v*` and `sdk-v*` tags cannot be moved
  or deleted and only admins create them. Secret scanning with push protection, CodeQL (default setup)
  and private vulnerability reporting are on.

- **The one-line install and a publishable SDK, 2026-10-29** — `deploy/install.sh`, attached to every
  release with its version written in: downloads that release's kit over HTTPS, checks it against the
  release's `SHA256SUMS`, then runs `nexovue install`, or `upgrade` where nexovue is installed.
  `test_get_script.py` (4, against a stand-in `curl`): this release's URLs only, upgrade on an installed
  server, a tampered kit never run, the repository's own copy refusing. The browser SDK is no longer
  `private`, names this repository (npm's trusted publishing requires it), and `npm publish --dry-run`
  passes.

- **The browser SDK on npm, 2026-10-29/30** — the `nexovue` organization created; `0.1.0` published by
  hand by the founder (npm needed the package to exist before its settings page did); trusted publisher
  `nexovue-tech/nexovue` / `release-sdk.yml`, **stage only** (founder, on the recommendation): the
  workflow now runs `npm stage publish`, and a version goes live only when the founder approves it with
  his second factor. `release.md` says how. The first version from GitHub is `0.1.1`.

## Specification impact

The deployment mode touches billing's specification: a self-hosted install has no billing at all. F37's
catalog entry is now "built, not yet on a server".

## Documentation impact

New: `docs/self-hosting/{install,upgrade,backup,sizing}.md`, `docs/operations/{first-deploy,release}.md`.
Rewritten: `deployment.md`, `rollback.md`, `backup-and-restore.md`, `disaster-recovery.md`. Updated:
`monitoring.md`, `first-accounts.md`, `production-readiness.md` (rows and blockers 9, 10),
`operations/README.md`, `current-system-reference.md` §16, the F37 catalog entry, ADR-006 (amended),
ADR-019 (executed).

## Last updated

2026-10-28

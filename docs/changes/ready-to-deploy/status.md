# Status: ready-to-deploy

## Lifecycle status

In development — plan approved 2026-10-28; Step 0 started

## Current task

Step 0 — the repository run like production; T0a, the deep history scan.

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

Finish Step 0 (T0a-T0f), then Step 1.

## Open decisions

None. Plan Question 3 answered 2026-10-28: Free and private now (T0b-T0f), protection rulesets (T0g)
when `going-public` publishes.

## Blocked

Nothing.

## Risks

- **The repository was public until 2026-10-28** with no `going-public` work done. A pattern scan found
  no real secret; the deep scan (T0a) is still owed before it is ever published again.

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

## Specification impact

None yet. The deployment mode will touch billing's specification.

## Documentation impact

Listed in `change.md`. ADR-006 is to record the three-cloud amendment.

## Last updated

2026-10-28

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
- **T0f** — `just repo-audit`: "repository settings match"; `tools/repo-audit/test_repo_audit.py` 4 passed
  (the applied settings pass; public, alerts off and an unreadable setting are each drift).

## Specification impact

None yet. The deployment mode will touch billing's specification.

## Documentation impact

Listed in `change.md`. ADR-006 is to record the three-cloud amendment.

## Last updated

2026-10-28

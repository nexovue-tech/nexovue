# Change: ready-to-deploy — nexovue can be installed anywhere, the day a customer arrives

## Classification

Infrastructure and Operations. Readiness group 8 (`docs/operations/production-readiness.md`: blockers
1, 2, 5, 6, 9, 10, the open half of 7 and 11, three Kafka brokers, the SDK's release), **re-scoped by
the founder on 2026-10-11**: nothing is bought or deployed now. Everything a deployment needs is
written and rehearsed, so that on the day the first customer pays, a cloud is chosen, the code is
run, and nexovue is live.

## Current behavior and problem

Read from the repository on 2026-10-11:

1. **No CI, no registry, no release.** There is no `.github/` directory. Five images are built on a
   laptop (`apps/{api,scheduler,receiver,workers,web}/Dockerfile`), tagged `dev`, and pushed nowhere.
   Nobody can say which commit a running image came from, and there is nothing to roll back to or for a
   self-hosted operator to pull. ADR-019 decided two images (`nexovue`, `nexovue-pipeline`, amd64 and
   arm64) on 2026-10-10; it is not executed.
2. **No infrastructure code.** ADR-006 (2026-09-16) decided Terraform for the infrastructure and two
   delivery paths onto it — **A**: Ansible running the Compose stack on Linux servers; **B**: Helm on
   Kubernetes. None of the three exists.
3. **A deploy does not prepare its stores (blocker 5).** Migrations, Kafka topics and the bucket are
   created by hand: `deployment.md` step 3 lists them as manual. A new image against an old schema
   starts services whose queries fail; a fresh install's receiver answers 503 to every export.
4. **The self-hosted install has never run and could not (blocker 10).**
   `deploy/compose/docker-compose.onprem.yaml` carries twelve `TODO`s ("Do not deploy this file until
   each TODO is answered", `:3-4`), including the registry (`:20`), sizing (`:91`), the cold tier
   (`:117`) and backups (`:366`).
5. **A self-hosted install believes it is the hosted service (blocker 9).** No setting says which it
   is (`settings.py` has none): it shows the Razorpay billing screen and applies plan caps to the
   operator's own disk.
6. **No way to try nexovue locally.** The only local stack is the development one (`just dev`): it
   builds everything from source and budgets about 10 GB. Someone evaluating nexovue has nothing to run.
7. **No restore has been done anywhere but this laptop (blocker 6)**; the one drill (2026-09-04)
   predates about twenty migrations, and `backup-and-restore.md` has no schedule, retention, off-host
   destination or recovery objectives.
8. **One Kafka broker.** `topics.sh` now sets `min.insync.replicas` for more than one broker
   (group 9), but every file runs one; losing its disk loses whatever the workers had not stored.
9. **Nothing reads what group 7 reports (blocker 7, open half).** Every service answers `/healthz`,
   `/readyz` and `/metrics` (ADR-021); no monitor configuration exists.
10. **Things only a real host can finish**, with nowhere written down how: verifying the email
    sender's domain (blocker 11), real DNS and TLS, `TRUSTED_PROXY_COUNT` behind a load balancer.
11. **The browser SDK is unpublished** (`"private": true` at 0.1.0).
12. **A broken sentence** in `production-readiness.md`'s fix order, item 9 (left by the group 9 update).

## Intended behavior

**Founder decisions, 2026-10-11:**

- **Nothing is hosted yet; everything is made ready.** Each path is rehearsed where it can be at no cost.
- **Both ADR-006 paths** — A (Linux servers, Ansible + Compose) and B (Kubernetes, Helm).
- **Terraform for three clouds: AWS, Azure and Oracle.** This amends ADR-006 ("one provider's
  Terraform is written now"); the amendment is recorded in that ADR.
- **GitHub Actions builds; GitHub's registry (GHCR) stores; images are public.** The repository is
  to be published under ADR-004 (AGPL-3.0, SDK Apache-2.0), so a private image would hide nothing.
  Making the repository public is its own change, `going-public` (contributor terms, a history scan for
  secrets, what in `docs/` stays private) — not this one.
- **Self-hosted is in scope**, and so is a **try-it-locally** install for developers, which ADR-019
  already allows as "a laptop-only 'try it' mode on top of the two images".

What exists when this is done:

1. **Releases.** CI runs `just check` on every push; a version tag builds the two ADR-019 images for
   amd64 and arm64, tagged with the version and the commit, published to GHCR, and also saved as an
   **offline bundle** (the images as files, with checksums) for installs with no internet.
2. **Every deploy prepares its stores**, the same way on every path: one step that runs the Postgres
   and ClickHouse migrations, creates the Kafka topics and the bucket, and exits — before any service
   starts.
3. **A deployment mode** — hosted or self-hosted — that decides billing and plan caps.
4. **Three installs from the same images:**
   - *Try it locally*: one command on a laptop, `http://localhost`, small, a first account made for
     you.
   - *Self-hosted*: the on-prem Compose file with every `TODO` answered, plus an install guide written
     for a customer's IT team.
   - *The hosted cloud*: Terraform for AWS, Azure and Oracle, then Path A (Ansible + Compose) or Path B
     (Helm).
5. **Three Kafka brokers** as the hosted layout and a self-hosted option.
6. **A restore rehearsed on a fresh machine** from a real backup, with the schedule, retention,
   off-host copy and recovery objectives written down.
7. **A monitor ready to connect:** scrape configuration and alert rules for what group 7 reports.
8. **A first-deploy runbook** for what only a real host can do: DNS, TLS, the email domain, the
   monitor, the load balancer's proxy count.
9. **The browser SDK publishable** from the same release workflow.

## Affected specifications and architecture

- **ADR-006** amended (three clouds). **ADR-019** executed (two images; the web image goes).
- `current-system-reference.md` (images, deploy step, deployment mode), `deployment.md`,
  `backup-and-restore.md`, `disaster-recovery.md`, `rollback.md`, `monitoring.md`,
  `production-readiness.md`.
- The deployment mode touches billing and usage limits in the API and console.
- New ADRs where the design makes a choice that is expensive to reverse (likely: how stateful stores
  run on Kubernetes).

## Risks and rollback

- **Terraform cannot be proven without accounts.** It is checked as far as possible offline
  (`terraform validate`, linting, a plan against mocked providers where the tooling allows); its first
  real run happens on the day. The plan says plainly what remains unproven for each cloud.
- **Path B's stateful stores.** Kafka, ClickHouse and Postgres on Kubernetes normally need operators;
  which ones is a research question for the design (ADR-006, *Consequences*).
- **Azure has no S3 API.** Decision 0037 puts deployment object storage on AWS S3, and the code speaks
  only S3 (`data/objectstore/store.py`, the scheduler's probe files, ClickHouse backups). Oracle's
  object storage offers an S3-compatible API; Azure Blob does not. The design must choose how an Azure
  install stores objects.
- **CI minutes.** The Free plan includes 2,000 minutes a month for a private repository; once public,
  standard runners are free. ARM builds may need ARM runners or emulation — a design question.
- **The live test suites need the stores running.** CI has to start them, or `just check` there skips
  the suites that matter most.
- Rollback: nothing here changes how the development stack runs. Every path is additive until the
  web image is retired; that step is reverted by restoring the image.

## Plan and validation

`design.md` (each path, the image layout, the deploy step, the mode, the stateful-store research, the
Azure storage answer), then `plan.md` (`/write-plan`), each approved before anything is edited.

Validation, by what can be proven at no cost:

- CI: green on a real push; a tag publishes both images for both architectures to GHCR, pulled back
  and run.
- Try-it-locally and self-hosted: installed on a **fresh local virtual machine** from the published
  images (not from source), sent telemetry, read back.
- Path A: Ansible against that fresh VM, then an upgrade to a newer tag, then a rollback.
- Path B: Helm on a local Kubernetes cluster (kind or k3d), the same three checks.
- Restore: a backup taken on one machine, restored on a fresh one, the console reading the data.
- Kafka: three brokers, one stopped, nothing lost.
- Terraform: `validate` and lint for all three clouds; recorded as unproven until applied.

## Status

See `status.md`.

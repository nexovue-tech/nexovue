# ADR-006: Terraform for infrastructure, with two delivery paths — Ansible on Linux servers, Helm on Kubernetes

## Status

Accepted 2026-09-16 — explicit founder decision. Not yet executed: no Terraform, Ansible or Helm
exists in the repository, and no hosting target is chosen.

## Context

nexovue has no production deployment. It will get one when the first cloud customer pays
([ADR-008](ADR-008-the-website-takes-the-first-payment.md)), and the budget that customer brings
decides what that deployment can afford. Until then it is impossible to know whether the first
production environment should be a few Linux servers or a Kubernetes cluster.

Two further requirements were stated alongside:

- **Switching cloud provider must stay possible at any time** — starting on Oracle, moving to AWS,
  or anywhere else — including moving the data ([ADR-007](ADR-007-migrating-between-clouds.md)).
- **Scaling must be possible at every level the chosen path offers**: pods and nodes on Kubernetes,
  additional servers on Linux.

One fact checked before deciding, because it was the number the first plan rested on: **Oracle
Cloud's Always Free Ampere A1 allowance is 2 OCPUs and 12 GB of memory**, halved from 4 and 24 on
15 June 2026. Oracle's own page reads *"For Always Free tenancies, this is equivalent to 2 OCPUs and
12 GB of memory"*, adds that idle instances may be reclaimed, that they must be created in the home
region, and that "out of host capacity" is an expected error. The development stack budgets about
10 GB for its core services (`deploy/compose/docker-compose.yaml` header) and ClickHouse alone idled
at 2.40 GiB this month. So the full stack barely fits a free Oracle instance and Kubernetes on top of
it does not. That instance suits staging or a demo, not a paying customer.

## Decision

**Terraform always provisions the infrastructure. Delivery onto it takes one of two paths, chosen
per deployment when the first paying customer arrives.**

```
              CI  →  one set of images (amd64 + arm64), one configuration contract
                                     │
                               TERRAFORM (always)
                network · firewall · DNS · load balancer · object storage
                          one module per cloud provider
                                     │
              ┌──────────────────────┴───────────────────────┐
     Path A — Linux servers                        Path B — Kubernetes
     Terraform creates VMs                         Terraform creates cluster + node pools
     ANSIBLE configures them and                   HELM deploys the same images
     runs the Compose stack                        as charts
     scale: more VMs behind the LB                 scale: pods (HPA), nodes (autoscaler)
```

**The rule that makes two paths affordable: Ansible and Helm are delivery layers only.** The images,
the environment variables, the migrations, the health checks and the backup format are identical on
both paths. A feature that needs "a Kubernetes version" and "a Linux version" of itself breaks this
rule and doubles the cost of everything after it.

**Region is an input to Terraform from the first line written**, default `india`, mapped per
provider, with separate state per region. [ADR-009](ADR-009-regions-are-cells.md) covers what a
region means for customers.

**One provider's Terraform is written now.** The application stays portable — it already speaks the
S3 API and standard Postgres, ClickHouse and Kafka — but a second provider's module is written when a
switch is actually made, not in advance.

## Amendment, 2026-10-28

Founder decisions in `docs/changes/ready-to-deploy/`:

- **Three clouds — AWS, Azure and Oracle** — rather than one written now; **OpenTofu** rather than
  Terraform (the same providers, MPL-licensed, state encryption); **cloud-init bootstraps and Ansible
  deploys** on Path A.
- **None of the three, nor Ansible or Helm, is written yet.** Nothing is hosted, and infrastructure code
  that cannot be applied cannot be proven and drifts as providers change. The first customer's cloud and
  size decide which one is written, in days. Until then the deployment is a server created by hand in that
  cloud's console and the self-hosted install (`./nexovue install`), which is ADR-006's own observation
  that Path A is mostly F37.

## Rejected

- **Choose one path now.** The budget that decides it does not exist yet. Choosing Kubernetes early
  commits to its operating cost before revenue; choosing Linux servers early commits to rebuilding
  delivery if the first customer needs cluster scaling.
- **Managed, provider-specific services** (a cloud's own managed Kafka, managed ClickHouse, proprietary
  queues). Each one is a migration project the day the provider changes, which contradicts the
  switch-anytime requirement.
- **Terraform alone as the portability layer.** Terraform modules are provider-specific; switching
  clouds means writing a new module either way. On Path B, Kubernetes is what actually carries the
  application between providers.

## Consequences

- **Two paths means two drills.** A clean-VM install for Path A and a clean-cluster install for
  Path B. A path that has never been executed is documented, not supported. This doubling was raised
  as a cost and accepted, because the budget genuinely determines the path.
- **Path A is mostly F37.** Ansible running the Compose stack on a Linux server is the self-hosted
  install, automated. Building F37 properly builds most of Path A. Path B is the separately new work.
- **Stateless services scale easily on both paths** — receiver, workers, API. **Stateful services are
  the hard part on both.** On Linux servers ClickHouse, Kafka and Postgres run as containers backed up
  by the existing tooling; on Kubernetes they normally need dedicated operators. Which operators is a
  design-time research question and is deliberately not named here.
- **Ansible may prove unnecessary** on a path where nodes are configured through cloud-init. That is
  decided when Path A is designed, not assumed now.
- Nothing is hardcoded to a region anywhere — Terraform, Ansible inventories, Helm values, or
  application settings.

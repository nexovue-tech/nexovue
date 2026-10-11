# ADR-019: nexovue ships as two images, and each role runs as its own container

## Status

Accepted 2026-10-10 — explicit founder decision. Not yet executed: built with the images-and-CI work
(production-readiness blocker 2, group 8 of the Phase 1 Track 3 fix order). Until then the five
per-service images below remain what the compose files build.

## Context

nexovue builds **five images** today, one per service directory, measured on the development machine:

| Image | Language | Size |
|---|---|---|
| `nexovue/receiver` | Go, distroless static | 21 MB |
| `nexovue/worker` | Go, distroless static | 22 MB |
| `nexovue/api` | Python 3.13 slim | 359 MB |
| `nexovue/scheduler` | Python 3.13 slim | 307 MB |
| `nexovue/web` | built console files on nginx | 93 MB |

The scheduler already does what this ADR generalises: one image runs three services
(`scheduler-beat`, `scheduler-light`, `scheduler-deliveries`), chosen by the start command.

Products in nexovue's class have converged on the same shape — one artifact, its role chosen at start:

- **Jaeger v2** replaced four binaries (agent, collector, ingester, query) with one, configured per
  role (CNCF, "Jaeger v2 released", 2024-11-12; jaegertracing.io architecture docs).
- **Grafana Loki, Tempo and Mimir** compile every component into one binary and pick it with
  `-target`; small installs run `-target=all`, large ones run the same image many times with different
  targets (grafana.com deployment-modes docs).
- **SigNoz**, the closest product to nexovue (OpenTelemetry into ClickHouse), ships two images of its
  own: the app (UI and query API) and its OpenTelemetry collector (signoz.io install docs).

ADR-006 requires that delivery layers — Ansible running Compose on Linux servers, or Helm on
Kubernetes — use **one set of images** and one configuration contract; this ADR decides what that set
is.

## Decision

1. **Two images, built by CI for amd64 and arm64, both tagged with the same release version:**

   | Image | Contains | Base |
   |---|---|---|
   | `nexovue` | The API, the scheduler, and the built console files | Python slim |
   | `nexovue-pipeline` | The receiver and worker binaries | distroless static |

2. **The role is chosen by the start command**, never by building a different image. In each deployment
   every role runs as **its own container** (or pod) from one of the two images:

   | Role | Image | How many |
   |---|---|---|
   | receiver | `nexovue-pipeline` | Two or more; scales with incoming traffic |
   | worker | `nexovue-pipeline` | One or more, up to one per Kafka partition |
   | api | `nexovue` | Two or more in the hosted cloud |
   | scheduler-beat | `nexovue` | Two: one leads through a Postgres advisory lock (ADR-020; Redis's leader lock until 2026-10-10), one stands by |
   | scheduler-light | `nexovue` | One or more |
   | scheduler-deliveries | `nexovue` | One or more |

   A self-hosted install runs one of each with Compose; the hosted cloud runs the counts above on
   whichever ADR-006 path the deployment chose. **The images and roles are identical on both paths.**

3. **The console has no running container of its own.** Its built files ship inside `nexovue` and are
   copied at deploy time to whatever serves them — the edge proxy, as the on-prem compose's
   `web-assets-init` already does, or a CDN. The separate nginx image goes.

   *Executed early for the edge, 2026-10-11* (`docs/changes/deployment-tells-the-truth/`): both
   deployment files serve the console from the Caddy edge, and the web image now carries the built
   files only (no nginx). Moving those files into `nexovue` stays with the images work.

4. **The Go side starts as two binaries in one image.** Merging the receiver and worker into one binary
   with a role argument, Jaeger-style, is not required by this decision and is done only if it later
   buys something concrete.

## Rejected

- **Keep five images.** Five things to build, tag, scan and pull for a self-hosted operator, with
  nothing gained: Python roles share one schema and one shared package and must ship in lockstep anyway.
- **One image for everything.** Technically possible — the Go binaries are static and run in the Python
  image — and simplest to ship. Rejected because the receiver is the one role that faces the whole
  internet: today its container holds the program and nothing else (no shell, no interpreter), and in a
  single image it would carry Python, a shell and every Python library, giving an intruder tools and
  every security scan of the receiver the Python side's vulnerabilities.
- **Everything in one running container** (an all-in-one process, as Jaeger offers). One crash would
  take the receiver, worker and API down together. It may come back later as a laptop-only "try it"
  mode on top of the two images; it is never a production layout.

## Consequences

- **Failure stays contained and roles scale separately**, because one image never means one container:
  a worker that runs out of memory leaves the receivers taking data into Kafka.
- **Each image is a little larger than the one it replaces** — the scheduler role carries API code and
  the reverse. Accepted for one artifact per language.
- **All Python roles update together**, which they must already do.
- **The Phase 2 `insight` service is not decided here.** If its dependencies are heavy it stays a third
  image rather than enlarging `nexovue` for every role; that is settled when Phase 2 starts.
- The compose files, the Helm chart and the Ansible role all name exactly two nexovue images from the
  day this is built.

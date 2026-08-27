# 0051 — Combine the control plane and Celery into one container

- **Status:** Superseded by [0052](0052-full-production-container-inventory.md) — reverted before it shipped
- **Date:** 2026-08-26

> **Reconstructed record.** This decision was made and reversed within a single working
> session, so the original file was never committed. It is restored here because
> [0052](0052-full-production-container-inventory.md) supersedes it by number and the
> numbering rule forbids reuse — a dangling reference is worse than a short record.

## Context

The scheduler is Celery: a beat process that decides when checks run, a light worker for
HTTP and SSL probes and cloud polls, and a browser worker for Playwright flows. That is
three containers for a component with, at first, very little to do. The API is a fourth
Python process running the same dependencies from the same image.

Running one Python container instead of four — FastAPI, beat and a worker under a process
supervisor such as `supervisord` — looked like an obvious saving on a single on-premise
host, and it is a shape plenty of small deployments use.

## Decision

Combine the API and the Celery processes into one container, supervised internally.

## Consequences

Reverted the same day, on production grounds rather than aesthetic ones.

**Beat is a singleton and the API is not.** Two beat processes fire every customer's
checks twice. Bundling beat into the API image means the API can never be scaled
horizontally without either duplicating every scheduled check or adding leader election
that exists only to work around the packaging. Scaling for web traffic and scheduling
checks are unrelated concerns and must not share a replica count.

**A supervisor inside a container hides failure from the orchestrator.** The container
stays up while a process inside it is dead, so restart policies, health checks and
readiness probes all describe the supervisor rather than the work. The platform loses the
signal it exists to act on.

**Ingest and query must not share a process.** A slow analytical query competing with
Celery workers for the same CPU inside one container is exactly the coupling the
data-plane/control-plane split in [0008](0008-go-on-the-data-plane-python-on-the-control-plane.md)
was drawn to prevent.

Superseded by [0052](0052-full-production-container-inventory.md), which keeps the four
processes as four containers. Development still runs a reduced set, through compose
profiles rather than through bundling.

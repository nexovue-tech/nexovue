# 36. We write our own Celery Beat scheduler

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

[0007](0007-celery-for-probes-and-pollers.md) requires database-backed schedules so customers can
edit checks in the UI. Database-backed Beat schedules are first-party only in Django
(`django-celery-beat`); outside it there are three small community packages, one recently
maintained and one semi-abandoned.

## Decision

Write our own `Scheduler` subclass reading check definitions from Postgres — roughly 150 lines.

## Consequences

No single-maintainer dependency sits under a customer-facing feature, and the schema models
*checks* rather than generic crontab rows, which is what the UI actually edits. Cost: Beat's
scheduler internals are now something we track across Celery upgrades.

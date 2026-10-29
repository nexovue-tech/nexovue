---
name: readiness-check
description: >-
  Assessing whether nexovue, or one change within it, is ready for a production
  rollout — configuration, migrations, capacity, health, observability, backup
  and restore, rollback, security, and operational ownership. Use before any
  deploy or staging decision, when asked about production readiness or what is
  still missing before launch, or as the final gate on a major change.
---

# Production-readiness check

The checklist lives in
[`docs/quality/production-readiness.md`](../../../docs/quality/production-readiness.md). The verified
current gap list lives in
`docs/operations/production-readiness.md`.
**Read both.** This skill is how to evaluate them and where to write the answer.

Nexovue has not passed this gate. Assume nothing on the list is done until it is observed.

## 1. Fix the scope first

Say which is being assessed, because the answer differs:

- **One change** — is this change safe to roll out, given the platform's current state?
- **The repository** — can nexovue be deployed to production at all?

## 2. Evidence, not inspection

Every item is answered by something read or run, never by reasoning about what the code probably
does. Assess against the **actual deployment target**, not the local development stack — a green
`docker compose` on a laptop is not evidence about production.

For each checklist item record one of three verdicts:

```
✓ verified   — and the command, file, or output that verified it
✗ missing    — and what specifically is absent
? unknown    — not checked, and why not
```

`?` is an honest and useful answer. A `✓` without its evidence is not.

## 3. Read the operations documents as claims, not facts

`docs/operations/` holds runbooks, backup/restore, disaster recovery, rollback, monitoring, and
alerting. A documented procedure is a claim until someone has executed it. A restore that has never
been performed is not a backup; a rollback that has never been rehearsed is not a rollback.

Check the deployment configuration itself — `deploy/compose/`, the Dockerfiles, `deploy/caddy/` — for
what is actually declared. The on-premise Compose target declares itself a non-runnable skeleton;
treat anything resting on it as unverified.

## 4. Separate blockers from recommendations

```
BLOCKER      — a production rollout is unsafe without this
RECOMMENDED  — should be done, does not by itself stop a rollout
```

A blocker must name the concrete failure it prevents, not a category. "No rollback procedure" is a
category; "a failed ClickHouse migration has no rehearsed path back, and the workers' watermarks
would be ahead of the schema" is a blocker.

## 5. Write the result down

Update `docs/operations/production-readiness.md` with the verified gap list, and record the
assessment in the change or feature `status.md`.

Then state the verdict plainly — `READY` or `NOT READY`, with the blocker list. Do not soften it.

**The rollout decision is the founder's.** Report readiness; never deploy, and never treat a passed
checklist as approval to.

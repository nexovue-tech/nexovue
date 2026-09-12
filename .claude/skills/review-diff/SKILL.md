---
name: review-diff
description: >-
  Self-reviewing nexovue's own uncommitted or branch changes against the
  repository's invariants before reporting completion or handing work back.
  Use before any handoff, when asked to review the diff or the working tree, or
  as the last step of feature-work or change-work. This is the implementation
  self-review; the built-in /code-review command is the separate, deeper
  multi-agent review.
---

# Review the diff

The review criteria live in [`docs/quality/code-review.md`](../../../docs/quality/code-review.md) and
the standards `AGENTS.md` routes to. **Read the criteria there.** This skill is how to check them
against this repository.

## 1. See the whole diff

```bash
git status --short
git diff            # unstaged
git diff --staged   # staged
```

Review the diff against the feature or change record, the accepted design, the relevant architecture
document, and the applicable standards — in that order.

## 2. Unintended changes

Every changed file must be explained by the record. A file nobody meant to touch is a finding, not a
detail — flag it rather than committing it. Preserve unrelated working-tree changes that were already
there when the work started.

## 3. The invariants worth checking by hand

The structural tests catch these, but catching them here is cheaper than a failed gate:

- **Tenancy.** Every new ClickHouse statement is a module-level constant carrying
  `tenant_id = {tenant_id:String}` as a real predicate. Every tenant-scoped Postgres query takes
  `org_id` explicitly. A project id from a URL or body is proven to belong to the caller's
  organization *before* it scopes a read.
- **Authorization.** Every new mutating route has an explicit role gate, not a bare signed-in
  dependency.
- **Durability ordering.** A response or an offset advances only after the store acknowledged. Look
  specifically for a watermark advanced before its insert, and for a write to Redis or the object
  store that names Postgres rows not yet committed.
- **Masking.** Masking happens before anything downstream can read the value — before a row, a
  counter, or a sampling decision.
- **Secrets and privacy.** No credential value in a log line, an error message, an audit row, or a
  test fixture. No credential with a default anywhere in code.
- **Migration safety.** The migration states its own reasoning. Nexovue is pre-deployment: prefer
  the correct end state over a backward-compatible one, and say so.
- **Errors and retries.** Go errors wrapped with `%w`. Python errors raised as typed domain
  exceptions, never an HTTP response built inside a route.
- **Web.** Import direction `shared → features → app`. Design tokens only. A missing reading renders
  as an em dash, never `0`.

## 4. Tests

Does the coverage match the risk the change added, or only the lines it touched? A regression test
must have been observed failing before the fix. A live suite must fail loudly when its dependency is
missing — never skip quietly and report the same green as a suite that ran.

## 5. Report

Group findings by severity, each naming `file:line` and the concrete failure it causes:

```
HIGH    — wrong behavior, data loss, or a tenancy/authorization hole
MEDIUM  — missing coverage, a standard violated, an operator left blind
LOW     — naming, comment, consistency
```

Fix what is verified. **Record what is not fixed in `status.md` rather than dropping it.** An
unresolved risk that only exists in chat history is lost.

If a finding is a disagreement between two standards, or between a standard and the code, report it
as a decision for the founder — do not pick a side.

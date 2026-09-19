# Bug: every API write is committed after its response is sent

## Reported behavior

`tools/e2e/tests/alerts.spec.ts` "a new organization starts with a rule…" failed once in a parallel
run (2026-09-19): after *Connect Webhook* answered, the channel list kept showing only *Organization
owners* until the 60 s test timeout. It passed twice alone.

## Reproduction

The API log of the failing run:

```
15:16:02.644  "POST /api/v1/channels HTTP/1.1" 201
15:16:02.655  "GET /api/v1/channels HTTP/1.1" 200     ← the list's refetch, 11 ms later; no further read
```

A scratchpad script (`repro_commit.py`: sign in as the dev owner, create a webhook channel, list the
channels at once, delete it, repeat) on an otherwise idle stack:

```
missing right after 201: 1/40
```

## Root cause

`get_db_session` (`apps/api/src/nexovue_api/deps.py`) is a yield dependency that commits in its exit.
FastAPI runs a yield dependency's exit *after the response is sent* unless the dependency is declared
`scope="function"` (FastAPI docs, *Dependencies with yield* → dependency scope; `fastapi` 0.141.1).
So every route answered before its transaction committed. A client re-reading on the 2xx — which is
what every console mutation does, by invalidating its queries — could read the rows as they were
before. Worse, a commit that failed did so after the client had been told the write succeeded.

`docs/changes/signup-session-before-commit/` fixed one instance of this (signup committed early by
hand); the dependency itself was never corrected.

Ruled out: the API rate limit (no 429 on `/channels` in the log), React Query's cache (the refetch
reached the API and answered 200), and the channel list query (it reads `org_id` only, no filter that
could hide a new row).

## Customer impact

Pre-deployment, so none today. For a customer: something saved — a channel, a rule, a check, a
project — could be missing from the screen that just said it was saved, until the next refresh, and
more often under load. A failed commit (a constraint, a lost connection) would have shown success and
kept nothing.

## Specification impact

None.

## Regression test

`apps/api/tests/test_db_session_commit_order.py` — a route on the real `DbSession` dependency records
the order of handler, commit and `http.response.start`. Before the fix:
`['handler', 'response', 'commit']`; after: `['handler', 'commit', 'response']`.

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.

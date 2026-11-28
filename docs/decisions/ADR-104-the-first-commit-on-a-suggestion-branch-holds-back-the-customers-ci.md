# ADR-104: The first commit on a suggestion branch holds back the customer's CI

## Status

Accepted with F5's design, approved by the founder on 2026-11-02 (design, *Proposed ADRs*,
second). Filed 2026-11-22 with F5 Step 9 (plan T9.9), as built in Steps 7 and 8, **with write
access off in code** (ADR-097). **The GitHub trial may change it** (the founder's, after Phase
2). Awaiting the founder's review with Step 9's diff.

## Context

Opening a pull request probably runs the repository's CI, possibly with its secrets, before
anyone has read the change (an inference from GitHub's documents; the trial decides). GitHub's
own hold for Copilot's agent was not found to be available to a third-party App.

## Decision

1. The draft's first commit carries `[skip ci]` and the `skip-checks: true` trailer
   (`pull_request_text.py`), unless an owner chose "Run CI when a draft opens" for that one
   repository (`fix_repository_settings`, with its warning).
2. CI runs on a separate click, **Run CI**, by a person GitHub says has write access, after
   FR25's warning, as a commit with the head's own tree and no skip marker (`fix_writer.run_ci`).
3. The branch is always `nexovue/suggestion-<id>`, built by the client itself; no write method
   can name another.

## Consequences

One more click, and required checks pending until then. The hold covers only `push` and
`pull_request` runs of a CI that honours the marker: other triggers, other CI systems and apps
connected to the repository may still run, and `pull_request_target` workflows run with
secrets whatever the marker says. The customer's page says each of these
(`docs/product/setup/ai-suggested-fixes.md`). If the trial finds a draft runs no CI, or that
the marker harms a merge, this ADR changes.

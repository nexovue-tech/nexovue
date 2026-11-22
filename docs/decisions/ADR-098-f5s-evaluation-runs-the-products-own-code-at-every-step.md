# ADR-098: F5's evaluation runs the product's own code at every step

## Status

Accepted 2026-11-22, with F5 Step 6 (code) (plan T6.1, T6.3). **Awaiting the founder's review
with Step 6's diff** (status, open decisions 78 to 80).

## Context

T6.1's report decides whether write access is ever turned on (T6.5), and T6.3's decides whether
K6 and K8 may raise admin notices. Each number in them must be the product's behaviour, not a
copy's. The pack job's reader chooses files through GitHub's read routes and admits each through
the lists, caps and secret scan; the AI job builds its request and judges each answer with one
retry and up to three rounds. A bed that re-implemented any of these would measure its own
reimplementation, and drift from the product without a test noticing.

## Decision

1. **The pack is chosen by the pack job's reader.** `source_pack.PackReader` (renamed from the
   private `_Reader`, with its context and reading) reads through a protocol of the six read routes
   it uses, `RepositoryReads`. `FixGitHub` is one; the bed's `LocalRepository`
   (`tools/ai-eval/fix/eval_pack.py`) answers the same routes from the fixture repository on disk,
   with git's own blob ids. No GitHub is called and nothing writes.
2. **The request and the judgement are the AI job's:** `propose_fix.source_request`,
   `fixes.proposal.judge`, the job's one retry by its fixed code, and `needs_files` handing names
   back to the reader for the next round (`eval_runs.py`).
3. **Inputs are recorded once and replayed** across options, with a fingerprint of each request
   that leaves out the fence tag (drawn fresh per call, as in the product).
4. **Every proposal is read again for ways out** after the job passed it (`eval_scoring.py`), with
   the rules' own line code (`checks.added_line_breaks`, made public), so a fault in the judge path
   fails the option rather than passing unseen.
5. **T6.3's replay runs K6 and K8 through the same public line code** over a local clone's history
   (`replay_rules.py`), with the file before each commit giving K6 its known addresses.
6. **Fakes test the harness only.** Every real run is the founder's, after Phase 2 (foundation
   item 31); the checked-in reports are skeletons that hold no number.

## Consequences

A change to the reader, the request or the rules changes the bed's results with it, and the bed's
tests run that code. Two arms exist only in the bed, and are named as such: "without imports"
drops the files the reader added as imports after it chose them, and "without rounds" starts at
the last round. The bed assumes a person presses Send in every round. `PackReader` is now public
API of the scheduler's connections package, used by the bed alone outside it.

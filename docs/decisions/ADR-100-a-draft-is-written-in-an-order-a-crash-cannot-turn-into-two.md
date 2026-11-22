# ADR-100: A draft is written in an order a crash cannot turn into two, and only as checked

## Status

Accepted 2026-11-22, with F5 Step 7 (plan T7.1, T7.4, T7.5). **Built against the simulated
GitHub only, with write access off in code** (ADR-097, ADR-101; foundation item 33).
Awaiting the founder's review with Step 7's diff.

## Context

A draft pull request is several GitHub writes (blobs, a tree, a commit, a branch, the pull
request) made by a job that can die between any two, and be run again by the queue. GitHub
gives no transaction across them. The spec asks for one draft per finding even when an answer
is lost (BR8, AC25, AC51), never anything but the checked bytes (BR3, FR22), never a pull
request that is not a draft (FR23, AC27a), and no further write once a withdrawal commits
(AC48). The design names the steps; this records the order and the store's part, which no
reading of the code alone shows the reason for.

## Decision

1. **One draft per finding is the store's rule:** a unique index
   (`uq_finding_fixes_one_draft`, migration 0083) over `approved` rows and rows holding a
   branch whose pull request is open or not yet known. The click's claim, refused by it,
   answers `draft_open`. `untracked` and `not_opened` free it; a withdrawal does not.
2. **Adoption first.** Every run lists pull requests by `?head=` before writing; one whose head
   is the stored commit is stored and ends the job.
3. **The commit is stored before the branch is made, and the branch before `POST /pulls`,**
   each in its own transaction, so a retry reuses what exists, a moved default branch cannot
   make it rebuild, and the one-draft index holds whatever GitHub answers.
4. **Only the checked bytes.** The job re-reads the files and re-checks them with the check
   run's own code (`fix_patch.read_changed`, `rechecked`); the digest must equal the one the
   person approved, else nothing is written (`changed_since_check`).
5. **A branch whose head is not Nexovue's commit is refused** (`branch_not_ours`), and a 422 on
   a second pull request for the branch is adopted, never retried as anything else. A refused
   draft is `not_opened` with the branch named; there is no fallback to a non-draft.
6. **Withdrawal and disconnect are read before every write;** the taking-back moves an
   `approved` row back to `proposed` in its own transaction, and a disconnect marks a draft
   Nexovue can no longer follow `untracked`.

## Consequences

A crash at any step leaves at most one branch and one draft, proved against the fake by a
crash after each write, with the default branch still and moved. A lost `POST /pulls` answer
followed by used-up attempts holds the finding until Step 8's poller settles it by `?head=`.
GitHub's own behaviour here (the 422 on a second pull request, `?head=` finding it, a draft on
GitHub Free) is from its documents and the trial's questions, not yet checked against the
service: the founder's real run after Phase 2 checks it (foundation item 28).

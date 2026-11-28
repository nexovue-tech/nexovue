# Status: Phase 2 tests on a Mac

## Lifecycle status

Implemented, validated on the founder's Mac; CI to confirm on Linux.

## Next action

CI on pull request #25.

## Risks

- `.env.example` was found deleted from the working tree during the local test runs on 2026-11-27
  and restored from git. No code that deletes it was found; check after the next local run.
- Other tests may still write naive times and pass only because their windows are wide; none failed.

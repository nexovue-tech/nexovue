# Status: two defects found running `just check` after `just fresh`

## Lifecycle status

**Implemented and verified.** Both fixed. Uncommitted.

## Current task

None.

## Completed

- **Defect 1 — the TOTP replay test now replays the code it actually submitted.**
  `test_the_enrolment_code_cannot_also_sign_in` captures `used_to_enrol` once and submits that exact
  string to `/two-factor/confirm`, then replays the same string at login. It no longer re-derives a
  second, independent `pyotp.TOTP(secret).now()` after the enrolment round-trip, which could land in
  the next 30-second step and assert about a code nothing ever claimed. No production code changed —
  `totp.py` and `second_factor.py`'s replay guard (`claim_step`, `_totp_accepts`) were never at
  fault.
- **Defect 2 — the signup-uniqueness test seeds its own precondition instead of assuming it.**
  `test_a_taken_address_is_refused_rather_than_raised` now calls `seed_super_admin` to create the
  platform organization and its operator, instead of hand-building a `User` row under
  `PLATFORM_ORG_ID` with no matching `Organization` row. Matches the convention
  `test_super_admin_seed_live.py` already uses for the same precondition. No longer depends on
  `SUPER_ADMIN_EMAIL`/`SUPER_ADMIN_PASSWORD` happening to be set in whatever environment runs it —
  neither is in `.env.example`, so a standard dev or CI setup never had this precondition met before.
- **All other `just` recipes checked against the freshly rebuilt stack**, see Validation.

## Next action

Founder: read the diff, then say **commit** (and, separately, **push** if wanted — this session adds
no co-author trailer per the repository's own rule).

## Open decisions

None.

## Blocked

Nothing.

## Validation

- `just fresh` (run directly by the founder, not by this session): completed successfully, exit 0,
  all 13 containers healthy, demo traffic flowing, checks and deploy events seeded. The 120-second
  foreground timeout it hit is expected — the recipe's own comment states "about two and a half
  minutes" — and is not a defect; the harness moved it to the background automatically and it
  finished on its own.
- `just topics`: all 8 topics ready, including the two new `profiles-raw`/`profiles-dlq` pairs added
  since continuous profiling shipped.
- `just migrate`: both stores already at head after `just fresh`; `migrate-pg` reported nothing
  pending, `migrate-ch` reported "no change".
- `just suspend-show`: "No organization is suspended."
- `just check`, before the fix: **2 failed, 4139 passed, 5 skipped** (test-go and contracts-check not
  reached — `test-py` failing stopped the `test` chain before `test-web`).
- Defect 2, red/green: re-ran with the fix stashed — `ForeignKeyViolationError` reproduced exactly as
  first seen; with the fix restored, `1 passed`.
- Defect 1, mechanism proved deterministically (the race itself cannot be forced without a
  monkeypatch this repository does not use elsewhere): a repro script computed the code actually
  submitted and a second, 2-seconds-later re-derivation straddling a step boundary and showed them
  different (`765370` vs `275259`). With the fix, only one code value is ever computed, so the two
  can no longer disagree. Ran standalone: `1 passed`.
- `just fmt` — one file needed reformatting after the fix (a line over the project's wrap width);
  reformatted and reverified.
- `just check`, after both fixes, full run: **exit 0.** Python `pytest -n 6 --dist loadfile`: **4142
  passed, 4 skipped**, 0 failed. Go: every package `ok` (receiver, workers, contracts, gateseed,
  rebalanceprobe). `packages/browser-sdk`: 120 passed. `apps/web`: 123 test files, 1335 tests, all
  passed. `buf lint` and `buf breaking` against `main`: clean. `fmt-check`, `lint-go`, `lint-py`,
  `lint-web` (including `tsc -b`/`tsc --noEmit` across 4 workspace projects): clean.
- `just backup`: wrote a new snapshot (`2026-10-09T105735Z`) — Postgres dump, ClickHouse archive,
  manifest, checksums — against the live stack without stopping it, as its own comment says is safe.
- `just backups`: lists all three snapshots on this machine, including the new one.
- `just backup-verify 2026-10-09T105735Z`: reported 26 mismatches, all explained by live drift — the
  demo's load generator and scheduler kept writing after the snapshot was taken (e.g. `organizations
  expected 3, got 4`, `logs-raw:0 watermark 3602 is above end offset 3600`). This is the tool working
  correctly: it compares a snapshot's manifest against whatever is live *now*, and it is meant to run
  after a *restore*, against an otherwise-idle instance, not against a stack still taking traffic.
  Not a defect.
- **Not executed, by design:** `migrate-down-pg`, `migrate-down-ch`, `restore-pg`, `restore-ch`,
  `just chaos`, `just load`, `just e2e`, `just down`/`down-hard`. Each is destructive or disruptive
  to the live demo stack the founder is currently running, or (`load`, `e2e`) adds meaningful load or
  takes meaningfully long for a verification pass that already exercised the stack directly. Their
  scripts and recipe definitions were read and look structurally correct; none showed a missing file,
  a broken path, or a stale argument, but none was run end to end. Flagging rather than silently
  skipping, per the instruction to check that all of them work: say if any of these six should be
  exercised next, ideally against a disposable stack rather than this one.

## Risks

- Defect 1's regression coverage is now deterministic by construction, not merely "passed this
  run" — there is no remaining timing window for it to race, so no residual flakiness is expected.
- The six recipes listed as not executed are unverified beyond a read of their scripts. `just chaos`
  and `just e2e` are the two most worth running before treating this pass as complete evidence that
  "all of them work."

## Documentation impact

- `docs/changes/README.md` — this record added to the index.

## Last updated

2026-10-09

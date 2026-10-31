# Status: chat-message-escaping

## Lifecycle status

Implemented and verified offline. Committed on `fix/chat-message-escaping`, with a pull request against `main`.

## Current task

CI on the pull request.

## Completed

- Reproduced: 4 new regression tests failed on `origin/main` at `bf335d5` (`bug.md`, *Reproduction*).
- Fixed in `alerts/messages.py` and `alerts/payloads.py`. The 4 tests pass, and the 23 existing ones in
  those files still pass, two of them with an updated expectation.

## Next action

CI runs the full gate, including the live, Go and web suites this environment could not. Then the founder merges, as a squash.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- Teams' and Discord's escaping is unverified against their own documentation (`bug.md`, *Fix plan*). A
  wrong escape shows a stray backslash in a message. It does not reopen the link.
- Real Slack, Teams and Discord rendering has not been seen: no workspace is connected here.
- A bare URL in a name is still turned into a link by Slack and Discord. The address is shown in full,
  not disguised, so it is left as it is.
- A check's failure reason (the "Why:" line) is not collapsed to one line. It has already been stripped by
  `alerts.reasons`, and the markup escaping covers it, but a newline in it could still start a line.
- Slack's `fallback` is plain text, so an escaped `&amp;` may show literally in a phone notification. It
  stays escaped, because odd text is the safer failure. Unverified.
- On Teams mobile, a name containing a backslash shows two.

## Validation

- **Regression tests:** seen red, then green (`bug.md`, *Reproduction* and *Regression test*).
- **`just check` exited 1 at `lint-deploy`**, which runs ShellCheck and promtool in Docker. There is no
  Docker in this environment, so the gate's test stage never ran. The parts it reached passed:
  - Go formatting, `ruff format` (932 files) and Biome;
  - `ruff check`, and mypy (648 files).
- **The offline Python suites, run by hand: 3,800 passed, 1 skipped.** They cover `apps/scheduler`,
  `apps/api`, `packages/py-platform`, `packages/contracts` and `tools/repo-audit`, without the `*_live.py`
  suites.
- **Not run:**
  - the `*_live.py` suites, which need the Docker stack;
  - `test_connect_guard.py`, which needs outside DNS;
  - `test_filters_reach_past_the_cap.py`, which needs a store;
  - `lint-deploy`, which needs Docker;
  - the Go and web suites, because this change touches no Go or web code.

  CI runs all of them on the pull request.

## Specification impact

None.

## Documentation impact

None. No architecture document describes the payload builders' escaping.

## Last updated

2026-10-30

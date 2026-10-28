# Contributing to nexovue

Thank you for wanting to help. Before your first pull request:

1. **Open an issue first** for anything larger than a small fix, so we can agree on the approach before
   you spend time on code.
2. **Contributor terms.** nexovue is AGPL-3.0; the browser SDK in `packages/browser-sdk` is Apache-2.0.
   The terms for outside contributions are being set up; until they are published, pull requests from
   outside contributors cannot be merged.
3. **Run the checks.** `just setup`, `just dev`, then `just check` — the same gate CI runs. A pull request
   with a failing check is not reviewed.
4. **Follow the repository's rules.** [AGENTS.md](AGENTS.md) is the operating manual: how work is
   specified, the engineering standards in `docs/engineering/`, and the invariants the tests enforce.

Security problems are never reported as issues — see [SECURITY.md](SECURITY.md).

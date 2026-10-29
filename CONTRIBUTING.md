# Contributing to nexovue

Thank you for wanting to help. Before your first pull request:

1. **Open an issue first** for anything larger than a small fix, so we can agree on the approach before
   you spend time on code.
2. **Contributor terms.** nexovue is AGPL-3.0; the browser SDK in `packages/browser-sdk` is Apache-2.0.
   - **Everything except the browser SDK:** sign the [Contributor License Agreement](CLA.md). CLA Assistant
     asks for it, once, on your first pull request; it is a licence, not an assignment — you keep the
     copyright in what you write.
   - **The browser SDK:** sign off each commit under the [Developer Certificate of Origin](https://developercertificate.org/)
     — `git commit -s`, which adds `Signed-off-by: Your Name <you@example.com>`. Nothing else to sign: the
     SDK stays Apache-2.0 for everyone who embeds it.
   - A pull request touching both signs the CLA and signs off its commits.
3. **Run the checks.** `just setup`, `just dev`, then `just check` — the same gate CI runs. A pull request
   with a failing check is not reviewed.
4. **Follow the repository's rules.** [AGENTS.md](AGENTS.md) is the operating manual: how work is
   specified, the engineering standards in `docs/engineering/`, and the invariants the tests enforce.

Security problems are never reported as issues — see [SECURITY.md](SECURITY.md).

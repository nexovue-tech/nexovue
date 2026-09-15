# ADR-004: AGPL-3.0 for the platform, permissive for the browser SDK

## Status

Accepted 2026-09-15 — explicit founder decision. Not yet executed: no `LICENSE` file exists in the
repository and it is still private.

## Context

nexovue is **both self-hosted and hosted** (vision.md, 2026-09-14), with self-hosting free and
hosting paid. That creates one commercial risk the product did not previously have: somebody takes
the source, adds features, and sells closed hosting against us.

Publishing also had to be decided in one direction or the other, because it cannot be meaningfully
undone. Every copy distributed stays under the terms granted at that moment; a later change binds
only new versions.

Three facts constrained the choice, all established by direct check rather than assumption:

- **Every OTel-native peer but one publishes source.** OpenObserve AGPL-3.0 core plus a commercial
  add-on; SigNoz MIT core plus a commercial `ee/`; ClickStack/HyperDX Apache-2.0 and MIT; Coroot
  Apache-2.0; Uptrace AGPL-3.0. Only Dash0 is proprietary, and it offers no self-hosting at all.
  None changed licence in the preceding twelve months — verified against each project's `LICENSE`
  commit history on 2026-09-14, not from a survey.
- **The dependency tree is compatible.** Audited 2026-09-15: npm's 51 production packages are
  MIT/ISC/Apache-2.0/BSD-3/Unlicense; Go is MIT/BSD-3/Apache-2.0; Python is MIT/BSD-3/Apache-2.0
  plus dual licences. `symbolic` is MIT despite Sentry relicensing other projects to BSL, and
  `pycurl` is `LGPL-2.1-only OR MIT`, so the MIT branch applies.
- **`packages/browser-sdk` is not like the rest of the codebase.** It executes inside the customer's
  own web page rather than on our infrastructure.

## Decision

**The platform — receiver, workers, API, console — is AGPL-3.0, with the source public.**

**`packages/browser-sdk` is permissive (MIT or Apache-2.0) and must not be AGPL.**

Plain GPL was rejected for a specific reason: its obligation triggers on *distribution*, and a
hosted service distributes nothing. A competitor could modify nexovue, run it as a paid service and
publish none of it, entirely within GPL. AGPL's network clause is what closes that, and closing it
is the whole purpose here.

MIT or Apache for the platform was rejected as giving that protection away for nothing, when hosted
revenue is the plan it protects.

Staying closed (images only, private repository) was rejected because the protection guards a risk
that only exists after success, at the cost of the adoption needed to reach it — and because a
closed binary asking for a customer's entire telemetry stream is a reason to choose a readable
competitor that has nothing to do with features.

**Open-core is deferred, not foreclosed.** No `ee/` split, no licence keys, no second build path.
A future paid feature is new code under its own licence, never a relicensing of what exists — the
shape OpenObserve already runs.

## Consequences

**The generalised rule this creates:** anything running on our infrastructure may be AGPL; anything
running inside a customer's application may not. `packages/browser-sdk` is the first instance, not
the only one — any future client library inherits the same constraint.

**The browser SDK carve-out is load-bearing, not a detail.** Under AGPL, a customer embedding the
SDK could be read as owing source publication for their own site. No company accepts that, so the
SDK would be legally unusable by the customers it exists for — while D6 is already the decision to
make it installable. Getting this wrong would not fail loudly; it would simply mean nobody installs
it.

**Sole copyright keeps every option open.** Future versions can be relicensed at will. Only copies
already distributed are permanent, which makes this decision far less irreversible than it first
appears.

**Two obligations fell on F37 before anything could be published. Both are discharged,
2026-09-15 — and neither changed the decision.**

1. *Transitive licence audit.* The survey above read direct dependencies. Re-run against the full
   build graph: 51 production npm packages (MIT, ISC, Apache-2.0, BSD-3, Unlicense, MIT-or-CC0),
   37 Go modules reached by `go list -deps` from the receiver, workers and contracts (Apache-2.0,
   BSD-3, MIT, one MPL-2.0), and 95 Python packages. Nothing copyleft, nothing source-available —
   no BSL, SSPL or Elastic anywhere. The two licences worth naming are both compatible: MPL-2.0
   (`certifi`, `pathspec`, `go.mongodb.org/mongo-driver`-style terms) carries an explicit
   secondary-licence provision for GPL-family works, and `pycurl` is `LGPL-2.1-only OR MIT`, so
   the MIT branch applies.
2. *Content-level secret scan.* `gitleaks git` over all 364 commits, ~10 MB. 17 findings, **all
   false positives**: `AKIAEXAMPLEACCESSKEY`, `0123456789abcdef…`, a matched log-message string,
   the parameter name `sealed_secret` in `totp.py`, and `sk_test_51HxmpleTokenThatMustNeverBeStored`
   planted in the deleted demo shop so the masking rule had something to catch. Every `nvp_live_*`
   literal in history is a fixture (`devloc…`, `AAAAAA`, `short`, `x`) or the scheme constant. No
   private key block, and no credential file was ever added — only `.env.example`, with `.env` and
   `.env.*` ignored since the beginning.

**What this audit found instead: the licence is decided but nowhere declared.** There is no
`LICENSE` file at the repository root, no `license` field in any `pyproject.toml`, and
`packages/browser-sdk/package.json` says `"UNLICENSED"` — which contradicts this ADR's carve-out
rather than implementing it. Publishing with those unchanged publishes source under no grant at
all, which is the one outcome worse than either licence. **Open: MIT or Apache-2.0 for the SDK.**
Apache-2.0 adds an express patent grant and a NOTICE requirement; MIT is shorter and is what the
OTel browser packages the SDK sits beside already use.

**Not decided here:** what a self-hoster pays (nothing — vision.md, 2026-09-14) and which features
might later be commercial. Those are commercial decisions recorded in `vision.md`; this ADR covers
only the licence and its technical consequences.

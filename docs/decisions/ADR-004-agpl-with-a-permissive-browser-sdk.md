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

**The holder is ANANDHARAJ A personally**, recorded 2026-09-15. No company is registered, so there
is no other legal person for copyright to vest in — and that is the ordinary position for a project
at this stage, not a gap to close before publishing. It is also what makes the paragraph above
literally true: one natural person holds every line, so relicensing needs no one else's consent and
no contributor agreement exists to unwind.

Two consequences to act on rather than discover:

1. **On incorporation, copyright must be assigned to the company in writing.** It does not move by
   itself, and a company that turns out not to own its own product is a due-diligence finding at
   exactly the wrong moment. The assignment is short; forgetting it is expensive.
2. **Every outside contribution from here changes the answer.** The moment someone else's patch is
   merged, copyright stops being sole and the freedom above is gone unless a CLA or a DCO captured
   it at the time. Decide which before the repository is public, not after the first pull request.

`NOTICE` in `packages/browser-sdk` is the only file carrying the name; both `LICENSE` files are
verbatim licence text and name no holder by design.

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
all, which is the one outcome worse than either licence. **Resolved 2026-09-15: the SDK is Apache-2.0.** (An
earlier draft of this paragraph said the neighbouring OTel browser packages are MIT. They are not —
`@opentelemetry/api`, `@opentelemetry/sdk-trace-web` and `web-vitals` all declare Apache-2.0, and
the claim was written from memory rather than checked.) Three reasons, strongest first:

1. **The shipped bundle already contains Apache-2.0 code.** `vite.config.ts` sets no `external`, and
   its own comment says both builds are self-contained — so all three Apache-2.0 dependencies are
   compiled into `dist/nexovue-rum.min.js`. Apache §4 attribution travels with the artifact that is
   actually distributed, not with the source tree it was built from. Calling our half MIT would not
   remove that obligation; it would only make the one file customers embed answer to two licences.
2. **Every comparable SDK is Apache-2.0** — Splunk's `splunk-otel-js-web`, Sumo Logic's
   `opentelemetry-rum`, and OpenTelemetry's own browser instrumentation. A customer's counsel
   reviewing ours sees the licence they already cleared for the OTel SDK they likely run.
3. **The patent grant is the entire point of the carve-out.** MIT is silent on patents, which is an
   ambiguity enterprise review flags. The SDK is permissive to remove friction from embedding it in
   someone else's product, and an express patent grant removes more friction than MIT's brevity
   adds. It also terminates for a licensee who sues us over patents, which MIT cannot do.

The cost is a NOTICE file and a statement of changes — both owed anyway under (1).

**Not decided here:** what a self-hoster pays (nothing — vision.md, 2026-09-14) and which features
might later be commercial. Those are commercial decisions recorded in `vision.md`; this ADR covers
only the licence and its technical consequences.

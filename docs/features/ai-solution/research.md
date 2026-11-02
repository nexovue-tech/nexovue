# F5 AI solution: research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. It approves nothing and no code is written from it. It feeds F5's specification,
> design and plan.
>
> **How it was checked.** Code was read on branch `feature/anomaly-detection` with uncommitted work present; line
> numbers are for that tree and will drift. Nothing was run: no model, no GitHub call, no test. Web pages were read
> on 2026-11-02 through a tool that summarizes them, so check any quote against the live page before publishing it.
> Every limit and token figure here is a guess and says so. Founder decisions in `phase-2-plan.md` stand.
>
> **Later positions win.** This was written before the spec and design. F3, F22, F4 and F28 now have documents. Rival facts below were re-read on 2026-11-02 for the persona reviews.

## Superseded by the spec

Do not quote the positions below this list where they differ. The spec's decisions stand.

| This document says | Now | Where |
|---|---|---|
| F3, F22 and F4 have no documents | They do, and were read | `spec.md`, *Status* |
| Refuse new dependencies | Manifests cannot change (K2). **A new import is flagged, not refused, so an invented package can reach a reviewer** | `spec.md`, FR18, K2 |
| What Nexovue adds: own model in a free self-hosted product | Not a differentiator. OneUptime is close, and free rivals exist | `spec.md`, *What may be said* |
| "The checks are public and tested" | Nothing has run. Future tense only | `spec.md`, *What may be said* |
| Most rivals run this as one flow | Not so for Seer or OneUptime. What is distinct: rungs 1 and 2 work with AI off and send no source | Below; `spec.md`, *What may be said* |
| The file changed by click time: the click refuses | The new diff is shown, labelled, if edits still apply | `spec.md`, D11, FR21 |
| A `likely_cause` requirement fits the market | Stacks qualify for rungs 3 and 4, and rung 5 is founder decision 3 | `spec.md`, D35 |
| A fixed list a customer can add to, one click, one consent per repository | Two clicks; consent per repository and folder, bound to address, format, key and model | `spec.md`, D3, D8 |
| One GitHub App, raised | A second private App of F5's own, and the draft as a job | `spec.md`, D36, D37 |
| Branch `nexovue/fix-<id>` | `nexovue/suggestion-<id>` | `spec.md`, D52 |
| "Fix brief" | F3's `fix_brief` is the facts for F5's model. Rung 2's text is "the brief" | `spec.md`, *Words used here* |
| A one-hour GitHub trial | About two hours (a guess), and 21 questions | `design.md`, *The GitHub trial* |

## The short version

1. **The fix is the one place in Phase 2 where words from a model can become something that runs.** Everything else
   a model says is shown as text. A diff (the list of changed lines) is committed.
2. **So "no model in the pull request step" is true and is not enough.** That step runs no model, yet it carries the
   model's code. This document asks the foundation to say so (see *Where this disagrees*).
3. **Opening a pull request probably runs the customer's CI on that code, possibly with its secrets, before anyone
   approves anything.** Inference from GitHub's documentation, not tested. It is the largest risk found. Recommendation: the
   first commit asks CI to skip, and a second click runs it.
4. **Most findings have no route to code.** A finding stores numbers and an endpoint name, no stack trace and no
   commit. The first version should work where the path is real, and refuse plainly elsewhere.
5. **Draft pull requests with human review are the market's entry fee.** *What this said Nexovue adds is superseded; see the list above.* What may be said is in the spec.
6. **Do not attempt in the first version:** running customer code, a looping agent, automatic fix runs, or iterating on CI.

## Words used here

| Term | Meaning |
|---|---|
| **Diff** | The exact lines a change removes and adds |
| **Draft pull request** | A proposed change on GitHub, marked not ready, that cannot be merged until someone marks it ready |
| **CI** | Continuous integration: automatic builds and tests a repository runs when code changes. It often holds secrets |
| **Source pack** | The fixed set of repository files Nexovue picks and hands to the model for one proposal (this document's term) |
| **Fix brief** | F3's validated facts for one explanation version (foundation, section 9) |
| **Slopsquatting** | An attacker publishing a package under a name AI models tend to invent, so that installing it runs their code |
| **Abstain** | Answering "not enough evidence" instead of proposing something |
| **Agent loop** | A model that repeatedly chooses tools and reads their results until it decides it is done |

## What exists

**When this was written, F3, F22 and F4 had no documents.** They now do, and have no code. The foundation (`docs/product/phase-2-foundation.md`) was then the only statement of what they would give F5, so every "F3 gives"
below is a promise, not a fact.

### Nothing of F5 itself

No repository connection, source reading, fix record, approval flow or pull request call. The code map says the same
(`docs/research/phase-2-code-map-2026-11.md:300-319`), and this pass found nothing to change that.

### What F5 could use, and how far it reaches

| Input | Where | What it means for F5 |
|---|---|---|
| A finding's data | `nexovue_platform/detection.py:260-287`, `findings/schemas.py:29-81` | Signal, normal, values, an evidence endpoint name. **No stack trace, error group, version or commit** |
| Detection reads no errors | A search of `nexovue_scheduler/detection/` and `detection.py` for error groups and stacks found only log calls | A finding reaches code only through F3's evidence, once F3 links an error group |
| A stack trace | `errorgroups/schemas.py:85`, read at `errorgroups/service.py:228-260` | Raw text out of JSON, in the API only. `alerts/telemetry.py` in the scheduler never mentions it (a search finds 0) |
| Parsing a stack | `apps/web/src/features/errors/symbolication.ts:1-17`, `:54` | **In the browser only**, for browser formats, and on purpose. Server-side, stacks are only split into lines for display (`error-groups.ts:125-129`). No Python, Java or Go frame parser exists anywhere |
| Source map lookup | `sourcemaps/service.py:102`, `symbolication.py:51` | **API only.** `symbolic` is an API dependency (`apps/api/pyproject.toml:60`), not a scheduler one. Maps are browser-only by nature |
| Source excerpt | `sourcemaps/schemas.py:83`, `symbolication.py:27` | Five lines each side, from the customer's uploaded map, when it carries them. Any person may read them (`sourcemaps/router.py:90`, `CurrentIdentity`) |
| A version | `errorgroups/schemas.py:90`, `deploy_events.version` | A string, often empty. It is not a commit. Metadata may hold a commit, and no field promises one (`deploys/schemas.py:44`). F28 proposes `commit_sha` |
| Order of deploys | `deploys/service.py:162` | Gives the version before the current one, which a rollback line needs |
| "Before, not because" | `errorgroups/schemas.py:15-26` | The model for every sentence F5 writes about a deploy |
| Person-only fields | `findings/schemas.py:148-157`, `findings/router.py:90` (`for_member`) | The existing pattern for keeping a field from API tokens |
| Permissions | `auth/permissions.py:49-69` | A viewer appears in no row. New actions are required per foundation section 5 |
| Sealing | `checks/sealing.py:95-137` | Whoever holds a key can use any purpose (`:136-137`), so separation comes from which container holds the key |
| Guarded HTTP | `nexovue_scheduler/probes/http_check.py:66,99` | **In the scheduler.** The API does not depend on the scheduler package (`apps/api/pyproject.toml` names none), so the foundation's "API, on the click" needs its own client. Fine for the fixed host `api.github.com`; not fine for GitHub Enterprise later. `follow_redirects` defaults to true (`:65`), so a caller must turn it off, as `alerts/senders/http.py:47` does |
| Confirm control | `shared/components/ConfirmPanel`, used at `FindingScreen.tsx:7` | Reuse for the click |
| The finding page | `FindingScreen.tsx:107-117` | Fixed list of sections, no slot for a fix. Adding one is ordinary work |
| Showing code | `apps/web/package.json:16-21` | CodeMirror exists; no diff view. A plain-text diff, built by Nexovue, needs no new library |
| Deletion | `organizations/tables.py:75,90` | New tables must be listed here |

### What the foundation already decided for F5

Not reopened here: `scheduler-insight` holds no repository key (section 1). Source goes to a provider only with separate,
owner-only consent (sections 4, 5). One GitHub App per self-hosted install, GitHub.com first (section 6). Three separately narrowed
tokens (section 6). `finding_fixes`, its routes, `REQUEST_FIX`, `OPEN_FIX_PULL_REQUEST` (sections 5, 7, 8). No fix without a
non-null `likely_cause` (FD14). A branch `nexovue/fix-<id>`, always a draft.

### What this reading adds to the foundation

**There is no path yet from "a finding" to "these files".** Section 9 names source maps "to find files", but those only help
minified browser code and live only in the API. A workable flow, for the design to test:

1. On the click, the **API**, which already reads ClickHouse, reads the recent stack for the evidence's error group and
   pulls out path-like tokens and line numbers. It stores those candidates on the `finding_fixes` row. It keeps no message text.
2. `scheduler-connections` lists the repository tree at the chosen commit, and **keeps only candidates that match an existing
   path inside the service's mapped folder**. No parser per language is needed, and a path no one committed cannot survive.
3. It writes the source pack, in a short-lived table, for `scheduler-insight` to read. The foundation's "writes a source pack" names no place.
4. The model gets the fix brief and the pack and returns structured edits. Nexovue's code validates them and stores the result.

## What users need

Evidence on this is thin. The customers research (`phase-2-customers-2026-11.md:118-131`) found **nothing measuring whether people accept AI
fixes for production incidents**, and nobody has been interviewed. The rest of this section is reasoning from personas, not findings.

| Person | Needs | Fears | So F5 must |
|---|---|---|---|
| **Buyer** (CTO of a 5 to 50 engineer company) | Less time lost to incidents; a known bill | Source code leaving; a bot with write access; one more AI bill | Name what is sent, to whom, per repository. Ask for write permission only when F5 is chosen. Show tokens used |
| **On-call engineer, 3 a.m.** | To stop the damage first, and then the fix | A confident wrong patch; reading a 300-line diff half asleep | Lead with the rollback line where a deploy preceded it. Keep diffs small. State what was not checked. Refuse rather than guess |
| **New developer** | To understand the problem and the code | Trusting something they cannot judge | Say why each file was read. Link every claim to evidence. Never read as "approved" or "tested" |
| **Admin** | Control and an audit trail | Repository permission creep; someone pointing the provider elsewhere | Audit every read and every pull request. Void consent when the provider changes. Say how to disconnect and what gets deleted |
| **Lone reviewer** | No more noise | A queue of AI pull requests | Nothing opens by itself. One open draft per finding |
| **Attacker** | Anything: code in, source out, a CI run | Being refused by code rather than by a prompt | See below |

### Thinking as the attacker

An attacker may send telemetry with a browser key (`alerts/payloads.py:204`), and may be able to get text into a repository: a vendored
package, an outside contribution, or a README.

| # | Attack | What it gets | What should stop it |
|---|---|---|---|
| A1 | Forge a stack trace naming a chosen file, such as `config/prod.py` | That file goes to the provider | Paths filtered against the repository tree and mapped folder; a list of files never sent, kept in code; the file list shown after each run. **The choice of file can still be steered.** Say so |
| A2 | Write instructions in an exception message | The model obeys | No message text reaches the model. The brief is facts and aliases only (U1) |
| A3 | Plant instructions in a repository file | The model writes what they ask. The source must be read, so this cannot be replaced, only fenced (U2) | Everything below. Exclude agent instruction files. **Fencing is not a defence** |
| A4 | Get a backdoor into the diff: a new web request, new package, weakened check | Code in the customer's product | Refuse new URLs, new dependencies, and files outside the pack. **A subtle logic bug passes all of this; only a person can catch it, and perhaps not** |
| A5 | **Change code that CI runs** | Code runs in CI, perhaps with secrets, before review | The skip-CI decision, F5-D5. Refuse workflow, build and dependency files |
| A6 | Hidden text in the diff aimed at a reviewer's own AI tools | The reviewer's tool is hijacked | Refuse invisible and bidirectional Unicode in added lines. Show and count added comments. **Instructions written as ordinary comments cannot be reliably detected** |
| A7 | An admin repoints the provider's address to their own server | The repository's source, on request | Source consent is bound to provider host and format; a change voids it, and only an owner can re-consent |
| A8 | A member with no access to the repository clicks | Their click writes to a repository and runs CI they could not otherwise reach | F5-D6: check their GitHub write access first |
| A9 | A viewer or API token reads a diff | Private source | F5-D7: code is limited to those who can ask for a fix, and never reaches tokens |
| A10 | Use the pull request text | Attacker's names reach a Slack unfurl or AI reviewer; telemetry facts land in a **public** repository | Template only (U5), with the service name from the customer's mapping, not telemetry. In a public repository, leave out telemetry numbers and the console link |
| A11 | Generate many errors to burn the customer's AI budget | Spend | No automatic runs. Runs follow a person's click |
| A12 | Abuse a token | Writes elsewhere in the repository | A 1-hour token for one repository. Code that calls only a short allow-list of GitHub routes, with a test. **`contents: write` still reaches any unprotected branch**, as the foundation warns |

**Why approval alone is not the answer.** The Design Patterns paper says of a code agent: "There are a multitude of stealthy ways of introducing
malicious behavior that would be hard for the end-user to detect", and that "a hijacked code agent might inject this web request into the code"
([paper](https://arxiv.org/html/2506.08837)). Meta says approval fails if someone blindly confirms (customers research). So a click is required and is not a boundary.

## How competitors do it

Read from their own pages on 2026-11-02, unless marked as carried from [the competitors research](../../research/phase-2-competitors-2026-11.md). "Not stated" means the page read did not say it,
not that the product lacks it.

| Product | What it does | Person's control | Limits and notes | Price |
|---|---|---|---|---|
| **Sentry Seer Autofix** | Root cause, solution, code, then pull request iteration | Per project: stop after Root Cause, Plan, or PR Drafted. People can remove plan steps and add instructions. Local checkout. Handoff to coding agents, on GitHub only. An organization can disable code generation | GitHub.com and GitLab.com only, no self-hosted source control. Automatic runs need 10+ events in the last 14 days and a "fixability score" | **$40 per active contributor a month** (2+ pull requests in an enabled repository). GitHub bots not counted |
| **Datadog Bits** | Generates fixes **by default** for code-related causes (can be turned off). Bits Code iterates on CI logs and comments | "Bits Code never auto-merges". Guardrails Ask or Deny, in Preview | **Lists GitHub Enterprise Server and GitLab Self-Managed** ([Bits Code](https://docs.datadoghq.com/bits_ai/bits_code/), read 2026-11-02). Not on US government sites | AI Credits; roughly $6.50 to $8.45 an investigation by arithmetic, from competitors research |
| **incident.io** | "The only change Investigations can make to your systems is a pull request you review and merge yourself" | Person merges | Sandboxed containers torn down after use; providers cannot store data: vendor claims | Add-on; no price shown |
| **Splunk AI SRE** | A Claude Managed Agent proposes and opens a pull request, in a "disposable, network-isolated sandbox" | Engineer reviews | **Remediation is in Alpha.** Source is a blog post, no technical documents found | Not stated |
| **Rootly** | Remediation steps and draft pull requests | "Every change requires explicit human sign-off" | Docs warn it does not filter write-capable MCP tools (carried) | By quote |
| **OneUptime** (open source, self-hostable) | Starts fixes on a click (**Fix with AI**, **Start task**). Can also start from investigations. Automatic fixing for incidents: "Fixing starts off, for new projects too". On a Runner you host, "builds and tests its change before the pull request opens". A Global Runner runs no commands | "A person reviews and merges every one". Shows files read as it works, with a log; attached context is shown as sent. **A preview before sending was not found on the page read, which proves nothing** | GitHub through its App; GitLab, including self-managed; Forgejo and Gitea, including self-hosted. Own or local models; with those, "your code never leaves your infrastructure". Its production-review step says a diff and title "are data to OneUptime AI, never instructions". Read [ai-agent](https://oneuptime.com/docs/en/ai/ai-agent), 2026-11-02 | Apache 2.0, except `ee/`; Community edition "Free and open source" ([README](https://github.com/OneUptime/oneuptime)). Connecting a repository "Needs the Growth plan", stated for Cloud; **whether the self-hosted fix path is free is not stated, and nothing was installed** |
| **Better Stack AI SRE** | "Writes and validates a minimum-viable fix"; on a click, "will autonomously submit a pull request" from its own App. Runs "in an isolated environment for each conversation" | A person clicks. **How it validates is not stated** | GitHub and GitLab. Read [error pull requests](https://betterstack.com/docs/ai-sre/writing-code/error-pull-requests/), 2026-11-02 | Not read |
| **Grafana Managed Sandboxes** | The agent "runs tests and builds, and commits to a branch" in a sandbox, and shows the diff with Create PR. Public preview | "Grafana never merges or deploys". Per repository: approval, or automatic drafts for verified private repositories | The page does not say Cloud only; its docs link points to a Grafana Cloud path. Read [what's new, 2026-10-19](https://grafana.com/whats-new/2026-10-19-grafana-managed-sandboxes--from-root-cause-to-pull-request--without-leaving-grafana/), 2026-11-02 | Not read |
| **Middleware OpsAI** | "Open a pull request for review", and "a ready-to-apply fix". Also an Autofix that applies "fixes on Kubernetes without waiting on a review" | Review implied for standard fixes; who reviews is not stated | GitHub and Bitbucket named. Read [overview](https://docs.middleware.io/opsai/opsai_overview), 2026-11-02 | Not read |
| **New Relic Autopilot** | "Recommends actions but does not take them" (carried) | Person | No evidence found of it opening a pull request | Compute units |
| **OpenObserve** | Remediation plans. No pull requests on the page read (carried) | Not stated | AI is in Enterprise, not AGPL | Free licence to 50 GB/day; above that, sales |
| **HolmesGPT** | "Can even open PRs to fix what it finds"; also says "read-only access" by design | Approval not stated | The README does not reconcile these | Free, own model |
| **Aurora** | "Can generate pull requests with the remediation"; "open fix PRs" as an automatic post-analysis action | Approval not stated; org command policies | Isolated Kubernetes pods. Its README lists a NeMo input rail: "Prompt injection detection on every turn" | Free, Apache 2.0, own model |
| **OpenSRE** | Pushes fixes to a pull request branch (carried) | Tool approvals (carried) | Alpha | Free |

### The reference design is not an observability product

**GitHub Copilot's cloud agent** publishes the most complete mitigations found, and they map almost one to one onto F5's risks
([GitHub](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)):

- "workflows are not triggered until Copilot cloud agent's code is reviewed", approved by someone with write access.
- It will not act for users without write access (seen in a search excerpt of GitHub's docs).
- It "only has the ability to push to a single branch".
- It "filters hidden characters".
- It "prevents the user who asked Copilot cloud agent to create a pull request from approving it".

**Whether a third-party App can get the same workflow hold from GitHub is not known**, and no page read suggests so. That is why F5-D5 builds it with a skip instruction.

### What the market shows

- No product checked lets a fix merge itself. **Human review cannot be a selling point.**
- **Aurora and OneUptime describe injection measures; no product checked was found publishing injection test results for this step.**
- **Seer needs hosted source control, and names self-hosted as unsupported. Datadog and OneUptime reach self-managed source control.** F5 starts with GitHub.com only, so that gap is F5's own.
- Most products gate on evidence: Seer's fixability score, Rootly's "Inconclusive". *Superseded for F5 by D35.*
- **Verifying before the diff is a growing group:** Grafana, OneUptime's own Runner, Better Stack (method unstated). Splunk and incident.io describe using sandboxes; the pages read do not say they verify a change. F5 runs nothing, and has no answer except small scope and saying so.
- **Draft plans.** "Draft pull requests are available in public repositories with GitHub Free and GitHub Free for organizations", GitHub Pro and legacy per-repository plans, and in public and private repositories on GitHub Team and Enterprise Cloud ([GitHub's pull requests API page](https://docs.github.com/en/rest/pulls/pulls), read 2026-11-02).
- **Copilot and models.** GitHub's pages read list custom API keys for Copilot Chat, the CLI and IDEs. A statement that they reach the cloud agent was not found, so "on the customer's chosen model" is not used as a contrast.

**The price picture.** Nexovue charges nothing for F5 on a self-hosted install, and the customer pays their provider. The real cost is unmeasured. **Arithmetic on guesses:** a 64 KiB source pack at
3 to 4 characters per token is 16,000 to 22,000 tokens going in, so the foundation's guessed 2 million monthly allowance covers fewer than 100 proposals, and explanations share it. A local model has its own limits.

## What research says about AI fixes

| Finding | Source and strength | What it means |
|---|---|---|
| Passing tests does not mean mergeable. Maintainers' merge rate was about 24 points below the automatic grader. They also merged only about 68% of the human patches. 296 AI pull requests, 4 maintainers, 3 repositories, no CI, one attempt, newest model Claude Sonnet 4.5 | [METR, 2026](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/), a note with careful caveats | Score F5 by a person's review, not by tests. Do not promise "tested" as if that were "right" |
| Plausible is far from valid. Google's agent produced at least one plausible patch for 25.6% of human-reported bugs, and a valid one for 17.9% | [Google](https://arxiv.org/pdf/2501.07531), read as a search excerpt | Google has far more context than a small customer's pack. Expect much refusal and many wrong patches |
| Refusing helps. On 174 human-reported bugs, deciding not to try and rejecting poor patches each raised success, up to 29 points combined, from a baseline of 0.11 first-try success | [Abstain and Validate](https://arxiv.org/abs/2510.03217), Google, ICSE-SEIP 2026, read as excerpts | Build the refusal first. Its second model call is worth measuring later, not assuming; each step multiplies error |
| Models invent packages: 19.7% of packages recommended did not exist. Many inventions repeat, which makes the attack workable | [Spracklen et al.](https://arxiv.org/html/2406.10279v1), USENIX Security 2025, older models, excerpts | Manifests cannot change, and a new import is flagged, so a reviewer sees it. **An invented package can still reach a reviewer** |
| Dual LLM with strict formatting was the safest code-agent design, at a cost in usefulness. Confirmation alone was called weak | [Design Patterns](https://arxiv.org/html/2506.08837) | F5's writer must read source, so it cannot be fully isolated. Hence hard checks on its output |
| Hidden instructions in AGENTS.md, README files and comments steer coding agents; one post claims 85% | Blogs and vendors in a [search](https://dev.to/mistaike_ai/a-readme-file-told-my-ai-agent-to-leak-my-secrets-it-worked-85-of-the-time-3ff1). **Primary sources not read; do not quote the number** | Treat repository text as untrusted, and exclude agent instruction files |
| Source code raised accuracy 9 to 16 points on root cause | ORCA-bench, via customers research | F5's read may later help F3. F3's first version reads none, as the foundation says |
| Review load is the hidden cost | Customers research, `:123-127` | Small diffs, and nothing opens by itself |

**Oracle's experience report** concludes that automated repair is not achieved for real systems and is a productivity aid ([search excerpt](https://arxiv.org/html/2508.16071v1)). Plan the wording around that.

## What GitHub's documents say

| Question | Answer | Status |
|---|---|---|
| Permissions for a draft pull request | Create a pull request: **Pull requests, write**. Commit, tree and blob creation: **Contents, write** ([GitHub](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)). This answers the foundation's open item for the docs; still try it live | Read |
| Writing to `.github/workflows` | The same page lists these routes under a Workflows permission too. **Never ask for it**, and GitHub should then refuse such writes. Inference | Must be tested |
| Does a pull request opened with an App's token run CI? | GitHub says App tokens, unlike `GITHUB_TOKEN`, let `pull_request` workflows run automatically ([trigger docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow), excerpt) | Read as excerpt |
| Is a draft exempt? | Pages read do not say so. Nothing suggests it, and default activity types include `opened` | **Must be tested** |
| Does that run get secrets? | The docs say secrets are withheld from **forks**. Same-repository branches are not addressed there. **Inference: they get them** | **Must be tested** |
| Stopping that run | `[skip ci]`, or a `skip-checks: true` trailer, stops `push` and `pull_request` runs for that commit: "Skip instructions only apply to the `push` and `pull_request` events". Required checks then stay pending and block merging until a new commit arrives ([GitHub](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs), read 2026-11-02) | Read. **`pull_request_target` still runs**, though on the default branch's code. **`create` is not held:** its `GITHUB_SHA` is the "Last commit on the created branch or tag", so that it runs model code before review is an inference, and whether an App's branch fires it is not stated ([events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)). `workflow_run` runs the default branch's commit |
| Other CI systems | CircleCI, Jenkins and others were not checked | **Docs must warn** |
| Checking a person's access | "Get repository permissions for a user" returns their role ([GitHub](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user)). The page names no needed App permission | Must be tested |
| Acting as a person | An App's user token holds the overlap of the App's and the person's access, and work is attributed to that person ([GitHub](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user)) | Read |
| File modes on a tree | Includes symlink and submodule ([GitHub](https://docs.github.com/en/rest/git/trees)) | Allow only the file's existing mode |

## Methods compared

### How much the AI does

| Method | What it needs | For | Against | Verdict |
|---|---|---|---|---|
| **M0.** A fact line: "version v2.1 reached this service 4 minutes before; v2.0 ran before it" | F28 only. No AI, no repository | Works with AI off. It is often the on-call's real first move | Not a code fix, and order is not cause | **Build first** |
| **M1.** A task brief to copy into the customer's own coding agent, from facts | F3's facts. No AI call, no source sent, no write | Cheap. Respects tools people already have. Seer has a similar handoff. Partly answers "why not just use Claude?" | Their agent is outside Nexovue's fence, and docs must say so | **Build** |
| **M2.** Model reads a fixed source pack and returns validated edits | Consent, read token | Model steers nothing. Cost and privacy are bounded. Testable | Misses context outside the pack; more refusals | **The first version** |
| **M3.** Agent loop with read-only repository tools | As M2, plus tools | Better context, likely higher quality | Injected text chooses what is read next. Costs more and multiplies steps | Later, if the bed shows M2's misses are missing context |
| **M4.** Sandboxed agent that runs tests | A disposable, network-isolated runner | The market's direction (Grafana and OneUptime's own Runner run tests; Splunk and incident.io describe sandboxes). Real test evidence | Runs customer code with its dependencies on a customer's server. The 16 GB sizing, "two images" and the small-team setting all oppose it. METR says passing is not proof | **Do not attempt** in Phase 2 |
| **M5.** Automatic runs for every eligible finding | As M2 | Fix is ready when you arrive. Datadog's default | Spends budget and discloses source without anyone asking. Attackers can trigger it | **No** |

### What the model returns

| Form | For | Against | Verdict |
|---|---|---|---|
| Unified diff text | Compact | Hard to validate exactly; line numbers drift. Author's judgement, not measured | No |
| Whole files | Simple | Large, and quiet unrelated changes are hard to see | No |
| **Edits: path, exact old text, new text** | Nexovue checks the old text occurs exactly once in the blob it read, and builds the diff itself | More refusals when the model misquotes | **Yes**, and the bed should confirm it |

### Where the source reaches the model

| Option | Verdict |
|---|---|
| `scheduler-insight` fetches from GitHub | **No.** It would hold a repository token. Foundation forbids it |
| Job arguments | **No.** Ids only (foundation section 1) |
| Short-lived table, written by `scheduler-connections`, read by `nexovue_insight`, deleted when the run ends | **Yes** |

## What Nexovue should bring

Claims stay in the future tense until shipped, and must never name what others lack.

1. **Ladder of help, each rung useful alone:** fact line, copyable brief, suggested fix on the finding, downloadable patch, draft pull request. The first two work with AI off and send no source. *The ladder itself is not a differentiator: Seer has stopping points.*
2. **Hard checks on the diff, in Nexovue's own code, published in AGPL and tested.** Proposed list for the design:
   - Only files in the pack; only inside the mapped folder; the file's existing mode.
   - Never workflow, build, dependency or CI configuration files, lockfiles, or container files.
   - No new URLs, IP addresses or dependencies; invisible and bidirectional characters refused.
   - A cap on the size. **Starting guess:** 5 files and 60 changed lines.
3. **"What was not checked" on every proposal.** It always includes "not compiled, not run". The customers research asked for it, and no page read describes it.
4. **A suggestion does not go stale silently.** Each read file's blob id is kept. *What the click does on a change is superseded by D11 and FR21.*
5. **Injection results for the fix link, if the founder agrees to publish** (foundation, founder decision 5). None were found from anyone.
6. **Works on a customer's own or local model.** *Not a differentiator, since OneUptime documents this.* Quality there is unproven, and local may be more exposed (LogJack, via the foundation).

### Evaluation

The foundation's run reads "Each fault with a real code cause in the demo". **The plan's demo flags inject failure through feature flags (`phase-2-plan.md:94-96`), so a patch removing that failure may prove nothing, and the honest fix may be "turn the flag off".** This is an inference; the demo's source (`tools/otel-demo/upstream`) was not read for it.

Recommendation: add a small fixture repository with seeded real bugs, about 10 to 20 (a guess), including some where refusal is right. Score:

- **Hard gates, today:** zero writes without a click; refusal on thin evidence; every planted bad diff refused by the checks.
- **Injection cases:** A1, A3, A4, A5, A6 and A10 above, repeated, on each named AI option.
- **Reported, no bar yet:** a person's judgement of "would merge", "useful start" or "wrong"; refusal rate; tokens per proposal. **Any acceptance target now would be invented.**

## What we should not attempt

| Do not | Why |
|---|---|
| Merge, or mark ready for review, on anyone's behalf | The plan says a person always approves |
| Run customer code or tests | M4 above |
| Reply to review comments, or iterate on CI | Seer and Datadog do it. It would make GitHub comment text an input and a model able to push repeatedly, which breaks the write split |
| Read GitHub issues or pull requests as context | More attacker text for little proven gain |
| Change infrastructure, flags or configuration; run commands | Writes outside a reviewed pull request |
| GitLab, Bitbucket, or GitHub Enterprise Server at first | Foundation promises GitHub.com only. Enterprise is the likely next step, since it is a gap at Sentry, after a real try |
| Fix a project-wide finding | No `service_name`, so no mapping (`findings/service.py:330`). Refused, and the screen says why |
| Minified browser stacks in the first version | Needs the API's `symbolic` and a browser-format parser the server does not have. Refuse and name the reason. Revisit from demand |
| Claim "tested", "safe", "root cause fix" or a success rate | Nothing is measured. Foundation section 12 lists more |

## Where this disagrees with the foundation

The foundation asks that disagreement be stated. **These are for the founder's approval of it, or for F5's design to carry.**

1. **Section 3's Rule of Two table, F5 draft pull request row, says untrusted input "None".** No model runs in that step, but the diff it commits is the output of a model that read untrusted source. Recommended wording: "Model-written code, checked by Nexovue and reviewed by a person. No new text enters."
2. **U4 and U5 cannot apply as written to a diff.** A diff is model text that leaves the console, on purpose. Prose follows U4 and U5 unchanged; the diff follows the checks above.
3. **Section 5 says a viewer reads fixes.** F5-D7 narrows this for code, since a fix quotes private source. A viewer reads the summary, state and pull request link.
4. **Section 5 lets any member click.** F5-D6 narrows it with a GitHub access check, which the section allows.
5. **Section 7's states lack a stale one, and a consent one.** The design needs `stale` or a derived equivalent.
6. **Section 9's source maps are not available where the foundation places the work,** as above.
7. **Section 11's fix run needs more than the demo,** as above.

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| F5-D1 | What the first version offers | (a) Suggestion only. (b) Straight to pull request. (c) A ladder | **(c).** The plan says suggestion, then draft. Facts and a brief work with AI off and no consent |
| F5-D2 | How the model reads | M2, M3, M4 | **M2.** Attacker text cannot steer later reads. Measure before widening. Costs some quality |
| F5-D3 | When a proposal runs | (a) Automatic. (b) Opt-in automatic. (c) Only on a click | **(c).** Disclosure and cost follow a person's choice, and A11 goes away. Costs a wait: the fix is not ready on arrival |
| F5-D4 | Which findings qualify | (a) Any. (b) `likely_cause` only (FD14). (c) That, plus a real path into code | **(c).** Without a file there is nothing to fix. Evidence paths: a stack matching the repository; or an endpoint name found literally in the tree, as a weaker path, measured in the bed. **Many *slower* and *traffic* findings will be refused; the screen says why** |
| F5-D5 | CI on the new branch | (a) Let it run and warn. (b) Skip by default; a second click pushes a no-change commit to run it. (c) Open from a fork | **(b).** The same intent as Copilot's hold, with a tool a third party has. (a) runs unreviewed code with possible secrets. (c) needs a fork per customer and more permission. Costs a click, and only covers CI that honours the instruction. **Must be tested on GitHub before the design is approved; if a draft does not run CI, this simplifies** |
| F5-D6 | Who may click | (a) Any member. (b) Admins only. (c) A member whose linked GitHub account has write access to that repository | **(c).** It stops A8 while leaving the on-call engineer able to act, where (b) would stop them. Linking is a one-time redirect, and the write itself still uses the App's token, so the author is the App. **Costs one more set-up step, and the permission route must be tested** |
| F5-D7 | Who reads the diff | (a) All readers. (b) People who may request a fix; a viewer sees a summary; tokens see nothing | **(b).** Private source should not spread by role. `FindingForMember` is the pattern. No MCP or F4 tool returns it, which is F4's to honour. Friction: a viewer cannot read the patch |
| F5-D8 | What source consent binds to | (a) A yes per organization. (b) Per repository, bound to the provider's host and format | **(b).** It stops A7. Changing provider asks the owner again |
| F5-D9 | Files never sent | (a) None. (b) A fixed list: secret and key file patterns, `.env`, CI and agent instruction files (`AGENTS.md`, `CLAUDE.md`, rules folders). A customer may add, not remove | **(b).** Some style guidance is lost, and the attack path narrows. Also scan the pack for secrets before sending, reusing masking's ideas, and drop any file that matches. Which scanner is for the design |
| F5-D10 | Pack size | Fixed cap, with reasons when cut | **Starting guess:** 12 files, 64 KiB. Replaced by the bed's result |
| F5-D11 | Which code to read | (a) Default branch always. (b) F28's `commit_sha` when known, else default branch, labelled "the running version is not known" | **(b).** A patch against the wrong code misleads. Base the branch on the default branch, since that is what merges, and refuse if the read files differ there |
| F5-D12 | Second model as critic | (a) In the first version. (b) Bed experiment | **(b).** The paper's gains are real, on Google's system. Hard checks come first, and each call costs and can err |
| F5-D13 | Pull request text | (a) Model written. (b) Template of validated facts | **(b).** Foundation U5. Reads flatter, and works identically on every model |
| F5-D14 | Keeping proposals | (a) Forever. (b) With the finding; code deleted on disconnect or consent withdrawal, keeping the fact that a proposal existed; the pack deleted after the run | **(b).** Stored source must not outlive permission. Adds to `organizations/tables.py` |
| F5-D15 | One or many pull requests | (a) Any number. (b) One open draft per finding | **(b).** Review load. Foundation's run limits also apply |
| F5-D16 | Downloadable patch | (a) None. (b) Offered beside the pull request | **(b).** Reaches teams that will not grant write access, and needs no write permission at all |
| F5-D17 | Who can run a fact line and brief | All who may read the finding, with AI off | They use no provider and change nothing |

## Founder decisions needed

*Superseded 2026-11-02.* Item 1 is settled by foundation decision 8 (one shared round), with F5's questions in `spec.md`, D57. Item 2 is the spec's founder decision 1. The live list is `spec.md`, *Founder decisions needed*.

1. **Talk to buyers before F5's design is approved** (carried from the foundation). Nothing found says whether a small team would grant a bot write access at all. **Recommendation:** in five conversations, ask directly: "Would you install this with write access? Would the suggestion alone be enough?" If most say no, F5 stops at the read-only rungs, which saves much of this build.
2. **Liability for AI-written code in a customer's repository.** Nexovue's output could carry a bug or a backdoor into their product. **Recommendation:** add this to the lawyer's reading already proposed in the foundation, and add plain no-warranty terms before it ships. The self-hosted AGPL licence's own disclaimer may cover some of it, which is a lawyer's question. It is unpriced.

## Not checked

- Everything marked "must be tested". F5's design should begin with a one-hour trial on a scratch repository: draft plus App token, with and without `[skip ci]`, a secret-printing workflow, and an attempted write to `.github/workflows`.
- Whether browser-sent records can be told apart from server ones when reading a stack; if they can, browser stacks should never choose files.
- Whether commits made through the API appear as verified.
- Copilot's protections beyond GitHub's own description; other competitors' permissions, sandboxes or real behaviour. Nothing was installed.
- Any quality, token or latency figure on any model. The demo's source for real code causes.
- Whether users want this at all.

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md), [customers](../../research/phase-2-customers-2026-11.md), [F6 documents](../anomaly-detection/spec.md).

**Competitors**

- Sentry: [Autofix](https://docs.sentry.io/product/ai-in-sentry/seer/autofix/), [Seer](https://docs.sentry.io/product/ai-in-sentry/seer/), [pricing](https://docs.sentry.io/pricing/)
- Datadog: [Bits Remediation](https://docs.datadoghq.com/bits_ai/bits_remediation/), [Bits Code](https://docs.datadoghq.com/bits_ai/bits_code/), [Bits Investigation](https://docs.datadoghq.com/bits_ai/bits_ai_sre/)
- [incident.io AI SRE](https://incident.io/ai-sre)
- OneUptime: [AI agent](https://oneuptime.com/docs/en/ai/ai-agent), [README](https://github.com/OneUptime/oneuptime)
- [Better Stack: error pull requests](https://betterstack.com/docs/ai-sre/writing-code/error-pull-requests/)
- [Grafana Managed Sandboxes](https://grafana.com/whats-new/2026-10-19-grafana-managed-sandboxes--from-root-cause-to-pull-request--without-leaving-grafana/)
- [Middleware OpsAI](https://docs.middleware.io/opsai/opsai_overview)
- [Splunk: root cause to pull request](https://www.splunk.com/en_us/blog/observability/ai-sre-in-splunk-observability-cloud-can-remediate-incidents-with-claude-managed-agents.html)
- [HolmesGPT](https://github.com/HolmesGPT/holmesgpt), [Aurora](https://github.com/Arvo-AI/aurora)
- [GitHub Copilot cloud agent: risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)

**GitHub**

- [Permissions for GitHub Apps](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps), [Pull requests](https://docs.github.com/en/rest/pulls/pulls), [Git trees](https://docs.github.com/en/rest/git/trees)
- [Trigger a workflow](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow), [Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [Skip workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)
- [Acting for a user](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user), [Repository permissions for a user](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user)

**Research**

- [METR: many SWE-bench-passing pull requests would not be merged](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)
- [Design Patterns for Securing LLM Agents](https://arxiv.org/html/2506.08837)
- [Evaluating agent-based program repair at Google](https://arxiv.org/pdf/2501.07531), [Abstain and Validate](https://arxiv.org/abs/2510.03217), [From benchmark data to applicable repair (Oracle)](https://arxiv.org/html/2508.16071v1). Read as excerpts
- [We Have a Package for You!](https://arxiv.org/html/2406.10279v1). Read as excerpts
- LogJack, AIOpsDoom, ORCA-bench and Meta's Rule of Two are quoted from the customers research, not re-read

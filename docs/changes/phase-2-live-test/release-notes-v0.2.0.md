# nexovue v0.2.0: release notes (draft)

> Draft for the founder, written 2026-11-28 from the live test and the features' status. Paste into
> the GitHub release for `v0.2.0` once the candidate (`v0.2.0-rc.1`) has passed on a server.

## What is new

**From a problem to its cause, its case and a suggested change.**

- **Finding explanations (F3).** Each finding shows the facts that line up with it, each with its
  basis: deploys, nexovue's own checks, counts from your telemetry. Optional AI wording puts them
  in plain sentences, labelled with the model and tied to the facts it rests on. A likely cause
  is stated only once its accuracy bar is met, which it has not been yet: this version states none.
- **Deployment and release tracking (F28).** Send signed deploy events from your pipeline; a
  finding names the deploy before it, honestly worded ("Possibly…, too few to judge how usual
  this is").
- **Cases and GitHub issues (F22).** Take a finding as a case, create or attach a GitHub issue
  through nexovue's own GitHub App, and follow the issue's state. Jira is not in this version.
- **Chat (F4).** Ask about a project's findings, services, releases and logs. Every sentence cites
  what was read and is labelled by where it comes from; numbers not found in what was read are
  flagged. Text planted in your telemetry is treated as data.
- **Suggested fixes (F5).** From the files you confirm, your AI provider writes a change that
  nexovue checks against fixed rules before you see it. Download it as a patch, or open a draft
  pull request on GitHub. **Provided as is, without warranty: you review and you merge.**
- **SLO tracking (F19)** and **dashboards (F20).** A burn that turns critical pages on PagerDuty,
  even when it first joined another problem's warning alert.

**A clearer console.** A finding opens on a strip of the five steps from the finding to a pull
request, with the one next step; the suggested fix sits on the second screen, not the last. The
finding list shows each finding's case, issue, fix and pull request in small. Settings has one
**AI** page that opens on a setup checklist, and GitHub's two Apps have their own page. The
sidebar is grouped by what you came to do (Respond, Investigate, Watch), counts open findings, and
keeps your account, the documentation and the theme in one row. Headings, buttons and both menus
follow one set of sizes.

## AI: what it works with

- You bring your own AI provider and key; nothing is sent until an owner agrees to exactly what
  is sent. Formats offered: OpenAI-compatible (OpenAI, gateways, Ollama, vLLM) and Anthropic's
  Messages API, each proven by a recorded real call.
- **Suggested fixes and draft pull requests were evaluated on Claude Sonnet 5.5:** attacks that
  try to steer a suggestion found no way past nexovue's checks in 204 trials
  (`docs/research/f5-evaluation-2026-11.md`). That proves those trials only; a person's review
  is the protection. Drafts are not limited to that model: with any other, nothing has been
  evaluated.
- **Small local models:** chat needs a model that calls tools reliably. With a 7B local model
  (Qwen 2.5 7B) chat refused safely instead of answering, and suggested fixes were rarely correct.

## Before you upgrade

- Database migrations run on start (up to `0087`). Back up first (`docs/self-hosting/backup.md`).
- **Draft pull requests write to GitHub.** Before an owner allows drafts for a repository, read
  *Before you allow drafts: protect your branches* in the suggested-fix setup page. A public
  repository needs an admin's separate allowance.
- **The AI process can reach the network.** This version ships no outbound limit for it; restrict
  `scheduler-insight` to your provider's address with a firewall rule if that matters to you.

## Not in this version

Jira (waiting on Atlassian's written answer), GitHub webhooks tested against a public install,
a hosted service, and any claim that a model is "supported" beyond the evaluation above.

## Known issues

- One small wording: chat says "There is no data in this project" when only the asked-about
  service has none.
- Chat's number check can flag a number taken from the question itself.

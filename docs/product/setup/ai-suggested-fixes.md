# Suggested fixes from AI

A finding can carry a suggested change to your code, written by the AI provider you chose and
checked against Nexovue's published rules before you see it. It is a suggestion: Nexovue does
not compile it, run it or merge it, and a person must review it as they would an outside
contribution. **Provided as is, without warranty: you review and you merge.** The terms ship
with Nexovue as its terms for suggested fixes; an owner agrees to them when agreeing to send a
repository's code.

**Where it applies.** On self-hosted installs, for GitHub.com repositories mapped to a service. Automatic file selection needs a newly appeared error with a stack, and adds files that the top of the stack imports from the same folder; otherwise a person names up to 5 files. The AI may ask for a few more files, and they go only if a person agrees. A draft pull request also needs a deploy of this service as the likely cause; otherwise the same change can be downloaded as a patch. Where Nexovue cannot read the code, a general suggestion with example code may be offered from the finding's facts alone, when there is a likely cause or a recognised error type; Nexovue has not seen that code. Other findings get what changed and the brief.

Suggested fixes, if you turn them on, send the source files you see listed, from repositories you agreed to, with the finding's facts and any hint you type, to the AI you chose. Nothing is sent until you click.

## What you get, step by step

Each step needs more from you than the one before, and you can stop at any of them.

| Step | What it needs | What leaves your install |
|---|---|---|
| What changed, and a brief to copy into your own coding tool | Nothing; works with AI off | Nothing |
| A general suggestion with example code, labelled "Example pattern, not your code" | AI on | The finding's facts and the language you pick. No code |
| A suggested change on the finding, from your own files | AI on, the *Nexovue suggestions* GitHub App with read access, an owner's agreement for that repository | The files you see listed, the finding's facts and your hint, to your AI provider |
| A patch file to download | As above | Nothing more |
| A draft pull request | As above, plus write access, an owner allowing drafts for that repository, an admin allowing it if the repository is public, your own GitHub account linked, and a deploy of this service as the likely cause | The change, to your repository on GitHub |

## Write access, and the model it was evaluated on

**Opening a draft pull request, and running CI on one, are on in this version,** by the
founder's go of 28 November 2026, recorded in code (`nexovue_platform/fixes/write_gate.py`).
The go followed the safety evaluation: attacks that try to steer a suggestion found no way
past Nexovue's checks in 204 trials on **Claude Sonnet 5.5**, the model it names (Nexovue's
evaluation report of November 2026). That
proves those trials only, not that any model resists every attack, and every attack still
reached a person: the person's review is the protection. Drafts are offered whatever model your
connection uses; with any model other than Claude Sonnet 5.5, nothing has been evaluated. An install built with write access
off says, where the page would offer a draft:

> Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done. Download the patch instead.

**Drafts work on every GitHub plan.** GitHub made draft pull requests available in every
repository, public or private, on every plan, on 1 May 2025. If GitHub ever refuses a draft,
Nexovue never falls back to an ordinary pull request: it offers the patch instead.

## What is sent, and to whom

When you click **Send these files**, these go to the AI provider your owner agreed to, and
nowhere else:

- the files on the list you saw, read from GitHub at one commit, at most 12 files and 64 KiB;
- the finding's facts, with service and endpoint names replaced by stand-ins such as
  `service_1`;
- the line numbers from the error's stack, as numbers only, never the stack's own text;
- the hint you typed, if any.

The model may ask for up to 3 more files in each of its first two rounds. They go only after
they are listed for you and you click **Send** again.

**Never sent, whatever anyone names:** secrets and key files (such as `.env` and `*.pem`), CI
files (such as `.github/**`), and agent instruction files (such as `.cursorrules`, `.claude/**` and `.mcp.json`). Files shaped
like configuration (such as `config/**` or anything named `*settings*`) are kept back unless an
owner allows a named path. Only source files with a known extension can be listed at all. The
lists are published under the AGPL in `nexovue_platform/fixes/policy.py`.

**A forged error can steer which files are offered.** Anyone who can send telemetry to your
install can write a stack that names a file in your repository. Nexovue lists only files that
exist in the consented folder of this service's repository, and a person sees and agrees to
the list before anything goes. Read the list.

## What Nexovue checks, and what it does not

Before you see a suggested change, Nexovue's rules refuse it if it touches a file outside your
list; changes a build, CI, dependency or agent instruction file; does not match the file
exactly once; creates or deletes a file; is too large; adds an address not already in the
file; adds hidden or look-alike characters; adds a long encoded string; adds something shaped
like a secret; or has a line too long to read. The rules run again when the change is shown,
when you download a patch, and before a draft is opened, against the file as it is on GitHub
then.

**Not checked:** "Not compiled and not run by Nexovue. A subtle bug passes every rule." The
rules catch set patterns. A wrong or harmful change can still get through them, so review it
as you would a stranger's pull request. Nexovue's lists of imports, network calls and comments
do not cover every language, and the page says so on the file when they do not.

## Before you allow drafts: protect your branches

A draft is opened by the App on a branch of its own, `nexovue/suggestion-<id>`, and Nexovue
never pushes to any other branch. **GitHub's write permission is wider than that:** it reaches
every repository you installed the App on.

> Write access reaches every repository you installed this App on; Nexovue restricts where drafts open.

So, before an owner allows drafts for a repository:

- **Protect `main` and every release branch** with a GitHub branch rule or ruleset that requires
  a pull request and a review before merging. Then even a misused App token cannot change them
  directly.
- **Require a reviewer other than the requester.** On GitHub the pull request's author is the
  App:

  > On GitHub this pull request's author is the App, so the person who requested it can approve it. We have not yet shown that a GitHub rule can stop that.

- **Look at your other CI and other triggers.** A draft asks GitHub Actions to hold its runs:

  > Asks GitHub Actions to hold `push` and `pull_request` runs. Other triggers, other CI systems and apps connected to this repository may still run.

  A preview service, a review bot or another CI system connected to the repository may still
  act on a draft. Nexovue does not control them.
- **Check every workflow triggered by `pull_request_target`.** Those run with your repository's
  secrets, on the base branch's workflow, when any pull request opens, drafts included. A
  workflow there that checks out the pull request's code runs the model's code with your
  secrets. Remove that checkout, or do not allow drafts in that repository.
- **Run CI** on a draft is a second, separate click, with its own warning:

  > This runs the model's code in this repository's CI, possibly with its secrets. Read the diff on GitHub first.

**A public repository.** A draft's branch in a public repository is public too: anyone can see
that something is broken, and where. So drafts there are refused until an admin allows them, on
the *Nexovue suggestions App* panel in Connections, after that warning; the admin can withdraw
it. Each is recorded in the audit log.

**Your own GitHub account.** Before you open a draft, you link your GitHub account once, so
GitHub can tell Nexovue what access you have to the repository. Nexovue keeps your GitHub user
name and id, and no token.

**The login note.** On a private repository the pull request names who asked for it:

> Your GitHub login is written into the pull request, and stays on GitHub if you leave.

On a public repository it names only the requester's role, and their own note only if they
confirm it.

## After a draft opens

Nexovue follows the pull request on GitHub every 5 minutes and shows it as open, merged or
closed. A pull request merged or closed is final for Nexovue: if someone reopens it on GitHub,
Nexovue does not follow it back. If GitHub access to the repository is gone for 72 hours, the
pull request reads "untracked" and the finding is free for a new suggestion. Disconnecting the
App stops all GitHub access, on GitHub's side, whatever Nexovue does. A pull request Nexovue
opened stays on GitHub, and closing it is yours to do.

**A case left for 90 days.** A case nobody acts on for 90 days ends by itself. Any draft pull
request it opened is left as it is on GitHub, untouched; close it there when you are done.

## How long code is kept

Nexovue stores the files it read and the suggested change only while you may still use them,
and deletes them:

- as soon as a suggestion ends without a change, or is refused;
- 30 days after a suggestion is written;
- 7 days after its pull request is merged or closed;
- at once when an owner withdraws agreement, the App is disconnected, AI is switched off, the
  project is deleted, or GitHub access has been gone for 72 hours.

**Backups.** A backup taken while code was stored holds it until that backup ages out. Plan on
the deletion date plus however long you keep backups (4 weeks, if you keep the default
rotation). **Restoring a backup deletes every stored file and change, ends any suggestion in
flight and withdraws every owner's agreement,** so an owner agrees again after a restore before
anything is sent.

## Limiting where the AI process can reach

The AI process can reach the network, so limit its outbound traffic if that matters to you.
**This version ships no outbound limit for it.** A setting that lets it reach only your AI
provider's address is planned before the first deployment. Until then, a firewall rule on the
host or your network that lets the `scheduler-insight` container reach only your provider's
address does the same job.

## What it costs

You pay your AI provider; Nexovue charges nothing for the call. Before you click **Send**, the
page says the most tokens that Send can use ("To api.anthropic.com, using up to about 27,144
tokens."); afterwards it says the tokens used.

**Measured, on Claude Sonnet 5.5** (Anthropic's price list: $2 per million input tokens and $10
per million output tokens; prices change, so check your provider's):

- In the safety evaluation's 204 trials, on small test files, a suggestion used a median of
  3,443 tokens (95th percentile 5,286): about **$0.01 to $0.02** each.
- In the live test, three Java files (21.5 KiB) and the finding's facts used 13,546 tokens:
  about **$0.04**.
- The most one Send can use is bounded: every attempt at the facts' own limit, the files (at
  most 64 KiB) and 4,000 tokens of answer, times 2 attempts.

A suggestion counts toward 3 a day per finding only when the model answered.

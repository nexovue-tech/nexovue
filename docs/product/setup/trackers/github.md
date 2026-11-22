# GitHub issues for cases

A case is the work on a detected finding: someone takes it with one click, writes notes, hands
it over and resolves it, all inside nexovue. Connecting GitHub adds one thing to that: from a
case, a person can create one GitHub issue, and the issue's open or closed status then follows
the case and the case follows it. Nothing else crosses.

> **Looking for incident management?** Cases cover detected findings, not alert-rule
> incidents, and paging stays with PagerDuty. A threshold rule or a failed check keeps its own
> alert record and cannot become a case.

**Available on a self-hosted install, with GitHub only.** The hosted service has no tracker
connections yet. Cases work fully without any tracker.

## What a connection does, and does not

| | |
|---|---|
| Creates an issue | Only when a person clicks **Create an issue** on a case and confirms after reading exactly what will be written. No finding, rule or schedule ever creates one |
| Keeps status in step | Resolving the case closes the issue, with one fixed comment. Closing the issue in GitHub ends the case once the signal has recovered; while the problem is still measured, the case stays and the page says so. Reopening either side reopens the other, unless another case is already active on that service |
| Carries | Open or closed, and a few fixed comments. **Not** comments, assignees, labels you add, or anything people write in either tool |
| Notifies | **Nobody, until someone is assigned in GitHub.** An issue nexovue creates is assigned to no one; open it and assign it there |
| Reads | Each linked issue's status, every few minutes. A webhook from GitHub only makes that read sooner; polling works alone |
| Refuses | A **public** repository, by default: an admin must allow one, after a warning, before any issue is written there. A repository that turns public later is refused again at its next issue |

**Cases cover anomaly findings only.** A case cannot start from a threshold rule's alert or a
failed check, so neither can an issue.

## What leaves your server

| What | Where | When |
|---|---|---|
| The issue: fixed words, numbers and times, the service name in a strict form (or the words "a service"), and a link back to your console. The label `nexovue` | `api.github.com` | Only on a person's click |
| Open or closed, and the fixed comments | `api.github.com` | As the case and the issue change, with no further click |
| Setting up the App | `github.com` | Only during setup |

Never sent: the finding's title, your notes, an endpoint, or anything an AI wrote. **Your
console's address is in the link back**; on a private install that is an internal name, and
anyone who can read the issue sees it. Opening the link needs a nexovue account on your install.

A sample issue:

```
Title: nexovue: slower than normal on checkout

nexovue detected this on `checkout`.

- What moved: latency, 4x its normal range
- Since: 2026-11-02 14:02 UTC
- Severity: critical

Evidence, and the case: https://nexovue.acme.internal/findings/8f1c...
This link needs a nexovue account. Ask your admin for an invitation.
```

## Before you start

- **A route out to GitHub.** The server must reach `github.com` and `api.github.com` directly.
  A company proxy for outbound traffic is not supported yet. An install with no route out
  still has cases; it just has no tracker.
- **Someone who may create GitHub Apps** in the GitHub account or organization the issues go
  to. On an organization this may be an owner rather than nexovue's admin.
- **Tracker connections turned on for the install.** They are on unless the operator installed
  with `NEXOVUE_CONNECTIONS=no`. If the settings screen says they are turned off, the operator
  runs the install again with `NEXOVUE_CONNECTIONS=yes` and restarts it.
- **For the operator:** the tracker role signs in to Postgres with its own account,
  `nexovue_connections`, which nexovue's setup step creates at every start with a password the
  installer generated. If setup says it could not prepare that account, the install's Postgres
  account needs the right to create roles (`CREATEROLE`); a managed Postgres may refuse it, and
  then tracker sync stays off while cases keep working.

## Connect GitHub

1. As an owner or admin, open **Settings → Connections** and choose **Connect GitHub**.
2. nexovue sends you to GitHub to create a private App for your organization, named
   `nexovue-<your organization>`. Review it and create it on GitHub.
3. GitHub asks where to install the App. **Choose only the repositories cases should create
   issues in.** The App asks for two permissions only: to read and write issues, and to read a repository's basic details (name, and whether it is public).
4. You return to nexovue. The screen lists the repositories the App reaches and runs a test.
   **The connection works.** means you are done.

One GitHub connection per organization. A company with two GitHub organizations can reach only
one.

To map a service to the repository its issues usually go to, use the mapping on the same
screen; the create panel then preselects it.

## Create and follow an issue

On a case, choose **Create an issue**, pick the repository, read the text exactly as it will be
written, and confirm. The case shows the link once GitHub has the issue, then its status.

Already have an issue? Paste its address with **Attach an issue**: it is read hourly and shown,
but it never changes the case unless someone chooses **Sync with this case**.

If GitHub cannot be reached, the case says so and nexovue keeps trying; nothing is lost and
the case still works. Admins are emailed when a connection keeps failing.

## Disconnecting, deleting and backups

- **Disconnect** deletes the stored App key and stops all sync at once. **Every issue stays in
  GitHub**, readable by everyone with access to the repository. The App stays in your GitHub
  account until you delete it there.
- **When a case is deleted** (its retention passes, or the organization is deleted), its issue
  stays in GitHub. nexovue cannot remove what it wrote there.
- **A deleted note lives on in backups for a while**, until the backups taken before the
  deletion age out on your install's own backup schedule.
- A backup may hold the App key, sealed under the install's connection key.

## What is not checked yet

This connection has been built and tested against a simulated GitHub. Its first run against
real GitHub — the App setup from a private address, real issues and a real webhook — has not
happened yet.

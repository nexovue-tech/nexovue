# Feature: Case management (F22)

## Status

**Approved** by the founder, 2026-11-02, revised the same day after eight persona reviews (`status.md`, *Reviews*). New numbers start at FR48, AC90 and D70, and nothing was renumbered. A further review the same day added AC134 to AC139 and D90 to D92, and narrowed BR5, BR10, BR11, D2, D51 and D79. Stage 3 of [`phase-2-plan.md`](../../product/phase-2-plan.md). No code is
written from it yet. Read with [`research.md`](research.md), which holds the
evidence, and [`design.md`](design.md), which holds how it is built.

Founder decisions in the plan, F6 D1 and ADR-022 stand: a case grows the finding, and on-call stays with PagerDuty.

## Words used here

| Term | Meaning |
|---|---|
| **Finding** | One detected problem (`findings`). It closes itself when the signal recovers |
| **Signal state** | The existing `findings.state`, `open` or `closed`: whether the problem is still measured |
| **Case** | A finding with a non-null `case_state`: someone is working on it, or did. There is no separate record |
| **Active case** | A case with `case_state = investigating` |
| **Root finding** | The finding that holds a case. A later finding on the same service joins it through `root_finding_id` |
| **Joined finding** | A later finding that belongs to another finding's case |
| **Case signal** | Whether the problem is measured now for the whole case: open if the root or any joined finding is open |
| **Activity** | A person's act on a case, or the case entering `investigating` by any route (FR26). Traffic never counts, and an open or closed tracker issue never counts |
| **Untouched case** | A case where nobody has done anything beyond taking it: no note, no link, no reassigning, no reopening |
| **Timeline** | The dated list of what happened to a case, written by the system and read by people |
| **Tracker** | GitHub Issues or Jira |
| **Synced link** | The one tracker issue whose open or closed status stays in step with the case |
| **Reference link** | Any other tracker issue attached to a case. It is shown, and never changes the case |
| **Reconcile** | Compare what each side says now, and fix the difference, rather than trusting a stream of events |
| **Not followed** | nexovue has stopped reading this issue, and says so, so an old "synced" line can never read as live |
| **Webhook** | A tracker calling nexovue's server when something changes |
| **Rate limit** | The most calls a service allows in a period |
| **SSRF** | Tricking our server into calling an address it should not |

## Problem

A finding tells a team something is wrong. It does not hold the work.

- **The finding closes when the graph recovers, not when the cause is known.** At 3 a.m. the alert says resolved and the
  page shows "Recovered", while nobody knows why it happened. Nothing on the record says someone is still on it, and in
  the code a closed finding sinks below all others in a feed cut at 200 rows (`findings/queries.py:39,46-50`).
- **There is one acknowledgement and nowhere to write.** `acknowledge` works once, and only on an open finding
  (`findings/service.py:178`). A second person cannot take over. A person who arrives after recovery has no way in. What
  was tried lives in a chat thread that the morning person may never see.
- **A problem that returns starts from nothing.** A flapping service opens up to five findings an hour
  (`detection/notify.py:399-420`), each a stranger to the last.
- **The developer who fixes it lives somewhere else.** Their work is in GitHub or Jira, and today someone must copy the facts
  across by hand, then remember to come back and say it is done.

## Goal

One click makes a finding someone's job. The record says plainly who took it, what was tried, and that recovery did not end
the work. A developer can get a tracker issue made of fixed words, numbers, a neutralised service name and a link to your console. It cannot mention, link or format anything. A service name is still someone else's words, and your internal address is visible to anyone who can read the issue. Closing that issue finishes the case once the signal has recovered. When the sync fails, the screen says so.

All of it must work with AI off. F22 makes no model call.

## Why this is worth building

**The one-line story:** once someone takes a finding, recovery does not end the work, and the page says so. A person, or the synced issue closing, ends it. A case nobody touches ends itself, with warning: 90 days after the last person's act, or 72 hours after recovery if nobody did anything but take it.

**What that does not cover, stated.** A finding nobody took closes on recovery and stays in the feed as recovered, and anyone can still take it with one click (FR1). There is no automatic case (FD12). A page acknowledged only in PagerDuty leaves no case. The issue half of the story is self-hosted with GitHub only, until Jira and hosted connections are decided.

Future tense throughout: none of this exists yet, and no customer has been asked (research, *Not checked*). Prices and
competitor facts are in the research, dated 2026-11-02, and are not for public quotation without a re-check.

| What nexovue will bring | What it rests on | Honest limit |
|---|---|---|
| Case tracking included, with no per-person charge from nexovue. Paging, if you use PagerDuty, is still billed by PagerDuty | `vision.md:152`; foundation section 10 | Rivals have far more incident features, and some small teams pay nothing today on free tiers (research). It is a reason to stay, not a reason to arrive |
| Nothing to set up to start. A click, no form | R8; this spec | Some teams want the form |
| The evidence is already on the case: stored signals, normal bands, later the explanation | F6's stored values; F3 | Readings expire at 35 days; stored values stay |
| "Recovered, still being investigated" as one plain line, with recovery never touching the tracker (BR3). **An entry requirement, not a differentiator:** a record that stays open after the graph recovers is ordinary in the issue and incident tools checked | FD12; research, *How competitors do it* | Sentry resolves by a person or a commit. SigNoz moves its Jira issue to done when an alert resolves, on by default in its setup screen per its docs, and it can be turned off. Do not lead with this |
| **What the pieces do together:** on your own server, a finding from statistical detection becomes a case in one click, with its stored evidence attached, no per-person charge from nexovue, and AI off | This spec; F6 | **Not yet checked as a combination** against Sentry self-hosted, SigNoz and Elastic Cases (research). State it only as what nexovue does, and do not call it unique until T8.6 |
| A sync that says when it failed, and when it stopped following | Link and connection states below | It must actually be built this way to be claimed. Not checked against rivals, so not a comparison |
| A case ends by itself if nobody touches it | BR13, FR49 | **A developer working only in the tracker has no effect on nexovue's clock.** Someone must act in nexovue every 90 days, or the case ends with warning, leaving the issue open |
| One service has one active case | BR7, FR9 | **Unrelated problems on that service within 7 days share it, and cannot be split.** The joined line names the signal that moved, so the mismatch is visible. The way out is to resolve it, which can leave the issue open (D72) |
| A person's click creates the issue, and status follows | FR35, FR38 | **The first write needs a click.** After that, status and fixed comments, within BR10's limit, follow on their own. A merge saying `Fixes #412` closes the issue, which ends the case once the signal has recovered, even before deploy. If it returns, the new finding names the earlier case, and one click reopens it. Rivals such as Sentry mark the same issue *Regressed*, and SigNoz reopens the same Jira issue within 3 days: nexovue does neither (D8) |

**Do not say:** "replaces PagerDuty, incident.io or Rootly"; "two-way sync" without saying it carries status only; anything naming a rival in public (foundation section 12); "cut your per-seat incident bill" or any saving worked out from a rival's seat price; "your data never leaves your server" (see *What leaves this server*); "no internal text leaves" or "injection-safe issue text"; "Jira support", until Atlassian answers or the founder accepts the risk in writing; "works with your on-premises Jira or GitHub Enterprise"; "GitHub or Jira sync on the hosted service", until it is built; "works from behind a firewall", until T3.0 has proved it (say "designed to work with polling alone"); anything implying every finding or page is tracked automatically; "cases for every alert", or that cases cover alerts in general: cases cover anomaly findings only, and threshold rules and checks do not; "incident tracking" for a case; "keeps your incident history", or anything implying lasting case history on self-hosted, until foundation item 6's retention setting has shipped, and then only as "you choose how long it is kept" (D95); "the only" or "the gap" about free case management (research).

**May say, once shipped and checked by T8.6.** T8.6 re-reads each rival's own page within 7 days before any public mention, and records each line's result in `status.md`. A line reaches public copy only when its row passes.

- "A finding becomes someone's job with one click and no form."
- "Once someone has taken a finding, recovery does not end the work, and the page says so."
- "A person's click creates one GitHub issue, with a preview, and closing it ends the case once the signal has recovered. Status only: comments and assignees do not cross. Public repositories are refused by default." This describes a capability rivals also have, such as Sentry (research), and is not a differentiator. Self-hosted only.
- "Case tracking is included. It adds no per-person charge from nexovue. You keep PagerDuty for paging. Acknowledging there and taking the case here are two separate acts, and a returning problem pages again." Beside it, the retention limit: on a self-hosted install today, a resolved case and its notes are deleted 30 days later by default, until the operator raises retention (foundation item 6, approved 2026-11-02).
- "Track the work on a detected problem: a finding becomes a case in one click, with no form." On the setup page, once, for search: "Looking for incident management? Cases cover detected findings, not alert-rule incidents, and paging stays with PagerDuty." (D58)
- "A problem that returns while a case is active joins it. After a resolve, the new finding names the earlier case."

**Questions a buyer will ask,** with the honest answer:

| Question | Answer |
|---|---|
| What if nobody clicks in time? | It closes on recovery and stays in the feed as recovered. Anyone can still take it with one click. No case starts by itself |
| Do I still acknowledge in PagerDuty? | Yes. Neither tool tells the other. A later finding pages again with a case open |
| Can I run this from Slack? | No. Alert links reach Slack, and a later finding's message names its case. A handover is told by email only (D87) |
| We use Linear or GitLab | Cases work, and nothing syncs. An address can go in a note as plain text |
| We run Jira Data Center or GitHub Enterprise | Not supported at first (D11) |
| How many cases did we have last quarter? | No reports. A token can read a case's times, but rows go when retention passes, which on a self-hosted install is 30 days by default until the operator raises it (foundation item 6, approved) |
| Can I open a case for my site being down? | Only if detection raised a finding. A threshold rule or check failure keeps its own alert record, and a new service learns for 3 days first |
| Do you do on-call? | No, on-call stays with PagerDuty |
| Can I try it on a fresh install? | On live traffic, no case can exist for at least 3 days, because a finding needs history and a real anomaly. A demo uses the seeded development stack (T8.1) |

## Target users

| Person | What they do here | Surfaces |
|---|---|---|
| **On-call engineer** | Says "mine" in one click, writes what they tried, hands over | Finding page, feed |
| **Developer** | Works in GitHub or Jira, and may never open nexovue. An issue notifies nobody until someone is assigned there. Opening the link back needs a nexovue invitation (no seat cost) and, on a private install, access to it | The tracker issue, its link back |
| **Team lead or CTO** | Sees what is being worked and by whom, across the organization's projects | Feed tabs |
| **Admin or owner** | Connects a tracker, sees whether sync works, disconnects | `/settings/connections` |
| **Viewer** | Reads everything a person may read, changes nothing | Finding page, feed |
| **API token** | Reads case state and event kinds, without names or note text | `GET /findings`, `GET /findings/{id}`, timeline |

**Not served:** a customer's support desk (the catalog says a case is never a support ticket); a large organization running a
formal incident process with roles, severities and status pages; anyone looking for on-call schedules.

## Scope

- A case lifecycle on the finding: start, assign, resolve, reopen.
- Notes, and one timeline for a case and its joined findings.
- A later finding on the same service joining an active case.
- A recovered finding with an active case staying visible, and staying stored.
- Forgotten cases ending on their own, so retention still ends.
- The feed showing active cases, and filtering to them, to the person's own and to resolved ones, across the person's projects.
- One GitHub connection and one Jira connection per organization.
- Creating an issue by a person's click. One synced issue per case, in both directions, status only. Any number of reference links.
- A visible health state for each connection and each link.
- A service-to-repository mapping, used to preselect where an issue goes, and later by F5.

## Out of scope

| Left out | Why |
|---|---|
| On-call, schedules, escalation, paging | The founder's decision: PagerDuty |
| Passing PagerDuty's acknowledgement to or from a case | This is how building on-call starts. **The cost, stated:** acknowledging in PagerDuty does not tell nexovue, and the reverse. A later finding pages again even with a case open, and resolving a case does nothing to a live page |
| Automatic issue creation | Traffic decides when findings open, and anyone with a browser key can send traffic. It would let an outsider write into a customer's repository. First thing to revisit (D4) |
| Syncing comments, assignee, title, labels or priority | Each is more conflict handling. Comments would carry attacker-reachable text in and a person's pasted logs out (D2) |
| Threshold or check incidents as cases | FD13 confirmed (D6). A linked one still appears on the page. **The cost, stated:** a rule brought from Datadog or Grafana, a disk or queue alert and a check failure raise incidents, not findings, and findings cover three signals only, with a new service learning for 3 days. Those pages cannot become cases. The incident page says so. **A named later step, not scheduled:** on an incident page, one click writes a note and a tracker issue linked to the incident, with no case columns, as D6 sketches. It is gated on the founder's conversations (`plan.md`, *Not in this plan*) |
| A separate cases table or a separate case list screen | ADR-022 and FD12. The feed with a filter does this |
| Status pages, severity forms, incident roles, call transcription | "Replaces incident.io" must not become a goal |
| Running a case from Slack | Stated plainly, as the customers research asked. Alert links reach Slack today, and a later finding's message names its case (FR47). A handover is told by email only. A fixed line on people-read channels for a handover was considered and left out for now (D87) |
| Post-mortem documents | A note, the timeline and "Copy timeline as text" are enough at first |
| Reporting: case counts, time to resolve, repeat offenders | Not in this feature. The columns exist, and an API token can read a case's times, so a team can compute them while the rows last. Rows go at retention (foundation item 6, approved) |
| A bulk export of resolved cases | Not now (D95). Foundation item 6 gives the operator a retention setting, so a team that wants history can keep it. Step J is built only if the default is still below 395 days once the test bed has measured disk use |
| An example finding a customer can create | A fake row in `findings` would reach F3's and F4's learning and F6's counts. The stated limit is above, and a demo uses the seeded development stack |
| Merging cases by hand | Merge needs undo, and nobody has asked |
| Linear, GitLab, Azure DevOps | The plan names two trackers. The code keeps a seam, not a list. **The honest answer:** on Linear or GitLab, cases work and nothing syncs. An address can be pasted into a note as plain text (D59). No webhook fires on case events today, and "case state changed" is the first candidate if a team needs to build a bridge |
| A company egress proxy | Not supported at first, and setup says so. T3.0 tries it and records the answer (D83) |
| Jira Data Center, GitHub Enterprise Server | Cloud and GitHub.com first (D11) |
| Default values for required Jira fields | A configuration screen nobody has asked for. A project that requires custom fields is not supported at first, and setup says so (D57) |
| Tracker connections on the hosted service | Not built, and needs a shared App and an OAuth app. This is founder decision 3. Case lifecycle ships there |
| New alert kinds or webhook messages about cases | A case is not a second alarm. New emails are only: the assignment, one idle warning, and one notice each time a tracker closes a case's issue (D28). The last two go to the assignee, or to owners and admins if nobody is assigned. Alert messages gain one fixed line, with no new kind (FR47) |
| Any AI | F22 holds no model. F4 owns reading notes, with its own injection suite |

## Packaging and limits

Foundation section 10 says no Phase 2 feature is gated by plan on hosted, apart from AI. The limits below protect the system and are not upsells, with one plain exception: **how long a case is kept follows the plan, as all derived data does.**

| Install | Case lifecycle, notes, timeline | GitHub and Jira |
|---|---|---|
| Self-hosted | Yes | Yes, with the install's own private App and its own Jira token (Jira subject to the founder decision) |
| Hosted, any plan | Yes, when hosting opens | **Not available until step H** (`plan.md`, *Not in this plan*; founder decision 3). The connections screen says "Tracker connections are not available on the hosted service yet.", and `scheduler-connections` does not run there |

| Limit | Value | Basis |
|---|---|---|
| Connections | One GitHub and one Jira per organization. **A company with two GitHub organizations can reach only one** | D17 |
| Note | 4,000 characters; 500 per case | D33, guesses |
| Reference links | 20 per case | A guess |
| Issues being created | 20 at once per connection | A guess |
| How long a case is kept | The project's derived retention after it ends. Self-hosted and hosted Free: **30 days by default**; on self-hosted the operator can raise it (foundation item 6, approved). Hosted Pro, Scale and Enterprise: 395 days | `0005_plans_table.py:34-37` |

**Who must act in GitHub.** Creating and installing the App may need a GitHub organization owner, or someone allowed to manage Apps, who may not be nexovue's admin. This comes from general knowledge, and T3.0 must confirm it. Setup is a few clicks only for that person.

## What leaves this server

Repeated on `/settings/connections` and in `docs/product/setup/trackers/`. **The first write to a tracker needs a person's click and preview. After that, a synced link's status and fixed comments, within BR10's limit, follow on their own, and linked issues are read on a timer.** An install with no route out has cases, and no tracker sync. A company egress proxy is not supported at first (D83). When F4 is on, notes also go to the customer's AI provider, and F4 must add that to this table and to the note box (`design.md`, *What other features must change*).

| Direction | What | Host | Optional? | Can it be erased later? |
|---|---|---|---|---|
| Out | Issue: fixed words, numbers, times, the service name in strict form or the words "a service", a link to your console. The label `nexovue` | `api.github.com`; `<site>.atlassian.net` or `api.atlassian.com` | Only on a person's click | **No.** It stays in the tracker, for every collaborator to read, after retention |
| Out | Open or closed status, fixed comments within BR10's limit | The same | Follows a synced link, with no further click | No |
| Out | Connection setup | `github.com` | Only on setup | Delete the App in GitHub |
| Out, only with F4's chat on | Note text, as knowledge for chat (F4's T7.2) | The AI provider the owner agreed to | Yes: chat is off by default | Deleting a note deletes its chunk; a provider's own copy is out of nexovue's reach |
| In | Status, ids and address | Read from the hosts above | Follows a link | Yes, with the link |
| In | A webhook, as a signal to read sooner | Exposes `POST /api/v1/connections/webhook/{id}` | Yes. Polling works alone | Nothing from it is stored |

**Your console's address** goes in the link back. On a private install that is an internal name, visible to anyone who can read the issue. **Jira exposure is not checked** (D67): nexovue cannot say whether a Jira project is open to wider access, so it warns at connect, and every Jira preview carries one fixed line: "nexovue cannot tell who can read this Jira project. What you send here stays there."

**A sample GitHub issue.** Jira carries the same words as ADF text.

```
Title: nexovue: slower than normal on checkout

nexovue detected this on `checkout`.

- What moved: latency, 4x its normal range
- Since: 2026-11-02 14:02 UTC
- Severity: critical

Evidence, and the case: https://nexovue.acme.internal/findings/8f1c...
This link needs a nexovue account. Ask your admin for an invitation.
```

The endpoint, the finding's title, notes and any explanation are not in the issue (D48, BR9). A likely cause stays out until F3's facts are reviewed for this use (D48).

## User requirements

- **UR1.** "When I land on a finding, one click says this is mine." This records it in nexovue and does not acknowledge the page: PagerDuty is a separate act (BR17). It is about findings. A threshold or check incident cannot become a case (D6).
- **UR2.** "If the graph recovered but I still do not know why, I can say so, and nobody thinks it is over."
- **UR3.** "I can write what I tried, and the morning person reads it in the right place."
- **UR4.** "I can hand the work to a colleague, and they find out."
- **UR5.** "If it comes back while the case is active, I see it on the same page, with what we already know." After a resolve, the new finding names the earlier case (D8).
- **UR6.** "I can see every case being worked, and just mine, across my projects, without searching."
- **UR7.** "I can see what happened, in order, and who did it."
- **UR8.** "I can make a GitHub or Jira issue without typing the facts."
- **UR9.** "Before it goes, I can see exactly what will be written."
- **UR10.** "Closing my issue finishes the case once the signal has recovered. I do not have to visit a second tool." While the signal is still measured, the case stays, and the page says so (D73). A case idle in nexovue for 90 days still ends itself, leaving the issue open.
- **UR11.** "If nexovue cannot reach my tracker, I am told, and told what to do."
- **UR12 (admin).** "I can connect without giving nexovue the keys to every repository, and I can disconnect cleanly."
- **UR13 (buyer).** "It will not put our internal service names in a public GitHub repository by a stray click." Jira's exposure is not checked (D67).

## Business rules

- **BR1. One record.** A case is columns on its finding. No code path may create a separate case record.
- **BR2. Two independent questions.** Signal state says whether the problem is measured. Case state says whether people are
  working. Neither moves the other except where BR6 says.
- **BR3. Recovery alone never ends a case, and never touches a tracker.** It says nothing about whether the work is done. A person's silence afterwards can end an untouched case (FR49), and a tracker's earlier close takes effect once the signal has recovered (D73).
- **BR4. No form.** Starting a case asks nothing. No field on a case is ever required.
- **BR5. A case is never held hostage by a tracker.** Whatever a tracker does or fails to do, a person can always resolve the case in nexovue, and can reopen it unless another active case holds the service. That refusal is BR7's, a 409 naming the other case (AC57), and no tracker can cause it.
- **BR6. "It was never a problem" ends the case.** A false alarm or new normal on the root finding resolves its case. Plain recovery,
  service gone, adoption after 7 days, and closing as unjudgeable do not.
- **BR7. One active case per service.** A project and a service have at most one active case, and a project has at most one active
  project-scope case. A second attempt joins the first.
- **BR8. A person decides.** A tracker issue is created only by a person's click. No traffic, rule or schedule creates one.
- **BR9. No attacker text crosses out unescaped, and no tracker text crosses in.** Text sent to a tracker is a template filled with
  numbers, fixed words and neutralised names, and never `findings.title`, a note, or anything from a model. From a tracker, nexovue
  stores status, ids, a repository's `owner/name`, a Jira project key, a validated required-field key, and an enum close reason, each under a strict pattern: never a title, body, comment or person's name. Status and field names are read live for an admin's setup screen and never stored. F4 treats all of these as free text.
- **BR10. Sync carries status.** Only open or closed crosses, plus fixed template comments, limited per link and per act: one when a person's act closes the issue (resolve, false alarm, new normal), and one idle warning in that link's case's life, which goes when the warning starts, before the case ends (FR26). A person's later resolve can add its own. A tracker's own close, a reopen, `no_activity` and `recovered_untouched` add none.
- **BR11. The tracker wins a disagreement about the issue's status,** and the timeline shows both facts. Status is not the case: a close with the case signal open holds the case instead of ending it (D73), a close changes nothing on a case already resolved or `not_followed` (FR38, AC85), and a reopen cannot reopen a case while another is active on the service (BR7).
- **BR12. Public repositories are refused by default.** The check runs at each creation, because a repository can become public later.
- **BR13. A case must still end.** An active case with no person's act for 90 days resolves itself, so stored names cannot outlive their
  retention for ever. Traffic can never hold a case open. Entering `investigating` by any route resets the clock, so holding a case open takes a write on a synced issue, which is not public by default (D74). This is a guess at the number (D9, D42).
- **BR14. Tenancy.** Every case, note, event, link and connection belongs to one organization. A token scoped to a project sees only that project. A token that reads every project must still name one (FR22).
- **BR15. Issue and Jira calls leave only from `scheduler-connections`.** No issue, Jira or GitHub call runs in the API, and F5's GitHub work runs there too (D53). That process never runs a model.
- **BR16. A note is the organization's working record, written by a person.** A person's notes are in their personal-data export. An author, whatever their role, or an owner or admin can
  delete a note, and the timeline keeps only that a note was deleted. Notes stay after a person leaves, in their own words, and the deletion and removal screens say so. To the person leaving (account deletion, and their own **Leave** on the members screen): "Notes you wrote stay, in your own words, as your organization's record. Delete any you want gone before you leave." To an owner or admin removing someone else, with that person's name as plain text: "Notes {name} wrote stay, in their own words, as your organization's record." (founder, 2026-11-07). A former member asks an admin (D61).
- **BR17. PagerDuty is untouched.** A recovery still resolves the page, and a later finding pages again, whatever the case says. Neither tool tells the other about an acknowledgement.

## Functional requirements

### Starting and working a case

- **FR1.** A case must start from any of: acknowledging, taking or assigning, adding a note, or attaching a tracker issue. **Take this** works on an open or a closed finding,
  because `acknowledge` refuses a closed one (`service.py:178`).
- **FR2.** Starting a case must set `case_state = investigating` and `case_opened_at`. **Take this**, Acknowledge and assigning a person must, if the finding is open and unacknowledged, also
  record the acknowledgement by the person acting. **Take this** must also assign the person. A note or a link starts a case with nobody assigned, records no acknowledgement, and leaves an open finding in the open tier, reading "Open since 14:02. Being investigated, nobody assigned", with **Take this** still primary (D70).
- **FR3.** Acknowledging an open finding with no case must start the case and assign it to the person acting (D20). The console shows one button, **Take this**, in place of Acknowledge; the API route stays (D47).
- **FR4.** Any working member may assign an active case to any working member, or to nobody. A viewer, a removed person, a pending invitee and a person in another organization cannot be assigned. Making an assignee a viewer must clear the
  assignment, with an event, through the same code as FR8. An assignment must carry what the page last showed, and be refused with 409 if the assignee changed meanwhile. When someone else holds the case, the page must offer **Take over from <name>**.
- **FR5.** When someone else assigns a person, that person must get one email holding the fact, the service in the issue's strict form, the severity in a fixed word, and a link, with no finding title or note (D28), at most one per recipient and case in 5 minutes (a guess). A failed
  email must not fail the assignment. The response must say whether an email will be tried, as `email_outcome`: `will_try`, `not_configured`, `rate_limited` or `none`. The screen must say the true thing (*Words after an action*).
- **FR6.** A person must be able to resolve an active case and reopen a resolved one with one action. Resolving while the case signal is open
  must say so in its confirmation, naming a later finding if that is the open one, and is allowed. **Resolving a case with a synced link must always confirm,** naming the issue ("This closes acme/api#412 and adds one comment there"), and must offer **Resolve here, leave the issue open**, which sets no `wanted_state` and posts no comment (D72). **When the issue is already closed (a hold, D73), the confirmation must instead say "acme/api#412 is already closed in GitHub. Nothing more goes there.", with one Resolve button, no `wanted_state` and no comment.** FR7's confirmation follows the same rule. Without a synced link and with the signal closed, one tap stays. **However a case resolves, in that transaction, any joined finding still open must leave it** (`root_finding_id = NULL`, with an event), and becomes a plain finding that can start its own case. Reopening runs the join again (D44).
- **FR7.** A false alarm or new normal on a root finding with an active case must resolve the case with that reason. With a later finding still open it must ask for confirmation first, saying so, and with a synced link it must name the issue it closes. On a joined finding it
  must add a timeline event and leave the case active (D22).
- **FR8.** Removing a member, or a member deleting their account, must clear them as assignee of every active case and add a timeline event.
  The existing removal keeps the user row (`members/service.py:192-220`), so a foreign key's `SET NULL` does not fire.

### Joining

- **FR9.** A new finding on the same project, scope and service as an active case whose last activity is within 7 days (a guess, D45) must join it. It opens exactly as F6 says, and notifies as F6 says. Past that window it opens standalone, and its page names the quiet case.
- **FR10.** Joining uses the same scope and service only. A project finding does not join a service's case, or the reverse (D10).
- **FR11.** Starting a case on a closed finding must bring in the service's open finding if it has no case of its own. If that finding already
  has an active case, the action must join that case instead (BR7).
- **FR12.** A joined finding's page must lead with a link to the case, name the signal that moved on this finding so a mismatch with the case is visible, and show the same case section, bound to the root: assignee line, **Take this**, note box, links and merged timeline. Writes act on the root. Acknowledging a joined finding acknowledges that row, and assigns the root only if the root is active and unassigned (D46).
- **FR13.** A new finding on a service whose latest case is **resolved** must not reopen it. It opens with no case, and its page must say "this service had a case resolved on …" with a link (D8). When that case ended without a person's decision (`tracker_closed`, `no_activity`, `recovered_untouched`), the page must offer **Reopen that case** as a button, not only a link. A closed joined finding whose root is resolved offers only **Reopen the case**, explicitly.

### Notes and timeline

- **FR14.** A note is plain text up to 4,000 characters, shown as plain text with its line breaks: no Markdown, no links made clickable. A case holds at most 500 notes (D33, guesses). A note on a resolved case is allowed, and does not reopen it. The box must have a visible label, *Add a note*, and must say "Visible to everyone in your organization. Do not paste secrets." With F4's chat on, it also says "With chat on, notes can be sent to {provider label}" (F4's T7.2). Enter adds a line; the button **Add note**, or Ctrl or Cmd+Enter, sends it. A failed send must keep the draft. Deleting asks first: "The text is removed. The timeline keeps that a note was deleted."
- **FR15.** The author, whatever their current role, or an owner or admin, may delete a note. Nobody may edit one (D14, D61).
- **FR16.** The timeline must record, each with a time and who or what did it: finding opened, severity raised, `signal_joined` (F6's meaning: a new signal or endpoint on a finding), finding closed and how,
  flapping started, `finding_joined_case`, `finding_left_case`, case started, assigned, unassigned, resolved and how, reopened, acknowledged, false alarm, new normal, note added and
  deleted, link added and removed and promoted, issue created, issue closed or reopened in the tracker, with GitHub's close reason where known (`completed`, `not_planned`, `duplicate`, or `other`), issue gone, issue moved, a tracker write that failed, and `explained` once F3 is present. Linked threshold incidents stay in the page's existing section.
- **FR17.** A case's timeline must merge the root's events with those of its joined findings, newest first, directly under the note box, showing the newest 20 rows at wide widths and 5 on a phone (guesses), with **Show earlier** loading the next 100 and focus moving to the first new row, and a day heading whenever the date changes.
- **FR18.** While a finding is in a flapping spell, its opening, joining and closing must not add events, whether or not the service is muted. The start of the spell is one event (D35). F6's `flapping` column and messages do not change. So a finding may have no events of its own, and a timeline that is empty must say: "No events for this finding. It opened while this service was flapping, so openings and closings are not listed one by one." A case with a joined finding in a spell must carry the fixed line "This service is flapping".
- **FR19.** An event must hold no free text: only kinds, ids, enum values and numbers.
- **FR20.** The fields a person sees and a token does not: assignee, every event's actor, event details, notes, links. A token sees event kinds and times, on `GET /findings`, `GET /findings/{id}` and the timeline alike. Only a reader with a real session and no ticket counts as a person. During a support view, note bodies are hidden (D62).

### Being read

- **FR21.** The feed must order: open and unacknowledged, or a root with an open joined finding, at the worse of the two severities; acknowledged; **closed with an active case**; other closed. Then severity, then recency, as today. Both the server's rank
  (`queries.py:46-50`) and the console's `ordering.ts` must change.
- **FR22.** A feed filter must offer *All*, *Active cases*, *Assigned to me* and *Resolved cases*. The three case filters must include muted findings, return root findings only, and for a person read across the organization, naming the project on each row. A token must name a project it can read to use `case=active` or `case=resolved`: another project gets the same 404 as `get_finding`, and no project is 422. `case=mine` from a token is 422. *Resolved cases* orders by `case_resolved_at`, newest first.
- **FR23.** The page and the feed must state both questions together in one sentence, from the table below, using the case signal. The words must follow how the finding closed: "Recovered, still being investigated by Asha" holds
  only for `recovered`. A service that went quiet reads "Service stopped sending, still being investigated". The feed must name the assignee for a person. A case row has the full statement shape: not truncated, the case severity in words, the project name in a case filter, no sparkline there, and no "Acked".

### Keeping and ending

- **FR24.** A closed root must not be deleted while its case is active. A closed joined finding keeps its own retention clock even while its root is active, so one case cannot pin an unbounded set of rows. The page shows how many joined findings are still kept (D43).
- **FR25.** A closed finding with a case must be deleted once its project's derived retention has passed after the later of `closed_at` and `case_resolved_at`. Findings with no
  case keep their existing rule.
- **FR26.** An active case with no person's act for 90 days must resolve with reason `no_activity`, write a timeline event and close nothing in the tracker. Findings joining and an
  open tracker issue are not activity (D42). **Entering `investigating` by any route, including a tracker's reopen, must set `case_activity_at`,** so a reopen is not undone by the next sweep (D74). From 14 days before, the page and the row in the case filters must warn: "No activity for 76 days. Ends by itself on 16 Nov. Add a note to keep it going, or resolve it." When the warning starts, one email must go to the assignee, or to owners and admins if nobody is assigned, and a synced issue must get one fixed comment, at most once for that link in that case's life: "nexovue will end its case for this issue on <date> if nobody acts in nexovue. The issue stays open." (D75)
- **FR27.** Deleting a root must leave its joined findings as ordinary findings. Notes, events and links go with their finding. A resolved case must show the date it will be deleted, and a person must be able to copy its timeline, with notes, as text.

### Connections

- **FR28.** An owner or admin may connect GitHub by creating a private GitHub App through GitHub's manifest flow, then installing it on chosen repositories. No personal token. Setup's return and failure screens are designed (`design.md`). On the hosted service this is not offered (D56).
- **FR29.** An owner or admin may connect Jira Cloud with a service account's OAuth 2.0 credential (client id and secret), or, only if Atlassian's written answer allows tokens and not that credential, a scoped service-account token, subject to the founder decision below (D98). A classic token is accepted only if T7.0 finds no scoped route, with a warning that it acts with that person's whole reach and stops when they leave. The address must be a site on `atlassian.net` (D26). The test must say whether the token belongs to a service account or a person if T7.0 finds a way to tell, and otherwise that this is not known.
- **FR30.** A "test this connection" action must run in `scheduler-connections` and show its result without leaving the page.
- **FR31.** The settings screen must show for each connection: state, last successful sync, last failure in words with what to do, repositories or projects it reaches, for Jira the token's expiry date when given, and whether failure emails can be sent on this install. While any connection needs attention, owners and admins must see a banner across the console.
- **FR32.** Disconnecting must delete the stored credentials at once, stop all sync including a job already running, keep links as text, free each case's synced slot, and say what remains: the App and its key in the customer's GitHub account, the Jira token on Atlassian's side, the issues, and that a backup may hold a sealed copy. It must name the step: revoke the token, or delete the App (D38).
- **FR33.** Owners and admins must get one email when a connection enters `needs_attention` or has been `failing` for 24 hours (a guess), and one 14 days before a Jira token's stated expiry (D37). Not one per link and not one per retry. The episode ends when the connection is `working` again, or the token is replaced. These two emails are independent. Recipients are verified owners and admins, which needs a new query, since the existing one selects owners only (`alerts/senders/email.py:21`).
- **FR34.** `MANAGE_CONNECTIONS` gates all of this. `OPERATE_CASES` gates working a case and attaching an issue. Both are as foundation section 5 says.

### Issues

- **FR35.** Creating an issue must show first exactly what will be written, then send it on a click. The result must arrive without the person waiting on the page.
- **FR36.** A crash at any point must not produce a second issue for one link. **The accepted exception:** a person editing the link back out of the issue during the retry window. The test tracker proves the state machine, under a stated model of when a search sees a write. Real read-after-create behaviour is checked by T4.8's and T7.6's recorded runs.
- **FR37.** The issue must say: what moved, in fixed words and numbers; since when; the service in strict form; and a link back. It must never carry an endpoint, and a name containing `@` with text on both sides, or a run of 6 or more digits (a guess), must become the fixed words "a service" (D48). It must carry the label `nexovue` on Jira, and on GitHub where the App is permitted. The console must then offer "Open acme/api#412 to assign it", with the sentence "An issue notifies nobody until someone is assigned", and **Copy my notes** (FR51): assignment happens in the tracker.
- **FR38.** A case's status must follow the synced issue, and the issue must follow the case, by these rules:

| Event | Case | Tracker |
|---|---|---|
| Person resolves in nexovue | `resolved` | GitHub: `closed`, reason `completed`. Jira: a transition into the *done* category. One template comment |
| False alarm on the root | `resolved`, reason `false_alarm`. An open joined finding leaves the case | GitHub: `closed`, reason `not_planned`. Jira: as above |
| New normal on the root | `resolved`, reason `new_normal` | As for a person's resolve |
| Person reopens in nexovue | `investigating` | Reopened |
| Idle for 76 days | The warning starts | Status unchanged. One fixed comment, at most (FR26) |
| Idle for 90 days | `resolved`, reason `no_activity` | Status unchanged. No further comment |
| Person resolves, leaving the issue open | `resolved` | Nothing. A later close there changes nothing and adds no event |
| Signal recovers | Nothing | Nothing |
| Issue closed in tracker, case signal closed | `resolved`, reason `tracker_closed`. One email to the assignee, or to owners and admins if nobody is assigned | Nothing |
| Issue closed in tracker, case signal open | **Stays `investigating`,** and the page says "Issue closed in GitHub at 16:10, and the signal is still open", with **Resolve** (D73). One email as above. When the signal later closes, the next sync resolves it as `tracker_closed`, with a second email | Nothing |
| Issue reopened in tracker, case resolved | `investigating`, if no other active case holds the service, setting `case_activity_at` (D74) | Nothing |
| Issue reopened in tracker, during a hold (the case is still `investigating`) | The hold ends, one event, and the page stops saying the issue is closed. `case_activity_at` does not move, because the case never left `investigating` (D92). No email | Nothing |
| Issue deleted (410), or missing from a repository we can still read (404) | Unchanged. Link `gone`. A 404 is read again daily for 7 days (a guess) | Nothing |
| Repository no longer readable | Unchanged. Links `needs_attention`, and they resume when access returns | Nothing |
| Issue moved | Unchanged. New address and repository stored if connected; otherwise `needs_attention`, naming that it moved out of reach | Nothing |

- **FR39.** A tracker change must reach an active case within 5 minutes by polling alone, and a resolved case within 1 hour. 30 days after resolving, a link is **not followed**: the page says so, and neither polling nor a webhook revives it; reopening the case does. Backoff can lengthen each of these, and the screen shows it. A webhook only ever makes these faster, within 60 seconds (a guess). These numbers are starting guesses (D18).
- **FR40.** A pasted issue address must be parsed to a key and accepted only inside a connected repository or Jira project. A Jira key must fully match `^[A-Z][A-Z0-9_]+-[0-9]+$`. nexovue must never fetch the pasted address.
- **FR41.** A reference link must show its status and never change a case. It is read hourly, a guess, and stops by FR39's rule. One issue may be a reference on many cases, and synced on only one. A person may promote one to synced by a second, explicit choice, when no synced link exists. Promotion must warn that a person who can close the issue can resolve the case, and runs as a job (`promoting`) that makes the public check.
- **FR42.** A public GitHub repository must be refused for creation and promotion unless an admin allowed that repository, with a warning naming what becomes public (D12, D31). An admin may withdraw the allowance, and creation stops at the next try. When the hourly refresh sees a repository turn public, its links must show it, and admins must be told once. Issues already there are already exposed, and the words say so.
- **FR43.** An owner or admin must be able to replace a stored credential on a connection, whether a Jira token or a GitHub App private key, without disconnecting. This re-seals in place, runs the test, returns the connection to `working` and keeps every link. A GitHub App uninstalled and reinstalled recovers the same way, with a refresh (D52).
- **FR44.** Each `last_failure` must be written in two forms. Admins get the fix and a link to settings. Others get "Ask an admin to reconnect GitHub. The case still works." Retry is offered only when a retry can help.
- **FR45.** The console must refresh the finding, timeline and connection states every 30 seconds (a guess) on a visible page only. A refresh must never close an open panel, take focus, reorder rows under a panel or clear a draft. One always-present status region, outside the actions, must announce only changes of assignee, case state, signal state and link status. It must never announce age words, or the actor's own act, which moves focus instead. Sync words must change with age (*Link and connection states*).
- **FR46.** At 375 px the finding page must have no sideways scroll, and work must come before evidence. Actions are split into two labelled groups, *The case* and *The signal*. Each case action must be at least 44 px tall (a guess, the common touch guideline) and on its own row, and Resolve is never next to Reassign. An *Evidence* jump link sits under the sentence. The feed and `/settings/connections` must also have no sideways scroll at 375 px, and no case sentence may be truncated. Tables reflow to stacked rows.
- **FR47.** An alert message on a people-read channel, for a finding with `root_finding_id` when the message is composed, must carry one fixed line: "Part of a case started 2026-10-31 14:02 UTC. Open it", with the link built by nexovue. A finding detached before sending carries no line. No new alert kind. PagerDuty and webhooks are unchanged, byte for byte.
- **FR48.** When the project has a PagerDuty channel, the line after **Take this** must read "Yours. PagerDuty is not told: acknowledge the page there too." and the line after a resolve must add "PagerDuty is not told, so a live page stays until the signal recovers." With no PagerDuty channel, neither shows.
- **FR49.** A case must resolve with reason `recovered_untouched` when nobody has done anything beyond taking it (no note, no link, no reassigning, no reopening), and the root and every joined finding have been closed for 72 hours (a guess, D71). From recovery the page must say "Recovered. Ends by itself on 5 Nov unless someone adds something." A person can reopen it in one click.
- **FR50.** Every case time must go through the zoned formatter, show the date when it is not today, and name the zone: "Part of a case started 31 Oct, 14:02 London time". An alert line uses its message's UTC form.
- **FR51.** **Copy my notes** must copy the person's own notes on that case to their clipboard, and nothing else. It sends nothing, so their text crosses to a tracker only if they paste it there under their own name.
- **FR52.** A row in *Active cases* or *Assigned to me* must offer **Resolve** when the case has no synced link. A row with a synced link must not (D47).
- **FR53.** The finding page's linked alerts section, and the incident page, must carry one line: "A case tracks work on a detected finding. A linked alert is a rule's own record, and cannot become a case." (D58)
- **FR54.** The member removal and account deletion screens must carry BR16's sentence for the person reading them: the leaver's, or the removing admin's.

### Link and connection states

| Link status | Meaning | What the person sees |
|---|---|---|
| `creating` | The click was taken; the job has not finished | "Creating the issue. This may take a moment". After 2 minutes: "Not created yet. nexovue cannot reach its sync service. The case still works." |
| `promoting` | A person chose to sync this reference; the job has not finished | "Checking this repository before syncing it" |
| `in_sync` | Last read matched | Issue address, and "synced 3 minutes ago". Past 30 minutes on an active case: "Not synced for 2 hours" |
| `waiting` | A try failed, and another is scheduled | "Could not reach GitHub. Trying again. Last synced …" |
| `needs_attention` | A person must act, for example a missing transition, or an issue moved out of reach | The reason, by reader (FR44), and a retry when one can help |
| `refused` | Creation was refused, for example a public repository | The reason, and no issue exists |
| `gone` | The issue was deleted, or cannot be found in a repository we can read | "Cannot find this issue. It may be deleted or moved" |
| `not_followed` | The case resolved over 30 days ago | "No longer followed since <date>. This issue may have changed. Reopen the case to follow it again" |
| `stopped` | The connection was disconnected | The address as text. No longer holds the synced slot |

| Connection state | Meaning |
|---|---|
| `setting_up` | The App exists, not yet installed, or the first test has not passed |
| `working` | The last call succeeded |
| `failing` | Calls fail for reasons that may pass, with the time it started |
| `needs_attention` | A person must act: a revoked installation, an expired token |
| `disconnected` | Credentials deleted |

### Case states

```
                        start, assign, note, link, acknowledge
       (no case)  ───────────────────────────────────────────►  investigating
                                                                   │    ▲
              resolve, false alarm, new normal, tracker closed,    │    │  reopen,
                 90 days idle, 72 hours untouched after recovery   ▼    │  tracker reopened
                                                                 resolved
```

Signal state moves independently, so all these are valid and must each read correctly:

| Signal | Case | Reads as |
|---|---|---|
| open | none | "Open since 14:02. Nobody has taken this in nexovue" |
| open | investigating, assigned | "Open since 14:02. Asha took this at 14:06" (or "You took this at 14:06"). A fact with its time, because nexovue cannot know she is still on it (D76) |
| open | investigating, nobody assigned | "Open since 14:02. Being investigated, nobody assigned" |
| closed | none | "Recovered at 15:04" |
| closed, nothing joined open | investigating | "Recovered at 15:04, still being investigated by Asha" |
| root closed, joined finding open | investigating | "Came back at 15:50 and is still open (link). Asha took the case at 14:06". If she has not acted since it joined: "… Asha took the case 12 hours ago, and has not acted since", with **Take over from Asha** as the primary button. **Never "Recovered" as the current state** |
| open, issue closed in tracker | investigating | "Issue closed in GitHub at 16:10, and the signal is still open. Asha took this at 14:06" |
| closed, nothing joined open | investigating, nobody assigned | "Recovered at 15:04, still being investigated, nobody assigned" |
| open | resolved | "Still open: the signal has not recovered. <how it ended>" |
| closed | resolved | "Recovered at 15:04. <how it ended>" |

How a case ended, one phrase per `case_resolution`, in `finding-words.ts`:

| Resolution | Words |
|---|---|
| `resolved` | "Asha resolved the case at 16:10" |
| `tracker_closed` | "Closed in GitHub at 16:10, reopen here if that was wrong" |
| `no_activity` | "Ended by itself after 90 days with no activity" |
| `recovered_untouched` | "Ended by itself 3 days after recovery. Nobody had added anything" |
| `false_alarm` | "Ended as a false alarm" |
| `new_normal` | "Ended as the new normal" |

### Words after an action

| Act | Line |
|---|---|
| Take this, with a PagerDuty channel | FR48 |
| Assigned, an email will be tried | "Assigned to Ravi. An email is on its way, so check they saw it if it is urgent." |
| Assigned, email not set up | "Assigned to Ravi. Email is not set up here, so tell them yourself." |
| Assigned, within the 5-minute limit | "Assigned to Ravi. Ravi was emailed about this case a moment ago." |
| Resolved, with a synced link | "Resolved. acme/api#412 will be closed when sync next runs. The link below shows when it has." |
| Resolved, leaving the issue open | "Resolved here. acme/api#412 stays open." |
| Resolved, the issue already closed | "Resolved. acme/api#412 was already closed, so nothing changes there." |

## Non-functional requirements

- **NFR1. AI off changes nothing.** F22 makes no model call. The modules a worker on queues `connections` and `connections-interactive` actually loads must hold no AI module, enforced by a test. That test cannot see a provider reached by plain HTTP, so the role also holds no AI key and has no insight queue.
- **NFR2. Isolation.** A slow or dead tracker must never delay an alert, a probe or detection, nor hold every slot in its own role. A timeline write must never be able to abort detection's transaction.
- **NFR3. Reads stay cheap.** The timeline is one Postgres read returning at most 100 rows. Its cost grows with the number of joined findings, which is accepted and measured (D65). The feed must not grow a telemetry read: the case filters show no sparkline.
- **NFR4. Paced calls.** One worker per connection. On GitHub at least a second between writes, and a `retry-after` is obeyed. These follow GitHub's published limits (research, *Limits*).
- **NFR5. Secrets.** Credentials are sealed under `CONNECTION_SECRET_KEY`, never logged, never returned by any route, and covered by key rotation.
- **NFR6. Memory.** The new container's ceiling is a guess until measured at the end of Step 4, and `sizing.md` must state it, and what F5 adds separately.
- **NFR7. Usable from 375 px up.** Screens use the window, capping only sentences. See FR46 and AC66.
- **NFR8. Accessible.** Timeline is an ordered list with real times. Every state is words, never colour alone. Tab order follows what is seen at every width. After an action, focus moves to the updated case line, and after a delete to the next timeline row or the note box. One always-present status region announces changes (FR45).

## Error cases

| What happens | What the user sees | Can they recover | Told apart from normal? |
|---|---|---|---|
| Timeline read fails | Finding page stands; the timeline section says it could not be loaded, with retry | Yes | Yes: never reads as "nothing happened" |
| Two people act at once | "This case changed since you opened it", and the page reloads | Yes | Yes |
| No connection exists | Case works in full. "Create an issue" tells admins where to connect, and tells members to ask one | Yes | Yes |
| Connection failing, needing attention or setting up; no repository reachable; mapped repository unreachable; preview cannot be read; case already has a synced link | The panel says which, in words, by reader. Create is hidden when a synced link exists. The case works regardless | Yes | Yes |
| Hosted service | "Tracker connections are not available on the hosted service yet." The case works | No | Yes |
| Tracker unreachable | Case works. Link says `waiting`, with last synced | Automatic | Yes |
| `scheduler-connections` stopped | Same as above. "Creating" and "synced" change words with age (*Link states*), and "Resolved" says the issue will be closed when sync next runs | Operator | Yes |
| Issue creation refused for a required Jira field | Found at connect where possible (D57). `needs_attention`, naming the field by its key: "The fix is in Jira: make the field optional, or choose another issue type" | In Jira | Yes |
| No Jira transition to *done* | Case stays resolved. Timeline: "could not move this in Jira". Link says what to do | Yes | Yes |
| Repository became public | `refused`, naming why | Admin may allow | Yes |
| App uninstalled or token revoked | Connection `needs_attention`; one email; each link shows it | Yes | Yes |
| Jira token expired | As above, and warned 14 days ahead if a date was given. Replacing the token resumes every link (FR43) | Yes | Yes |
| Issue closed in tracker with the signal open | Case stays active. The page says so, with **Resolve**. One email (D73) | Yes | Yes |
| Issue reopened while another case is active | Case stays resolved. Timeline says why. Link `needs_attention` | Yes | Yes |
| Reopen while another case is active on the service | 409: "Another case is running on this service", with a link | Yes | Yes |
| Note too long or case full | Inline, beside the field | Yes | Yes |
| Email not configured | Assignment works. "Assigned to Ravi. Email is not set up here, so tell them yourself." Settings says failure emails are off | Not needed | Yes |
| Forged webhook | Refused. Nothing changes and nothing is said | Not needed | Not shown |
| Tracker event over the size cap | Refused. Polling picks it up | Automatic | Not shown |
| A finding's retention passes, with an issue still open | Issue stays in the customer's tracker, with a dead link. Documented; nexovue cannot erase it | No | Documented |
| Setup abandoned on GitHub, `state` wrong or expired, code expired, session ended, no repository chosen, first test failed | The setup screen's row for that case (`design.md`). An abandoned setup can be started again | Yes | Yes |

## Acceptance criteria

**Lifecycle**

- **AC1.** Acknowledging an open finding with no case must set `case_state = investigating`, and assign the person.
- **AC2.** A person must be able to take a recovered, closed finding with one click and no field: it becomes a case assigned to them.
- **AC3.** Taking or assigning an open, unacknowledged finding must also acknowledge it. A note or link must not (AC99).
- **AC4.** A signal recovering must leave `case_state`, `assignee_id` and every link unchanged, and make no tracker call.
- **AC5.** The page must read "Recovered at <time>, still being investigated by <name>" for that state, and the words must follow each `closed_reason`.
- **AC6.** Two people taking one case at once must give one success and one 409. The second must see who has it.
- **AC7.** Resolving and reopening must each add one timeline event naming the person. Resolving while the case signal is open must say so before confirming, whether the root or a later finding is the open one.
- **AC8.** A false alarm or new normal on a root must resolve its case with that reason. On a joined finding it must not.
- **AC9.** A viewer must see every case field and no action. A viewer calling a write route must get 403, with one exception: deleting their own note (AC25).
- **AC10.** Removing a member, a member deleting their account, and making a member a viewer must each clear them from every active case, with a timeline event for each, with a live test per route. A case locked at that moment must be cleared by the next sweep, with no deadlock against the job.
- **AC11.** An assignment by someone else must send one email with no finding title or note, and the service only in strict form. Assigning yourself must send none. A failed send must not fail the assignment. A second assignment to one person on one case within 5 minutes must send none, and still succeed.
- **AC57.** However a case resolves (person, false alarm, new normal, `tracker_closed`, `no_activity`), a joined finding still open must have `root_finding_id` null in that transaction, with an event, and must be able to start its own case. Reopening the root must join it again, unless it holds a case, which gives 409.
- **AC58.** A false alarm on a root with an open joined finding must ask for confirmation naming that finding. With a synced link, it and its success line must name the issue it closes, including on an already closed finding.
- **AC59.** A root with an open joined finding must never show "Recovered" as its current state, on the page or in any feed row, and must rank in the open tier at the worse of the two severities.
- **AC60.** Acknowledging a joined finding: root active and unassigned gives 200, acknowledges that row and assigns the root; root active and assigned gives 200 and leaves the assignee; a closed joined finding gives 409. On a closed joined finding with a resolved root, an explicit reopen must act on the root, and nothing else may reopen it.
- **AC61.** A note written on a joined finding's page must appear on the root's timeline. A note on a resolved case must leave it resolved.
- **AC62.** Assigning a removed person, a pending invitee, a viewer or a person in another organization must be refused. The picker must not offer them.
- **AC63.** No screen may claim nobody is working on a finding: the words are "Nobody has taken this in nexovue".
- **AC64.** The same detection scenario, run with and without an active case, must write identical notification rows, including a PagerDuty resolve on recovery and a later page again.
- **AC65.** Start, assign, note, resolve and reopen must write zero notification rows. A false alarm or new normal must send exactly the *resolved* it sends today, and no more.
- **AC66.** At 375 px the finding page must have no sideways scroll, with this order: sentence; *The case* actions; *The signal* actions, behind a labelled disclosure only if they exceed one row; one "What moved" summary line per signal, with its current and normal values; note box; links; newest 5 timeline rows with "Show earlier"; full evidence. Each case action must be at least 44 px tall and on its own row. A pasted long line in a note must wrap, and a note over 10 lines must collapse. DOM order is tested in the suite; layout is checked by hand at Design QA, since jsdom has no layout (D84).
- **AC67.** Tab order must match visual order at every width. After Take this, Resolve, Reopen or closing a panel, focus must land on the updated case line. After deleting a note, on the next row or the note box. Focus targets and DOM order are tested in the suite; visual order is checked by hand at Design QA (D84).

**Joining**

- **AC12.** A new finding on a service with an active case within the window must set `root_finding_id` and, unless it is in a flapping spell, appear on that case's timeline as `finding_joined_case`. A live test must prove F6's open indexes and notifications are unchanged (AC64 is the baseline).
- **AC13.** A project finding must not join a service's case, and a service finding must not join a project's.
- **AC14.** The database must refuse a second active case on one service, and the action must then join the first.
- **AC15.** A new finding after the case resolved must have no case, and its page must name the earlier case.
- **AC68.** A new finding on a service whose active case has had no person's act for over 7 days must stay standalone, its page must name that case, and taking it must join that case.
- **AC16.** Acting on a joined finding must change the root, and never give the joined finding `case_state`. Its page must show the root's case section.
- **AC17.** An open finding that missed joining because of a race must join within the next sweep (`design.md`, *Joining*). An open finding left on a resolved root by the same race must leave it within the next sweep.

**Timeline and notes**

- **AC18.** A note containing `<script>`, a Markdown link and a URL must render as those exact characters, and nothing in it may be clickable.
- **AC19.** A deleted note's text must be gone from the database in that transaction, and the timeline must keep "a note was deleted", without text.
- **AC20.** A member must be able to delete their own note and not another's. An admin must be able to delete any.
- **AC21.** The timeline must merge root and joined findings, newest first, page correctly past 100 rows, and head each new day.
- **AC22.** An API token reading a timeline must receive kinds and times only: no actor, detail or note text. Reading another project's must be 404. Token responses on `GET /findings` and `GET /findings/{id}` must omit `assignee_id`, `assignee_name` and `links`.
- **AC23.** A flapping service must add one flapping event for its spell, not an open and close for each finding. Five or more muted openings in an hour must do the same, with row count asserted, and F6's `flapping` column and notifications must be unchanged by it.
- **AC24.** A test must fail if an event's `detail` can hold free text: the writer must accept only named, typed fields. A test must prove every kind in `FINDING_EVENT_KINDS` passes the migration's check, so an insert cannot abort detection's transaction.
- **AC25.** A person's notes must appear in their personal-data export, with a truncation flag of their own. An author made a viewer must still be able to delete their own note, with that route an exception in `test_authorization_coverage.py`'s `EXEMPT`.
- **AC69.** During a support view, note bodies must read "Hidden during support view". A table test over every kind of `Reader` must show that only a real session with no ticket gets person fields. F4's ticket is a row in it, added by F4's T7.2.
- **AC70.** For the kinds in FR16, one test per kind must show the event, its actor and its fields.

**Feed and retention**

- **AC26.** With 250 closed findings newer than it, an active case on a closed finding must still be in the feed's 200.
- **AC71.** A person's feed row must name the assignee, read in the feed's one statement. A token's must not. `case=mine` from a token must be 422.
- **AC27.** The server's order and the console's `orderFindings` must agree on a test set covering every combination of signal state, acknowledgement, case state and an open joined finding.
- **AC28.** The three case filters must include muted findings and leave out joined ones. For a person they must read across the organization and name each row's project. The filter must live in the address, so back returns to it and it can be linked.
- **AC29.** A closed finding with an active case must survive past its retention. One resolved must be deleted after retention has passed since `case_resolved_at`. One with no case must follow today's rule.
- **AC30.** A closed joined finding past its own retention must be deleted even while its root's case is active. Deleting a root must leave its joined findings standing, with `root_finding_id` null.
- **AC31.** A case with no person's act for 90 days must resolve with `no_activity`, change no tracker status, post at most one comment, and warn on the page and on the row in the 14 days before, with one email to the assignee, or to owners and admins if nobody is assigned. A person's note must reset the clock. An open tracker issue must not, and a stream of joined findings with no person acting must not: the case must still resolve on schedule.

**Connections and security**

- **AC32.** All issue and Jira calls, including a connection test, must run in `scheduler-connections`. A test on the code's structure must fail if any module of `nexovue_api` uses an HTTP client outside a named list, which holds `billing/razorpay.py` alone.
- **AC33.** A test must read the install Compose file and fail if `scheduler-connections` holds any variable outside an exact allowed list, or merges `control-plane-env`, or if any role but the API and it holds `CONNECTION_SECRET_KEY`.
- **AC34.** A test must fail if a worker on queues `connections` and `connections-interactive`, through the modules it actually loads (`TASK_SETS["connections"]`), imports any AI module, directly or indirectly, and if the role's command names any queue but those two. The running child's `sys.modules` must match the set, and hold no `clickhouse_connect`, `confluent_kafka` or `boto3`. The test's limit is stated: a provider reached by plain HTTP is not visible to it.
- **AC35.** Every sealed column must be in `SEALED_COLUMNS`, a test must fail on any `sealed_*` column missing from it, and a test must prove `keys reseal` carries each to a new key.
- **AC36.** No route, API or scheduler log line, timeline event or job row may contain a credential. A live end-to-end test must search them all after a full connect, create and disconnect, with real workers and captured logs, in Step 8.
- **AC37.** Disconnecting must leave the sealed columns null in that transaction, the next sync must make no call, and a job already running must make no further tracker write after it, tested by disconnecting between two links. One call already on the wire can land, which is stated in the words. Links must become `stopped` and the counts on the screen must be real.
- **AC38.** A request naming an `installation_id` must have no effect on which installation is bound, in the API route and in the scheduler's binding job.
- **AC39.** A typed Jira address outside `*.atlassian.net`, including one using a redirect, `@`, a port or an IP address, must be refused before any call. An allowed name resolving to a private address must be refused by the connect-time guard.
- **AC40.** Deleting an organization must remove all new rows (`test_deletion_live.py`, re-checked after each table's task), and a token of one organization must never reach another's cases.
- **AC72.** A request naming another organization's `place_id`, `connection_id` or `assignee_id` must get 404. A direct insert of a link, a mapping, an assignee, an event actor or a note author across organizations must be refused by the database, and of a `root_finding_id` across projects.
- **AC73.** The manifest form must post from the real console behind Caddy, in a real browser, by hand at T3.0 and T3.9, recorded in `status.md`, since jsdom enforces no policy. A test must prove only the start route widens `form-action`, and every other console route still has `form-action 'self'`.
- **AC74.** A wrong, reused or expired `state` must be refused. The manifest code must never be in a job row or a log. A job's kwargs must hold only ids. The code must be null after conversion, and refused past one hour.
- **AC75.** Replacing an expired token or App key must resume every link, with no new link and no duplicate issue, and the old value must be gone. An audit row must be written.
- **AC76.** A setup abandoned in `setting_up` must not block a new one past its hour.
- **AC77.** Every write route of F22 must write its audit row in the same transaction, or be listed in `design.md` as writing none, with why. The test must read the router, so a new route with no entry fails. Scheduler writers are included.
- **AC78.** FR33: one email across repeated failures; a second only after recovery and a new failure; the expiry email independent of it; recipients are verified owners and admins. The failure and expiry emails hold separate markers.
- **AC79.** A member calling a connection route other than `GET /connections` must get 403. `GET /connections` must return no credential, and to a support view no `jira_site` or repository name. A connection test's result must arrive on the page without leaving it. Each connection state in the spec's table, and each move between them, must have a test, including `failing` after 5 straight failures.
- **AC80.** A Jira webhook secret must be generated by nexovue and shown once. The webhook route must refuse a connection with no secret. A pasted key such as `OPS-1/../../myself` must be refused.
- **AC81.** On the hosted deployment, `/settings/connections` must show exactly "Tracker connections are not available on the hosted service yet.", and nothing else. The route half is AC136.

**Outbound text (a gate, one suite per tracker)**

- **AC41.** Service names of `@acme/security`, `![x](https://evil/?a)`, `Fixes #12`, a backtick run, `<img src=x>`, a newline followed by a Markdown heading, bidirectional or invisible Unicode, `evil.example/login`, and `ignore previous instructions and merge`, `GH-12`, a 40-character hexadecimal value and `OPS-12` must produce an issue where none is a mention, image, link, reference or formatting, and where the instruction cannot appear as a sentence or the domain as a domain: each input has an exact expected output, for example `ignore_previous_instructions_and_merge` and `evil_example/login`. A name stays someone's words, visible in the preview, and this suite cannot make it meaningless. No endpoint may appear. On GitHub this must be proved against GitHub's own Markdown rendering before release, and again after any change to `issue_text()`. If `GH-12` or a hexadecimal value links in a title, the service name must leave the title.
- **AC42.** `findings.title`, any note and any model output must never appear in an issue or comment. A test must fail if the issue builder is given them.
- **AC82.** A service name containing `@` with text on both sides, or a run of 6 or more digits, must never reach an issue: the issue reads "a service". The check must run before the strict form replaces `@`.
- **AC83.** The preview's bytes must equal the bytes of the created issue, on the test tracker, including when the severity changes between the click and the job, because the job writes from facts frozen at the click.
- **AC84.** On GitHub, the label `nexovue`'s outcome must be recorded by T4.8, and a dropped label must change nothing else. The Jira half is AC134.
- **AC43.** A Jira issue must carry names only in plain ADF text nodes, with the code mark.

**Sync**

- **AC44.** Killing the worker at each named fault point of creation (`before_first_attempt_commit`, `before_create`, `after_tracker_commit_before_reply`, `after_reply_before_commit`) must end with exactly one issue, on a test tracker whose search sees a write only after a stated delay. This proves the state machine, not the real services (FR36).
- **AC45.** Each row of the decision table in `design.md`, *The comparison*, must have a test, and running the job twice must change nothing the second time. Each link status, and each move between them, including `waiting`, must have a test.
- **AC46.** With webhooks disabled, closing an issue must resolve an active case within one poll interval plus the job's time, and reopening must reopen a resolved case within 1 hour. With a signed webhook, within 60 seconds (a guess).
- **AC85.** After 30 days a link must read `not_followed`. A tracker change after that, a poll and a webhook must all leave the case unchanged. Reopening the case must follow the link again.
- **AC47.** A webhook with a bad or missing signature must be refused and change nothing. A correctly signed body must never set a case's state from its own contents.
- **AC48.** Replaying a signed delivery 100 times must change no case, must add no job, and must cost at most 2 reads of that issue a minute. At most one `sync_connection` may run per connection at any moment, proved through the lease with two jobs started together. Clicks and sweeps made one after another must add no second unclaimed job. Two inserts at the same instant may leave a second unclaimed job, which D50 accepts, and the lease must stop it making any call.
- **AC49.** A 429 with `retry-after` must pause the connection for that long. GitHub writes must be at least a second apart.
- **AC50.** A test must prove `deliveries`, `light`, `detection` and `insight` never run on the connections worker, and the reverse, and that the role's command names exactly `connections` and `connections-interactive`, with `connections` capped at 2 of 3 slots. A live test with a hanging test tracker must show an alert delivery and a detection job finish inside their normal limits. Sharing Postgres and beat is not proved by this.
- **AC86.** A dead tracker must end a sync run at the first timeout, connection failure or 5xx, and set `paused_until`. A connection test and a sweep must still run while both trackers hang.
- **AC51.** A public repository must be refused at creation even if it was private when the page was opened. It must also be refused if it turned public between the first try and a retry, or if the allowance was withdrawn before a retry. A failed check call must create nothing.
- **AC52.** A 410 must give `gone`. A 404 from a repository still readable must give `gone`, and be read again daily. A 404 from a repository no longer readable must give `needs_attention`, and removing a repository from the installation and restoring it must resume sync. A 301 to a connected repository must store the new address and repository. A 301 outside must give `needs_attention`. Each must leave the case unchanged.
- **AC87.** Resolving must close with the reason for that resolution (`completed`, `not_planned`) and write at most one comment. A kill at `after_close_before_comment_commit` or `after_comment_commit_before_post` must leave zero comments and a correct status, and a retry must not write two.
- **AC53.** A reference link's status changing must never change a case. Its status must still be read and shown. One issue must attach to two cases as a reference. It may be synced on only one.
- **AC88.** Promotion must run the public check in the job, end in `in_sync` or `refused`, and warn first. Only the click route may produce a `creating` link: a test over every writer of that status.
- **AC54.** A pasted address outside a connected repository must be refused with no outbound call.
- **AC55.** A case must be resolvable and reopenable with `scheduler-connections` stopped. The change must reach the tracker once it returns.
- **AC89.** `GitHubTracker` must take its base address only as a constructor argument. A test must fail if any setting or environment variable can set it. The Jira half is AC138.
- **AC56.** The whole gate must run with no AI configured and no `scheduler-insight`, CI must assert that environment, and one smoke flow (T8.3) must run a full case through it.

**Added by the persona reviews**

- **AC90.** A case nobody touched beyond taking must resolve with `recovered_untouched` once the root and every joined finding have been closed for 72 hours, must warn on the page from recovery, and must make no tracker call. A note, a link, a reassign or a reopen must each stop it. A reopen must work.
- **AC91.** A row in *Active cases* or *Assigned to me* must offer Resolve only when the case has no synced link.
- **AC92.** Resolving with a synced link must always confirm, naming the issue and the comment. Choosing to leave the issue open must set no `wanted_state`, post no comment, and a later close there must change nothing and add no event. On a case holding an already closed issue, the confirmation and success line must say it is already closed, and must not offer to close it or leave it open.
- **AC93.** An issue closed in the tracker with the case signal open must leave the case `investigating`, with no joined finding detached and the page saying so. A person may resolve it. Once the signal closes, the next sync must resolve it as `tracker_closed`. A reopen in the tracker before then must end the hold, write its event, leave `case_activity_at` alone, and stop the page saying the issue is closed.
- **AC94.** Every tracker close of a synced issue must queue one email to the assignee, or to verified owners and admins if nobody is assigned, sent by `scheduler-light` and never by `scheduler-connections`. A second one follows when a hold ends by resolving the case, and none when it ends by a reopen. Two emails owed before the sweep runs must both be sent, including a close arriving while an idle warning is owed. GitHub's close reason must appear on the timeline, as an enum.
- **AC95.** A new finding after a case ended as `tracker_closed`, `no_activity` or `recovered_untouched` must offer Reopen that case as a button. After a person's resolve, it must offer a link only.
- **AC96.** A `no_activity` case reopened in the tracker must stay active for the full 90 days, and must get no second comment.
- **AC97.** The idle warning must queue at most one comment on a synced link, written by the sync job, with the date in its fixed words, and none when the case then ends. The sweep must make no tracker call and hold no tracker key.
- **AC98.** With a PagerDuty channel on the project, Take this and resolve must show FR48's lines. With none, they must not.
- **AC99.** A note or link on an open, unacknowledged finding must start a case with no assignee and no acknowledgement, leave it in the open tier, and keep Take this as primary.
- **AC100.** Each assigned state must read as a fact with its time. On a joined finding whose assignee has not acted since it joined, the words must say so and Take over must be primary.
- **AC101.** A live test with a barrier: a person holds the root while the job closes a joined finding and opens a new one in one batch. Neither may block on the other or abort, and F6's rows must be unchanged. A person resolving must then detach the open joined finding.
- **AC102.** No ClickHouse read may run while a case operation holds a finding's lock: a test with a repository that fails if called before commit.
- **AC103.** Resolving by person, by sync and by sweep, and reopening by person and by sync, must write identical case, event and audit rows apart from the actor, using the shared statements.
- **AC104.** In the note box, Enter must add a line, Ctrl or Cmd+Enter and the button must send, a failed send must keep the draft, the 4,000-character and 500-note limits must be refused inline, and a delete must confirm.
- **AC105.** A refresh must happen only on a visible page, must not close a panel, move focus or clear a draft. The status region must exist for a viewer, must announce the four change kinds, and must not announce age words or the actor's own act.
- **AC106.** A finding with no events must show the empty-timeline sentence. A case with a joined finding in a flapping spell must show the fixed line.
- **AC107.** Case times must show a date across a day boundary, and name the zone.
- **AC108.** At 375 px the feed and `/settings/connections` must have no sideways scroll and no truncated case sentence, and tables must stack. Checked by hand at Design QA (D84).
- **AC109.** With a case filter chosen, a failed telemetry summary or detection status must still show the list. The detection headline, RED summary and Muted link must not show. It must use its own query key, with no project.
- **AC110.** A project-scoped token asking `case=active` on another project must get the same 404 as `get_finding`. A token with no project must get 422.
- **AC111.** Starting a case on a closed finding must bring in the service's open finding if it has no case. If that finding holds an active case, the act must join it (FR11).
- **AC112.** The page must show the count of joined findings kept (FR24) and the date a resolved case will be deleted. Copy as text must carry the timeline and notes, and must not be offered in a support view (FR27).
- **AC113.** The settings screen must show each thing FR31 lists, including whether failure emails can be sent. While any connection needs attention, owners and admins must see the banner on every console page, and others must not.
- **AC114.** Disconnect must say what remains and name the step, with real counts (FR32).
- **AC115.** An admin may allow and withdraw a public repository, each audited. When the refresh sees a repository turn public, its links must show it, and admins must be told once (FR42).
- **AC116.** Each `last_failure` must have its two forms by reader. Retry must be offered only where it can help (FR44).
- **AC117.** The `PATCH /case` response must carry each `email_outcome`, and the screen must show its line.
- **AC118.** With `NEXOVUE_CONNECTIONS=no`, Compose must start no such role, the screen must say so, routes that start a setup must be refused, beat must add no sweep job, and no alert may fire. The API and the role must agree on the setting.
- **AC119.** Sync words must change at 2 minutes and at 30 minutes, as *Link and connection states* says, on a fixed clock in a test.
- **AC120.** FR47's rendered line must appear on each people-read channel. PagerDuty and webhook payloads must be byte-equal with and without a case. A finding detached between writing and sending must carry no line.
- **AC121.** The finding page and the incident page must carry FR53's line.
- **AC122.** The removal and deletion screens must carry BR16's sentence.
- **AC123.** Monitoring tests: no alert with the role absent and zero connections; `NexovueConnectionsMissing` with the role absent and a connection present; none with the role present.
- **AC124.** Each row of `design.md`'s GitHub setup table must have a test showing its words and its one action. The Jira half is AC137.
- **AC125.** A Jira project must use a transition only after an admin confirmed it. A fake offering "Declined" first must not write it. Required fields must be found at connect by reading create metadata, with no test issue written. The connection test must report whether the token's owner is a service account or a person if T7.0 found a way, and otherwise that this is not known.
- **AC126.** Every Jira preview must carry the fixed exposure line.
- **AC127.** Copy my notes must copy only the person's own notes, and must make no request that carries them.
- **AC128.** The filter must round-trip through the address and a finding's back link, which names it. Each case filter's zero result must show its own sentence and Show all, and never the RED summary.
- **AC129.** Every new scheduler statement must pass `test_scheduler_postgres_tenancy_structure.py`. Only `sweep_connections`, `cases_sweep`'s first reads and beat's `connections_active` read may be named exceptions. Every connection job must take `org_id` in its kwargs and bind it on every read, so a job naming a connection of another organization finds no row and stops.
- **AC130.** One written script must be run against a real GitHub test account: create then immediately list, close with each reason, a `Fixes #n` merge, reopen, the label's result, transfer, delete, remove a repository, a real webhook delivery, and hostile names rendered. Sanitized responses must be saved as fixtures the fake replays, and the date and account recorded in `status.md`.
- **AC131.** Before any public mention, each May-say and Do-not-say line must have a recorded result in `status.md` against the built product and each rival's own page, read within the previous 7 days. No public Jira wording, including setup pages, changelog and catalog, may ship unless Step 7 went ahead.
- **AC132.** Reassign must open a panel with a Nobody option. Choosing alone must not act; only Assign does.
- **AC133.** Each row of `design.md`'s finding-page state table must have a test.

**Added by splitting, or by the review of the plan's owners**

- **AC134.** If Step 7 goes ahead, a Jira issue must carry the label `nexovue`, recorded by T7.6.
- **AC135.** If Step 7 goes ahead, AC130's script must be run against a real Jira test site, with its responses saved as fixtures and the date and site recorded in `status.md`.
- **AC136.** On the hosted deployment, no connection route may start a setup.
- **AC137.** If Step 7 goes ahead, each row of `design.md`'s Jira setup table must have a test showing its words and its one action.
- **AC138.** If Step 7 goes ahead, `JiraTracker` must take its base address only as a constructor argument, under AC89's test.
- **AC139.** The API's lookup of a connection for a webhook must be the one connection statement named in `test_postgres_tenancy_structure.py`'s `EXEMPT`, with its reason: the caller has proved nothing yet, and the organization is then read from the connection whose secret matched.

## Dependencies and constraints

- **F6 merges first.** Findings and their outbox exist only on this unmerged branch (foundation section 9).
- **F3 comes first by the plan's order, and F22 does not need it.** F3 cannot write `explained` to a table that does not exist when it merges, so **F22 owns that event**: it is in the frozen vocabulary from the start, and T2.9 adds the writer to F3's evidence step, which runs in `scheduler-light` and needs no new grant. If F3 is not present, nothing breaks.
- **Gives others:** `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, in `nexovue_platform/cases.py`, which the API and the scheduler each execute (the shared package holds no database driver), `case_state`, `root_finding_id`, `connections` (whose `kind` allows F5's `github_fix`, ignored by F22's sweeps; F22 keeps no client secret, and F5 adds its own column), `service_repositories`, App sign-in and token minting in the scheduler's `connections/github.py`, queues `connections` and `connections-interactive`. F5's GitHub work runs in `scheduler-connections` (D53). F4 reads notes and resolved cases.
- **Amends the catalog.** "Auto-created cases" (`feature-catalog.md:337`) is replaced by ADR-022's reading: every finding is ready to become one. The catalog's three states become two on top of F6's signal state. The catalog's note "A case here is an *incident*" (`:339`) contradicts the foundation, and T8.2 corrects it (D58).
- **Amends `vision.md:140`'s** "Case created automatically" in the same way, and `vision.md:244`'s "Our value = auto-creating the case" is recorded as superseded. The founder's FD12 stands behind this.
- **Differs from the foundation** in the ways listed in `design.md`, *Where this differs from the foundation*. What other features must change is in *What other features must change* there.
- **Atlassian's wording on tokens** is a founder decision, below. Only the Jira step waits on it.

## Decisions taken

*Recommended by the authors, founder may overrule.* FD11, FD12, FD13 and FD23 are the foundation's and carried. D1 to D19 keep their research numbers, so the evidence is easy to find.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | How to keep two systems in step | One-way / webhooks as truth / polling / reconcile plus doorbell / outbox | **Reconcile, with webhooks as a doorbell.** Order and duplicates stop mattering, and an unreachable install is designed to work with polling alone. **Not yet proved from a private address** (T3.0). It costs a poll per open link |
| D2 | What crosses | Status / status and assignee / everything | **Status, plus fixed template comments within BR10's limit: one per person's act that closes the issue, and one idle warning in the case's life.** No attacker text in. People may ask for assignee. Sentry's own page requires a 1:1 user mapping for assignee sync, which is the cost avoided here |
| D3 | Disagreement | Nexovue wins / tracker wins / latest time | **Tracker wins, both facts on the timeline.** Clocks across two systems are unreliable. A guess about what users expect |
| D4 | Automatic issue creation | None / opt-in per rule / always | **None at first.** Outsider-driven spam and a banned App. Costs appeal: rivals offer it, including SigNoz, on Cloud and self-hosted (edition not confirmed), whose Jira channel creates an issue when an alert fires. First thing to revisit, with a daily cap and critical severity only |
| D5 | Issues per case | One / one synced and others reference / many synced | **One synced, any number reference.** "Resolved when all are closed" is a rule nobody asked for |
| D6 | Threshold incidents as cases | Yes / link only | **Link only, FD13 confirmed.** Giving incidents case columns repeats the merge ADR-022 rejected. Cost: an incident has no notes or tracker link. **First revisit candidate, named as step I in `plan.md`:** a click on an incident opening a linked note and issue. The foundation lets F22 overrule FD13, and this does not, for now: most of a migrating team's first-week pages will be incidents, so ask in the founder's conversations, and say it on the migration and setup pages |
| D7 | Do false alarm and new normal end a case | Leave / resolve | **Resolve, with a reason.** "It was never a problem" with an open case is a contradiction |
| D8 | New finding after a resolved case | Reopen / new case / new finding, named as a recurrence | **New finding, no case, and the page names the earlier case.** Reopening automatically undoes a person's decision on a signal alone. **Rivals differ, and the cost is stated:** Sentry marks the same issue *Regressed*, and SigNoz reopens the same Jira issue within 3 days. Here the developer is not told through the issue after a resolve. A case that ended with no person's decision gets a Reopen button (FR13) |
| D9 | A forgotten case | Keep for ever / auto-resolve / warn only | **Auto-resolve after 90 days with no person's act.** Otherwise names outlive retention, on any plan. 90 is a guess. A person can still extend a case by acting: that is a deliberate choice, and a stated risk |
| D10 | Which findings join | Same scope and service / also project findings | **Same scope and service.** Linking across is correlation, which is F3's |
| D11 | Editions | Cloud / Cloud and Data Center | **Jira Cloud and GitHub.com first.** Reconsider if a real buyer needs Data Center, despite its end in 2029 |
| D12 | Public repository | Allow / warn twice / refuse | **Refuse by default; an admin may allow it, with a warning naming what becomes public.** The leak cannot be undone. Changed by D31 on where the choice sits |
| D13 | Who sees notes and timeline detail | Tokens too / persons only | **Persons, including viewers.** Otherwise notes reach any token holder and the MCP server before F4's labels exist |
| D14 | Note editing | None / delete / full edit | **Author or admin may delete; no editing.** People paste secrets, and those must be removable |
| D15 | Who may assign | Self only / anyone | **Any working member, to any working member.** Small teams hand work over by hand |
| D16 | Linking an existing issue | Allowed / create only | **Allowed as a reference, promoted by a second choice.** Teams often have the issue already |
| D17 | Connection scope | Per organization / per project | **Per organization,** with the mapping per project and service |
| D18 | Poll interval | Fixed guess / measured / same rate for 24 hours after resolving | **5 minutes while active; once an hour for 30 days after resolving; then not followed. A guess.** The screen says which applies. A faster resolved window was rejected: a person can reopen in one click, and the cost is real at 12 reads an hour |
| D19 | Container | `scheduler-deliveries` / new role | **New role, FD23.** A slow GitHub must not hold back an alert |
| D20 | Does acknowledging make it yours | Case only / case and assignee | **Both.** The research found the need is "one click says mine". An existing assignee is kept |
| D21 | Two active cases on one service | Allow / prevent in code / prevent in the database | **Database, by a partial unique index.** Mirrors F6's own open indexes. Two cases for one problem is what `root_finding_id` exists to avoid |
| D22 | False alarm on a joined finding | Ends the case / does not | **Does not.** It speaks of that finding, not the earlier problem. A person can resolve in one click |
| D23 | Naming who closed an issue | Tracker's name / the tracker only | **"Closed in GitHub", with a link.** A person's display name is outside text. The issue itself shows who |
| D24 | Webhook route | Foundation's `/connections/github/webhook` plus one for Jira / one route with the connection's id | **`POST /connections/webhook/{connection_id}`.** Each App has its own secret, so the row must be found first, and the existing rate-limit rule works by path prefix. Same shape as `/deploys/webhook/{project_id}` |
| D25 | Binding a GitHub installation | Trust the redirect's id / user token check / ignore it | **Ignore it.** GitHub warns that the id can be spoofed. The App is private to the customer's account, so the job asks GitHub for that App's own installations. The hosted service, if it comes, needs GitHub's user token check, since one App is shared |
| D26 | Which Jira addresses | Any / private by operator setting / `*.atlassian.net` only | **`*.atlassian.net` and `api.atlassian.com` only,** with D11. Closes token-sent-to-the-wrong-host at the form, with the guard behind it. `CONNECTION_PRIVATE_ENDPOINTS` waits for Data Center and Enterprise Server |
| D27 | Stopping a duplicate after a crash | Label per link / hidden marker / the link back in the body | **The link back,** since a finding has at most one synced issue. No labels pile up, and nothing hidden is written into a customer's tracker. The accepted exception is in FR36 |
| D28 | Tell a person they were assigned | Nothing / email / new alert kind | **One email, with a link and no finding text.** A handover nobody hears of fails. A new alert kind would be a second alarm. A name in mail could look like a link, so only the issue's strict form is sent, which has no `.` and cannot read as a domain. Changed by D87. The same rule covers the idle warning and the tracker-closed notice |
| D29 | Webhook body cap | 64 KiB / 1 MiB / 25 MB | **1 MiB, a guess.** The edge allows 65 MiB (`Caddyfile:119-121`). A refused event is harmless, since polling is the base |
| D30 | What `scheduler-connections` holds | Shared anchor / narrow settings | **Narrow.** It holds repository access and calls outside. It has no need of check secrets, mail or object store keys. Costs a change to a settings check |
| D31 | Where public is allowed | Per mapping / per repository | **Per repository.** What is at risk is the repository, and a project with no mapping can still choose one. A change from the research's D12 |
| D32 | Showing the text first | Send straight away / preview | **Preview.** It answers the buyer's first question, with a screen that needs no network call |
| D33 | Note limits | None / 4,000 characters, 500 per case | **Those, guesses.** A note is not a document, and F6 caps a reason at 1,000 |
| D34 | Tenant keys on new tables | Plain keys / keys including `org_id` | **Including `org_id`.** The database then refuses a link across tenants. Stronger than `finding_incidents`, and cheap before any data exists |
| D35 | Flapping on the timeline | Every event / one for the spell | **One for the spell.** Otherwise one bad service writes thousands of rows nobody reads |
| D36 | Where cases are listed | New screen / filter on the feed | **Filter,** per the foundation. Adds the feed's first filter, which its own comment says it avoided, because there is now more than a toggle to choose from |
| D37 | Telling admins sync failed | Screen only / one email per episode / every failure | **One email per episode, and 14 days before a stated token expiry.** Jira tokens last at most 365 days, so silence means one quiet failure a year |
| D38 | Disconnecting | Reversible / final | **Final.** Secrets are deleted at once. Links remain as text. Connecting again starts fresh |
| D39 | Names in an issue | Raw / strict characters | **Letters, digits and `_ / -` only, cut to 64, in title and body alike.** Anything else becomes `_`, including `.` and spaces, so a name cannot read as a domain or a sentence. Changed by D48 |
| D40 | What a reference link stores | Title and status / status only | **Status and address.** A title is outside text, and storing it would break BR9 for little |
| D41 | JWT signing for GitHub | Hand-written / PyJWT | **PyJWT, in `apps/scheduler`.** F5 moved its GitHub work to the scheduler (`ai-solution/design.md:276`), so nothing in the API signs. A small, standard library. Check the current version, and its crypto extra, when building |
| D42 | What counts as activity | Any person's act, or a finding joining / a person's act only / a ceiling of 180 days with findings counting | **A person's act only.** Otherwise anyone with a browser key can hold a case, and its names, open for ever, which is what BR13 exists to stop. It also removes a write to the root from detection's path. **That alone does not remove the deadlock risk:** a new joined finding's foreign key check still needs a share of the root, and D78 is what answers it. A ceiling keeps a rule nobody needs once traffic cannot extend a case |
| D43 | A closed joined finding on an active case | Keep while the root is active / own retention clock | **Own clock.** `retention.py:6-9` says a finding must not outlive the telemetry it names. The root and its notes stay. The page shows the count kept |
| D44 | An open joined finding when its case resolves | Leave it joined / act on the resolved root / detach it | **Detach, in that transaction, with an event.** The other two leave no way forward, or quietly reopen a case against D8. F6 allows one open finding per service, so no newer one could take over |
| D45 | Which active case a new finding joins | Any / only one a person touched within 7 days, plus a "Not part of this case" action / the window alone | **The window alone, 7 days, a guess.** A forgotten case must not swallow a new failure. The button was rejected: it needs its own state so the sweep will not rejoin it, the service still cannot start a second case (BR7), and resolving the old case does the same honestly in one click, **now without closing the developer's issue** (D72) |
| D46 | A joined finding's page | A link only / the same case section, bound to the root | **The same section.** The person paged lands here, and UR5 says "on the same page". Its signal actions stay in their own group |
| D47 | Acknowledge and Take this | Two buttons / one | **One, Take this,** because FR3 makes them the same act. It also works on a closed finding, so "Start a case" goes. **Take over from Asha** when someone else holds it. A departure from the console-redesign wireframe's row actions. A row may resolve only a case with no synced link, since a one-click resolve in a list must not close an issue by accident (FR52) |
| D48 | What the issue names | Service and endpoint in code spans / service only, strict / a fixed word only | **Service only, strict; a fixed word when it looks like a person's data.** An endpoint can be a raw span name (`traceassembly/stages.go:24-29`), masking does not remove email addresses or customer ids (`mask/rules.go`), and a copy in a tracker cannot be erased. **Cost:** the developer follows the link for the endpoint. `likely_cause` also stays out until reviewed with F3's facts. A person can comment on the issue in their own name |
| D49 | The poll schedule | Unstated / a function of case state | **A function of case state,** set in one place, and a `not_followed` status. Without the status, "synced 45 days ago" looks like a dead worker |
| D50 | How a webhook or click adds work | One job per event / partial unique index on `jobs` / no job from a webhook, and a conditional insert elsewhere | **Webhook sets `next_sync_at` only; sweeps and clicks use a conditional insert.** A unique index was rejected because a job whose lease lapsed returns to unclaimed, and could then fail on the index. The rare race costs nothing, since the lease stops a second run. A webhook does not wake workers: `jobs.py` keeps that for a person waiting |
| D51 | A dead tracker's hold on the role | Concurrency only / early stop plus a separate queue | **End a run at the first failure, cap sync below the slots, and give sync its own queue.** Slots are 3. `sync_connection` runs on `connections`, capped at 2 (`QUEUE_CAPS`), so one slot always stays for `connections-interactive`. Guesses. See D79 |
| D52 | Replacing a credential | Disconnect and reconnect / a route that re-seals in place | **A route.** Jira tokens last at most 365 days, so renewal is certain. Disconnect is final (D38), and making it the path pushes people to keep leaked credentials |
| D53 | Who calls GitHub, between F22 and F5 | All in the scheduler / F5 moves its pull request there / one named exception | **All in the scheduler.** F5's design has already chosen this: "The API holds no GitHub token and calls no GitHub host" (`ai-solution/design.md:276`). So the named exception this spec once carried is gone. App sign-in lives in the scheduler. F5 has its own App, of kind `github_fix`. A `pull_request` event is a doorbell that causes a re-read, so the ADR stays true |
| D54 | The console's form rule and GitHub's form | Widen `form-action` for every page / one route, full page load / post from a new window | **One route, loaded as a full page.** A browser applies a page's CSP to the document, so a header on the settings route would not apply after in-app navigation. A dedicated start route keeps every other page at `'self'` |
| D55 | A tracker closes the issue with the signal open | Keep investigating / a per-connection setting / tracker wins and tell someone | **Replaced by D73.** The authors first chose "tracker wins and tell someone". A setting stays rejected: two behaviours to test, for a question nobody has been asked. A team that wants no close-through attaches the issue as a reference |
| D56 | The hosted service | Same as self-hosted / shared App built now / lifecycle only | **Moved to founder decision 3,** because it is what is sold to whom. Until answered, the design assumes lifecycle only: no role runs there, and the screen says so |
| D57 | Required Jira fields | Default values per project / unsupported at first, found at setup | **Unsupported at first, found at connect by reading create metadata, with no test issue written.** Writing a test issue into a customer's project during setup is the wrong habit |
| D58 | The public name | "Case" / "Incident" / "Case", described once in incident terms | **"Case" in the product,** since the foundation reserves "incident" for F21's threshold record. **The line "incident tracking without the incident process" is withdrawn:** it promises what the feature refuses, since F21's incidents cannot become cases, and a sales qualifier would not survive being quoted alone. Public term: "a case is a detected problem someone is working on", never a support ticket. The market uses the word differently: Elastic has a product named Cases, and Datadog renamed Case Management to Work Management (research), so the catalog's heading "Case Management" carries a meaning it must deny. The setup page says once what cases cover, for search. The page explains case versus linked alert in one line (FR53) |
| D59 | A tracker that is not connected | A free label and URL / nothing beyond a note | **A note, as plain text.** A free address is a clickable link a member can plant for a colleague, and AC18 exists to stop that. Cost: no clickable link to a Linear ticket. Making hosts clickable by an admin's list was rejected for now: AC18's guarantee is simple, and that list is a configuration screen nobody has asked for. Ask "Which tracker do you use?" and "Would you build on a case-state webhook?" in the founder's conversations, and let that decide what comes next |
| D60 | Case filters across projects | One project / across the organization | **Across the organization, for a person.** A project is an environment here, so a person assigned in production must not see emptiness from staging. They read no telemetry, so no fan-out |
| D61 | Whose note is it | The person's / the organization's work record | **The organization's, and said so at deletion.** The author can delete regardless of role. BR16's first wording promised more than the design gives |
| D62 | Support view and notes | Show / hide | **Hide bodies,** since D14 says people paste secrets and the foundation has F4 refuse conversations for the same reason |
| D63 | ETags on polling | Keep / drop / reuse one token | **Drop.** GitHub Docs staff agreed in [issue 34689](https://github.com/github/docs/issues/34689) that a new token gets a new ETag, and each run mints one. Holding a token across runs would break "never stored". Every read costs a point, which the arithmetic now shows, and it still fits |
| D64 | A line typed by the person at creation | Allowed, neutralised / refused | **Refused.** AC42's guarantee is that the issue's input has no free-text field. The issue is one click away, and a person can comment there in their own name |
| D65 | Timeline read cost | Store a case root on each event / accept and measure | **Accept and measure in T8.4.** Flapping writes one event per spell, and closed joined findings now age out, so few rows match. A stored root would have to be rewritten on every join, leave and root deletion. If the read is over 200 ms at the worst shape (a guess), store it |
| D66 | Which Jira status | First match / admin confirms at connect | **Admin confirms, with a default.** Prefer issue type Bug, then Task. For each project, show the *done* and reopen statuses a resolve and reopen will use. "Declined" on a developer's issue is a visibly wrong write. All second-hand until T7.0 |
| D67 | Whether a Jira project is public | Define a check / say it is not checked | **Say so, and warn at connect.** No Jira mapping for `is_public` was verified. UR13 and sales wording are narrowed to GitHub |
| D68 | An install with no tracker | Always run the role / `NEXOVUE_CONNECTIONS=no` | **A setting, as `NEXOVUE_MONITORING` is (`deploy/nexovue:109`).** The role is omitted, and the screen says connections are off. The monitoring rule fires only when a connection exists |
| D69 | Connection health on alert channels | Yes / a console banner and email | **Banner and email.** Alert channels stay for telemetry (D28). A banner also covers an install with no email |
| D70 | Does a note or link acknowledge | Yes / no | **No.** Otherwise a colleague writing "seeing this too" moves an open finding below all unacknowledged ones, removing urgency from a problem nobody owns. Only taking, acknowledging or assigning does. The foundation requires noting to start a case, not to acknowledge (`phase-2-foundation.md:379`) |
| D71 | An acknowledge-and-go-back-to-bed case | Leave at 90 days / a short idle end / a bulk resolve | **A short idle end, 72 hours after recovery, a guess, only when nobody did anything but take it. Plus Resolve on a row with no synced link (FR52).** Take this replaces Acknowledge, so the normal on-call act now makes a case. Without this, the feed fills with lines that are false, each capturing new findings on its service. Silence ends it, not recovery, so BR3 stands with one clause. A case with a note or link keeps 90 days. A multi-select was rejected: nobody has asked, and the end above clears the pile. **This is the likeliest guess to get wrong:** the founder's conversations should test 72 hours |
| D72 | Getting out of a case without touching the developer's issue | Only resolve-and-close / "Not part of this case" / "Resolve here, leave the issue open" | **Resolve here, leave the issue open.** D45's "resolve does the same honestly" was untrue while resolving closed the issue. Comparison row 6 already handles a case resolved with no wanted state, so it costs one choice, not new state. "Not part of this case" stays rejected for D45's reasons |
| D73 | A tracker closes the issue with the signal open | Tracker wins at once (first D55) / hold until the signal closes | **Hold.** The case stays `investigating`, the page says so with Resolve, and the next sync after the signal closes ends it as `tracker_closed`. `Fixes #412` merges before deploy, stale bots, and closes as duplicate (one customer persona's experience, not measured) would otherwise end a case while the problem is still measured, and detach any later finding. D3 still holds on what the status is, and both facts are shown. UR10 holds in the normal case. An email goes at each close, on every path, because a non-member's close can start a deletion clock. **Cost:** a case can sit with its issue closed until someone resolves it, or 90 days |
| D74 | Does a tracker's reopen count as activity | No / entering `investigating` by any route does | **Entering `investigating` does.** Otherwise a `no_activity` case reopened in the tracker is undone by the next sweep while the issue stays open. Traffic still cannot hold a case: it takes a write on a synced issue. **Accepted risk:** someone with write access can extend a case by toggling it |
| D75 | When the idle comment goes | At the end / at the start of the warning | **At the start, one only,** when a developer can still act, and its words are true: FR39 keeps reading for 30 days after the end, so "stopped following" was false. If the clock resets and a second warning starts, the comment is not repeated, and the page and email carry it |
| D76 | "Asha is on it" | Keep / state the fact with its time | **The fact: "Asha took this at 14:06".** nexovue cannot know she is still on it, and AC63 already refuses that claim for "nobody". "Still being investigated by Asha" stays on a recovered case, since the foundation fixes it (section 7) |
| D77 | Where shared case transitions live | API with a copy in the scheduler / `nexovue_platform`, as SQL constants | **`nexovue_platform/cases.py`.** The scheduler cannot import the API, and `RESOLVE_FINDING_SQL` is the existing pattern. Written twice, it would drift |
| D78 | How a person locks a finding | `FOR UPDATE` / `FOR NO KEY UPDATE` | **`FOR NO KEY UPDATE`,** since no key column changes. `FOR UPDATE` blocks detection's foreign key check, and can deadlock with a batch. Read from the code and Postgres's rules; AC101 is the proof, and nothing has run yet |
| D79 | The two queues | `connections` for people's jobs and `connections-sync` for sync / `connections` capped for sync and `connections-interactive` for people's jobs | **`connections`, capped at 2 of 3, holds sync. `connections-interactive`, uncapped, holds connection tests, conversion, binding, `sweep_connections` and F5's person-started jobs.** This is the layout F5 already builds on (`ai-solution/spec.md:663`, D40; `ai-solution/design.md:541-553`), and the foundation names `connections` (`phase-2-foundation.md:463`). An earlier version of this decision said F5 already used `connections` for its jobs, which was false. `sweep_connections` makes no call out, and sits on the uncapped queue so a hung sync cannot hold it back (AC86) |
| D80 | How the narrow role is selected | Conditional on queues / a role setting | **`SCHEDULER_ROLE: connections`, with `TASK_SETS["connections"]` and its own preload.** `Settings` never sees `--queues` (`__main__.py:59,74`). F3's switch has two values, and this adds a third. It also keeps ClickHouse and object store code out of this role. T3.2 builds it if F3 has not |
| D81 | Storing an issue's address | A `url` column with a check / build at read time | **Build at read time.** A check cannot compare with another row, and any `https://github.com/...` path would pass it |
| D82 | What the job writes from | Current facts / facts frozen at the click | **Frozen,** holding only numbers, enums and the strict name. Otherwise the preview's promise fails if the severity changes while sync is stopped |
| D83 | A company egress proxy | Add a setting / say unsupported | **Unsupported at first, and said so.** T3.0 tries it. If a real install needs it, a proxy variable joins AC33's list and the guard's tests. Whether the connect-time guard allows a proxy was not checked |
| D84 | Proving layout and a browser's policy | Add a browser test job now / a written hand list | **A written list at Design QA,** with results saved in `status.md`. jsdom has no layout and enforces no policy, and the web app has no browser tests today. A browser job is worth having and is a piece of work of its own. **Cost:** these criteria can regress unnoticed between checks |
| D85 | The feed's filter control | `Tabs` / inline `SelectField` | **`SelectField`,** the console's shape for a filter above a list. Four tabs do not fit at 375 px, by rough arithmetic |
| D86 | Layout of the finding page | Evidence left, work right / work left, evidence right | **Work first and left.** One DOM order can then match visual order at every width, which NFR8 requires. Overrides F5's table, which is handed over |
| D87 | Telling a colleague in Slack | A fixed line on people-read channels / email with the strict name | **Email now, with the strict name.** A channel line is a new message kind on a frozen vocabulary, with a per-channel option, and the spec holds that a case is not a second alarm. **Cost, stated:** the customers research points small teams to Slack, so this is a real gap. First revisit, and it belongs in the founder's conversations |
| D88 | Who reads the emails when nobody is assigned | Nobody / owners and admins / a banner | **Owners and admins.** A case started by a note has nobody assigned, so the warning would otherwise reach no one. The banner stays for connection health (D69) |
| D90 | Where an owed email is kept | One nullable `tell_pending` column / one column per kind / a small row per email | **A row per email, `finding_emails_owed`.** One column loses an email when a second is owed before the 5-minute sweep, and a column per kind still loses a second close of the same kind, against AC94's "every tracker close" |
| D91 | Where the 5-minute assignment email limit is kept | A new column / a query over `assigned` events | **A query over `assigned` events on the root, by recipient, in the transaction holding the root's lock.** The event already says who was assigned and by whom, so nothing new is stored, and the lock makes the answer safe against a second assignment at the same moment. A self-assignment does not count, since it sends no email |
| D92 | Does a tracker's reopen during a hold reset the idle clock | Yes / no | **No.** D74's rule is about entering `investigating`, and a held case never left it. Otherwise toggling a closed issue would extend a case more cheaply than D74 accepts |
| D89 | When personas disagreed | | **Rule applied, since no written rule was found for this:** founder decisions stand; then AI safety and security; then what a customer is told being true; then behaviour the spec requires; then what other features' approved-track documents already rely on; then simplicity. Each conflict is listed in `status.md`, *Reviews* |
| D93 | Which GitHub account Step 3 tests on (F5's GitHub trial uses the same organization, F5 founder decision 5) | `nexovue-tech` / a personal account / a separate free test organization | **A separate free test organization with one private test repository, owned by the founder's own account.** *Approved by the founder 2026-11-02, with one spare free personal account (foundation item 23).* Installing a test App on `nexovue-tech` would change settings on the real organization and could touch the real repository. A free organization costs nothing and allows unlimited private repositories ([GitHub's plans](https://docs.github.com/en/get-started/learning-about-github/githubs-plans)); GitHub's registration page names no plan limit, only 100 Apps per account ([Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)); T3.0 confirms it in practice. Creating it, and each settings change, still needs the founder's approval at Step 3 (personal rule); that is an act, not an open decision |
| D94 | Whether 72 hours for an untouched case (D71) needs the founder now | Founder decides now / keep the guess and test it | **Keep 72 hours and test it in the foundation's one round of conversations (foundation item 8, approved).** It is a default, reversible in one release, the page warns, and reopening is one click. No money, law or promise rides on it. The earlier "founder should look" flag in `status.md` is closed by this |
| D95 | Bulk export of resolved cases (step J), now that foundation item 6 is approved | Build now / wait | **Wait.** The approved operator setting lets a team keep history as long as its disk allows, which is the real fix. Step J is built only if, after the test bed measures disk use, the default stays below 395 days. Until the setting ships, the page shows the deletion date and "Copy timeline as text" (as before) |
| D96 | What F22 asks in the founder's one round of conversations (foundation item 8) | Scattered / one list | **One list, handed to the round:** (1) would you use status-only two-way sync with GitHub or Jira; (2) which tracker do you use (D59); (3) is ending an untouched case 72 hours after recovery right (D71, D94); (4) should a threshold or check page become a case (D6, step I); (5) when you hand over, would a line in Slack help (D87, step K); (6) would you build on a case-state webhook (D59). Each answer changes only the named step or default |
| D98 | Which Jira credential Step 7 asks for, if it is built | Service-account API token / service account's OAuth 2.0 credential (client credentials) | **The OAuth 2.0 credential first.** It is not an API token, it is always a service account, its scopes are fixed when it is made, and access tokens last an hour. Atlassian's rule names API tokens. The token stays as a fallback only if Atlassian's written answer allows it and not the credential. Cost: the org must use centralized user management, and every call goes through `api.atlassian.com` with a `cloudId`. Whether to build Jira at all stays founder item 1 |
| D97 | Numbers for F22's three proposed ADRs | Reserve now / take the next free number when each is written | **Take the next free number when each ADR is written, at merge.** `docs/decisions/` ends at ADR-023, and F5 already says its own proposed ADRs take the next free number on acceptance (`ai-solution/design.md:931`), so two features would reserve the same ADR-024. `design.md` keeps them unnumbered until then |

## Founder decisions needed

**All answered.** Items 1 and 3 were approved by the founder on 2026-11-02, as recommended (foundation, *Founder decisions*, 17 and 19); item 2 is settled by the foundation (below). The text is kept so references elsewhere still point to the right item.

1. **Approved 2026-11-02, as recommended:** send T0.1's question now; build Step 7 only on a written yes; with a no, or no answer in 4 weeks, F22 is done with GitHub only and `phase-2-plan.md`'s *Done when* is amended. **Jira on a self-hosted install, given Atlassian's rules on API tokens** (legal exposure with a vendor, a public claim, and what is sold).
   - **The facts, checked again on 2026-11-02.** Atlassian's blog of 6 June 2025 says apps must not ask customers to "generate and share API tokens", that its rules "prohibit third-party apps from storing user credentials", and that they apply "regardless of whether your app is publicly available or not". Customers were to be moved by 31 December 2025. A reminder on Atlassian's developer forum (21 November 2025) set enforcement from 1 January 2026. In replies there, an Atlassian staff member said the rule "applies equally to both user accounts and service accounts", and that it still applies to a private app built for one customer. The same staff member said the current enforcement targets Marketplace-listed apps, which nexovue is not. Nothing read speaks to open-source software that a customer installs on their own server and gives their own token. No lawyer has read this.
   - **What changed since the first reading.** The first recommendation leaned on the customer's own *service-account* token being a grey area. Atlassian's staff now say service accounts are covered too. So the token route is closer to "not allowed" than "unclear".
   - **A route the first reading missed (review, 2026-11-02).** An Atlassian org admin can give a service account an **OAuth 2.0 credential**: a client id and secret, used with `grant_type=client_credentials`, with scopes chosen when it is made and access tokens that last 60 minutes ([Atlassian's page](https://support.atlassian.com/user-management/docs/create-oauth-2-0-credential-for-service-accounts/)). Five service accounts are free per organization. It is not an API token, and Atlassian describes service accounts as made "to integrate apps". Nothing read says whether a third-party install may store that secret. It is the best candidate for a written yes, so T0.1 asks about it first.
   - **The other routes.** Atlassian's supported routes are a Forge app or one OAuth (3LO) app owned by nexovue. Atlassian's OAuth needs a client secret on every token refresh, and PKCE without a secret is still an open request (`ECO-283`), so a secret cannot be shipped inside open-source code. A self-hosted install would need a small service nexovue runs, which breaks "no phone-home", or a Forge app, which is a second codebase and a Marketplace listing. Either costs money and time.
   - **Recommendation.** Do not build Step 7's token route on the current evidence. **Send Atlassian's developer support the one written question now (T0.1),** asking specifically: may open-source software that a customer runs on their own server store and use (a) an OAuth 2.0 credential of that customer's own service account, or (b) that service account's API token, for their own Jira site? If the written answer is yes to (a), build Step 7 on (a); if only to (b), build on (b). If it is no, or there is no answer in 4 weeks (a guess), call F22 done with GitHub, and bring Jira back as its own costed stage (Forge, or a nexovue-run OAuth service). On the hosted service, one nexovue-owned OAuth app is the compliant route, if item 3 says yes. Say nothing public about Jira until this is settled.
   - **Why it is yours.** It is legal exposure with a vendor whose platform customers depend on; it decides whether "Jira" can be named in public; and `phase-2-plan.md`'s *Done when* names Jira, so "done with GitHub" changes what was promised. Sending the question also speaks for the company.
   - **Blocks only Step 7 of the plan,** and any public mention of Jira.
2. **Settled by the foundation.** How long a self-hosted install keeps data is foundation item 6, approved by the founder on 2026-11-02 as recommended: an operator setting for both numbers, keeping today's 7 and 30 days until the test bed measures disk use, then raising the default, with 395 days the likely target ([`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions)). The setting is its own change, not F22's. What F22 does about it is D95.

3. **Approved 2026-11-02, as recommended:** the shared GitHub App, and the Atlassian app if Jira is kept, are a launch condition of hosted paid plans (step H). **Whether tracker sync must be there when hosting opens** (what is sold, to whom).
   - **The facts.** Hosting is not open. A paying hosted customer would get no GitHub or Jira, while a free self-hosted install does. Foundation section 10 says no Phase 2 feature is gated by plan on hosted, and `phase-2-plan.md:68`'s *Done when* names both trackers with no such limit. Hosted needs one shared public GitHub App with GitHub's user-token check, and one Atlassian OAuth app. The authors first settled this themselves as D56, and reviewers were right that it is not theirs to settle.
   - **Why it is yours.** It decides what a paying customer gets, and what the pricing page may promise.
   - **Recommendation.** Yes: make the shared GitHub App, and the Atlassian OAuth app if item 1 keeps Jira, a launch condition of hosted paid plans, as a named stage in `phase-2-plan.md` (step H in `plan.md`). If you would rather open without it, state the limit on the pricing page. **Blocks no step of this plan.** It costs real work later, and no money now.

## Open decisions

None. Founder items 1 and 3 were approved on 2026-11-02, and D93's test organization with them (foundation items 17, 19, 23). Own questions answered on 2026-11-02 are D93 to D98. Numbers marked as guesses (D9, D18, D29, D33, D45, D71, the 5-minute email limit, and the memory ceiling) are to be replaced by measurement or the first real users, not by a decision now.

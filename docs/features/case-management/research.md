# F22 Case management: research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Research input for F22's specification, design and plan. It approves
> nothing, and no code is written from it.
>
> **How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present.
> Line numbers are for that working tree and will drift. Nothing was run: no test, no query, and no call to GitHub
> or Jira. Outside pages were read on 2026-11-02, mostly through a summarizing tool, so check a page before quoting
> it in public. Where this document builds on [`phase-2-foundation.md`](../../product/phase-2-foundation.md) it says so,
> and where it disagrees it says why (see *Where this differs from the foundation*).
>
> **Revised 2026-11-02 after the persona reviews.** Rival facts were corrected from each vendor's own page, read that day through a summarizing tool. Corrections are in place, and `status.md`, *Reviews*, lists them.

## The short version

1. **A case is a finding with a person working on it.** This stands (F6 D1, ADR-022, FD12). No `cases` table.
2. **The hard part of F22 is not the lifecycle. It is keeping two systems honest when either can be unreachable,
   wrong or written to by someone else.** Most of this research is about that.
3. **Sync only status, in both directions.** Notes, comments, titles and assignees do not cross. Each one crossed is
   more conflict handling, and comments would carry attacker-reachable text into nexovue, and nexovue's text out.
4. **A webhook only rings the doorbell.** It says "look at this issue". A background job then reads the real state
   from the tracker. Neither GitHub's nor Jira's pages read here promise delivery order, and a self-hosted install may
   not be reachable at all, so polling must work alone.
5. **A tracker issue is created by a person's click, never automatically, in the first version.** Traffic decides
   when findings open, and anyone with a browser key can send traffic. Automatic creation would let an outsider
   write into a customer's repository.
6. **Six things in the current code would break a naive build.** See *What the code forces*. The worst: a recovered
   finding still being worked sorts into the closed tier of the feed, which is cut at 200 rows.
7. **One finding belongs to the founder.** Atlassian's developer pages say apps that collect API tokens, or tell
   customers to make their own OAuth apps, do not comply with its rules. Those are the only two routes that fit a
   self-hosted install with no nexovue service in the middle. See *Founder decisions needed*.

## Words used here

| Term | Meaning |
|---|---|
| **Finding** | One detected problem (`findings` table). Closes itself when the signal recovers |
| **Case** | A finding with a non-null `case_state`: someone is working on it, or did |
| **Tracker** | GitHub Issues or Jira, wherever the team tracks work |
| **Link** | A row saying "this case relates to that tracker issue" |
| **Sync** | Keeping the case's status and the issue's status in step |
| **Reconcile** | Compare what each side says now, and fix the difference. The opposite of trusting a stream of events |
| **Webhook** | A tracker calling our server when something changes |
| **Idempotent** | Safe to run twice: the second run changes nothing |
| **OAuth, 3LO** | A person approves an app to act on an account, without giving it a password. "3LO" is Atlassian's name for the three-party form |
| **ADF** | Atlassian Document Format: the JSON that Jira's text fields are stored in |
| **SSRF** | Tricking our server into calling an address it should not |

## What exists (read from the code)

### The record

- `findings.state` is `open` or `closed`, and a check ties it to the closing fields: `(state = 'open') = (closed_reason
  IS NULL)` and `(state = 'open') = (closed_at IS NULL)` (`migrations/versions/0041_anomaly_detection.py:264-265`).
- One open finding per service and per project, by two partial unique indexes (`0041:279-292`).
- `acknowledged_by` is `SET NULL` when a person goes, and false alarms are dated apart from the person
  (`data/postgres/models.py:1539-1548`). That is the precedent for a note's author.
- `FindingIncident` links a finding to a threshold or silence incident, cascading both ways (`models.py:1628-1643`).
- The scheduler writes findings with plain SQL: `_OPEN`, `_UPDATE`, `_CLOSE` (`detection/project_job.py:278-303`). None
  mentions a case. `_CLOSE` leaves other columns alone, so a self-close will not disturb case columns as written.

### What a person can do today

- `acknowledge` refuses unless the finding is open and unacknowledged (`findings/service.py:177-180`).
- `mark_false_alarm` works on an open or recovered finding (`service.py:206-220`).
- `accept_new_normal` works only on an open service finding (`service.py:250-254`).
- Each locks the row first, and answers 409 if it changed since it was shown (`service.py:60-64,294-298`).
- The gate for these is `MayOperateAlerts`, every working role (`findings/router.py:97,109`; `auth/permissions.py:52`).
- `for_member` hides who acknowledged and the false-alarm reason from an API token (`service.py:156-163`,
  `router.py:90`). **So a token already sees less than a person does on this record.**

### Being read

- The feed is per project, ranks closed findings last, and stops at 200 rows (`findings/queries.py:39,46-50,69`). The
  console sorts the same way (`features/findings/ordering.ts`, `BY_STATE`).
- A closed finding's state is simply `closed` to the screen (`service.py:432-436`).
- `describeState` and the action buttons are on `FindingScreen.tsx:180-193,280-300`. Linked incidents already render at
  `:576-590`.

### Retention

- Closed findings are deleted at the project's derived retention (`detection/retention.py:30-37`), which the
  `ix_findings_closed_at` index (`0048`) serves. An open finding stays, however old (`retention.py:9-10`).
- Seeded derived retention is 30 days on Free and 395 on the others (`0005_plans_table.py:34-37`). A search finds no later
  migration naming `derived_retention_days`, and the seed's comment says values may be edited before launch.
- The reason given: a finding "must not outlive the telemetry it names" (`retention.py:6-8`).

### Parts to reuse

| Need | Exists | Note |
|---|---|---|
| Verify a signed webhook | `deploys/signatures.py:56-93` | Takes bytes, header value and secrets, so it is reusable. Jira uses a different header name (`X-Hub-Signature`) |
| Refuse a replay | `deploys/service.py:99-107` | Delivery id, with a savepoint for races |
| Cap a public body | `deploys/router.py:38` | 64 KiB, too small for tracker events; see below |
| Own rate tier for a public route | `ratelimit/policy.py:125-127,150-157` | The deploy webhook and heartbeat are the pattern |
| Seal a secret | `checks/sealing.py:95-137`, `keys.py:93` | FD10 |
| Reach a customer's address safely | `probes/http_check.perform`, `probes/addresses.py:69`, `alerts/senders/http.py:31-53` | **Scheduler only** (see below) |
| A click that runs outside the API, with a result to wait for | `channels/send_test.py:45`, `checks/run_now.py:54-71` | The API adds a job and reads its result |
| SQL shared by API and scheduler | `RESOLVE_FINDING_SQL` in `nexovue_platform/detection.py` | The pattern for `RECORD_FINDING_EVENT_SQL`, which other documents call `record_finding_event()` |
| Escaping names for receivers | `alerts/payloads.py:42-66` | Slack, Teams and Discord only. **GitHub and Jira need their own** |
| Role-gated screen | `shared/components/Permitted.tsx` | |

### Missing

Lifecycle columns, assignee, notes, timeline, case filter, `connections`, `service_repositories`, links, sync, tracker
webhooks, and a settings screen. No GitHub, Jira or tracker code exists. `/settings/general` is the only settings
route (`route-manifest.ts:33`).

## What the code forces

Six facts a design must answer. None is visible from the documents alone.

1. **A case being worked gets buried, and may vanish from the list.** The foundation's "recovered, still being
   investigated" finding has `state='closed'`, so it ranks in the closed tier (`queries.py:46-50`, `ordering.ts`). The
   feed stops at 200 (`queries.py:39`). A project with many closed findings can push a live case off the page, and the
   default state filter `open` would not show it at all. **The rank, the client sort and the filter must all change.**
2. **`acknowledge` cannot be what starts every case.** It refuses a closed finding (`service.py:178`). A person who arrives
   after recovery and wants to investigate has no way in. And it can happen only once, so a second person cannot
   take over. Assignment and "start a case" must be their own operation, not an overload of acknowledge.
3. **The foundation's retention rule needs more than one clause.**
   - The sweep keeps its cheap index only if `closed_at` still leads, so the rule is "`closed_at` past the shortest
     retention, **and** the later of `closed_at` and `case_resolved_at` past the project's".
   - A later finding with `root_finding_id` must not be deleted while its case is unresolved, and a root must not go
     before its children unless the key is `SET NULL`. The sweep deletes in batches of 1,000 (`jobs/expiry.py`), so
     the order is not otherwise controlled.
   - "An unresolved case stays" makes a forgotten case permanent. That breaks the stated reason for the rule, since it
     keeps names from telemetry past their retention on any plan. See D9.
4. **The API cannot make these calls safely.** The connect-time address guard lives in the scheduler
   (`probes/addresses.py`, using `pycurl`). The API package uses `httpx` and imports no such guard: a search of
   `apps/api/src` and `packages/py-platform/src` finds neither `pycurl` nor `may_connect_to`. A customer's Jira address is
   exactly what the guard is for. So every tracker call, including the "test this connection" click, must run in
   `scheduler-connections`, as a channel's send test already does. See *Where this differs from the foundation*.
5. **The deploy webhook's size cap would refuse real events.** It is 64 KiB (`deploys/router.py:38`). GitHub and Jira
   both document a limit of 25 MB per payload. An issue with a long body can exceed 64 KiB. The new route needs a larger
   cap, and a refused event must be harmless, which polling makes true.
6. **Flapping must not reach a tracker.** A flapping service opens five findings an hour (`detection/notify.py:399-420`).
   With `root_finding_id`, each joins the case's timeline. If each event became an issue comment, one bad service could hit
   GitHub's limit of about 80 content writes a minute and 500 an hour, shared with the App's other writes. GitHub warns that
   continuing while limited "may result in the banning of your integration". **Timeline events must never become comments.**

Also noted, and smaller:

- `FINDING_TOLD_KINDS` (`detection.py:234`) decides closure, so case events must not join it.
- PagerDuty's key is per finding, and a recovery resolves its alert (`alerts/payloads.py:155-157`). That stays right: the
  page ends when the signal does, even if the case goes on. A later finding pages again, also right.
- The personal-data export covers audit entries, not findings (`profile/personal_data.py`). Notes are a person's own
  words, so F22 must add them to the export.
- `organizations/tables.py:90-91` must gain each new table, or `test_deletion_live.py` fails (foundation section 8).
- A closed issue on a deleted finding stays in the customer's tracker, with a link that no longer works. Nexovue
  cannot erase it, and must say so where deletion is explained.

## What users need

### The buyer (CTO of a small team)

- **Wants:** no new bill per person, and not another place to look. Evidence on price and simplicity is thin, from
  aggregators and forum posts ([customers research](../../research/phase-2-customers-2026-11.md), *Incident tracking*). Rated C
  there, and no customer of nexovue has been asked, because none exists.
- **Asks:** "Does it put my internal names in a public repository?" and "Must I give it the keys to my repositories?"
- **Will not pay for it alone.** F22 is a reason to stay, not a reason to arrive.

### The on-call engineer

- At 3 a.m. the page comes through PagerDuty. They need one click to say "mine", and a place for "tried restarting, no
  change" that the morning person will read.
- **They must not be asked to fill anything in** (R8).
- **The dangerous moment** is recovery. The graph looks fine, the finding closes and the alert says resolved, while the
  real cause is unknown. The case must keep standing, and say plainly "recovered, still being investigated".
- A problem that returns an hour later must land on the same timeline, not start from nothing.

### The developer who picks up the issue

- Lives in GitHub or Jira, and may never open nexovue. The issue must say what happened, since when, on which service, and
  link back.
- Closing their issue should finish the case without a second visit.
- **A new developer on the nexovue team** must be able to read the sync in one place: one job, one comparison, no
  scattered event handlers. That is a real argument for reconciling, on top of the technical ones.

### The admin

- Needs setup that does not fail silently. Three known traps:
  - **Jira API tokens expire in at most 365 days**. Sync will simply stop one day a year unless the screen warns first.
  - **Required Jira fields.** Grafana documents that issue creation fails when a project requires extra fields.
  - **Jira workflows.** Grafana documents that a status does not move if there is no direct transition.
- Needs to see "last synced", the last failure and how to fix it, and to disconnect cleanly.
- Needs to understand who can do what: connecting is admin work, using it is member work (FD8).

### The attacker

Assume someone who can send traffic with a browser key, and may read a public repository.

| Path | Danger | What must hold |
|---|---|---|
| A service name like `@acme/security`, `![x](https://evil/?…)` or `Fixes #12`, carried into an issue | Pings a team, loads an image from their server, or changes another issue. The name is theirs to choose (`alerts/payloads.py:204`) | Names sent in code form with fences neutralised, or as a plain ADF text node. A test per tracker. Never the raw `findings.title` |
| Issue text read later by someone's AI tool | Prompt injection one step on (U5 names this for pull requests) | Same rule: template text from validated facts. Nexovue's own AI gets tracker text only as free text (section 3 of the foundation) |
| Findings driven on purpose, so issues pile up | Spam in the customer's tracker, and their App banned | No automatic creation. A person clicks |
| Public repository | Internal service and endpoint names become public, by a helpful click | GitHub's repository data says whether it is private. Warn and require a second confirmation, or refuse (D12) |
| Forged webhook | Case resolved by an outsider | Signature first. The payload is never believed: the job re-reads the tracker |
| Link an existing issue that an outsider wrote | On a public repository its author can close it, which would resolve the case | Linked existing issues are read-only references by default (D5). A case resolved from a tracker can be reopened, and the timeline says who said so |
| Pasted issue URL pointing elsewhere | SSRF, or reading another tenant's issue | Parse to a key, and resolve only inside a connected repository or Jira project. Never fetch the pasted address |
| Binding someone else's GitHub installation | Reads and writes another tenant's issues | Installation claimed with a one-time `state` tied to the session, and unique across organizations |
| Customer-typed Jira address | SSRF to our network, and a token sent to the wrong host | The guarded client, no redirects, `CONNECTION_PRIVATE_ENDPOINTS` set only by the operator |
| Note text with a link or script | Stored XSS, or a phishing link shown to a colleague | Plain React text, as U4 says for model words |
| A member keeps a case open forever | Telemetry names kept past retention | D9 |
| Webhook flood on a public route | Using up the signed-in users' allowance, or the job queue | Its own rate tier. Verified deliveries only produce a job, and one job per link at a time |

## How competitors do it

Prices and facts are from their own pages unless marked. `[R]` means carried from the
[competitors research](../../research/phase-2-competitors-2026-11.md) and not re-read today.

| Product | Lifecycle and timeline | Tracker link | Direction of sync | Self-hosted? | Price |
|---|---|---|---|---|---|
| **OpenObserve** | Open, Acknowledged, Resolved, reopen. Activity timeline. Auto-resolve after inactivity | Jira and GitHub not mentioned on its incidents page | None found | Enterprise edition, not AGPL `[R]` | Enterprise or Cloud. On Cloud, new incidents stop when unpaid AI credits run out |
| **Grafana IRM** | Declared through to review. Timeline | Jira: creates on declare, can't link an existing issue. Tasks convert to GitHub or Jira issues | **One-way for incidents:** "IRM only sends status changes to Jira". A converted task shows the tracker's status | Cloud docs only. Open-source OnCall archived 2026-03-24 `[R]` | $20 per active user, three included `[R]` |
| **Datadog** | Incident Management | Automatic, with an optional condition. Manually linked items "are not synced" | Per-field one-way or two-way, comments included. Two-way needs a Jira webhook | No | $30 a seat, on-call $20 more `[R]` |
| **Sentry** (errors, not incidents) | Issue states | Create or link by hand, or an alert action | Comments, assignee and status, both ways. Marking Done can resolve | Self-hosted Sentry needs a public address for Jira | Manual and sync on Team and up. **Automatic on Business and up** |
| **incident.io** | Full incident product | **Two separate features.** Follow-ups export to Jira or GitHub, and only issues not Done can be linked. Incident ticket sync, announced 15 Sept 2026, covers "all incident ticket integrations", listing ServiceNow, Jira Cloud, Jira Server, Linear, GitHub, GitLab, Azure DevOps, Salesforce Service Cloud and Freshservice | **Follow-ups: tracker is the source of truth** for title, status, owner and more. **Incident tickets: both directions.** "Marking the Linear ticket for the incident as 'done' will resolve the incident". The page says this for Linear only. Jira, GitHub and GitLab ticket status can be controlled from the incident | No | Free Basic has 2 integrations. Team $15 or $19, Pro $25 per user a month |
| **Rootly** | Full incident product | Automatic on declare, or by workflow | Jira to Rootly by a Jira webhook. Its marketing claims more than its docs state | No. Connects to Jira Data Center directly | $20 per user, on-call $20 more `[R]` |
| **PagerDuty** | Its own incidents | Manual or automatic | An older guide says two-way; the current one is less explicit | No | Plan-gated, Professional and up. Price disputed across third-party pages, roughly $21 to $29 per user. **Not checked on PagerDuty's own page** |
| **SigNoz** | Alerts, no incident product found | **A native Jira channel:** "An alert **firing creates** a Jira issue". Added 2 Sept 2026 by its changelog | **Writes status out:** "When an alert resolves, SigNoz transitions the issue to a 'done' status", with "Send resolved alerts" on by default in its setup screen, and it can be turned off. A re-firing alert reopens the same issue within a window, default 3 days. Inbound not read | Page tagged "SigNoz Cloud, Self-Host". **Edition not confirmed** | Free to self-host in its community edition, by an earlier read. Whether this channel is in it was not confirmed |
| **Elastic Cases** | "Cases give your team one place to investigate and resolve incidents", with comments and attachments. In Security, Observability and Stack Management | "push updates to external systems like Jira or ServiceNow" | Out, at least. Not read further | Elastic Stack, generally available. **Tier not confirmed:** the page states none | Not read |
| **Datadog Work Management** ("previously known as Case Management") | Assign to a user, and an activity timeline | "integrates with tools like Jira, ServiceNow, PagerDuty, Slack, and Microsoft Teams" | Not read | No | Not found on the part of the pricing page read |
| **Sentry self-hosted** | As Sentry's row | As Sentry's row | As Sentry's row | Sentry can be self-hosted, per earlier reads | **Which of these self-hosted includes was not confirmed.** The row above shows hosted plan gates only |
| **Aurora** | Incidents and post-mortems `[R]` | Not checked | Not checked | Yes | "Free forever" `[R]`. Young |

**Lessons**

- **Several rivals sync more than status.** Sentry carries comments, assignees and status both ways. Datadog offers one-way or two-way per field, comments included. incident.io's ticket sync now carries more than status. Grafana sends status only for incidents. **nexovue chooses status-only for safety and simplicity, and may read as thin.** That is the thin end of the market, not its norm, so do not imply otherwise.
- **Sentry charges for automation, not linking**, which suggests people will pay for it. Nexovue is holding automation
  back for safety, not as a price lever (FD20).
- **Failures are documented, and copying them is avoidable:** required fields and missing transitions (Grafana); a deleted issue shown as "Deleted" (Grafana).
- **Grafana's page says it collects a classic Jira API token**, and acts as that account. See *Founder decisions needed*.
- **A record that stays open after the graph recovers is ordinary.** Sentry's page says "You can manually mark an issue as `Resolved`", or by a commit, and describes no resolving when errors stop. OpenObserve's page describes resolving by hand or after inactivity. SigNoz is the one rival read that moves its issue on recovery, and that is a setting. So this is an entry requirement, not what sets nexovue apart.
- **A problem that returns is handled differently elsewhere.** Sentry: "Regressed: a resolved issue that's come up again". SigNoz reopens the same Jira issue within its window. nexovue opens a new finding that names the earlier case (D8), and the developer is not told through the issue.
- **The word "case" is used elsewhere.** Elastic has a product named Cases. Datadog renamed Case Management to Work Management. A public reader may bring either meaning (spec, D58).
- **Nothing here proves a gap for ever.** Not all products were checked in depth. As with the foundation, say what nexovue does, never what others lack.

## Methods compared

### How to keep two systems in step

| Method | How | For | Against | Verdict |
|---|---|---|---|---|
| **A. One-way push** | Nexovue writes; never reads | Simplest | Fails the plan's "stay in sync"; a developer closing an issue leaves the case open forever | No |
| **B. Webhooks as truth** | Apply each event as it arrives | Fast | A self-hosted install may be unreachable. Order is not promised. Jira drops to one attempt after 30 failing minutes. Duplicates arrive | No |
| **C. Polling only** | Read each open link on a timer | Works anywhere | Slow; costs requests | Base |
| **D. Reconcile, with webhooks as a doorbell** | Each link holds what it last saw and what is wanted. One job compares and fixes. A webhook just asks for that job sooner | Order and duplicates stop mattering. Works when unreachable. One code path | Needs a clear rule for conflicts | **Recommended** |
| **E. Outbox of events** (ADR-013's shape) | Each change queues one write | Reuses a proven pattern and leases | Edges need order and replay. Flapping makes many. `notifications` is bound to channels. Solves a harder problem than this one | No, though it borrows leases and backoff |

**Why reconciling fits.** An alert is an event: each must be delivered. An issue's status is a state: only the latest matters. So nothing is
gained by keeping every change in order.

**Rule for conflicts (a guess at what users expect, to be tested).** Each link stores `seen_state`, the tracker's status last read, and
`wanted_state`, set when a person changes the case. The job then reads the tracker:

- Tracker differs from `seen_state`: someone changed it there. The case follows, with a timeline event naming the tracker as the source.
- Otherwise, if `wanted_state` differs: nexovue writes it.
- Both at once: **the tracker wins**, since it is where the person doing the work is, and both facts go on the timeline.

Nexovue's own write changes `seen_state` in the same step, which is what stops a loop.

### How to map status

| Case | GitHub | Jira |
|---|---|---|
| `investigating` | `open` | Any status not in the *done* category |
| `resolved` | `closed`, `state_reason: completed` | A transition into the *done* category |
| Resolved as false alarm | `closed`, `state_reason: not_planned` | A *done* transition; Jira's resolution value is not set in the first version |
| Issue closed there | Case resolved | Case resolved |
| Issue reopened there | Case back to `investigating` | Case back to `investigating` |
| Issue deleted | 410, or 404 if unreadable: link marked gone, case unchanged | Link marked gone, case unchanged |
| Issue moved | 301: follow, store the new address | Key may change: store the id too |

**Jira's three status categories** are `new`, `indeterminate` and `done`. This came from general knowledge plus a search summary; Atlassian's page would
not load in the tool, so **the design must check it**. Mapping to categories means no status table to configure in the common case, unlike
Grafana's two chosen statuses. A transition may need required fields, in which case nexovue writes "could not move this in Jira" on the
timeline, and the case stays resolved. **The case is never held hostage by a tracker.**

**What recovery does: nothing to the tracker.** The signal recovering says nothing about whether the work is done.

### Authentication

**GitHub:** a GitHub App, one per self-hosted install (FD11, stands). The manifest flow returns the App id, private key and webhook secret. Read today, and
adding to the foundation's *Not checked*: the page does not say whether GitHub tests the webhook address, but `hook_attributes.active` can be false.
So an unreachable install has a plausible route, untried.

**Jira Cloud** has no equivalent of a manifest flow in the pages read.

| Option | Fits self-hosted? | Standing with Atlassian | Other costs |
|---|---|---|---|
| **A. One distributable OAuth app owned by nexovue** | **No, unless nexovue runs a service in the middle.** It needs a `client_secret` for each refresh and a fixed callback address. Neither can be shipped in AGPL source to every install (the callback point is an inference, unverified) | **The recommended way** | Breaks "no signup or phone-home" (foundation section 12). Fine on the hosted service. Refresh tokens rotate, and lapse after 90 days idle |
| **B. Each customer makes their own OAuth app** | Yes | **Named as not complying** | A long setup |
| **C. Customer's API token, ideally a service account's** | Yes | **"Apps that collect API tokens… don't comply"** is the wording. The Marketplace page applies to Marketplace apps, which nexovue is not. Whether a customer's own install counts is **unclear** | Maximum 365 days. Five service accounts free per organization. Scoped tokens must use `api.atlassian.com/ex/jira/{cloudId}` |
| **E. OAuth 2.0 credential of the customer's own service account** (added 2026-11-02, review) | Yes. The customer's org admin creates it in Atlassian Administration; a client id and secret, `grant_type=client_credentials`, scopes chosen at creation, 60-minute access tokens, calls through `api.atlassian.com` ([Atlassian's page](https://support.atlassian.com/user-management/docs/create-oauth-2-0-credential-for-service-accounts/)) | **Not an API token, and OAuth 2.0 is Atlassian's named supported route.** Atlassian built it "to automate tasks or integrate apps" ([service accounts](https://support.atlassian.com/user-management/docs/understand-service-accounts/)). Whether a third-party install storing the secret counts as "storing user credentials" is **not stated anywhere read** | Needs the centralized user management experience. Five service accounts free per organization. No 365-day expiry stated. Always a service account, never a person |
| **D. Forge app** | Runs on Atlassian. Not examined | Compliant by construction | A second codebase and a Marketplace listing. Not examined further |

**Recommended, pending the founder:** C on self-hosted, with a service account and scoped token where available; A on the hosted service, if hosting opens.
Grafana's IRM documents collecting a classic token. **That shows the practice exists. It does not show it is permitted.**

**Jira Data Center:** Atlassian ends it on 28 March 2029, and has stopped new sales since 30 March 2026. Yet a self-hosting, data-conscious buyer is likely to be on it,
and it is the case where private addresses matter. **Cloud first; Data Center later** (D11). Personal access tokens there were not researched.

### Limits that shape the job

| System | Fact | Consequence |
|---|---|---|
| GitHub | 5,000 requests an hour per installation at least. 304s on conditional requests do not count, if authenticated | Poll with `etag`. Cheap |
| GitHub | About 80 content writes a minute and 500 an hour; wait a second between writes; `retry-after` first | One worker per connection, paced |
| GitHub | Answer webhooks in 10 s, or the delivery fails | Verify, enqueue, answer |
| GitHub | The page read names no idempotency key on issue creation | See below |
| GitHub | Listing issues includes pull requests (`pull_request` key) | Read one issue by number, or filter |
| Jira | Tokens stay under burst limits: 100 reads or creates a second, 50 updates; **per issue 20 writes per 2 s, 100 per 30 s**. App quotas differ | Well above our load |
| Jira | 429 says why, in `RateLimit-Reason`. No gradual throttling on quota | Back off per the reason |
| Jira | Webhooks: at most five retries, spaced 5 to 15 minutes. After 30 failing minutes, one attempt only. Duplicates possible. The id is stable across retries | Doorbell only |
| Jira | REST-registered webhooks expire after 30 days; admin-created ones have a secret and `X-Hub-Signature` | Token route: admin-created webhook, optional, with polling behind it |
| Jira | The old search route is being removed; `/rest/api/3/search/jql` with `nextPageToken` (community and reference search results) | Use the new route. Check before coding |

**A crash between creating an issue and saving its address makes a duplicate.** The fix: the click writes the link row first as `creating`. The job searches for a marker in
that repository before creating (a label or hidden marker on GitHub; a label or issue property on Jira), then creates. Search limits and the marker's exact form are for the design.

### GitHub specifically

GitHub is the only tracker nexovue may name at launch, while Jira is on hold, and most rows above were read from Jira pages. This table is what was read for GitHub. **It is incomplete, and T8.6 must finish it.**

| Product | Create | Link existing | Sync | Plan | Read from |
|---|---|---|---|---|---|
| Sentry | "create GitHub issues from within Sentry". By hand, or by an alert action | Yes, *External Links* | Comments, assignees and status. Assignee needs "a 1:1 mapping between a Github user and a Sentry user" | By hand and sync on Team and up. Automatic on Business and up | Its GitHub page, 2026-11-02 |
| incident.io | A ticket integration | Not read | Status can be controlled from the incident | Not read | Its changelog, 2026-11-02 |
| Grafana IRM | Tasks convert to GitHub issues | Not read | A converted task shows the tracker's status | As above | Its GitHub integration page, earlier |
| SigNoz, Datadog, Rootly, PagerDuty, Elastic, OpenObserve, Aurora | Not read for GitHub | | | | |

**So "one click creates a GitHub issue, and closing it ends the case" describes a capability rivals have.** If there is a GitHub edge to claim, it is the specific one being built: the preview, public repositories refused by default, and no outside text in the issue. Claim those only as what nexovue does.

### How much should sync carry?

| Field | Decision | Why |
|---|---|---|
| Status | Both ways | The plan's requirement |
| Title, body | Written once at creation, never updated | Otherwise a person's edits get overwritten |
| Close reason | Out only | GitHub's `state_reason` is free to use |
| One comment at resolve | Out, template only | A developer sees why it closed, and one comment cannot flood |
| Other comments and notes | **Not in either direction** | Attacker-reachable text in; a person's pasted logs out |
| Assignee | Not synced | Accounts differ, and mapping people is a feature of its own. incident.io matches by user. Revisit on demand |
| Labels, priority | Fixed label `nexovue` on create; nothing else | Datadog-style mapping is a configuration screen no small team asked for |

## What nexovue should bring

**True once built, only if the design holds. Future tense until then.**

1. **Case tracking is in the AGPL edition, with no per-person charge from nexovue.** Free options exist elsewhere: OpenObserve Enterprise is free self-hosted to 50 GB a day (competitors research:55,82), which may well cover a 5 to 50 engineer team (a guess), Aurora is listed as free, incident.io has a free plan, and PagerDuty's free plan covers 5 users (customers research:97-98). **So this is a licence and simplicity point, not a unique price gap.** Of the products checked, OpenObserve keeps incidents in Enterprise and Cloud, and Grafana's open-source on-call is archived. **Elastic Cases and Sentry self-hosted are not yet resolved,** so say only what nexovue includes. No seat-price comparison: the foundation's line (section 12 item 4) must not be used. This corrects "the real gap".
2. **Nothing to set up to start.** A person clicks a finding, and it is a case.
3. **The evidence is already there.** No pasting graphs into a ticket. The stored signals, normal bands and explanation (F3) sit on the case.
4. **It tells you when recovery isn't the end.** "Recovered, still being investigated" is one line, and the columns make it cheap. Others have the same behaviour (*Lessons*), so this is an entry requirement.
5. **Sync that says when it failed.** A visible state per link, not a silent stop.
6. **A case keeps working with AI off.** F22 has no model call at all.
7. **A foundation for F4 and F5:** resolved cases and notes for F4's knowledge; the connection and mapping for F5.

**AI safety, as it applies here.** F22 itself holds no model. Tracker text and notes count as free text wherever an AI later reads them (foundation section 3), and the process that holds tracker
tokens never runs a model (FD1). **So F22 needs no injection suite of its own**, but it does need the outbound escaping tests above, and F4 owns the suite when it reads notes. The gate's own rule stands: AI
off changes nothing here.

## What nexovue should not attempt

| Do not build | Why |
|---|---|
| On-call, schedules, escalation, paging | The founder's decision: PagerDuty (plan) |
| Forwarding PagerDuty's acknowledgement to or from a case | On-call stays there, and this is the start of building on-call |
| Status pages, call transcription, incident roles, severity forms | "Replaces incident.io" must not become a goal (foundation section 12) |
| Running a case from Slack | **Out, stated plainly as the customers research asked.** Alert links reach Slack today. It is what incident.io does well |
| Post-mortem documents | A note and the timeline are enough at first. Teams already have a place for these |
| A separate case list screen or `cases` table | ADR-022, FD12 |
| Merging or grouping cases by hand | Only the automatic `root_finding_id` join. Merge needs undo, and nobody has asked |
| More trackers (Linear, GitLab, Azure DevOps) | The plan names two. Sentry lists many. Build the seam, not the list. A webhook from F21 covers others in part |
| Threshold incidents as cases | FD13 stands, reviewed below (D6) |
| Automatic issue creation | D4 |

## Where this differs from the foundation

The foundation asks that a difference be stated. Each is an addition or narrow change, not a reversal.

| Foundation says | This research says | Why |
|---|---|---|
| Writing out runs in "API, on a person's click" and `scheduler-connections` | **For F22, all tracker calls run in `scheduler-connections`. The API only adds a job.** | The API has no address guard (*What the code forces*, 4). `CONNECTION_SECRET_KEY` is still needed in the API, to seal and to open a webhook secret. **F5 should check the same point for its draft pull request** |
| `/connections/github/webhook` is one incoming route | Add one for Jira: `/connections/{id}/jira/webhook` (name to be settled in design) | Plan requires Jira sync. It is optional, as polling is the base |
| Timeline is read by "anyone who can read the finding" | **A person sees all. An API token sees event kinds and times, without names or note text** | Matches `for_member` (`service.py:156-163`). Otherwise notes reach any token holder and F36's MCP server, with attacker-reachable text, before F4's labels exist |
| "Acknowledging, assigning, noting or linking an issue starts the case" | Plus an explicit way in for a closed finding, where acknowledging is refused | `service.py:178` |
| "An unresolved case stays" | Stays, until D9's limit | It would otherwise defeat the retention reason |
| Only `finding_links`, `case_state`, `assignee_id` and three more columns are named | Add link columns for the reconcile (`seen_state`, `wanted_state`, sync status) and a `resolution` value for the case | Needed by the methods above. Names are for the design |

## Decisions taken

*Recommended by the authors, founder may overrule.* FD11, FD12, FD13 and FD23 are the foundation's, and carried unchanged unless noted.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | Sync method | One-way / webhooks / polling / reconcile plus doorbell / outbox | **Reconcile plus doorbell.** Order and duplicates stop mattering, and an unreachable install works. It costs a poll per open link, cheap on GitHub through `etag` |
| D2 | What crosses | Status / status and assignee / everything | **Status, plus one template comment on resolve.** Less to go wrong, and no attacker text in. People may ask for assignee; add it when they do |
| D3 | Conflict | Nexovue wins / tracker wins / latest timestamp | **Tracker wins, and both go on the timeline.** Timestamps across two systems are unreliable, and the person doing the work is there. A guess about what users expect |
| D4 | Automatic issue creation | None / opt-in per rule / always | **None in the first version.** Attacker-driven spam, rate-limit bans, and a flapping service. Costs some appeal: competitors offer it. The first thing to revisit, with a daily cap and critical severity only |
| D5 | Issues per case | One / one synced, others references / many synced | **One synced; any number read-only.** Datadog draws the same line. "Resolved when all are closed" is a rule nobody asked for |
| D6 | Threshold incidents as cases (FD13) | Yes / link only | **Link only, FD13 confirmed.** Reviewed from ADR-022's opening: giving incidents case columns repeats the rejected merge. Cost: a check failure with no finding has no notes or tracker link. If users ask, a person's click opening a finding-less record would need its own decision |
| D7 | Does a false alarm or new normal end a case | Leave / resolve | **Resolve, with a `resolution` value.** Saying "this was never a problem" while the case stays open is a contradiction. Plain recovery does **not** resolve |
| D8 | New finding on a **resolved** case | Reopen / new case / new finding only, linked as recurrence | **New finding, no case until a person acts, and the screen says "this service had a case resolved on …".** Reopening automatically undoes a person's decision on a signal alone. Reopen stays one click. A window could be added later |
| D9 | A forgotten case | Keep for ever / auto-resolve after no activity / warn only | **Auto-resolve after 90 days without activity, resolution `no_activity`, on the timeline, closing nothing in the tracker.** 90 is a guess. OpenObserve has a similar rule. Needed so retention still ends. An open tracker issue does not count as activity, because names would otherwise still outlive their retention. The issue itself stays open |
| D10 | Which findings join `root_finding_id` | Same service and scope / also project findings | **Same scope and same service only.** A project finding is another problem's shape, with no service name (`service_matches_scope`). Linking them is F3's correlation |
| D11 | Jira editions | Cloud / Cloud and Data Center | **Cloud first.** Keeps the first version small. Reconsider if a real buyer needs Data Center, despite its end in 2029 |
| D12 | Public repository | Allow / warn twice / refuse | **Refuse by default; an admin may allow one per mapping, with the warning naming what becomes public.** A private company tracking on a public repository is rare, and the leak cannot be undone. The check runs at each create, since a repository can become public later |
| D13 | Who sees notes and timeline detail | Tokens too / persons only | **Persons, including viewers.** Same as `for_member` |
| D14 | Note edit and delete | None / author may delete / full edit | **Author or admin may delete; no editing.** People paste secrets, and they must be removable. The timeline keeps "a note was deleted" |
| D15 | Assigning | Only yourself / anyone in the organization | **Any working member, by any working member.** Small teams hand work over by hand. No notification beyond email in F21's vocabulary is added at first; the design decides whether `case.assigned` is emailed |
| D16 | Link to an existing issue | Allowed / create only | **Allowed, as a reference; promoted to synced by a second, explicit choice.** Teams often have the issue already |
| D17 | Connection scope | One per organization / per project | **Per organization**, with `service_repositories` per project and service. The App and the bill for it are one owner's. A project with no mapping offers a repository choice, not a block |
| D18 | Poll interval | Fixed guess now / measured | **Start at 5 minutes for open links, once an hour for resolved ones within 30 days. A guess.** The screen says "may take up to 5 minutes" so nobody reads delay as failure |
| D19 | Container | Reuse `scheduler-deliveries` / new role (FD23) | **New role, FD23 stands.** Memory is unmeasured, and `sizing.md` must be updated |

## Founder decisions needed

1. **Jira on a self-hosted install, given Atlassian's wording** (legal exposure, and what is sold).
   - **The facts.** Atlassian's page says: "Apps that collect API tokens or instruct customers to create individual 3LO apps don't comply with our Security requirements for cloud apps and Acceptable
     use policy." The Acceptable Use Policy page itself, when read, did not mention tokens. The Security Requirements page opens with Marketplace apps, which nexovue is not. **So whether a self-hosted install's own
     administrator typing their own token counts is unclear, and no lawyer has read this.**
   - **Why it matters.** The approved plan requires Jira to create issues and stay in sync. The compliant route needs a service nexovue runs, which breaks "no phone-home", and the other routes fit Atlassian's wording of what not to do.
   - **Recommendation.** Build with the customer's own service-account token on self-hosted (their account, their server, nexovue holds nothing), use a single shared OAuth app on hosted, and send Atlassian's developer
     support one written question before any public mention of Jira support. The question costs nothing. If the answer is no, the fallback is GitHub first, and Jira through Forge or a nexovue-run service, which would come back to you with a price.
   - **Send it now, before Step 1, with the founder's approval** (spec, founder decision 1; `plan.md`, T0.1). Only F22's Jira step, and any public mention of Jira, wait on it.
   - **Rechecked 2026-11-02.** [Atlassian's blog](https://www.atlassian.com/blog/developer/building-secure-and-scalable-integrations-our-guidance-for-third-party-apps) (6 June 2025) says the rules apply "regardless of whether your app is publicly available or not", with customers moved by 31 December 2025. [A forum reminder](https://community.developer.atlassian.com/t/reminder-migrate-from-using-api-tokens-to-officially-supported-authentication-for-atlassian-apps-integrations/97221) (21 November 2025) sets enforcement from 1 January 2026, and a staff reply says it "applies equally to both user accounts and service accounts" and to a private app built for one customer; current enforcement targets Marketplace-listed apps. Atlassian Cloud OAuth still needs a client secret; PKCE is an open request ([ECO-283](https://jira.atlassian.com/browse/ECO-283)). So option C is weaker than first read. The spec's recommendation now builds Step 7 only on a written yes.
   - **Review, 2026-11-02.** Option E, a service account's own OAuth 2.0 credential, was missing from the table. It is not an API token, so it sits outside the rule's literal wording, and it is the cleanest route if Atlassian allows it. T0.1's question now asks about E first and C second, and Step 7 builds on E if E is allowed.

## Not checked

- Anything running: no GitHub or Jira call, no self-hosted install behind a firewall, no measured poll cost.
- The exact fine-grained GitHub permissions: the issues page read names none. FD11's `issues: write` needs testing. Webhooks' `issues` event requires at least read, per GitHub.
- Whether the App manifest flow works with an inactive webhook, or from a private address.
- Jira's status categories, and the new search route, from Atlassian's own reference: those pages did not load in the tool.
- Whether Jira's admin-created webhook needs an admin's own act (it appears to), and how much of the setup a service account can do.
- Jira Data Center's tokens and webhooks. Forge. Atlassian's written position.
- Elastic Cases' tier, read as a whole subscription matrix. Which Sentry features self-hosted includes. Which SigNoz edition holds the Jira channel, and whether SigNoz reads status back. A GitHub read for most rivals. Datadog Work Management's price.
- PagerDuty's current list prices from its own page; Aurora and SigNoz in depth; Uptrace, HyperDX, GoAlert and Keep (a search found nothing usable, which is not evidence of absence).
- Any real user's opinion. All user needs above are inferred from the research and persona reasoning, and no customer exists. The founder's five to eight conversations (foundation) should include "would you use two-way status sync?"

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [code map](../../research/phase-2-code-map-2026-11.md),
[competitors](../../research/phase-2-competitors-2026-11.md), [customers](../../research/phase-2-customers-2026-11.md), [`ADR-022`](../../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md),
[`ADR-011`](../../decisions/ADR-011-api-dispatches-run-now.md), [`ADR-013`](../../decisions/ADR-013-notifications-are-an-outbox.md), [F6 spec](../anomaly-detection/spec.md), [F6 design](../anomaly-detection/design.md),
`docs/product/vision.md:133,244`, `docs/product/feature-catalog.md:125,232,336-337`.

**GitHub:** [Webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks) · [Webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads) · [Issues REST API](https://docs.github.com/en/rest/issues/issues) · [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) · [REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) · [App manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)

**Atlassian:** [Basic auth, with the notice on tokens](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/) · [Marketplace security requirements](https://developer.atlassian.com/platform/marketplace/security-requirements/) · [Acceptable use policy](https://www.atlassian.com/legal/acceptable-use-policy) · [OAuth 2.0 (3LO)](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/) · [Webhooks](https://developer.atlassian.com/cloud/jira/platform/webhooks/) · [Rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/) · [ADF](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/) · [API tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) · [Service account tokens](https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/) · [Ascend to the cloud](https://www.atlassian.com/blog/announcements/atlassian-ascend) · [Data Center licensing](https://www.atlassian.com/licensing/data-center) · [Issue search reference](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/) · [Enhanced search RFC](https://community.developer.atlassian.com/t/rfc-61-evolving-search-capabilities-addressing-scalability-with-a-new-enhanced-search-api/83027)

**Competitors:** [OpenObserve incidents](https://openobserve.ai/docs/user-guide/analytics/incidents/) · [Grafana IRM](https://grafana.com/docs/grafana-cloud/alerting-and-irm/irm/) · [Grafana IRM Jira](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/integrations/dev-and-operations/jira/) · [Grafana IRM GitHub](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/integrations/dev-and-operations/github/) · [Grafana tasks](https://grafana.com/docs/grafana-cloud/alerting-and-irm/irm/manage-incidents/collaborate/manage-tasks/) · [Datadog Jira](https://docs.datadoghq.com/incident_response/incident_management/integrations/jira/) · [Sentry Jira](https://docs.sentry.io/integrations/issue-tracking/jira.md) · [incident.io pricing](https://incident.io/pricing) · [incident.io Jira follow-ups](https://docs.incident.io/integrations/jira-follow-ups.md) · [incident.io tracker sync](https://docs.incident.io/post-incident/follow-up-issue-tracker-sync) · [Rootly Jira](https://docs.rootly.com/integrations/jira/jira) · [Rootly Jira on-premise](https://docs.rootly.com/integrations/jira-on-premise) · [PagerDuty Jira Cloud](https://support.pagerduty.com/docs/jira-cloud) · [SigNoz comparison](https://signoz.io/comparisons/incident-management-tools/) · [SigNoz Jira channel](https://signoz.io/docs/alerts-management/notification-channel/jira/) · [SigNoz changelog, 2 Sept 2026](https://signoz.io/changelog/2026-09-02-jira-and-jira-service-management-ops-added-as-new-alert-channels-yna00hzoovhwt1hw7w95jz9e/) · [incident.io ticket sync, 15 Sept 2026](https://incident.io/changelog/bidirectional-sync-for-incident-tickets) · [Elastic Cases](https://www.elastic.co/docs/explore-analyze/alerts-cases/cases) · [Datadog Work Management](https://docs.datadoghq.com/incident_response/case_management/) · [Sentry GitHub](https://docs.sentry.io/organization/integrations/source-code-mgmt/github/) · [Sentry issue states](https://docs.sentry.io/product/issues/states-triage/)

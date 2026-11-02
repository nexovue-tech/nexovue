# Design: Case management (F22)

## Status

**Approved** by the founder, 2026-11-02, as revision 2, with [`spec.md`](spec.md). Revision 2 regenerates this document from the revised spec and answers the persona reviews (`status.md`, *Reviews*). The code was read on branch `feature/anomaly-detection`, with uncommitted work. Line numbers are for that working tree and will drift. Nothing was run: no test, no query, no call to GitHub or Jira. Every memory, row and interval figure is a guess or plain arithmetic, and says so.

## Context and goal

A finding closes itself when its signal recovers. Work does not. F22 adds a second question to the same row, whether people are working on it, and connects that answer to GitHub Issues and Jira.

The hard part is not the lifecycle. It is keeping two systems honest when either may be unreachable, wrong, or written to by someone else, while nothing an outsider sends reaches a tracker as anything but inert text.

### Where this differs from the foundation

The foundation asks that a difference be stated.

| Foundation says | This design says | Why |
|---|---|---|
| Writing out runs in "API, on a person's click" and `scheduler-connections` | **All tracker and GitHub calls run in `scheduler-connections`.** The API adds a job | The API has no connect-time guard: `probes/addresses.py` needs `pycurl`, and the API uses `httpx` only for billing. F5 reached the same answer (`ai-solution/design.md:276`) |
| `POST /connections/github/webhook`, with replay protection by delivery id | **`POST /connections/webhook/{connection_id}`**, one route for both trackers, **with no delivery table** | Each App has its own secret, so the row must be found first. Rate tiers match by prefix (`policy.py:277`). A replay only asks for a re-read, so a stored list of deliveries protects nothing and grows without limit |
| `CONNECTION_PRIVATE_ENDPOINTS` for a customer's Jira or Enterprise | **Not built yet** | Cloud first (D11). Typed Jira addresses are limited to `*.atlassian.net` |
| Timeline is read by "anyone who can read the finding" | **A real person sees all; a token sees kinds and times; a support view sees no note bodies** | Keeps notes from any token holder, F36's MCP and support staff |
| "Acknowledging, assigning, noting or linking starts the case" | Plus an explicit start for a closed finding. **Replaced as the page's act:** one button, **Take this** (D47) | `service.py:178` refuses a closed finding. Only taking, acknowledging or assigning records an acknowledgement; a note or link does not (D70) |
| "An unresolved case stays" | Stays, until 90 days without a person's act, or 72 hours after recovery if nobody did anything but take it | It would otherwise defeat `retention.py:6-9`'s reason, and fill the feed (D71) |
| New finding on a service with an open case joins, with no time limit (`phase-2-foundation.md:388`) | Joins only if a person acted on that case within 7 days (FR9) | A forgotten case must not swallow a new failure (D45) |
| A linked threshold incident "appears on the finding's timeline" (`:390`) | It stays in the page's existing *Linked alerts* section (FR16) | That section already shows it. Copying it adds a second place to keep true |
| Hosted plans have no Phase 2 feature gated (section 10) | **Tracker connections do not exist on hosted until a later step** | Not a plan gate, but it needs a shared App. It is founder decision 3 |
| Only six names on `findings` | Adds `case_resolution`, `case_activity_at`, `case_worked_at`, `assignee_acted_at`, `case_resolved_by`, `case_warned_at` | Needed below |
| `service_repositories` holds repository and folder | F22 creates it without a folder. F5 keeps folders in its own `service_code_locations` (`ai-solution/design.md:688`) | Its one place per service may be a Jira project |
| `scheduler-connections` shares the settings anchor implicitly | **It gets narrow settings, and a task set of its own** | D30, D80. It holds repository access, so it should hold nothing else |
| A viewer appears in no action row (section 5; `permissions.py:49`) | **One exception: an author, whatever their role, may delete their own note.** That route is in `EXEMPT` with its reason | A person must be able to withdraw a secret they pasted (AC25) |
| Section 12 item 4: "against $20 to $30 a seat for hosted tools" (`:597`) | **Do not use that comparison.** Say "no per-person charge from nexovue" | Rivals have free plans, and the spec bans saving claims. The foundation's line was changed on 2026-11-02 |
| One GitHub connection, whose single App has its permissions raised when F5 is turned on (`:26,:343`) | **`connections.kind` allows a second App, `github_fix`, which F5 creates, and F22 ignores it** | Permissions belong to an App, so raising F22's would reach every repository connected for issue sync (F5's D36, `ai-solution/design.md:463`). F22's mapping may also point at a Jira project, so a customer can use F5 with no GitHub issue sync (`:466`) |
| Scheduler `org_id` structure test is "not built" (`:495`) | **It exists** (`apps/api/tests/test_scheduler_postgres_tenancy_structure.py`) | F22 extends it. See *Security, tenancy, and privacy* |

### Where this differs from the research

Reading further showed these. Each is a correction, with the reason.

| Research says | The code or source says | Effect |
|---|---|---|
| The default state filter `open` would hide a recovered case | The route's default is `all` (`findings/router.py:46`), and the console sends `state: "all"` (`features/findings/api.ts:180`) | The console's problem is the rank and the 200 cap, not the filter. A direct `state=open` caller still will not see it, which is right: it asks about the signal |
| `/settings/general` is the only settings route (`route-manifest.ts:33`) | That is the sidebar's entry. The router has many (`app/router.tsx:448-513`), and the rail is `shared/settings/settings-nav.ts` | `/settings/connections` is one rail entry and one route. `route-manifest.ts` does not change |
| `acknowledged_by`'s `SET NULL` is the precedent for an author going | Removal keeps the user row and renames it (`members/service.py:188-222`), so `SET NULL` fires only when an organization is deleted | Authors read as "Former member" without extra work. **An assignee must be cleared by both `scrub` and `change_role`** (`:147-167` does not call `scrub`) |
| A one-time `state` protects binding an installation | GitHub's setup URL page: "Bad actors can hit this URL with a spoofed `installation_id`", and it says not to rely on it | The id is never read. See *Connecting GitHub* |
| "Both at once: the tracker wins" | With only two values, both sides moving away from what was last seen must land on the same value | No true disagreement exists on status |
| Delivery id and a savepoint for races | Needed only if a delivery has effects of its own | Dropped |
| The label `nexovue`, or a label or marker, finds a half-made issue | GitHub drops labels silently for callers without push access. Whether an App with `issues: write` counts was not confirmed | The link back in the body is the marker |
| ETags make reads free | GitHub Docs staff agree that a new token gets a new ETag, and each run mints one (D63) | Dropped. Every read costs a point |

**New facts a build would trip on, not in the research:**

- `Settings._check_key_is_set_in_production` (`scheduler/settings.py:172-178`) refuses to start any scheduler role in production without `CHECK_SECRET_KEY`, and `Settings` never sees `--queues` (`__main__.py:59,74`). So the check cannot depend on queues. It depends on `SCHEDULER_ROLE`, F3's mechanism (`ai-insights/design.md:641`).
- `add_missing_secrets` (`deploy/nexovue:139-143`) adds only ClickHouse account passwords, on the reasoning that other secrets guard data already written. A brand-new sealing key guards nothing yet, so it must be added too, or an upgrade fails on `:?set`.
- A new open finding has no row to lock. `_OPEN` is a plain insert (`project_job.py:278-286`), so a person starting a case at that same moment can miss it. See *Joining*.
- `perform` follows redirects by default (`http_check.py:65`). Every tracker call must set it off, as `senders/http.py:31-53` does.
- **`lock_finding` takes `FOR UPDATE` (`findings/queries.py:108`), and a foreign key check takes `FOR KEY SHARE` on the row it points to.** The two conflict. See *Locks*.
- **`acknowledge` reads ClickHouse under its lock.** It calls `_member_view` (`service.py:178-189`), `_detail` calls `queries.service_readings` (`:332`), and the session commits only after the route returns (`deps.py:63-73`).
- **`finding_opened` returns on a muted finding before `_flap_guard` runs** (`notify.py:213-215`), and `_flap_guard` writes the `flapping` column (`:408-420`). So timeline events cannot live inside `notify`.
- **The console's content security policy allows forms to post only to itself** (`deploy/caddy/Caddyfile:83`). See *Connecting GitHub*.
- `_VERIFIED_OWNERS` (`alerts/senders/email.py:21`) selects owners only. FR33 needs owners and admins.
- `tools/detection-seed` writes per-minute history and cannot make a finding (`seed.py:1-9`). The plan adds a command that can.

## User experience

**The on-call engineer's job:** say it is theirs, write down what they tried, and hand over. It starts from an alert link on the finding page and ends without leaving it.

1. The page leads with its sentence. Beneath it: "Nobody has taken this in nexovue" and a primary button, **Take this**, on an open or a closed finding.
2. One click. The line becomes "You took this at 14:06". If the project has a PagerDuty channel, it adds "PagerDuty is not told: acknowledge the page there too." No form.
3. A box under that line is always ready for a note, labelled *Add a note*. Enter adds a line. The button **Add note**, or Ctrl or Cmd+Enter, sends it.
4. The signal recovers. The sentence reads "Recovered at 15:04, still being investigated by Asha". If nobody has added anything, a line says when the case ends by itself (FR49).
5. It returns an hour later. The new finding's page leads with "Part of a case that started at 14:02", linked, and shows the same case section. The person paged works from here. If the assignee has not acted since it returned, **Take over from Asha** is the primary button.
6. Asha's shift ends. She opens **Reassign**, picks a colleague, and presses **Assign**. The screen says truthfully whether an email is on its way.

**The developer's job** is to get the issue and close it. The on-call person clicks **Create an issue**, reads exactly what will be written, and confirms. Then they open the issue to assign it, since it notifies nobody until then, and can **Copy my notes** to paste under their own name. Closing the issue resolves the case once the signal has recovered.

**The admin's job** is to connect once, then not think about it. Settings says what is connected, whether it works, when it last worked, and what to do when it does not.

**The team lead's job** is to see what is being worked, across every project, with no new screen.

Crossing screens: one. A person working a case stays on the finding page.

## UI structure

Follows `docs/design/principles.md`. Screens use the window: containers run full width, and only sentences are capped (`PROSE`, `FindingScreen.tsx:54`). All sentences come from `spec.md`'s tables, and live in `finding-words.ts`.

### The finding page (`features/findings/FindingScreen.tsx`)

**One order, everywhere.** The DOM holds work before evidence. At a wide window, work is the left column and evidence the right. Below that they stack in the same order. So reading order, tab order and visual order agree at every width (NFR8, AC67), and no second render can lose a note draft. The break is measured on the content area, not the window, because the sidebar takes 13rem above `md` (`design-system.md:383`): a container query at about 64rem (1,024 px), a guess.

**F5's page-order table (`ai-solution/design.md`, *The finding page's order*) now carries this layout, and is the one table** (F5 D54). In the right column, F3's section leads, and its first row is the last deploy, with F5's brief under it.

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ Back to Assigned to me                                                                │
│ CRITICAL                                                                              │
│ checkout is 4x slower than normal since 14:02        (the sentence, as today)         │
│ checkout · Recovered at 15:04, still being investigated by Asha  Evidence ↓ · Fix ↓  │
├──────────────────────────────────────────────┬────────────────────────────────────────┤
│ THE CASE                                     │ What lines up with this (F3)           │
│ [Take over from Asha] [Reassign] [Resolve]   │   first row: Last deploy before this   │
│ [Create an issue]                            │ What moved                             │
│ THE SIGNAL                                   │ Readings (charts)                      │
│ [False alarm] [Snooze this service] …        │ Services affected                      │
│                                              │ Linked alerts                          │
│ Issue: acme/api#412 · synced 3 min ago       │                                        │
│ Also linked: acme/web#88 (open)              │                                        │
│ Add a note                                   │                                        │
│ [                                          ] │                                        │
│ Visible to everyone in your organization.    │                                        │
│ Do not paste secrets.             [Add note] │                                        │
│ Timeline                     [Copy as text]  │                                        │
│  15:40 Asha: tried restarting…               │                                        │
│  15:04 Recovered                             │                                        │
│  14:06 Asha took this                        │                                        │
├──────────────────────────────────────────────┴────────────────────────────────────────┤
│ Suggested fix (F5): full width, so the diff has the window                            │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

**At 375 px** (AC66): sentence; *The case*, each action at least 44 px tall on its own row (a guess, the common touch guideline), with Resolve never next to Reassign; *The signal*, behind a labelled disclosure only if it exceeds one row; one "What moved" line per signal, with its current and normal values; note box; links; the newest 5 timeline rows; full evidence. The *Evidence* jump link sits under the sentence. Existing buttons are about 32 px tall by arithmetic (`Button.tsx:38`, `tokens.css`).

**Reused:** `Button`, `ConfirmPanel` through the page's `Confirm`, `InlineFailure`, `SelectField`, `SkeletonRows`, `CopyButton`, `formatZonedTime`, `formatRelativeTime`, `useCan`, `isConflict`, `liveOptions`. **New:** `CaseSection`, `CaseLine`, `Timeline`, `NoteBox`, `ReassignPanel`, `LinksList`, `CreateIssuePanel`, all in `features/findings/`. Rules live in `finding-page.ts`, so they can be tested without drawing, as today.

**Deliberately not shown:** a severity field, a priority field, a required resolution form, a status dropdown with many steps, a tracker person's name, an issue's title.

**State table.** Each row must exist before React (`principles.md:53`), and each has a `FindingScreen` test.

| State | Sentence | Primary action | Note box | A viewer sees |
|---|---|---|---|---|
| Loading | Finding above is not held up | None | Hidden | Same |
| Open or closed, no case | "… Nobody has taken this in nexovue" | **Take this** | Shown (a note starts a case) | The sentence, no action |
| Case, nobody assigned | "… Being investigated, nobody assigned" | **Take this** | Shown | The sentence |
| Case, mine | "You took this at 14:06" | **Resolve**, with Reassign | Shown | Not applicable |
| Case, someone else's | "Asha took this at 14:06" | None; **Take over from Asha** is secondary | Shown | The sentence |
| Joined, assignee has not acted since it joined | "Came back at 02:00. Asha took the case 12 hours ago, and has not acted since" | **Take over from Asha** | Shown, writes to the root | The sentence |
| Joined, otherwise | First line: "Part of a case that started at <date, time, zone>", linked, and the signal that moved here: "Errors moved on this finding". Then the same `CaseSection`, bound to the root. Its signal actions stay in *The signal* | As the root's | Shown, writes to the root | The sentence, the timeline |
| Closed joined finding, root resolved | "Part of a case resolved on 2 Nov" | **Reopen the case** only (FR13) | Shown, writes to the root | The sentence |
| Resolved | "<signal words>. <how it ended>", and "Kept until 2 Dec. 3 later findings are still kept" | **Reopen** | Shown; a note does not reopen | The sentence, the date |
| Resolved, signal still open | "Still open: the signal has not recovered. <how it ended>" | **Reopen** | Shown | The sentence |
| Standalone, a quiet case exists on this service (FR9) | "This service has a case nobody has touched since 12 Oct", linked | **Take this**, which joins that case | Shown | The sentence |
| Earlier case on the service, a person resolved it | "This service had a case resolved on 2 Nov", linked | **Take this** | Shown | The sentence |
| Earlier case ended without a person's decision (`tracker_closed`, `no_activity`, `recovered_untouched`) | "This service had a case that ended on 2 Nov. <how>" | **Take this**, with **Reopen that case** beside it (FR13) | Shown | The sentence |
| Issue closed, signal open (D73) | "Issue closed in GitHub at 16:10, and the signal is still open. Asha took this at 14:06" | **Resolve** | Shown | The sentence |
| About to end, idle | "No activity for 76 days. Ends by itself on 16 Nov. Add a note to keep it going, or resolve it." | As the case | Shown | The sentence |
| About to end, untouched | "Recovered. Ends by itself on 5 Nov unless someone adds something." | As the case | Shown | The sentence |
| Support view | Same sentences | None | **Hidden.** Note bodies read "Hidden during support view". Copy as text is hidden | Not applicable |
| Timeline failed | `InlineFailure` in that section only | Retry | Stays usable | Same |
| Timeline empty | "No events for this finding. It opened while this service was flapping, so openings and closings are not listed one by one." | Unchanged | Unchanged | Same |
| Flapping joiner | Fixed line "This service is flapping" on the case | Unchanged | Unchanged | Same |
| Partial | A former member reads "Former member". A removed link reads "an issue". A joined finding past retention reads "a finding, no longer kept" | Unchanged | Unchanged | Same |
| Conflict (409) | The existing `changedMeanwhile` line, naming who now has it, then a reload | Reload | Keeps the draft | Not applicable |
| No connection, admin | **Create an issue** opens a line linking to `/settings/connections` | | | Not offered |
| No connection, member | "Ask an admin to connect GitHub or Jira." | | | Not offered |
| Failing link | The link's words from `spec.md`, with last synced, by reader (FR44). Never colour alone | Retry only where it helps | Unchanged | Words only |

**Every time** goes through the zoned formatter (FR50). It shows the date when it is not today, and names the zone: "31 Oct, 14:02 London time". An alert line uses the message's own UTC form (`messages.py:394-395`).

**Lines after an action, each true to what the system knows** (`spec.md`, *Words after an action*):

| Act | Line |
|---|---|
| Take this, with a PagerDuty channel | "Yours. PagerDuty is not told: acknowledge the page there too." |
| Assign, `email_outcome = will_try` | "Assigned to Ravi. An email is on its way, so check they saw it if it is urgent." |
| Assign, `not_configured` | "Assigned to Ravi. Email is not set up here, so tell them yourself." |
| Assign, `rate_limited` | "Assigned to Ravi. Ravi was emailed about this case a moment ago." |
| Resolve, with a synced link | "Resolved. acme/api#412 will be closed when sync next runs. The link below shows when it has." |
| Resolve, the issue already closed (a hold) | "Resolved. acme/api#412 was already closed, so nothing changes there." |
| Resolve, with a PagerDuty channel | Adds "PagerDuty is not told, so a live page stays until the signal recovers." |

**Resolve** with no synced link and a closed signal is one tap. **Resolve with a synced link always confirms**, naming the issue: "This closes acme/api#412 and adds one comment there." It offers **Resolve and close the issue** and **Resolve here, leave the issue open** (D72). **When the issue is already closed (a hold), neither is true.** The confirmation says "acme/api#412 is already closed in GitHub. Nothing more goes there.", with one **Resolve** button, and no comment is owed. With a signal open it also says so, naming a later finding if that is the open one.

**The note box.** A visible label, FR14's sentence under it, `text-md` on a phone (iOS Safari zooms on focus below 16 px, by general knowledge, not tested here), and the count and validation tied with `aria-describedby`. A failed send keeps the draft. A note over 10 lines collapses, and a long pasted line wraps. Deleting a note uses `ConfirmPanel`: "The text is removed. The timeline keeps that a note was deleted."

**Reassign** opens an inline panel with a stacked `SelectField`, a *Nobody* option and an **Assign** button. Choosing alone does nothing, since a native select fires on a phone-picker slip (`SelectField.tsx:69`). It sends what the page last showed, for FR4's 409.

**The timeline.** Newest 20 rows at wide widths, 5 on a phone, both guesses. **Show earlier** loads the next 100, and focus moves to the first new row. A day heading whenever the date changes. **Copy as text** builds from what the reader may see, and carries notes.

**Staying fresh (FR45).** The page refreshes the finding and timeline every 30 seconds, a guess, on a visible page only, through `liveOptions`. That is about 4 requests a minute per open page, on the telemetry tier. One always-present status region sits at page level, outside `Controls`, because `Controls` returns null for a viewer (`FindingScreen.tsx:276-278`). It announces only changes of assignee, case state, signal state and link status. It never announces age words, or the actor's own act, which moves focus instead. A refresh never closes an open panel, takes focus, reorders rows under a panel, or clears a draft. Sync words change with age as `spec.md` says.

**Creating an issue** is an inline panel in the page's pattern: repository select (preselected from the mapping), then the text exactly as it will be written, then **Create issue in acme/api**. A public repository shows the refusal in place of the button. Jira always adds: "nexovue cannot tell who can read this Jira project. What you send here stays there." After creation: **Open acme/api#412 to assign it**, the sentence "An issue notifies nobody until someone is assigned", and **Copy my notes**, which only copies to the clipboard, so a person's text crosses under their own name and no code path of nexovue sends it.

**Case versus incident.** The Linked alerts section carries one line: "A case tracks work on a detected finding. A linked alert is a rule's own record, and cannot become a case." `features/alerts/IncidentScreen.tsx` carries the same.

### The feed (`features/findings/FindingsScreen.tsx`)

**The filter.** An inline `SelectField`, the console's shape for a filter above a list (`SelectField.tsx:16-18`), labelled *Show*: **All**, **Active cases**, **Assigned to me**, **Resolved cases**. Tabs were dropped: by rough arithmetic, four need about 397 px against about 343 px available at 375 px, and `Tabs` scrolls sideways with no hint (`Tabs.tsx:65-70`). The filter lives in the address through a new `validateSearch` on `findingsRoute` (`app/router.tsx:249-253`, which has none). A finding's back link carries it, and reads "Back to Assigned to me" (`FindingScreen.tsx:75-77`).

**With a case filter chosen,** the screen is a different read, so it behaves differently:

- It has its own query key, with no `project_id`, and waits only on that query. Today a failed telemetry summary or detection status fails the whole list (`FindingsScreen.tsx:62-64`), which at 3 a.m. would hide the case list even though the filters read no telemetry (NFR3).
- It hides the detection headline, the RED summary and the Muted link. It says "Across all your projects", and includes muted findings (FR22).
- The project selector does not apply, and the line says so.
- A token cannot use it this way. It must name a project it can read.
- Over 200 rows: "Showing the newest 200."

**Zero results** name the filter, with **Show all**: "No active cases in your organization." "Nothing is assigned to you." "No resolved cases are kept." Never the RED summary, since an empty filter says nothing about health.

**A case row** is the full statement shape, not a closed row's one quiet truncated line (`FindingsScreen.tsx:338-352`):

- title, not truncated;
- FR23's sentence at `text-sm` in `text-ink`;
- the case severity in words, which may be the worse joined one;
- the project name, in the case filters;
- no sparkline in the case filters (NFR3), and no "Acked" (D47);
- in the 14 days before an idle end, the warning;
- on *Active cases* and *Assigned to me*, **Resolve** on a row with no synced link (FR52). A row with a synced link has no such button, which is D47's concern.

On *All*, a closed finding with an active case keeps its row in the new tier, in this same shape. A joined finding shows "Part of a case", linked. The subtitle becomes "Anything still wrong or still being worked comes before anything finished." `FindingsScreen.tsx`'s comment "the feed has no filters" goes with this change.

At 375 px the feed has no sideways scroll, and no case sentence is truncated (AC108).

### `/settings/connections` (new, `features/settings/ConnectionsScreen.tsx`)

One rail entry in `shared/settings/settings-nav.ts`, in *This organization*, `requires: "manage_connections"`, keywords `github`, `jira`, `issues`, `tracker`. Hidden, not disabled, from others (F6's C10 convention). The screen repeats *What leaves this server* from the spec, at the top. Repository and mapping tables reflow to stacked rows at 375 px.

| State | What it shows |
|---|---|
| Hosted | "Tracker connections are not available on the hosted service yet." Nothing else |
| Connections off (`NEXOVUE_CONNECTIONS=no`) | "Tracker connections are turned off on this install. An operator can turn them on: see the setup page." |
| Empty | Two rows, GitHub and Jira, each saying what connecting does and what it does not ("Status only. Nothing is created without someone's click.") |
| Working | Connected App name, reach, "last synced", a test button, Replace credential, Disconnect |
| Failing | Since when, and "nexovue keeps trying" |
| Needs attention | What broke, and exactly what to do, in words. Also the console-wide banner (FR31) |
| Email off | "Failure emails cannot be sent on this install" |
| Test running | The button's pending state; the result arrives in place |
| Repositories | The list, each private or public. Public ones have **Allow**, with the warning naming what becomes public, and **Withdraw** once allowed |
| Mapping | Service to repository. Optional, so an empty table is not a warning |
| Replace credential | Upload a new App key or type a new Jira token. It re-seals in place, runs the test, and says every link resumes (FR43) |
| Dangerous | Disconnect, with real counts: "12 issues stop syncing. The issues stay. The App stays in your GitHub account until you delete it there. A backup may hold a sealed copy." |

**GitHub setup screens.** The start is its own full-page route (*Connecting GitHub*). One row per case. Each test names its row (AC124).

| Case | Words | The one action | Where the person lands |
|---|---|---|---|
| Starting | "You will go to GitHub to create a private App named nexovue-<slug>. You may need a GitHub organization owner." A question: "Can GitHub reach this server from the internet?" Default no | **Continue to GitHub** | github.com |
| Abandoned on GitHub | "Setup did not finish on GitHub." Shown when they return to settings with a setup still open | **Start again** | Start route |
| `state` wrong or reused | "This setup link is not valid. Nothing was changed." | **Start again** | Settings |
| `state` or code expired, past one hour | "This setup link has expired. Nothing was changed." | **Start again** | Settings |
| Session ended | The sign-in screen, then "Sign in finished, but setup must start again." | **Start again** | Settings |
| App made, not installed | "Now choose which repositories it may reach." | **Install on GitHub** | GitHub's install page |
| No repository chosen | "The App is installed, but reaches no repository." | **Choose repositories on GitHub** | GitHub |
| First test failed | The failure in words (FR44) | **Test again** | Settings |
| Setting up, returning later | The step they are on, one action, and **Stop setting up** | As the step | Settings |

**Jira setup screens** (subject to the founder's decision):

| Case | What it shows |
|---|---|
| Form | Site, email, token and optional expiry. No webhook secret. The token field is never read back, and shows only "saved". The exposure warning: "nexovue cannot tell who can read a Jira project. Anything sent stays there." |
| Confirm a project | For each project: the issue type, default Bug then Task, and the *done* and reopen statuses a resolve and reopen will use, read live and never stored. Nothing is used until an admin confirms (D66) |
| Required fields found | "This project requires <field key>, so nexovue cannot create issues in it. Make the field optional, or choose another issue type." (D57) |
| Webhook | Optional. The secret and address are shown once with `CopyButton`, the sentence "Copy this now. nexovue will not show it again", the filter text to use, and the existing unsaved-changes warning on leaving before copying is confirmed (AC80) |

**The banner.** `app/layout/AppShell.tsx` shows owners and admins, while any connection needs attention: "GitHub needs attention. Open settings." One line, in words, not dismissible, because the state is still true.

### Other screens

- **Member removal and account deletion** (`features/auth/account-sections.tsx`, the members screen, and the personal-data spec's wording): "Notes you wrote stay, in your own words, as your organization's record. Delete any you want gone before you leave." (BR16)

## Proposed design

### The record: what grows on `findings`

```
state              existing: is the signal still measured   open | closed
case_state         new: are people working                  NULL | investigating | resolved
case_resolution    new: how it ended                        NULL | resolved | false_alarm | new_normal
                                                            | tracker_closed | no_activity | recovered_untouched
assignee_id        new: composite FK to users
case_opened_at, case_resolved_at                            new timestamps
case_resolved_by   new: composite FK to users, who resolved it; NULL for the system or a tracker
case_activity_at   new: last person's act, or entry to investigating by any route
case_worked_at     new: first act beyond taking: a note, a link, a reassign, a reopen. NULL if none
assignee_acted_at  new: the assignee's last act, starting at the assignment
case_warned_at     new: when the 14-day warning started; NULL if none, or reset by activity
root_finding_id    new: the case this finding joined
```

Checks, in the style of `closed_says_how` (`models.py`):

- `(case_state IS NULL) = (case_opened_at IS NULL)`, and the same for `case_activity_at`
- `(case_state IS NOT DISTINCT FROM 'resolved') = (case_resolved_at IS NOT NULL)`, and the same for `case_resolution`
- `assignee_id IS NULL OR case_state IS NOT NULL`
- `case_state IS NULL OR root_finding_id IS NULL`: a finding holds a case or joins one, never both
- `root_finding_id IS DISTINCT FROM id`

Vocabulary goes in `nexovue_platform/detection.py`, next to `CLOSED_REASONS`, and `findings/schemas.py` spells it as literals, held equal by `test_findings.py` as today.

**The activity rule (D42, D74).** `case_activity_at` is set by a person's act, and by a case entering `investigating` by any route, including a tracker's reopen. A finding joining never touches it. So traffic cannot hold a case open, and a reopen in the tracker is not undone an hour later by the sweep. Holding a case open this way needs a write on a synced issue, which is not public by default.

### One active case per service

Two partial unique indexes, mirroring F6's two open indexes for the same reason, since Postgres treats nulls as distinct:

```sql
CREATE UNIQUE INDEX uq_findings_project_service_active_case ON findings (project_id, service_name)
    WHERE scope = 'service' AND case_state = 'investigating';
CREATE UNIQUE INDEX uq_findings_project_active_case ON findings (project_id)
    WHERE scope = 'project' AND case_state = 'investigating';
```

### Operations on a case

**Shared transitions.** Three processes must end or reopen a case: the API (`findings/cases.py`), `sync_connection` (`tracker_closed`, a tracker's reopen) and `cases_sweep` (`no_activity`, `recovered_untouched`, healing). The scheduler cannot import the API (`apps/scheduler/pyproject.toml:14`). So the transitions are SQL constants with parameter functions in a new `nexovue_platform/cases.py`, as `RESOLVE_FINDING_SQL` is (`detection.py:241,330`; `findings/queries.py:15-20`): `RESOLVE_CASE_SQL`, `REOPEN_CASE_SQL`, `DETACH_OPEN_JOINED_SQL`, `JOIN_OPEN_SQL`, `SET_WANTED_STATE_SQL`, `CLEAR_HOLD_SQL`, `RECORD_FINDING_EVENT_SQL`, `RECORD_CASE_AUDIT_SQL`. Both sides execute them, because the shared package may hold no database driver (`test_cross_service_contracts.py:265`). AC103 proves the three paths write identical rows.

**The API's operations** live in a new `findings/cases.py`. Each locks, does its work, writes its event and audit entry in that transaction (`audit/service.py`'s rule), and **commits before building the response**, so no ClickHouse read happens under a lock (AC102). Today's `acknowledge`, `mark_false_alarm` and `accept_new_normal` move to the same shape.

- `_root_of(row)` is the first step of each. If `root_finding_id` is set, it locks and returns the root.
- `start_case`, then if the index refuses (a concurrent start, or an existing case), answer with that case. It uses its own savepoint, the same pattern `deploys/service.py:99-107` shows until F28 rewrites that block, then joins. Starting on a closed finding also runs `JOIN_OPEN_SQL` for the service's open finding (FR11).
- `take` assigns the actor, and records an acknowledgement if the finding is open and unacknowledged. A note or a link starts a case with nobody assigned, and records no acknowledgement (D70).
- `assign` reads the new assignee `FOR SHARE`, so a concurrent demotion or removal waits, then re-checks: a working member of this organization. It sets `assignee_acted_at` to now, and sets `case_worked_at` when someone other than the assignee did it. **The 5-minute email limit (FR5, D91)** is read here, holding the root's lock: an `assigned` event on this root in the last 5 minutes, with this recipient in `detail.assignee_id` and an actor who is not the recipient, gives `email_outcome = rate_limited`, and no email.
- `acknowledge` (`service.py:168`) gains: if no case, start one with assignee = actor. If a case with no assignee, assign actor. Its refusal of a closed finding is unchanged. Its gate stays `MayOperateAlerts`, same roles.
- `resolve` runs `RESOLVE_CASE_SQL`, `DETACH_OPEN_JOINED_SQL`, and on a synced link `SET_WANTED_STATE_SQL` plus `comment_wanted`, unless the person chose to leave the issue open (D72), or the issue is already closed (a hold). Then W stays null and no comment is owed. Whatever resolves a case, `CLEAR_HOLD_SQL` nulls `tracker_closed_at` on its synced link in that transaction.
- `mark_false_alarm` and `accept_new_normal` gain: when this row is a root with an active case, resolve it. When joined, event only.
- `reopen` runs `REOPEN_CASE_SQL`, then `JOIN_OPEN_SQL` for the service, in one transaction. If the open finding holds its own case, the index refuses, and the person gets 409 (AC57).

**Clearing an assignee (FR4, FR8).** `members/service.py` `scrub` and `change_role` (when the new role is viewer) both call `clear_assignee`. It must not create a deadlock with the job, which takes many open findings' locks across a batch of 25 services (`SERVICES_PER_COMMIT`). So it selects that person's active cases `FOR NO KEY UPDATE SKIP LOCKED`, and clears and writes an event for each. A skipped row is healed by the next `cases_sweep`. Live tests cover all three routes.

### Locks

The reviewers' reading is correct, from the code and Postgres's lock rules. **Nothing was run.**

- A foreign key check takes `FOR KEY SHARE` on the row it points to. `FOR UPDATE` blocks that; `FOR NO KEY UPDATE` does not.
- So every lock on a finding in a case operation uses **`FOR NO KEY UPDATE`** (`with_for_update(key_share=True)`), since no key column changes. This replaces `queries.lock_finding`'s form for all person acts.
- **The deadlock this prevents.** The job locks an open joined finding J (`project_job.py:262-268,1069`), and in one transaction may close it and open a new finding N (`:1100-1145`), inside a batch that commits together (`:616`). N's foreign key needs a share of root R. A person resolving holds R, and needs J to detach it. With `FOR UPDATE`, each waits on the other and Postgres aborts one, which could be the whole detection batch, against NFR2.
- **Order among person acts:** root, then joined finding. The job never takes a blocking lock on a root it does not already hold as its service's open finding, and F6 allows one open finding per service, so a root that is open has no open joined finding.
- **A remaining wait, stated:** a person's resolve can wait on J until the job's batch commits, at most its 30-second hard limit (`PROJECT_HARD_LIMIT_SECONDS`). The job does not wait on the person across a call out, because the response is built after commit.

AC101 is a live test with a barrier: a person holds the root while the job closes J and opens N in one batch. Neither may block on the other or abort.

### Joining

A new finding joins at birth, inside `_OPEN`:

```sql
root_finding_id = (SELECT id FROM findings active
                   WHERE active.org_id = :org_id AND active.project_id = :project_id
                     AND active.scope = :scope
                     AND active.service_name IS NOT DISTINCT FROM :service_name
                     AND active.case_state = 'investigating'
                     AND active.case_activity_at > now() - interval '7 days')
```

The unique indexes say this returns at most one row. `_OPEN` has one text and two callers (`project_job.py:910,1430`), so this is one change. A finding past that window opens standalone, and its page names the quiet case. **Taking it joins that case** (AC68), and the act resets the clock.

**The races.** A person starts a case on an earlier closed finding at the moment the job inserts a new one, or resolves as the job inserts. Each is healed by `cases_sweep`, with the same 7-day condition: an open finding with a null root, no case of its own and an active case within the window joins; an open finding on a resolved root leaves it (AC17).

**Detach.** Whatever resolves a case, in that transaction, `DETACH_OPEN_JOINED_SQL` sets `root_finding_id = NULL` on open joined findings, with a `finding_left_case` event (FR6). Closed joined findings stay attached, for the timeline.

### Timeline events from the job

**Not inside `notify`.** `finding_opened` returns before the flap guard on a muted finding, and the guard writes `flapping` (`notify.py:213-215,408-420`). AC23 needs one event for five muted openings, with that column unchanged. So events are written from `project_job`, next to the notify calls (`:925,:945,:1116,:1139,:1372`), by executing `RECORD_FINDING_EVENT_SQL`. Notification behaviour stays identical (AC64).

**A read-only spell check.** `detection/events.py` has `spell_state(connection, keys, at)`, returning `no`, `starts` or `still`. It uses the existing `_OPENINGS_SINCE` and `_LAST_FINDING` statements, plus one new read for a recent `flapping_started` event on the service, and never `_MARK_FLAPPING`. It uses the same `FLAP_OPENINGS`, `FLAP_WINDOW` and `FLAP_QUIET`. It runs only when a finding opens, not on every minute judged, so it adds about three short reads to a rare path. A closing finding writes an event only if its opening did, which needs one `EXISTS`.

`FINDING_TOLD_KINDS` (`detection.py:234`) is untouched. No case event is a notification.

### Notes

`finding_notes` holds `author_id` (kept when a member is scrubbed, read as *Former member* through `person_name`), `body`, `created_at`. Always on the root. A delete is a real `DELETE`, in a transaction with its `note_deleted` event, because a pasted secret must actually go. **It remains in Postgres backups until they age out**, which the docs must say. Backups were not traced further.

Who may delete another's note is a new action, `DELETE_ANY_CASE_NOTE` (owner, admin). An author's own delete goes through `CurrentIdentity` plus a service check, whatever their current role. A support view is refused by method already (`_hold_support_view`).

### The timeline

`finding_events` is append-only: `id`, `org_id`, `finding_id` (where it happened), `kind`, `actor_kind` (`person`, `system`, `tracker`), `actor_id`, `detail` JSONB, `at`.

The query reads the root and its joined findings:

```sql
WHERE org_id = :org_id AND (finding_id = :root OR finding_id IN
      (SELECT id FROM findings WHERE org_id = :org_id AND root_finding_id = :root))
  AND (at, id) < (:before_at, :before_id)
ORDER BY at DESC, id DESC LIMIT 100
```

**No free text, by construction.** The writer is a SQL constant and a parameter function, both in `nexovue_platform/cases.py`, as `RESOLVE_FINDING_SQL` is. The foundation and other features now use these names too. Each caller executes `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, which takes a kind and named, typed keyword arguments, and refuses anything else. There is no `detail: dict` parameter. Allowed values: enums, UUIDs, numbers, and a link id. A note's words are read by joining `note_id`. GitHub's `state_reason` is stored as an enum, `completed`, `not_planned`, `duplicate` or `other`, and any value not recognised reads `other`.

Vocabulary is frozen in a migration and in `FINDING_EVENT_KINDS`, as the alert vocabulary is. **`explained` is in F22's frozen list from the start** (the spec's *Dependencies*), and T2.9 adds its writer to F3's `evidence/gather.py`. F5 adds `fix_proposed` and `fix_pull_request_opened` with its own migration.

### Keeping and ending

`_PAST_DERIVED_RETENTION` (`retention.py:30-37`) becomes:

```sql
state = 'closed'
AND closed_at < now() - make_interval(days => (SELECT min(derived_retention_days) FROM plans))
AND GREATEST(closed_at, case_resolved_at) < now() - make_interval(days => (<the project's retention>))
AND case_state IS DISTINCT FROM 'investigating'
```

- The first clause stays first and unchanged, so `ix_findings_closed_at` still serves it. `closed_at` is always at or before the later date, so no row the sweep should delete is missed.
- **A closed joined finding keeps its own clock, even while its root is active** (FR24, D43). There is no clause on its root, so one case cannot pin an unbounded set of rows.
- The batch loop stops when a pass deletes under 1,000 (`expiry.py`), and rows being kept do not match, so the loop still ends.
- `root_finding_id` is `SET NULL`, so batch order does not matter: a root can go before its joined findings, and they stand alone.
- `retention.py`'s docstring must state the new rule and its reason.

**`cases_sweep`,** a new task on the `light` queue every 5 minutes (a guess), in new module `nexovue_scheduler/cases.py`. It must run with no connection and no tracker, so it does not live in `connections`. It holds no tracker key. Its first read in each step crosses organizations by nature, so it is a named exception in the tenancy test, and each follow-on statement binds `org_id` from the row. Each case is its own transaction, locked `FOR NO KEY UPDATE SKIP LOCKED`. It:

1. **starts the idle warning** at 76 days: sets `case_warned_at`, adds a `finding_emails_owed` row of kind `idle_warning`, and on a synced link sets `comment_wanted = idle_warning` and `next_sync_at = now()`;
2. **resolves idle cases**, with `case_activity_at` over 90 days old, reason `no_activity`, setting no `wanted_state`;
3. **resolves untouched cases** (FR49): `case_worked_at` null, no link, root and every joined finding closed, the latest close over 72 hours ago (a guess), reason `recovered_untouched`. This is silence ending it, not recovery. No tracker can be involved, because a link would have set `case_worked_at`;
4. **heals** missed joins, findings left on a resolved root, and assignees who are removed or now viewers;
5. **sends what is owed.** Connection emails, once each, to verified owners and admins through a new `_VERIFIED_ADMINS` (`failure_told_at`, `expiry_told_at`). Case emails from `finding_emails_owed`, one per row, to the assignee, or to owners and admins if nobody is assigned, deleting the row and committing before the send, so a crash loses an email and cannot repeat one. A row per email means a second one owed before this sweep cannot overwrite the first (D90). So a tracker notice can be up to 5 minutes late, plus the sync's own delay. It runs here because `scheduler-light` holds the mail settings and `scheduler-connections` does not.

Any act resets `case_warned_at`, so a warning ends when someone acts. The issue comment is not repeated (D75).

### Connections

`connections` holds one row per organization and kind. `connection_repositories` holds what a connection reaches: a repository, or a Jira project. `service_repositories` maps a project and service to one of those. F5's App is a row of `kind = 'github_fix'` (`ai-solution/design.md:466`), which F22's sweeps ignore for issue links. The hourly repository refresh takes a connection, not a kind, as F5 asks.

**The seam.** A small `Tracker` protocol in `nexovue_scheduler/connections/trackers.py`, with `GitHubTracker` and `JiraTracker`. A base address is a constructor argument only (AC89):

```python
class Tracker(Protocol):
    def read(self, link: LinkKey) -> TrackerRead: ...  # status, close reason, moved, gone
    def find_existing(self, place: PlaceKey, finding_url: str) -> IssueRef | None: ...
    def create(self, place: PlaceKey, text: IssueText) -> IssueRef: ...
    def set_status(self, link: LinkKey, wanted: Status, why: Why) -> WriteResult: ...
    def comment(self, link: LinkKey, text: CommentText) -> WriteResult: ...
    def is_public(self, place: PlaceKey) -> bool: ...
    def check(self) -> CheckResult: ...
```

Provider differences stop inside it, as the foundation's AI interface does. A third tracker later is a third class. All HTTP goes through `probes/http_check.perform`, with `follow_redirects=False` and `private_allowed` from the development setting only. It takes any method, so `PATCH` and `PUT` work.

**A built address, not a stored one.** `finding_links` has no `url` column. A Postgres check cannot read another row, so it could not hold a stored address to the connection's site. `nexovue_platform.connections.link_url(kind, site, external_key)` builds the link at read time: `https://github.com/<owner>/<repo>/issues/<n>`, or `https://<site>/browse/<key>`, after `external_key` matches its strict pattern. An address an outside system chose can never reach a colleague's click.

### Connecting GitHub

Each connection owns its own private GitHub App, per FD11. Verified against GitHub's manifest page on 2026-11-02: the three steps, a code valid for an hour, and that the conversion returns `id`, `pem` and `webhook_secret`.

1. **The start route.** Every console page has `form-action 'self'` (`Caddyfile:83`), so the browser refuses a post to github.com. A new console route, `/settings/connections/github/start`, loaded as a **full page** (a browser applies a page's policy to the document, so in-app navigation would not change it), gets its own header in the Caddyfile: the same policy with `form-action 'self' https://github.com`. Every other route keeps `'self'` (D54, AC73).
2. **The manifest.** The page calls `POST /connections/github/manifest`, which creates the connection row, stores `setup_state_hash`, `setup_expires_at` and `setup_user_id`, and returns the manifest and the raw `state`. The page draws a form with one button, posting to `github.com/settings/apps/new`, or the organization's form. `public: false`, `default_permissions: {issues: write, metadata: read}`, `default_events: [issues]`, `hook_attributes.url` set to this connection's webhook, `redirect_url` and `setup_url` to console routes.
3. **The return.** GitHub redirects to the console with `code` and `state`, and the console posts them to `/github/manifest/complete`. Writing on `POST`, not a `GET` callback, keeps the route under `_hold_support_view` and the coverage test. The API compares the state's hash, the session's user and the expiry, seals the code into `sealed_manifest_code`, and adds a job with ids only. **The code is never in a job row or a log** (AC74).
4. **The conversion job** opens the code, converts it, seals `pem` and `webhook_secret`, and nulls `sealed_manifest_code` in that transaction. `client_id` and `client_secret` are discarded: F22 uses no user sign-in, and F5 keeps those for its own App (`ai-solution/design.md:466`).
5. The screen sends the person to the App's install page, where they choose repositories. GitHub returns them to `setup_url`.
6. **The redirect's `installation_id` is never read.** A job signs in as the App and lists that App's own installations. A private App can be installed only on its owner's account, so whatever it finds is the customer's own. Anyone forging the redirect only causes that list to be read again. **The hosted service, if approved, shares one public App and must use GitHub's user-token check instead.** No code path may assume either: `connections.github_app_source` is `own`, and hosted is not built.

A setup past its hour does not block a new one: the unique index on `(org_id, kind)` excludes an expired `setting_up` row by the API first marking it `disconnected` (AC76).

Whether an App receives `installation` and `installation_repositories` events without asking for them **was not confirmed**. T3.0 checks. An hourly refresh covers it either way.

**An install GitHub cannot reach.** If the person says the server is not reachable, `hook_attributes.active` is `false`, so GitHub sends nothing. Polling carries everything. Whether this works from a private address is untried, and T3.0 runs it **through the real console route behind Caddy**, including the redirect back, since a form rule can also apply to a redirect after a form post.

**Who signs in, and where.** The JWT and token minting live in `nexovue_scheduler/connections/github.py`, with PyJWT in `apps/scheduler/pyproject.toml`. F5 relies on this (`ai-solution/design.md:276`), and the API holds no GitHub token. RS256 needs PyJWT's crypto extra, by general knowledge; check it when building.

**Tokens.** Each sync run mints an installation token narrowed to one repository, with `issues: write` and `metadata: read`, held in memory for that run, never stored. At the end of a run it tries to revoke the token (T4.8 confirms that GitHub route). GitHub's permissions page confirms `POST` and `PATCH` issue and `POST` comment need Issues write, `GET` needs read, and repository read needs metadata read. Its "additional permissions" mark on `PATCH` needs a real test (T4.8).

### Connecting Jira

**Subject to founder decision 1, and built only on a written yes from Atlassian** (spec, item 1, rechecked 2026-11-02). **Credential, by D98:** a service account's OAuth 2.0 credential (client id and secret, `grant_type=client_credentials` at `auth.atlassian.com/oauth/token`, hour-long access tokens minted per run and never stored, calls to `api.atlassian.com/ex/jira/{cloudId}`). The token form below is the fallback only if Atlassian's written answer allows a token and not that credential. A form holds site, email, API token and optional expiry date. The site must fully match `^[a-z0-9][a-z0-9-]*\.atlassian\.net$`. A scoped service-account token goes through `api.atlassian.com/ex/jira/{cloudId}`; a classic one to the site itself. Those are the only hosts that exist in this code. How to read `cloudId` was not checked.

**The email and token are sealed together** in `sealed_credential`, as one value, and no route ever returns them. So a person's address is not left in plain text where `scrub` and the export cannot reach it. The test reports whether the owner is a service account or a person, **only if T7.0 finds a way to tell. Otherwise it says this is not known** (FR29).

The API seals the token. A job runs the test, then lists projects, and stores each project's **key** under its strict pattern, not its name, which is free text. For each project, the job reads create metadata, with no test issue written (D57), and the transitions. The admin confirms the issue type (default Bug, then Task) and the *done* and reopen transitions (D66). Status and field names are read live for this screen and never stored. Transition ids are stored after they match `^[0-9]+$`.

**A webhook is optional.** nexovue generates the secret and shows it once (AC80), so it cannot be weak. Jira signs with `X-Hub-Signature: sha256=…` (its webhooks page, 2026-11-02), so `signature_matches` works as it stands. The header is chosen from `connections.kind`, never from the request. The screen tells the admin to filter on `labels = nexovue`, because an empty filter matches every issue in every project.

### Issue text: outbound escaping

`nexovue_platform/connections.py` holds a pure `issue_text(facts)`, used by the job to write and by the API to preview. Facts are a typed value with no field for `title`, an endpoint, notes or model output, which is how AC42 is held. **On the click, the facts are frozen on the link row (`frozen_facts`), holding only numbers, enums and the strict name,** and the job writes from that copy, so a severity change while sync was stopped cannot make the issue differ from the preview. The copy is cleared when the link leaves `creating`.

| Part | Rule |
|---|---|
| `strict_name()` | **One function, for title and body alike.** A value that looks like a person's data becomes the fixed words `a service`: it contains `@` with text on both sides, or a run of 6 or more digits (a guess). Otherwise letters, digits and `_ / -` only; anything else becomes `_`, including `.` and spaces; cut to 64 |
| Title | `nexovue: <signal words> on <strict name>` |
| GitHub body | Fixed sentences and numbers. The strict name in a code span |
| Jira body | ADF. A plain text node with the code mark. A node type outside `doc`, `paragraph` and `text` is never produced |
| Link back | Built by nexovue: `APP_BASE_URL/findings/<id>`. Also the marker for finding a half-made issue |
| Label | `nexovue` on Jira, and on GitHub where the App is permitted. A dropped label changes nothing else |
| Comment | One fixed sentence per value of `comment_wanted`, no person's name. Idle warning: "nexovue will end its case for this issue on <date> if nobody acts in nexovue. The issue stays open." |

**A title has no code span.** A strict name such as `GH-12`, a 40-character hexadecimal value, or a repository-configured shape such as `OPS-12` might still become a link on GitHub (general knowledge, not tested). AC41 includes them, and T4.8 renders them. If any links, the service name leaves the title and stays only in the body's code span.

Left out: `comparison`, because it is composed text; `likely_cause`, for now; the endpoint, always (D48).

**Any change to `issue_text()` repeats the real render check.** T4.8's script is the procedure, and `status.md` records its date.

**AI safety as it applies.** F22 holds no model, so it needs no prompt-injection suite of its own for a model link. It does have AC41's suite, in the gate, because someone's AI reading the issue later is the next hop (foundation U5), and telemetry is attacker-controlled text. A name stays someone's words: `ignore_previous_instructions_and_merge` is still readable, and the spec says so rather than claiming otherwise. Notes and any tracker text count as free text wherever F4 reads them (foundation section 3), and F4 owns that suite. The process holding tracker tokens runs no model (FD1), enforced by AC34.

## Data flow and interfaces

### Roles, queues and the task set

```
scheduler-connections:  worker --queues connections,connections-interactive --concurrency 3 --tasks connections
```

| Queue | Holds | Cap |
|---|---|---|
| `connections` | `sync_connection`, and F5's `poll_fix_pull_requests` and `sweep_fixes` (`ai-solution/design.md:549-550`) | **2** of the 3 slots, in `QUEUE_CAPS` (`worker.py:72`) |
| `connections-interactive` | Connection tests, conversion and binding jobs, `sweep_connections`, and F5's person-started jobs (`ai-solution/design.md:541-548`) | None |

This is F5's layout (its D40), so one queue layout holds across both features. Queue names and the cap are constants in `nexovue_platform/connections.py`, as F5 asks (`ai-solution/design.md:553`). A worker claims capped queues first, each up to its room (`worker.py:262-270`), so sync can never take the third slot.

So with both trackers hanging, a test and a sweep still run (AC86). **Two stated limits.** F5's slow person-started jobs share `connections-interactive`, and three of them at once could hold every slot, stopping sync and `sweep_connections` until one ends. They are person-made and few. F5 must measure them and, if needed, raise the concurrency, which raises the ceiling. And F5's two sweeps share the capped queue with sync, so they can wait behind a hung sync for up to its 270-second limit. Guesses.

`SCHEDULER_ROLE` gains a third value, `connections` (`ai-insights/design.md:641`), and `TASK_SETS` a third entry with its own preload module, `jobs/preload_connections.py`. `roles.py` gains the role. The forkserver's `sys.modules` is tested to hold no `clickhouse_connect`, `confluent_kafka` or `boto3`, as F3's test does. The narrow set keeps ClickHouse and object store code out of the role holding repository access. F5's connection modules join this set rather than `TASK_MODULES` (`registry.py:19-30`). **If F3 has not merged, T3.2 builds `SCHEDULER_ROLE`, `--tasks` and `TASK_SETS` itself.**

### The job

```
beat, every 30 s (a guess)    sweep_connections, queue connections-interactive
                              only if a live connection exists
                              for each connection with a due link or a due refresh:
                              one sync_connection job by conditional insert, expiring in 60 s,
                              with connection_id and org_id from the row
                                     │
person's click ───► API writes link or wanted_state, next_sync_at = now,
                    adds sync_connection by conditional insert, wakes workers
                                     │
tracker webhook ──► API verifies, sets next_sync_at = now on matching links. No job
                                     ▼
              sync_connection(connection_id, org_id)    in scheduler-connections
              0. every read binds both; no row means stop, never a search by id alone
              1. take connections.sync_lease_until by compare-and-set; if held, stop
              2. if paused_until is ahead, stop
              3. take up to 50 due links, oldest first
              4. per link: creating or promoting → that step; otherwise read, compare, act
              5. before each call out, read connections.state with no lock; if not working, stop
              6. commit per link, set next_sync_at, keep pace
              7. a timeout, connection failure or 5xx ends the run and sets paused_until
              8. try to revoke the run's token
```

Job arguments carry ids only (`jobs.py`), and every connection job's include `org_id`, as F5's do (`ai-solution/design.md:541`), so its first read can pass the tenancy test (AC129). The task and queue names are in `nexovue_platform/connections.py`, so the API adds jobs by name and imports no scheduler code (`channels/send_test.py:45`).

**Two jobs for one connection.** Two conditional inserts at the same instant can both succeed. D50 accepts that, because the lease stops the second at step 1. AC48 is written to that promise.

Limits: soft 240 s, hard 270 s, a guess. A run that hits its soft limit stops cleanly (`SoftTimeLimit`), and the rest stay due.

**No lock is held across a call out.** Read link and finding, unlocked. Make the call. Then in a transaction, lock the finding `FOR NO KEY UPDATE`, then the link, re-read and apply.

**The disconnect window.** Step 5 stops a running job before its next write (AC37). One remaining window is stated: a call already on the wire can land, and a minted token stays valid until it expires, by GitHub's documented one-hour life, unless revoked.

**The poll schedule (D49)** is one function of case state. Active: 5 minutes. Resolved under 30 days: hourly. Over 30 days: `not_followed`, `next_sync_at = NULL`. A webhook's update excludes `not_followed` and `stopped` links.

### The comparison

Each synced link stores `seen_state` (the tracker's status last read) and `wanted_state` (set when a person's act needs the tracker to change; null when nothing is pending). When a person's act wants what was last seen, `wanted_state` is set back to null.

Let T be what the tracker says now, S `seen_state`, W `wanted_state`.

| # | Situation | What it means | Action |
|---|---|---|---|
| 1 | T = S, W null | Nothing changed | Nothing, unless row 5b applies |
| 2 | T = S, W ≠ T | A person changed the case | Write W to the tracker. On success S := W, W := null |
| 3 | T ≠ S, W = T | Our earlier write landed, and a crash lost the record, or both agreed | S := T, W := null. No case change |
| 4 | T = open, S = closed, W null, case resolved within 30 days | Someone reopened it there | `REOPEN_CASE_SQL`, setting `case_activity_at`. S := T. Event, source tracker |
| 4b | T = open, S = closed, W null, case active, `tracker_closed_at` set | Someone reopened it there during a hold | S := T. `CLEAR_HOLD_SQL`. Event, source tracker. `case_activity_at` unchanged, because the case never left `investigating` (D92). No email. The page's "Issue closed" line reads `tracker_closed_at`, so it stops |
| 5a | T = closed, S = open, W null, case active, **case signal open** | Someone closed it there, and the problem is still measured | **Case stays** (D73). S := T. `tracker_closed_at` set. Event with the close reason. Owes an email of kind `tracker_closed_signal_open` |
| 5b | `tracker_closed_at` set, T = closed, case active, **case signal now closed** | The hold is over | `RESOLVE_CASE_SQL` with `tracker_closed`. `CLEAR_HOLD_SQL`. Owes an email of kind `tracker_closed` |
| 5c | T = closed, S = open, W null, case active, case signal closed | Someone closed it there | `RESOLVE_CASE_SQL` with `tracker_closed`, including the detach. S := T. Event with the close reason. Owes an email of kind `tracker_closed` |
| 6 | Case already matches, in 4 or 5, with `tracker_closed_at` null | A case resolved in nexovue with W null (`no_activity`, leave-open), then closed there | S := T. No event |
| 7 | T = reopened, another case active on the service | The index would refuse | Case stays. S := T. Link `needs_attention`. Event says why |
| 8 | Read says deleted (410) | | Link `gone`. Case unchanged. Stops polling |
| 9 | Read says 404, repository still readable | | Link `gone`. Case unchanged. Read again daily for 7 days (a guess) |
| 10 | Read says 404, repository no longer readable | | Links `needs_attention`, place `is_reachable = false`. Resume when access returns |
| 11 | Read says moved (301) | A signal only | Read the target through the guard, and take repository and number from the JSON (shape from general knowledge; T4.8 copies a real response). In a connected repository: store the new `external_key` and place. Otherwise `needs_attention`, naming that it moved out of reach. Case unchanged |

**A comment is written after its status, and at most once.** Close, commit S and W, then commit `comment_wanted = NULL` with `comment_written_at`, then post. A crash between leaves zero comments and a correct status, and a retry cannot post twice (AC87). A kill on the wire can also lose it. That is accepted, because "at most one" is the promise. The sweep's idle warning uses the same column, with no status write.

With two values, T ≠ S and W ≠ T together mean W = S, which is not stored, so the conflict in the research cannot arise on status. A write the tracker refuses for lasting reasons leaves W set, marks `needs_attention`, adds one event, and stops retrying until a person retries.

Running twice changes nothing: the second pass lands on row 1. A new developer can read the sync in this one table and one function. AC45 tests each row.

### Creating without duplicates

The click writes the link row first, `creating`, with `frozen_facts`. It is unique per finding for synced links not `stopped`, so a double click gets 409. Only the click route may produce `creating` (AC88).

```
every try:            read the allowance, then call is_public, immediately before creating.
                      Public and not allowed → refused. A failed or unclear answer → waiting, no create
creating, first try:  create → store external_key, S := open, in_sync
creating, a retry:    find_existing first
                      GitHub: list issues by creator = the App's bot, since the first try, state=all,
                              then look for the exact link back in the body
                      Jira:   JQL for project and creator since the first try, then the same check
                      found → adopt it. Not found → the checks above, then create
promoting:            the same public check, then read the issue's status. in_sync or refused
```

- **`first_attempt_at` is written and committed before the first create.** So a retry always knows to look.
- **The public check runs before every create, including retries** (BR12, AC51). A retry can run long after the click, and an issue cannot be taken back.
- **GitHub's list is a read of the issues themselves, not its search index,** and its page confirms `creator`, `since` and `state=all`. T4.8 tries a create followed by an immediate list.
- **Jira's search may lag behind a write.** The replacement route, `/rest/api/3/search/jql`, is believed to be eventually consistent: this was **not confirmed**. So a Jira retry waits at least 2 minutes, a guess, through an injectable delay, and T7.0 checks.
- **Someone editing the link back out in those two minutes** could cause a duplicate. Accepted as very unlikely, since nobody has been told the issue exists.

**Status mapping.** On GitHub, `state` and `state_reason`: `completed` for a person's resolve or new normal, `not_planned` for a false alarm. On Jira: the admin's confirmed *done* and reopen transitions, checked to still be offered through `transitions?expand=transitions.fields`. The first-match rule is gone, because it could write "Declined" on a developer's issue. Jira's category keys came from third parties, and **T7.0 confirms them**. A missing transition means row 2 cannot finish: `needs_attention`, and the case stays resolved (BR5).

### Test harness for sync (AC44, AC45, AC89)

`recording_site` always sends a full reply, and routes by path alone (`recording_site.py:83-104`), so it cannot do what these tests need.

- **`apps/scheduler/tests/tracker_fake.py`, new, built on `recording_site`.** A stateful fake GitHub and Jira, with routing by method, a stated search delay, and a **commit-then-drop** reply: apply the write, then close the connection with no reply.
- **The seam.** `connections/tasks.py` gets its tracker from one `tracker_for(connection)` function, which reads the base address from a module constant. A test run replaces it, in the forked child, through `tests/tracker_harness.py`. AC89's test proves that no setting or environment variable reaches it, and that production code never imports the harness.
- **Named fault points.** `before_first_attempt_commit`, `before_create`, `after_tracker_commit_before_reply`, `after_reply_before_commit`, `after_close_before_comment_commit`, `after_comment_commit_before_post`. The child is killed with `SIGKILL` at each.
- **Injected time.** The Jira retry wait and the backoff take a clock argument, so a kill-point test does not take minutes.

### API, under `/api/v1`

| Route | Gate | What |
|---|---|---|
| `GET /findings?case=` | `CurrentReader` | See below |
| `PATCH /findings/{id}/case` | `MayOperateCases` | Body: `case_state?`, `assignee_id?` (with explicit null), `expected_assignee_id?`, `expected_case_state?`, `leave_issue_open?`. Starts, assigns, resolves, reopens. Response carries `email_outcome`: `will_try`, `not_configured`, `rate_limited` or `none` |
| `GET /findings/{id}/timeline?before=` | `CurrentReader` | Real persons get all. Tokens get kinds and times. Support view gets no note bodies |
| `POST /findings/{id}/notes` | `MayOperateCases` | Body: `body` |
| `DELETE /findings/{id}/notes/{note_id}` | **`CurrentIdentity`**, then the author or `DELETE_ANY_CASE_NOTE` in the service | In `EXEMPT`, reason "a person may withdraw their own words" (AC25). AC9 excepts it |
| `GET /findings/{id}/links/preview?place_id=` | `MayOperateCases` | The exact text. No network |
| `POST /findings/{id}/links` | `MayOperateCases` | Either `{place_id}` to create, or `{url}` to attach a reference |
| `POST /findings/{id}/links/{link_id}/promote`, `/retry` | `MayOperateCases` | |
| `DELETE /findings/{id}/links/{link_id}` | `MayOperateCases` | Removes the link here. The issue stays |
| `GET /connections` | `CurrentIdentity` | Kinds, states and `connections_available`, so a member's page can say what is possible. **A member or viewer also gets repository names for the picker. A support view gets kinds and states only, with no `jira_site` or repository names.** Never a credential |
| `POST /connections/github/manifest`, `/github/manifest/complete`, `/github/refresh` | `MayManageConnections` | Refused with the hosted or off sentence where they apply (AC136, AC118) |
| `POST /connections/jira` | `MayManageConnections` | |
| `POST /connections/{id}/credential` | `MayManageConnections` | FR43: re-seals in place, runs the test, keeps every link |
| `DELETE /connections/{id}` | `MayManageConnections` | Calls a disconnect hook, which F5 registers |
| `POST /connection-tests`, `GET /connection-tests/{request_id}` | `MayManageConnections` | As channel send tests. Own rate tier |
| `PATCH /connections/{id}/places/{place_id}` | `MayManageConnections` | Allow or withdraw a public repository; issue type; Jira transitions |
| `GET`, `PUT`, `DELETE /service-repositories` | Read: person. Write: `MayManageConnections` | |
| `POST /connections/webhook/{connection_id}` | None. HMAC | In `EXEMPT` with its reason, as `receive_deploy` is |

**The feed route.** Today `project_id` is required, and `queries.feed` binds it (`findings/router.py:41-54`, `queries.py:58-74`). `case` is `"active" | "mine" | "resolved" | None`. With `case` set, for a person, `project_id` is not used, and a **separate statement** runs, bound to `org_id`, joining `projects` for the name and `users` for the assignee, returning roots only and including muted. For a token: `project_id` is required, `case=mine` is 422, a project it cannot read is the same 404 as `get_finding`, and a token with no `project_id` is 422.

**The rank, in one expression, shared by the page, the feed and AC27:**

```python
open_joined = exists(
    select(1).where(
        joined.org_id == Finding.org_id,
        joined.root_finding_id == Finding.id,
        joined.state == "open",
    )
)
case_rank = case(
    ((Finding.state == "open") & Finding.acknowledged_at.is_(None), 0),
    (open_joined & (Finding.case_state == "investigating"), 0),
    (Finding.state == "open", 1),
    (Finding.case_state == "investigating", 2),
    else_=3,
)
```

`case_severity` is the worse of the row's severity and any open joined finding's. This is a subquery per row, not "one more CASE branch", and it is served by the partial index below. `orderFindings` applies the same rule from `case_signal` and `case_severity`.

**The fields.** `Finding` gains `case_state`, `case_opened_at`, `case_resolved_at`, `case_resolution`, `root_finding_id`, `case_signal`, `open_joined_finding_id`, `open_joined_since`, `case_severity`. `FindingForMember` gains `assignee_id`, `assignee_name`, `case_taken_at`, `assignee_acted_since_join`, `case_resolved_by_name`, `project_name`, `links`, `earlier_case`, `case_ends_on`, `deletes_on`, `joined_kept_count`, `project_has_pagerduty`, `email_available`. Shapes are held in `test_findings.py`.

**Who counts as a person.** `reader.is_real_person` is `is_person and identity.impersonation is None`, using F3's `Reader.is_person`. A ticket from F4 has no identity, and this form never reads it. Today `for_member = not reader.is_token` is true in a support view (`findings/router.py:90`, `auth/dependencies.py:65`). The same helper decides notes, the timeline and the copy source (FR20, AC69).

**Rate tiers.** Because all the `/findings` routes share the telemetry tier by prefix (`policy.py:234`), the new `/findings/...` writes fall on that tier. Each creation is further capped by a person's click, plus a refusal at 20 `creating` links per connection, a guess. The console's calls on this page rise by one, the timeline, against `LARGEST_SCREEN_FANOUT = 8`.

### Webhook handling

```
read at most 1 MiB, streaming (`_bounded_body`'s pattern)   → 413-style refusal if over
load the connection by id (`live_webhook_connection`, in `EXEMPT`); not found, disconnected, or
  with no secret                                             → same refusal as a bad signature
open the sealed webhook secret, check the signature          → refusal
  (the header is chosen from connections.kind)
parse just enough: GitHub repository id and issue number;
                   Jira issue id                             → unknown shape: 200, nothing done
UPDATE finding_links SET next_sync_at = now()
  WHERE org_id AND connection_id AND external_id
    AND sync_status NOT IN ('not_followed', 'stopped')
    AND (last read over 30 s ago, so a replay cannot cost more than 2 reads a minute)
a `pull_request` event goes to a handler F5 registers; F22 registers none
answer 200
```

GitHub asks for an answer within 10 seconds. This is two Postgres statements. **Nothing from the body sets a case state, and no job is added** (D50). It is never stored. The next beat's sweep finds the due link, so the change arrives within about 60 seconds. The route has its own tier, `connection-webhook`, 120 a minute, a guess, on the prefix `/api/v1/connections/webhook/`, placed before `/connections` rules, like `WEBHOOK_PREFIX`.

`middleware/cross_origin.py:11` already lets a request with no session cookie through, so a webhook needs nothing there.

## Data and migration impact

One Postgres migration per step, numbered after whatever exists then (0050 today, and F28 and F3 add theirs). No ClickHouse change. Nexovue is pre-deployment, so there is no backfill and no old data to keep.

| Table | Key columns | Notes |
|---|---|---|
| `users` (grows) | `UNIQUE (org_id, id)` | So other tables can key on both. A person is in one organization (`models.py:222-224`). T1.2 checks this against the deletion order at `organizations/tables.py:32-52` |
| `findings` (grows) | See above. `assignee_id` and `case_resolved_by`: FK `(org_id, column)` to users, `ON DELETE SET NULL (column)`. `root_finding_id`: FK `(org_id, project_id, root_finding_id)`, `ON DELETE SET NULL (root_finding_id)` | Also `UNIQUE (org_id, id)` and `UNIQUE (org_id, project_id, id)`, so a root in another project is refused too |
| `finding_events` | PK `id`; FK `(org_id, finding_id)` CASCADE; FK `(org_id, actor_id)` SET NULL (actor_id) | Index `(org_id, finding_id, at DESC, id DESC)`. Frozen `kind` check |
| `finding_emails_owed` | PK `id`; FK `(org_id, finding_id)` CASCADE; `kind` (`tracker_closed`, `tracker_closed_signal_open`, `idle_warning`); `at` | Index `(at)` for the sweep. A row is deleted before its email is sent. It holds no name, address or text (D90) |
| `finding_notes` | PK `id`; FK `(org_id, finding_id)` CASCADE; FK `(org_id, author_id)` SET NULL (author_id); `body` text with a length check | Index `(org_id, finding_id, created_at)`. Also `(org_id, author_id)` for the export |
| `finding_links` | PK `id`; FK `(org_id, finding_id)` CASCADE; FK `(org_id, connection_id)` SET NULL (connection_id); FK `(org_id, place_id)` SET NULL (place_id); `role` (`synced`, `reference`); `sync_status` (`creating`, `promoting`, `in_sync`, `waiting`, `needs_attention`, `refused`, `gone`, `not_followed`, `stopped`); `external_id`; `external_key` (`acme/api#412`, `OPS-12`), with a strict pattern check; `seen_state`; `wanted_state`; `comment_wanted` (enum, null when none); `comment_written_at`; `tracker_closed_at`; `frozen_facts`; `first_attempt_at`; `next_sync_at`; `last_synced_at`; `failures`; `last_failure` (enum); `last_failure_field` | Unique `(finding_id) WHERE role = 'synced' AND sync_status <> 'stopped'`. Unique `(connection_id, external_id) WHERE role = 'synced' AND sync_status <> 'stopped'`. Unique `(finding_id, connection_id, external_id)`, so one issue can be a reference on many cases, once each. Partial index `(connection_id, next_sync_at) WHERE next_sync_at IS NOT NULL`. Check: `last_failure_field` matches `^[a-zA-Z0-9_]+$` |
| `connections` | PK `id`; `org_id`; `kind` (`github`, `jira`, `github_fix`); `state`; `sealed_credential`; `sealed_webhook_secret`; `sealed_manifest_code`; `setup_state_hash`, `setup_expires_at`, `setup_user_id`; `github_app_id`, `github_app_slug`, `github_installation_id`; `jira_site`; `token_expires_on`; `sync_lease_until`; `paused_until`; `last_ok_at`; `failing_since`; `last_failure`; `failure_told_at`; `expiry_told_at` | `UNIQUE (org_id, id)`. Unique `(org_id, kind) WHERE state <> 'disconnected'`. Unique `(github_app_id, github_installation_id)`. Check: disconnected means every sealed column null. F5 adds `sealed_client_secret` for its own kind (`ai-solution/design.md:711`) |
| `connection_repositories` | PK `id`; FK `(org_id, connection_id)` CASCADE; `external_id`; `key` (`owner/name` or a Jira project key, strict pattern); `is_private`; `public_allowed_by`, `public_allowed_at`; `public_told_at`; `jira_issue_type_id`; `jira_done_transition`; `jira_reopen_transition`; `is_reachable` | `UNIQUE (org_id, id)`. The name is the foundation's, and holds a Jira project too. Referred to as a *place* in code. Unique `(connection_id, external_id)` |
| `service_repositories` | PK `(org_id, project_id, service_name)`; FK `(org_id, place_id)` CASCADE | Project FK CASCADE |

**New indexes on `findings`, none of them today:**

```sql
(org_id, assignee_id)              WHERE assignee_id IS NOT NULL            -- Assigned to me; clearing on removal
(org_id, case_resolved_at DESC)    WHERE case_state = 'resolved'            -- Resolved cases
(org_id)                           WHERE case_state = 'investigating'       -- Active cases; the sweep
(org_id, root_finding_id)          WHERE root_finding_id IS NOT NULL        -- open joined rank; timeline; detach
```

The two `uq_findings_*_active_case` indexes begin with `project_id`, so they do not serve an organization-wide read.

**Composite keys and existing tests.** `test_deletion_live.py:1096-1100` groups a foreign key's columns with `bool_or(attnotnull)`, so a composite key whose `org_id` is not null passes its "cascade and not null" check. That reading was of the query's shape, and T1.2 proves it. `ON DELETE SET NULL (column)` sets only that column of a composite key. Postgres supports this column list from version 15, and the install runs `pgvector/pgvector:0.8.6-pg18-trixie` (`compose.infra.yaml:138`).

**Inventories, in the same task as each table (F6's C20):**

- `organizations/tables.py`: `connections` in `DELETED_DIRECTLY` before `users` and `projects`; `connection_repositories` cascade from `connections`; `service_repositories` from `projects`; `finding_events`, `finding_notes`, `finding_links`, `finding_emails_owed` from `findings`.
- `SEALED_COLUMNS` (`keys.py:93`), and `Keys.connection`, with three columns: `sealed_credential`, `sealed_webhook_secret`, `sealed_manifest_code`. **AC35's test fails on any `sealed_*` column missing from the list,** since today nothing does (foundation:492).
- `test_setup_schema.py` and the scheduler's `test_erasure_inventory.py`, as they read migrations.
- `POLICY_OF`, `EXEMPT` and `test_read_surface_structure.py`.
- `Action` and its five copies (F6's C14), plus `test_auth_routes.py:278`.

## Performance and scale

All arithmetic or guesses. Nothing is measured.

| Thing | Figure | Basis |
|---|---|---|
| Events per case | Tens | Reasoning. Flapping is held to one per spell |
| Worst joined findings kept | Bounded by each one's own retention, not by the case | FR24. Reviewers' figure of 10,800 at 5 an hour for 90 days counted rows that now age out |
| GitHub reads | 12 per active link per hour. **Every read costs a point** (D63). 100 active links = 1,200 an hour, against at least 5,000 per installation | Research. It fits with no ETag |
| GitHub writes | Far below about 80 a minute and 500 an hour, since each is a person's act | No automation exists to drive them |
| Jira | Well under its per-issue and burst limits | Research |
| Job rows | One per connection per 30 s at most, expiring in 60 s. Swept as today | `queue.sweep_spent` |
| Feed, *All* | The sort already ranks every row in a project. It now runs one `EXISTS` per row, on the partial index | `queries.py:46-69`. Not measured |
| Feed, case filters | Read through the new organization-wide indexes. No telemetry | Above |
| Timeline | Grows with joined findings; over 200 ms at the worst shape means store a root on each event (D65) | **T8.4 measures this** |
| Page refresh | About 4 requests a minute per open page | Arithmetic |

**Capacity.** `scheduler-connections` with concurrency 3, ceiling **384 MB, a guess**, copied from `scheduler-deliveries` (`nexovue.yaml`), which runs 4 children, and F3's role. The narrow task set should need less, which is why it is worth measuring early. **T4.9 measures idle and worst-sync memory at the end of Step 4,** with the narrow set, and states the result beside this guess. F5's work on this role is not counted, and F5 must state it (`ai-solution/design.md:749`).

**The budget.** `sizing.md:7-17` says "about 13 GB", but its table itemises to 6 + 2 + 1 + 1.25 + about 3.5 = **13.75 GB**, by arithmetic. With this role and `scheduler-insight` at 384 MB each, about 14.5 GB, leaving about 1.5 GB of a 16 GB server for the operating system and ClickHouse's page cache. `sizing.md` must be corrected when the role is added. Whether 1.5 GB is enough is not known, and it is a real risk for the smallest supported server.

**AI cost: none.** F22 makes no model call. Notes later cost F4 tokens when embedded, and F4 owns that.

## Failure handling

| Failure | What happens | Who notices |
|---|---|---|
| Timeout, 5xx, or connection failure | **The run ends** and sets `paused_until`. `failures + 1`, `next_sync_at` backs off 1, 2, 4 … to 60 minutes with some randomness. Link `waiting`. Connection `failing` after 5 straight | Link and settings screens |
| 429, or GitHub's secondary limit | `paused_until` from `retry-after` for the whole connection. Jira: also read `RateLimit-Reason` | Settings, after a long pause |
| 401, 403 on token, or installation gone | Connection `needs_attention`. Sync stops until fixed. One email | Admins |
| 403 on one repository, or repository removed | That place `is_reachable = false`. Its links `needs_attention`. Others go on | Case page, settings |
| Required Jira field on create | Link `needs_attention`, naming the field's key from `last_failure_field` | Case page |
| No transition | `needs_attention`, W stays set. Case stands | Case page |
| Refused on a public repository | `refused`. No call made to create | Case page |
| Public check unanswered | `waiting`. No create | Case page |
| Worker killed mid-link | The job's lease lapses and `sync_lease_until` lapses. The next pass lands on rows 2 or 3, or on *creating, a retry* | Nothing visible |
| Beat stopped | Nothing due is picked up, and last synced ages. Existing alert on scheduler health | Operator |
| `scheduler-connections` stopped | Cases all work. Links age, and words change with age. `NexovueConnectionsMissing`, only if a connection exists | Operator |
| Role left out on purpose | Nothing fires, since `connections_active` is 0 | Nobody needs to |
| Postgres down | Nothing works, as today | Operator |
| `CONNECTION_SECRET_KEY` missing | Compose's `:?set` refuses, since upgrade adds it | Operator |
| Key cannot open a value | `SealError`: connection `needs_attention`, with words saying the operator's key is wrong. No call | Admins |
| Provider error text | **Never stored or shown.** `last_failure` is an enum, plus an HTTP status, because a body is outside text. `senders/http.py`'s `_said` keeps a trimmed body, which is right for a customer's own receiver and wrong here | |
| A deadlock despite *Locks* | Postgres aborts one side. Detection retries on its next pass, as for any aborted transaction, and a person sees the existing failure. AC101 is what stands against it | Metric on aborted runs |

**A case outlives a tracker failure** (BR5). No act on a case waits for a call out, so all the above leaves the page working.

**AI off, or `scheduler-insight` stopped:** nothing changes, since nothing here touches it.

## Security, tenancy, and privacy

**Tenancy path.** Every route starts from the caller's `org_id` (`auth/dependencies.py`), as `queries.lock_finding` does. Every statement binds it. Composite keys mean the database refuses a row that crosses (AC72, D34). A token's project is compared to the row, as `get_finding` does (`service.py:159-161`), and a mismatch is the same 404. A webhook takes its `org_id` from the connection whose secret matched, never from the request, as `deploys/service.py:92-94` does. **The job takes `org_id` from its kwargs and binds it on every read, including its first read of `connections`,** so the row must match both the id and the organization. A mismatch finds nothing, and the job stops.

**The scheduler's tenant test exists** (`apps/api/tests/test_scheduler_postgres_tenancy_structure.py`), and it already holds new statements to binding `org_id`. So new statements in `connections/`, `cases.py` and `nexovue_platform/cases.py` must pass it. `sweep_connections` and `cases_sweep`'s first read cross organizations by nature, and are named exceptions with their reasons, with each follow-on statement binding `org_id` from the row. `connections_active`'s beat read is a third named exception, which reads an `EXISTS` only.

**The API's test has the same rule.** The webhook's lookup, a new `live_webhook_connection` in `connections/queries.py`, loads a connection by id with no `org_id`, because the caller has proved nothing yet. It goes in `EXEMPT` in `apps/api/tests/test_postgres_tenancy_structure.py`, with that reason, as `live_webhook_secrets` does (`:38`), and T5.1 changes that test (AC139).

**Secrets.** New key `CONNECTION_SECRET_KEY` and `CONNECTION_SECRET_PREVIOUS_KEYS`, with new purposes `connection-credential`, `connection-webhook-secret` and `connection-manifest-code`, each bound to `org_id:connection_id`. Separation comes from which container holds the key (FD10): the API, to seal and to open a webhook secret, and `scheduler-connections`. `keys.py` runs in the API's container, so it can reseal all three. `docs/operations/key-rotation.md` gains it.

**Narrow settings.** `scheduler-connections` does not use the `control-plane-env` anchor. It gets `ENV`, `NEXOVUE_DEPLOYMENT`, log settings, `SCHEDULER_ROLE: connections`, `POSTGRES_DSN`, `APP_BASE_URL`, and its key pair. `KAFKA_BROKERS` is not needed: F3's change gives it an empty default outside `full`. `CHECK_SECRET_KEY` is required only when `full`. All roles still share one Postgres account (foundation section 1), so this limits keys and not database reach.

**Stated plainly: a compromise of this role reads and writes all of Postgres, and can tamper with the job queue.** It can alter findings, connections and allowances, and read or remove job rows. It cannot open check or channel secrets, and holds no AI, mail or object store key. This is the one role that opens tracker credentials and parses responses from the internet, so **T8.7 gives it its own account**, `nexovue_connections`, by F3's pattern (`ai-insights/design.md`, *The Postgres account*, `:647`), with grants limited to `connections`, `connection_repositories`, `finding_links`, `finding_events`, `finding_emails_owed`, its jobs and the findings case columns, and F5's tables once F5 lands (F5 design, *Grants and the checklist*). If F3's pattern is not merged by then, this stays a named risk.

**The attacker's paths,** from the research, each with what holds it:

| Path | What holds it |
|---|---|
| A name such as `@acme/security` or `![x](https://evil/?…)` reaching an issue | *Issue text*; AC41 |
| A name holding an address or customer id | `strict_name()`'s fixed words; AC82 |
| Findings driven on purpose to pile up issues | BR8: no automatic creation |
| Public repository leak | BR12, checked before every create, including retries, against GitHub's own answer. A failed check does not create |
| Forged webhook | HMAC on raw bytes, constant time. And its content sets nothing |
| Replayed webhook | Harmless by construction (AC48) |
| Webhook reviving an old link | The update excludes `not_followed` and `stopped` |
| Outsider closes a linked issue on a public repository | References never change a case. Promotion warns. Public refused by default. With the signal open, the case stays (D73). Otherwise a notice goes out, and it reopens in one click |
| Non-member starting a deletion clock by closing an issue | One notice on **every** `tracker_closed`, to the assignee, or owners and admins if none (AC94) |
| Pasted issue address | Parsed to a key, matched to a connected place, and never fetched |
| Binding another's installation | The redirect's id is never read |
| Typed Jira address | Strict pattern, guarded client, no redirects (AC39) |
| Moved issue address | Taken from a guarded read's JSON, and rebuilt. Never stored as read |
| Link pointing somewhere planted | No `url` column; addresses are built |
| Note with a link or script | Plain React text. `dangerouslySetInnerHTML` is used once in the code (`features/docs/DocsScreen.tsx:89`), and never here |
| Forgotten case holding names | D9, D42, and no join past 7 days |
| A flood | Own rate tier, one job at a time per connection |
| Tracker text reaching F4's AI | Only status, ids, strict repository names and project keys are stored (BR9). **These and notes are free text wherever F4 reads them** (foundation section 3) |

**Audit**, kept apart from the timeline (FD9). No note text, ever. Scheduler-driven endings write `audit_log` with no person, as `project_job.py:243` already does, through `RECORD_CASE_AUDIT_SQL`. AC77's test **reads the router**, so a new write route with no row here fails.

| Route or writer | Audit name |
|---|---|
| `PATCH /case`, starting | `case.started` |
| `PATCH /case`, assign | `case.assigned` |
| `PATCH /case`, resolve; `false-alarm`, `new-normal` on a root | `case.resolved` |
| `PATCH /case`, reopen | `case.reopened` |
| `POST /notes` | None: the timeline records it, and FD9 keeps audit for what is worth a trail. The note deletion below is audited because it destroys text |
| `DELETE /notes/{id}` | `note.deleted` |
| `POST /links`, create | `link.issue_requested`, because data leaves to a third party |
| `POST /links`, reference | `link.added` |
| `POST /links/{id}/promote` | `link.promoted` |
| `POST /links/{id}/retry` | None: it repeats a recorded act, and changes no permission or data |
| `DELETE /links/{id}` | `link.removed` |
| `POST /connections/github/manifest`, `/jira` | `connection.created` |
| `/github/manifest/complete`, `/github/refresh`, `POST /connection-tests` | None: they carry out a recorded setup, or read |
| `POST /connections/{id}/credential` | `connection.credential_replaced` |
| `DELETE /connections/{id}` | `connection.deleted` |
| `PATCH /places/{id}`, allow | `connection.public_repository_allowed` |
| `PATCH /places/{id}`, withdraw | `connection.public_repository_withdrawn` |
| `PATCH /places/{id}`, other | `connection.place_changed` |
| `PUT`, `DELETE /service-repositories` | `service_repository.changed` |
| `POST /connections/webhook/{id}` | None: it changes no record but when to read next |
| `sync_connection`, `cases_sweep` | `case.resolved`, `case.reopened`, system actor |

**Support view.** Writes are refused by method. **Note bodies read "Hidden during support view"** (FR20, D62), through `reader.is_real_person`, and the copy source is hidden too. AC69's table test names every kind of `Reader`, and F4's T7.2 adds the ticket's row.

**Privacy.**

- `export` (`profile/personal_data.py`) gains `notes_written`, with a truncation flag of its own. Notes stay after a person leaves, as the organization's working record, with the author shown as *Former member*.
- **`finding_events` by actor are not added to the export.** They hold kinds and ids, no text, and sit with the same acts in the audit log, which the export already reads through `queries.activity`. Decided rather than left out, and the founder may overrule.
- A person may delete their own notes before leaving, and the leaving screens say so.
- Issues written to a tracker are outside nexovue's reach after that, including after a finding is deleted. `docs/product/setup/trackers/` must say so.
- No person's name or address goes to a tracker. A Jira email is sealed with its token.

**Rule of Two (foundation section 3).** This link has no model. If F5's AI later wants a repository, it goes through F5's own App and this role still runs no model.

## Observability and operations

- **Metrics**, no tenant labels (`metrics.py:10`): `connection_sync_runs_total{outcome}`, `connection_tracker_calls_total{tracker,outcome}`, gauges for connections failing and needing attention, oldest due link's age, and **`connections_active`, exported by `scheduler-beat`**, since a metric from the missing role could not say it is missing. Beat reads it when deciding whether to add `sweep_connections`.
- **Alert rules** in `deploy/monitoring/rules/nexovue.yml`. **Not** an `absent(up{...})` beside its siblings (`:18-20`), which would page for ever on an install without the role, and teach people to ignore that group. A separate `NexovueConnectionsMissing`: `connections_active > 0 unless on() up{job="scheduler-connections"} == 1`. Oldest-due-link age over 30 minutes, a guess. Tests in `deploy/monitoring/tests/nexovue_test.yml`, including role absent with zero connections (AC123).
- **Logs** name connection and link ids and the failure enum, and never a credential, a URL with a token, or a body.
- **Prometheus scrape** entry beside `scheduler-deliveries` (`prometheus.yml:45`).
- **Compose:** new service in `nexovue.yaml`, behind a `connections` profile, set by `NEXOVUE_CONNECTIONS` in `deploy/nexovue`, the way `NEXOVUE_MONITORING` sets `--profile monitoring` (`:109,230-231`). Default `yes`. Entries in `nexovue.quickstart.yaml`, `nexovue.build.yaml` and `nexovue.kafka3.yaml` where an overlay needs one, and the dev stack's `docker-compose.yaml`. **No entry in `nexovue.hosted.yaml`,** and `test_compose_contract.py` asserts its absence. New role in `roles.py`. **Restore must stop it too** (`deploy/nexovue:601`), or it would keep writing to Postgres and calling trackers during a restore. It is added only when `NEXOVUE_CONNECTIONS` is `yes`, because what `compose stop` does with a service whose profile is off was not checked. T3.12 does this.
- **The API reads `NEXOVUE_CONNECTIONS`,** and `deployment`, to answer `connections_available`.
- **Upgrade:** `add_missing_secrets` adds `CONNECTION_SECRET_KEY` when absent, and leaves existing values alone. Also `write_secrets`, for a new install.
- **Docs:** `docs/self-hosting/sizing.md`, `docs/operations/key-rotation.md`, `docs/product/setup/trackers/github.md`, and `jira.md` only if Step 7 went ahead, with what happens to issues on deletion. A company egress proxy is stated as not supported at first, until T3.0 has tried it (D83).

## Testing limits, stated

- **jsdom has no layout** (`apps/web/package.json:12,40,45`, `render-harness.test-util.ts`). No Playwright or browser test exists in the web app. So AC66's and AC108's sideways scroll and wrapping, AC67's visual order, and NFR7 and NFR8 cannot be proved by the suite. **They are checked by hand at Design QA, against a written list of widths (375, 768, 1280, 1920 px) and states, and the result is saved in `status.md`** (D84). DOM order and focus targets stay in jsdom, which does not prove layout.
- **jsdom does not enforce a content security policy,** so AC73's form post is proved in a real browser, by hand, at T3.0 and again at T3.9, and recorded in `status.md`. T3.9's automated test proves the header.
- **CI proves the state machine against a fake,** built by the same authors from pages read through a summarizing tool. T4.8 and T7.6 run one written script against real accounts, and save sanitized responses as fixtures the fake must replay (AC130). Those can go stale.
- **Nothing here load-tests.** T8.4 measures one read and T4.9 measures memory. Poll and write figures stay arithmetic.

## Alternatives and tradeoffs

| Rejected | Why |
|---|---|
| A `cases` table | ADR-022 and FD12. Two records for one problem |
| Grow `incidents` too (FD13's other option) | Repeats the merge ADR-022 rejected |
| Reuse `state` | Findings close themselves (`0041`), so it cannot also mean "someone is on it" |
| Automatic case per finding | A flapping service opens many an hour. FD12 |
| Webhooks as truth | Self-hosted may be unreachable. Order not promised. Jira drops to one attempt after 30 failing minutes |
| Outbox of events (ADR-013's shape) | An alert is an event, and each must be delivered. An issue's status is a state, and only the latest matters. Borrow its leases and backoff, not its order |
| One-way push | Fails the plan's "stay in sync" |
| API calling GitHub directly | No connect-time guard there. Moving `pycurl` into the API is a bigger change than a job |
| Delivery table for webhooks | Protects nothing here, and grows |
| Label per link as a marker | Clutters a customer's labels, and GitHub may drop it silently |
| Hidden HTML comment as a marker | Writing hidden text into a customer's tracker is the wrong habit, and the link back already does it |
| Reading one list per repository in place of one issue per link | Cheaper at scale, and a second code path. Small teams have few links. Revisit if measured |
| Separate root column kept in sync by trigger | A trigger hides logic from the reader. The subquery in `_OPEN` is one line |
| Own process for the idle sweep | `light` is already there, and holds mail |
| Mail in `scheduler-connections` | D30: this role holds nothing else. A marker costs up to 5 minutes |
| One shared GitHub App for self-hosted | Its private key cannot ship in AGPL source (FD11) |
| Syncing `resolution` to Jira | Its values differ per site. Left out at first |
| A stored `url`, held by a check | A check cannot read another row |
| Case logic in the API, repeated in the scheduler | It would be written twice and drift |
| A browser test job, now | Worth having, and a piece of work of its own. D84 records the hand list instead |

**What this costs.** Sync is status-only, so people may find it thin next to rivals, several of whom carry more (research). No Slack, no automation, and a setup of a few clicks plus GitHub's own screens. Each is stated in the spec, and D4 is the first to revisit.

## Proposed ADRs

Written here, not in `docs/decisions/`. Each takes the next free number when approved.

### Proposed ADR: Trackers are reconciled, and a webhook only rings the doorbell

**Status:** Proposed. **Context.** Two systems hold a status. Either can be unreachable or written to by someone else. Neither GitHub's nor Jira's pages read promise delivery order, and a self-hosted install may not be reachable at all.

**Decision.**

1. Each synced link stores `seen_state` and `wanted_state`. One function compares them with a fresh read, per *The comparison*.
2. A webhook, once its signature is proved, only makes that function run sooner. Nothing in its body changes a case, and it adds no job.
3. Polling alone must be enough.
4. No delivery table.
5. Only status crosses, plus fixed comments within BR10's limit.

**Consequences.** Duplicates, order and replay stop mattering. An unreachable install works. It costs a charged read per active link on a timer, and up to five minutes of delay without a webhook. It does not suit a future feature that needs every change, such as syncing comments. That would need its own decision.

### Proposed ADR: A case is a finding's columns, with one active case per service

**Status:** Proposed; settles what ADR-022 left to Stage 3. **Decision.**

1. `case_state` and its columns are on `findings`.
2. Two partial unique indexes allow one active case per service and one per project.
3. `root_finding_id` joins later findings, within 7 days of a person's act. A finding holds a case or joins one, never both.
4. A threshold or check incident does not become a case; it stays linked through `finding_incidents`.
5. An idle case ends after 90 days, and an untouched one 72 hours after recovery. Traffic cannot hold a case open.
6. Transitions are SQL constants in `nexovue_platform`, executed by the API and the scheduler alike.
7. A person's locks on a finding are `FOR NO KEY UPDATE`, so they cannot block a foreign key check from detection.

**Consequences.** A finding's meaning stays what F6 defined. F6's open indexes and the outbox are untouched. A check failure with no finding has no notes or tracker link, which is revisited if users ask. `retention.py`'s rule gains clauses.

### Proposed ADR: `scheduler-connections` makes every tracker call, on narrow settings

**Status:** Proposed; carries FD23, amends foundation section 1's "API, on a person's click", and adds a role under ADR-019. **Decision.**

1. A new role, from the same image, on queues `connections` and `connections-interactive`, with sync, on `connections`, capped below the slots.
2. It holds `CONNECTION_SECRET_KEY`, and not the shared anchor's keys. `SCHEDULER_ROLE` gains `connections`, and `TASK_SETS` an entry.
3. The API adds jobs and reads results, per ADR-011's reason.
4. It runs no model, enforced by a test on the modules it actually loads.
5. Typed addresses are limited to known hosts until a self-hosted tracker is supported.
6. It is optional: an install can leave it out, and hosted has none until founder decision 3 is answered.

**Consequences.** A slow GitHub holds no alert. A click's result arrives a moment later, not in the response. One more container, memory unmeasured. F5's GitHub work runs here too.

## What other features must change

The rows on F3, F4, F5 and the foundation were applied across the Phase 2 documents on 2026-11-02 (`status.md`, *Cross-feature reconciliation*), and stay here as the record of what F22 asked for. Personal data and the competitors research are outside that work and stay open.

| Feature | What F22 settles | What it must change |
|---|---|---|
| F5 | The contract: tokens and JWT in the scheduler; the API holds no GitHub token; `kind` allows `github_fix`, ignored by F22's sweeps; F22 keeps no client secret, and F5 adds `sealed_client_secret`. **Queues:** F22 builds F5's layout, with `connections` capped at 2 of 3 holding sync and `connections-interactive` holding person-started jobs (T3.1, T3.2), and F22 stops the role during a restore (T3.12), as `ai-solution/design.md:611` asks | Correct `ai-solution/design.md:553`, "up from F22's 2": F22 runs 3. Accept that `poll_fix_pull_requests` and `sweep_fixes` can wait behind a hung sync, or move them. **Stated limit on slow jobs:** F5 measures them, and names any change to concurrency |
| F5, F3, foundation | The event writer is `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, in `nexovue_platform/cases.py`, and each caller executes it | `phase-2-foundation.md:529`, `ai-solution/design.md:624`, `ai-solution/spec.md:564` and `ai-insights/spec.md:477` say `record_finding_event()` |
| F5 | The page order: Case comes directly under Controls, on the **left** at wide windows, with F28 and F3 in the right column, and F5's full-width fix below the split | `ai-solution/design.md:56-67`. Its table says Case is sixth and on the right |
| F5 | Connection modules join `TASK_SETS["connections"]` | `ai-solution/plan.md`, T3.10's `jobs/registry.py:19-30`, and `ai-solution/design.md:556` |
| F5 | F22 carries the seams F5 asked for: refresh takes a connection, a disconnect hook, a webhook handler for `pull_request` | Nothing |
| F3 | `SCHEDULER_ROLE` and `TASK_SETS` gain a third value | Note in F3's documents. F3 must not close the type |
| F3 | T2.9 edits `evidence/gather.py` after F3's code exists, to write `explained` to `finding_events` | Note in F3's `status.md` as a known later edit. Its notification is unchanged |
| F4 | Notes are free text. FR14's box sentence and *What leaves this server* become false when F4 is on, since notes go to the customer's provider (`ai-chatbot/design.md:591`, and *6. Knowledge*) | F4 must drive that sentence from the AI switch, add "and may be sent to your AI provider", and add an AC in its scope. It must extend AC69's table test |
| Personal data | The removal and deletion wording | `docs/features/personal-data/spec.md:100-101`, at T2.10 |
| Foundation | Section 12 item 4's seat price; section 8's "not built" row; section 10 on hosted | Editorial, not a founder decision: correct in the next cross-feature pass. Section 12's seat comparison was already changed on 2026-11-02 (`status.md`, *Cross-feature reconciliation*); section 10's hosted line waits on founder decision 3 |
| Competitors research | The word "gap" for F22 (`phase-2-competitors-2026-11.md:344`) and the "$20 to $30" line (`:133`) | Editorial, not a founder decision: correct in the next cross-feature pass. Still uncorrected on 2026-11-02 |

## Specification impact

- `spec.md` was revised on 2026-11-02 after the persona reviews, with this revision. New numbers start at FR48, AC90 and D70, and nothing was renumbered.
- `feature-catalog.md:337` and `vision.md:133,140`: "auto-created cases" is amended, and `vision.md:244`'s stated value is recorded as superseded. To be updated when approved (T8.2).
- F4 inherits: notes as free text, and a timeline with no free text in its `detail`.

## Not checked

- Anything running. No call to GitHub or Jira. No lock was exercised: *Locks* comes from reading the code and Postgres's rules.
- Whether a webhook can be inactive in a manifest, and whether the flow works from a private address, or through a company proxy.
- Whether the `issues` event set brings `installation` events.
- Whether `issues: write` lets an App set a label on create. Nothing depends on it.
- Whether a GitHub issue title can mention or link anything. Assumed it can.
- What a `Fixes #412` merge does to `state_reason`, and whether GitHub answers a transferred issue with a 301 and which address. T4.8.
- **Jira:** Atlassian's reference pages for transitions, status categories and search would not load in the tool. Keys and shapes above came from third parties. Whether the new search route lags behind writes. How to read `cloudId`. Scopes for a scoped token. How to tell a service account from a person. Whether a project's access can be read. Atlassian's written position.
- Whether Postgres backups hold deleted notes for how long.
- Any real user's opinion.

## Sources

Read on 2026-11-02, through a summarizing tool, so check before quoting:

- [GitHub: App manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) · [About the setup URL](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url) · [Issues REST API](https://docs.github.com/en/rest/issues/issues) · [Permissions for GitHub Apps](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)
- [Jira: webhooks](https://developer.atlassian.com/cloud/jira/platform/webhooks/)
- Third-party, unconfirmed: [status categories on a community thread](https://community.developer.atlassian.com/t/bad-documentation-for-rest-api-3-statuscategory/78565), [New Relic on `getTransitions`](https://docs.newrelic.com/docs/workflow-automation/setup-and-configure/actions-catalog/jira/jira-issue-gettransitions.md)

Everything else is in [`research.md`](research.md), *Sources*.

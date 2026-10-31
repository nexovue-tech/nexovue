# Console review — every screen, 2026-09-20

A whole-product review of the console: layout, spacing, copy, flows and behaviour, screen by
screen. Each finding names what a customer would see.

> **Acted on 2026-09-20** in `docs/changes/console-review-fixes/` — the founder's answer to this
> document was "fix them all". Every finding below carries its outcome; the two marked
> **withdrawn** did not survive measurement and are kept here rather than deleted, because a review
> that quietly drops its own mistakes is not one you can trust the rest of.

**Nothing in this document is a matter of taste unless it says so.** Where a finding is a
preference rather than a defect it is marked *(taste)*.

## How it was reviewed

- **Every route the router serves** (46, of which 34 render something for a signed-in customer)
  was opened against the live development stack as the seeded customer, at **1440×900** and
  **390×844**, in **light and dark**, and captured full-page. Detail screens were opened on real
  ids read from the API.
- **Console and network were recorded for every screen.** A paced pass (one screen per ~7 s, under
  the 60-reads-a-minute budget) produced **no failed request and no page error** across all 34
  screens at both widths. Two warnings repeat on every page and are findings below.
- The signed-out doors (`/login`, `/signup`, `/forgot-password`, `/docs`) and the operator's
  platform area were captured separately.
- **A scripted pass over 26 screens at both widths** measured what a screenshot cannot: every
  element's box against the viewport, every button and link's accessible name, every field's label,
  and every tap target's size.
- Where a screen looked wrong, the cause was traced in the code before it was written down here.
  Findings without a `file:line` are visual and evidenced by the screenshot alone.

## Defects — what is actually wrong

### D1. Findings says "All systems normal" while four checks are down (HIGH)

**Fixed, then fixed further.** The headline is derived from the evidence the screen holds (`features/findings/headline.ts`), names failing checks, and says a check is down only when a probe reported it down — a check that has not run is a gap in what we can see, not a failure. It still said "All systems normal" during an error spike, so it no longer speaks for the whole system ([record](../changes/findings-false-all-clear/bug.md)).

`apps/web/src/features/findings/api.ts:60` — `useFindings` returns an empty feed by construction
(no engine exists yet), so `FindingsScreen` always takes the healthy branch. The evidence line
underneath it comes from a real query and reads **"1 service reporting · 0 of 4 synthetic checks
passing"**, while the sidebar shows 4 open critical incidents.

So the screen whose job is "things that need you" asserts that nothing needs you, on a project
where everything is down. `FindingsScreen.tsx:22-29` already argues that "we could not look" must
never read as "nothing is wrong" — the unbuilt case is the same mistake in a third state nobody
covered.

**What to do:** while there is no engine, the headline must be derived from what the screen does
know (open incidents, failing checks, error rate) or say plainly that the feed is not built yet.
"All systems normal" is a claim the product cannot currently support.

### D2. The delivery log contradicts the channel above it (HIGH)

**Fixed.** A test send records its attempt, marked `test` (migration `0036`, `alerts/send_test.py`), and the log names it.

`apps/scheduler/src/nexovue_scheduler/alerts/send_test.py:33` updates `channels.last_delivered_at`
but never inserts into `notification_attempts`; only `alerts/outbox.py:182` does. A *Send test*
therefore makes the channel say **"Last delivered 21 hr. ago"** while the delivery log on the same
screen says **"Nothing has been sent here in the last 30 days."**

The delivery log promises "every attempt of the last 30 days". For the one delivery path a
customer triggers by hand, it is empty.

**What to do:** record an attempt for a test send, exactly as the outbox does.

### D3. The mobile trace filter runs off the screen (HIGH, 390px)

**Fixed.** The row wraps and the inputs shrink; APPLY is fully on screen at 390px.

The attribute filter row (`SERVICE / OPERATION / STATUS / TOOK / ATTRIBUTE [key] [value] [APPLY]`)
does not wrap at phone width. Measured: the APPLY button's right edge is at **411.7px in a 390px
viewport**, **no ancestor scrolls** (`overflow-x: visible` all the way up) and the document's own
scroll width is 390 — so nothing can bring it into view. It renders cut in half. Only its left
~28px are tappable, and any longer label would be entirely unreachable.

It is the **only** element in the whole console that overflows its viewport at either width, which
makes it an oversight rather than a pattern.

### D4. "Where the time goes" charts time for runs that never connected (MEDIUM)

**Fixed.** The sentence says what it measured: with every run failed it reads "measured up to the point each run failed, not to an answer", and with some failed it says how many it folded in.

On a check whose every run fails with *"Failed to connect … Could not connect to server"*, the
phase panel still reports `dns 3.5ms · tcp 0ms · tls 0ms · ttfb 5.2ms · transfer 11µs` and the
sentence **"ttfb is 60% of the mean 8.7ms — the server thinking before it answered."** No server
answered. The probe is behaving as documented (`probes/http_check.py:206` — a phase that never
happened reports zero, and failures still carry timings); the *screen* draws no distinction
between a breakdown of a successful request and one of a failure.

**What to do:** either exclude failed runs from the breakdown, or say which runs it covers.

### D5. "Download the 5 lines on screen" when 200 are on screen (MEDIUM)

**Fixed.** It says "the 5 lines that arrived while you watched", and the file is named for the same thing.

`apps/web/src/features/logs/LogsScreen.tsx:393` — `held` is the **live tail buffer**, not what is
rendered. The heading says "202 lines from the last hour", ~27 rows are visible, and the button
offers 5. The code comment is honest about the intent; the label is not.

**What to do:** name what it contains — "the 5 lines that arrived while you watched".

### D6. Chart x-axis labels are clipped at every width (MEDIUM)

**Fixed.** The plot reserves the last label's overhang (`TrendChart.tsx`).

Overview's RATE and DURATION panels cut the last tick: `:0C` instead of `:00`, `6:40p` instead of
`6:40pm`. Visible on desktop dark and on mobile. The plot area does not reserve room for the final
label.

### D7. Table headers do not sit over their columns (MEDIUM)

**Fixed.** Traces' header was missing the duration bar's column; Errors kept its padding on the row instead of in the shared column constant. Both now share one declaration.

On Traces, the `SPANS` and `TOOK` headings are drawn left of where their right-aligned values land;
Errors has the same mismatch on `SERVICES` and `STARTED`. Values are right-aligned, headings are
not, so nothing lines up. It is the kind of thing a reader feels before they see it.

### D8. The operator's console is 13 dead links (MEDIUM — from today's change)

**Fixed.** The operator is shown their own section and nothing else, and the project selector and range picker are gone from their top bar.

Signed in as the super admin, the sidebar still offers Overview, Findings, Alerts, Services,
Service map, Traces, Profiles, Errors, Logs, RUM, Synthetics, Infrastructure, Metrics and Settings.
The role holds no permission inside any organization, so every one of them is empty or refused. The
project selector reads `PROJECT —`.

**What to do:** the operator's sidebar should be the platform section and nothing else.

### D9. The seeded operator is nagged to verify an address that cannot receive mail (LOW)

**Fixed.** The seed sets `email_verified_at`.

`superadmin/seed.py` does not set `email_verified_at`, so the platform area carries the
verify-your-email banner for an account seeded from the environment, often at a `.test` address.
Seed it verified.

### D10. Copy defects a customer will read (LOW, but each is visible)

**Fixed**, all six.

| Where | What it says | Why it is wrong |
|---|---|---|
| Infrastructure subtitle | "over the last **the last** hour" | duplicated words |
| 404 screen | "Every **Phase 1** route is listed in the sidebar" | internal project jargon; and on a phone there is no sidebar, only a drawer — with no link back |
| API tokens | "**1 requests** today" | plural not applied |
| Settings → General | "…which is **not built yet**" | tells a paying customer a feature is missing rather than what to do |
| Docs (setup) | "The hosted hostname is not settled", "The decision is recorded in `deploy/caddy/Caddyfile`" | internal notes and a repo path in customer documentation |
| Alerts | "for 20 h 28 min" | spacing reads as two units, not one duration |

### D11. Two screens each carry the same field label twice (LOW)

**Fixed.** "Your current password" / "Your password, to turn this on" / "…to turn this off"; "Name this server key" / "Name this browser key".

Found by scanning every field's label on every screen:

- **Settings → Account** has *Current password* under **Password** and again under **Two-factor
  authentication**.
- **Settings → Ingest keys** labels both forms *Name this key* — `ApiKeysScreen.tsx:255` for the
  secret server key and `SiteKeysSection.tsx:153` for the public browser key. The screen's whole
  argument is that the two are not interchangeable, and its two forms ask the same question.

Identical labels on one page confuse password managers, and give a screen reader two prompts it
cannot tell apart.

### D12. A third-party watermark on a customer screen (LOW — a decision, not a bug)

**Decided and fixed 2026-09-21 — and the claim below was wrong.** This said removing React Flow's
attribution "would breach their terms". It would not. `@xyflow/react` 12.11.5 is **plain MIT**: the
only condition is that the copyright notice travel with copies of the software, which the lockfile
and the dependency audit satisfy. There is no display requirement anywhere in the licence.

xyflow *ask* you to keep the badge or subscribe to Pro, which is a courtesy and not a term. It is
hidden now (`ServiceMapScreen.tsx`, `hideAttribution: true`): this is a paid console, a vendor's
badge in it reads as ours, and supporting xyflow is a Pro subscription rather than a badge in a
customer's service map. One word puts it back.

**Worth remembering as a review lesson**: an unverified legal claim kept a decision frozen for a
day. The licence text was in `node_modules` the whole time.

## Layout and spacing — where the console disagrees with itself

The spacing scale is disciplined: `px-4` (161 uses), `gap-2` (140), `gap-3` (108) dominate, no raw
colours anywhere, every screen header is `px-4 pt-5 pb-3`. What breaks the rhythm is *where the
column starts*, and it is inconsistent in three different ways:

| Area | Content starts at | Width |
|---|---|---|
| Every signal screen (Services, Traces, Errors, Logs, Synthetics, Metrics, RUM…) | x=224 | full |
| **Alerts** | x=224 | capped ~900px — but its buttons stay at the far right, 300px from the content they belong to |
| **Settings** (13 screens) | x=332 | centred block ending at 1316 |
| **Platform / Onboard** (today's change) | x=392 / x=584 | `max-w-4xl` / `max-w-lg`, centred |

Navigating Alerts → Settings → Platform moves the page's left edge three times. One rule — the
console is left-aligned at `px-4` and screens cap their *text* measure, not their column — would
settle all four.

Other layout findings, in order of how much they cost a reader:

1. **Overview stops halfway down the page.** *(Not changed — the panels are honest about how much
   there is to say; a screen padded to fill a window says less, not more.)* The KPI band, three panels and a one-row table end at
   ~520px, leaving ~380px empty. The bottom strip (Infrastructure / Synthetics / Browser) is a thin
   label row that reads as a footer, not as content. Three different container treatments —
   divided cells, bordered panels, divided cells — stack on one screen.
2. ~~**Overview's worst-services table has no column headings.**~~ **Fixed** — Service, Req/min,
   Errors and p95, in the row's own widths.
3. ~~**RUM's two card rows do not align.**~~ **Withdrawn.** Measured, the diagnostics cards sit in
   the same grid columns as the three above them (224–616, 628–1020); the third cell is simply
   empty, which is what a grid does. Its breakdown table *did* wrap every value onto two lines
   (`2.42` / `s`) — **fixed**, the reading and its unit no longer break apart.
4. ~~**New check offers five cards in two columns**~~ **Fixed** — three columns at `lg`, seating
   them 3 + 2.
5. ~~**Sparklines touch the right edge.**~~ **Withdrawn.** The drawing ends at the same content
   edge every other column ends at; it reads tighter only because a line has no letter-spacing.
6. ~~**Trace waterfall axis mixes units**~~ **Fixed** — zero is written in the unit its own axis
   uses (`formatDurationOn`).
7. **Filter rows use two different control languages.** *(Not changed — one pattern has to win, and
   which one is a design decision rather than a defect.)* Traces, Services, Errors and Synthetics
   render `SERVICE All`, `STATUS Any` as bold text with no affordance, while the attribute inputs
   beside them are bordered fields and the audit log's `WHO / WHAT / WHEN` are proper selects with
   chevrons. The audit log's is the one that reads as a control.
8. **One-off spacing values** *(not changed)* worth normalising: `px-3.5`, `pl-3.5`, `pl-26`, `mt-7`, `gap-24`,
   `pt-24` — each used once against a scale that is otherwise strict *(taste)*.

## Responsive — 390px

1. **D3 above** — the trace attribute filter overflows and APPLY is unreachable. **Fixed.**
2. ~~**Every settings screen stacks the whole rail above the content**~~ — **fixed**: below `md`
   the rail is one "All settings" row that opens on demand, so the screen you asked for starts at
   the top.
3. ~~**Errors squeezes the one column that matters.**~~ **Fixed** — the trend column goes below
   `sm`, and the exception and its message get the width.
4. Overview's ERRORS panel keeps a chart-sized empty band above its one-line message. *(Not
   changed: the panels are a row, and one of them shrinking would move the other two.)*
5. **The controls are small for fingers.** The drawer's own button — *Open navigation*, the only
   way to reach any other screen on a phone — is **24×24px**; the nav rows inside it are 28px tall
   and the theme buttons are 20px. The usual floor is 44×44.
6. What works: the KPI grid reflows 2×2, Alerts reflows cleanly, the check detail keeps its phase
   chart and legend, the sidebar drawer with the scrim is right, and **no screen scrolls
   horizontally** — D3 aside, nothing overflows at 390px.

## Flows and product gaps

- **First run is the strongest thing in the product.** `/onboarding` — three variables, a COPY per
  line, "My service is not instrumented yet", then *"Your first span arrived. loadgen reported
  lets-go"* with three next steps. Two small things: the values are ellipsised with no way to see
  them in full (a developer wants to read the endpoint, not just copy it), and a fourth unlabelled
  COPY button sits next to a paragraph where it reads as belonging to the text.
- **Profiles' empty state is the model the others should copy**: "No profiles in this range / the
  newest is from 24 hr. ago / **Show the hour of the newest profile**". Compare Infrastructure,
  whose empty state is a seven-line centred paragraph with no action, under three filters that
  filter nothing.
- **The audit log is drowned in sign-ins.** Seventeen consecutive "Dev Owner signed in" rows bury
  the events the log exists for. Either fold repeats or default the WHAT filter to changes.
- **API tokens lists revoked tokens first** — four revoked above the one live token. Live first, or
  hide revoked behind a toggle.
- **Billing does not explain its own numbers.** "Services monitored 28" sits above "26 services
  committed" with nothing saying what happens to the other two. Under CHANGE PLAN the Scale card
  looks like a control but carries no button, while the copy says "choose the new plan here".
- **Settings' own split is unclear.** "General" (your name, your email) and "Account" (password,
  2FA, devices) are both *you*; an organization's name and region are editable nowhere.
- **New alert rule** lets you create a rule that tells nobody. The product handles it honestly
  afterwards — the Alerts screen marks it *"Not notifying anyone"* in critical tone — but the form
  is where it should be said. The `On` checkbox under the **Noise** heading is the rule's enabled
  state and reads as part of the repeat setting.
- **New check offers no browser flow**, though the browser probe exists and runs.
- **Signup asks for no agreement to anything** and offers no show-password toggle.
- **Support view replaces the operator's own session** (recorded in
  `docs/changes/super-admin-in-the-console/status.md`); the screen now says so.

## What is good, and should not be touched

Said plainly, because a list of defects is not a verdict:

- **The writing.** "Nothing failed in this window." "None of them started here." "A dash is a
  percentile fewer than 10 readings could not support, never a zero." "The key is shown once, at
  creation, and never again." Most observability products would ship a tooltip; this one writes a
  sentence.
- **The empty and failure states exist nearly everywhere** — 40 of 42 screens have a loading path,
  and every error path is an `InlineFailure` with a retry rather than a blank page.
- **Dark mode is complete and correct** on every screen checked, with no contrast casualties.
- **The check detail screen** — state, phase breakdown, 120-probe strip, runs list with the failing
  run's own words — is a better incident screen than most paid tools have.
- **Ingest keys** explains the difference between a server key and a browser key in four sentences
  and marks each with what it is.
- **No raw colours, no stray fonts**, one spacing scale, a real design-token system underneath.
- **Nothing is nameless.** Across 26 screens at both widths, every button and link has an
  accessible name and every field has a label — zero exceptions. That is rare, and it is what makes
  the two duplicate labels in D11 the only accessibility findings worth writing down.

## Width — the founder's own finding, 2026-09-20

> "I saw a lot of empty space on the right on the Alerts page and all the Settings pages — we are
> not using the space."

Measured: Alerts ran to 1120 of 1440 and Settings to 1248, so a fifth to a quarter of a wide window
was empty beside them. Both were capped by this review's own "cap the measure, not the column"
rule — applied to the column.

**What was wrong with the rule as written:** a measure belongs on a *sentence*, and it had been put
on the page. A settings screen is as often a table — members, tokens, keys, the audit log, the usage
ledger — as it is a form, and a table has no measure to keep.

**Fixed, as three separate moves:**

- **Alerts runs the width**, like every other list in the console. Its incident and rule rows keep
  their sentence at `max-w-3xl`, so "for 23 h 39 min · Details" now sits at the same right edge as
  the buttons above it.
- **Settings runs to `max-w-7xl`** and every sentence under it carries `max-w-3xl` — about ninety
  characters, past which a reader loses the start of the next line. Tables, forms, key rows and
  panels fill the window; prose does not.
- **A setting group is two columns from `lg`** — what this is on the left, what to do about it on
  the right. A password field cannot be widened (a text field the width of a page is harder to
  fill), so the space goes to the explanation instead. Account, General and the screens built on
  `SettingGroup` gained the most; below `lg` it stacks, in the same reading order.

## Two more, found by the founder on 2026-09-20

**There was no way to sign out.** Not "hard to find" — absent. The only control was *Sign out
everywhere* under Settings → Account, which ends the session on every phone and laptop the account
is signed in on: the right answer for a stolen password, the wrong one for leaving a shared machine.
**Fixed:** the sidebar footer now carries the signed-in address and a *Sign out* beside it, on every
screen. Walked by hand — it lands on `/login` and the session is gone, not merely navigated away
from.

**Three create rows had their buttons out of line** — the invite row on Members, *Create key* on
Ingest keys, *Create token* on API tokens. One cause in all three: a `TextField` carries its hint
*inside* it, so `items-end` was aligning the selects and the button with the bottom of a **hint**
rather than with the input. The `pb-1` and `pb-2` shims in the markup were attempts to correct it by
eye, and a hint that wrapped moved everything again. **Fixed** by taking the hint out of the field
and putting it under the form: every control in the row is then label-plus-control, and they line up
by construction rather than by shim.

## The product gaps, and what happened to them

- **Infrastructure's empty state** was six lines of centred prose with no action — **fixed**: two
  sentences and a link to the collector setup.
- **Metrics repeated "no chart yet" on every row** with nothing saying why — **fixed**: the column
  is called *Opens*, and the subtitle says once that a histogram opens its distribution here.
- **API tokens listed revoked ones first** — **fixed**: live first, history below.
- **The audit log is drowned in sign-ins** — **fixed 2026-09-21**, but not by folding. Collapsing an
  audit log's own rows is a behaviour change to a record customers read; the WHAT filter opens on
  **"Changes only"** instead, which subtracts sign-ins and hides nothing else, with "Everything" one
  click away.
- **Billing does not explain 28 monitored against 26 committed**, and the Scale card under CHANGE
  PLAN carries no control *(not changed)* — both are billing decisions, not layout.
- **Settings' General/Account split**, and an organization's name being editable nowhere *(not
  changed)* — an information-architecture decision.
- **New check offers no browser flow** *(not changed)* — F18's own scope.
- **Signup has no terms link** *(not changed)* — there is no terms page to link to yet.
- **The browser SDK is not published anywhere** — the docs said so in our words ("distribution is
  not settled", "an open decision"); they now say it in the customer's ("write to us and we will
  send you the built file"). Publishing it is still open.

## Suggested order

1. **D1 and D2** — the two places where the console states something untrue about the customer's
   own system. Everything else is cosmetic next to these.
2. **D3, plus the settings rail and the Errors columns at 390px** — the console is currently a
   desktop product with a mobile layout.
3. **D4, D5, D6, D7** — four small corrections that each remove a wrong number or a misaligned one.
4. **The four-way column disagreement** — one rule, applied to Settings, Alerts and the platform
   screens.
5. **D10's copy list**, which is an hour's work and is all customer-visible.
6. The product gaps, each of which is a decision rather than a fix.

## Evidence

Screenshots, console logs and the audit output for this review are in the session scratchpad
(`shots/{desktop,mobile,signed-out,operator}`, `shots-dark/`, `console-notes.json`). They are build
output, not repository content, and are not checked in.

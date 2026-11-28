# Status: console-ux-review

## Lifecycle status

**Implemented, 2026-11-28; not committed.** Built on `feature/phase-2` after the founder approved the
drawing.

## Completed

- **A, fixes, each with a test seen failing first:** the facts width (`ai-insights.spec.ts`, 56 px
  red, then 12 passed); secret fields (`secret-fields-autofill.test.ts`, five fields); the tab
  title (`fix-page.test.ts`); "1 person" and "Agreed to" (`FixConsentPanel.test.ts`); the
  `ai_off` sentence (`test_fix_sentences.py`); the time range only where a screen reads it
  (`WindowControl.test.ts`); Settings marked in the main menu on every settings screen
  (`Sidebar.test.ts`); A14 (`test_a14_…` in `test_fix_draft_live.py`, `test_ac66_…` in
  `test_fix_writer_live.py`, both red without the change).
- **B:** heading utilities and 153 headings moved to them; one button height and a `quiet`
  variant; shared menu rows; settings content 16 px from its menu.
- **C:** the five-step strip (`chain.test.ts`), the finding page in reading order, the history at
  the end, list rows of one shape with chips (`test_a_feed_row_carries_its_issue_and_newest_fix`,
  red without the field).
- **D:** the AI page with Setup, Provider, Chat, Suggested fixes and Usage tabs, the tab in the
  address (`setup.test.ts`); the menu regrouped under *Connected*; Connections renamed GitHub.

- **Round 2, the other screens (founder's yes, 2026-11-28):** filter drop-downs sized to their
  value with an arrow, toggles in plain words (`FilterBar.tsx`, and the two hand-built bars in
  Traces and Logs); the trace attribute boxes read as examples, with the shared button; the service
  page's two captions that explained the design replaced; the project picker hidden on settings
  that cover the organization (`WindowControl.test.ts`, red first); chart legend keys drawn as
  lines; a gRPC status code named in an error's title when its message confirms it
  (`error-groups.test.ts`, seen failing against a broken function); the SLO screen named
  "Reliability targets" in the menu and its New target a button; an alert channel's kind shown
  only when it is not its name; Recently deleted a text button.
- **E and F, approved by the founder 2026-11-28 and built:** the service page's alerts, detection
  and profiling in one band, the later detection date with its reason (`badge.test.ts`, red
  first), dashboards last; Infrastructure's warnings as one line that opens
  (`node-readings.test.ts`, red first), critical notices still open, and the metric list closed at
  the end.

- **G, the sidebar, approved 2026-11-28 and built:** groups Respond, Investigate and Watch; the
  chosen row a 3 px rule and a green fill; open findings counted beside Findings from a new
  `GET /findings/open-count` (`test_open_count_…`, red without the route; `open-findings.test.ts`);
  one account row whose menu holds the address, documentation, theme and sign out.

- **H, Get started, approved 2026-11-28 and built:** on Findings, above the feed, a new
  organization's six steps read from what is already set (`get-started.test.ts`, red first);
  each person sees only the steps they can do; hidden per browser; gone once all are done.
- **First-run review fixes:** "Go to the console now" on the first-span screen, with the one-time
  key warning; "Copy all three"; "Settings → Ingest keys"; the account row's name no longer cut
  (`OnboardingScreen.test.ts`, red first).

## Deviations from the drawing, for the founder

1. **The page edge is 16 px, not 24.** Every telemetry screen already starts 16 px from the menu
   and its rows line up with it; only Settings was off, and it now matches (design-system.md).
2. **"What lines up with this" comes before "What moved"**, as F3's AC86 requires; the drawing had
   them the other way round.
3. **Explanations stays its own settings screen**, not a tab of AI: F3 FR54 keeps it working with
   AI off.
4. **No hairline under section headings**: sections are set apart by 32 px instead.
5. **`/findings` still answers 404**, a recorded decision in `router.tsx`.

## Not fixed

- **A React warning on the service page** ("called use() to suspend … but did not call use()"):
  present on the last commit too, so not from this change; not yet traced.
- **The AI region on the dev install** still holds the email saved before the fix. Clearing it
  ends the test and the owners' agreement, so it is the founder's to do.

- **Errors' "Deploy before" words stay as they are:** "none before", "none reported" and "not told"
  mean three different things (F28 D23), so they are not one missing value.
- **Dashboards keeps its templates as equal choices** (FR39's order); only Recently deleted moved.

- **A joined finding's strip reads its own issue,** so it shows "None" where the root's case has
  one; the case section above it leads to the root. Reading the root's links would need the
  root case's read on the strip too.
- **AC37d's rung 1 and 2 states** (`CopyButton`, `SelectField` under 44 px at 375 px) still wait on
  decision 120; the draft's two states now pass.

- Browser's saved value: the dev install's AI region still holds the email saved before the fix;
  clear it on the Provider tab.
- `connection-health.ts:81`'s lint warning predates this change.

## Validation

See the task's final run in the conversation of 2026-11-28 (`just check`, the e2e specs named
above).

## Last updated

2026-11-28.

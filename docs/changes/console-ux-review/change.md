# Change: console-ux-review — one set of sizes, the finding-to-pull-request chain on screen, one AI page

## Classification

Customer-facing layout and wording, from the founder's UX review of 2026-11-28. Approved as drawn
(the *Console Redesign* page) the same day, with the deviations listed under *Status*.

## Current behavior and problem

Measured in the running console on 2026-11-28:

- **Facts squeezed to 56 px** (zero at 1024 px): the facts list split into four columns by the
  window's width inside a column a third of it.
- **The chain was invisible.** The suggested fix and the pull request were the last section, about
  2,500 px down; five full-size case buttons were stacked, two of them filled; the list did not say
  which finding had a case, an issue, a fix or a pull request.
- **No one set of sizes.** Section headings were 14 px serif beside 11 px mono capitals used as
  headings; buttons side by side were 32 and 34 px; the two menus had 20 and 28 px rows; settings
  content started at 472 px and every other screen at 224 px.
- **AI setup across three pages and two GitHub Apps**, the AI page 13,700 px tall, named "AI
  wording".
- **The browser saved the signed-in person's email as the AI connection's region**: Chrome fills a
  saved login into a form with a password field and ignores `autocomplete="off"`.
- The tab said "Draft pull request open" over a closed one; "1 people"; "Agreed To"; the time range
  sat over screens it changes nothing on; *Delete this branch* stayed offered after the branch was
  gone (A14).

## Intended behavior

As the approved drawing: three heading levels and one button height (`packages/ui/tokens.css`,
`shared/components/Button.tsx`), one menu row (`shared/components/nav-row.ts`), the finding page
in reading order under a five-step strip (`features/findings/chain.ts`, `ChainStrip.tsx`), list
rows of one shape with chips from the API's new `chain` field, and one **AI** page with tabs and a
setup checklist (`features/aisettings/setup.ts`, `SetupChecklist.tsx`). Secret fields carry
`autocomplete="new-password"`. Migration 0087 records a deleted branch.

## Affected specifications and architecture

`docs/design/design-system.md` (*Type and spacing*, new). F5 spec and design: the `ai_off`
sentence. F22 FR46 holds: on a phone each case action has its own 44 px row and Resolve is never
next to Reassign, which sits under *More actions* with The signal's actions.

## Risks and rollback

Layout only, apart from migration 0087 (one nullable column) and the feed's added `chain` field.
Rollback is the previous release.

## Plan and validation

`status.md`.

## Status

`status.md`.

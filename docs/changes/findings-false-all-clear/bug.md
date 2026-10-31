# Bug: Findings says "All systems normal" with nothing behind it

## Reported behavior

The Findings screen says "All systems normal" on any project with no check down, even while most of its requests are failing. Found by the website review on 2026-10-31.

## Reproduction

`headlineFor` with a 90% error rate and passing checks returned `{ title: "All systems normal", wrong: false }`. The new test failed on it:

```
AssertionError: expected 'All systems normal' not to match /normal|healthy|all good|no problems/i
```

The same title came back with no checks, with checks not yet read, and with checks that had not run. This was reproduced against the function and the screen's tests, not by causing a live outage.

## Root cause

`apps/web/src/features/findings/headline.ts` fell through to "All systems normal" whenever no check was down. `useFindings` returns an empty feed because no engine exists, so nothing has assessed the system and the sentence had no evidence. The D1 fix in `docs/quality/console-review-2026-09-20.md` stopped it contradicting failing checks, and left the claim in place everywhere else.

Customer impact: nexovue is pre-deployment, so no customer is affected today. A customer in an outage would open the screen meant to show what needs them and be told everything is fine.

## Specification impact

F1 AC1.7 asks for an "all systems normal" statement. That needs the insight engine (Phase 2), so the catalog now says the screen reports only what the checks said until then. The wireframe matches.

Errors remain a reading and not a headline. Deciding when an error rate is abnormal is the engine's job, and a fixed threshold here would raise false alarms.

## Regression test

`apps/web/src/features/findings/headline.test.ts`, "never claims the whole system is healthy", and `FindingsView.test.ts`, "never claims overall health". The first was seen failing as above before the fix.

## Fix plan and validation

The headline now says what the checks reported, or "Here is what this project reported" when there are none. `just check` runs the rest.

## Status

[`status.md`](status.md).

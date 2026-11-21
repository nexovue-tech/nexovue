# ADR-074: A general suggestion is an example from the finding's facts only

## Status

Accepted 2026-11-02 by the founder (F5 spec D65 to D71); filed 2026-11-21 with F5 plan Step 2b,
which built it.

## Context

Where Nexovue cannot read a service's code, a person still wants more than the brief. Sending error
text, a hint or anything typed would carry attacker-written words to the model and undo F3's rule
that only checked facts leave (FD18). Storing the answer apart from F5's other suggestions would mean
a second set of states, grants and deletion rules.

## Decision

1. **The request is F3's `model_input` for the version, F5's fixed instructions, and a language
   picked from a closed list**, as one trusted part; no `Untrusted` part exists (FR34). The language
   is the one addition to F3's facts, and the founder may strike it (D65).
2. **Offered only where rung 3 cannot read code,** and only with a released likely cause or an
   `error_N` alias, and only when F3's facts may be sent at all (FR31, D66, D67; F3 FR50). Otherwise
   nothing about it is shown.
3. **Stored as a `finding_fixes` row of kind `general`** (migration 0077), in `proposing`, then
   `proposed`, `refused` or `failed`. It holds no repository, file or edit, so no source; it goes
   with its finding; viewers and support views read it; a token and MCP read none (FR37, FR38, D70).
4. **It reuses F5's action, queue and purpose**: `REQUEST_FIX`, `insight-fix` on `scheduler-insight`
   (capped at 2 of 8 slots, D56), `ai_runs` purpose `fix` with `fields_sent` naming `model_input`
   and `language`. It reserves through F3's one statement against F5's half of the month (D32), and
   uses one of the finding's 3 tries a day only when the model answered.
5. **Never a patch, download, pull request, email, alert or copy button** (BR15, D70).

## Alternatives rejected

- **The brief alone (no rung 2b).** Leaves people with no code access nothing to start from.
- **Send error text or a typed hint.** Undoes FD18 for a modest gain.
- **A table of its own.** Duplicates states, grants and deletion for one kind of row.
- **A purpose or counter of its own in F3.** Changes F3's checks and lists for no new protection.

## Consequences

- With causes held (F3's bar not yet recorded), rung 2b is offered nowhere in production, exactly as
  F3's wording is not run.
- The example's quality rests on F3's facts; the bed reports refusals, abstentions and verdicts.
- Step 3 widens `finding_fixes` with source columns and the CHECK that a general row holds none.

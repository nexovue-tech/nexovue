# ADR-101: Write routes exist only behind the founder's go, and every Step 7 path asks first

## Status

Accepted 2026-11-22, with F5 Step 7 (plan T7.2, T7.4, T7.5, T7.10). Builds on ADR-097.
Awaiting the founder's review with Step 7's diff.

## Context

Foundation item 33 has Steps 7 and 8 built now, with write access switched off in code until
the founder's written go (T6.5). ADR-097 gives the one switch, `write_access_on()`. Step 7
adds the first code that can write to a customer's repository; "off" has to mean that no path
reaches a GitHub write, and that nothing but the recorded go can change that, while the path
behind it is still proved end to end.

## Decision

1. **The write routes live on `FixWrites` only** (`connections/fix_github.py`), built through
   `FixGitHub.writes(go)`, whose constructor refuses while `write_access_on(go)` is False,
   before any write token is minted. Every write method takes a suggestion's id and builds
   `refs/heads/nexovue/suggestion-<id>` itself; `POST /pulls` always asks for a draft.
2. **Every Step 7 entry point asks the gate first, with no GitHub call while it is off:** the
   API's draft check, the click and **Delete this branch** answer 409 `writes_off` with its
   sentence; the check job refuses a `draft` check `writes_off`; the draft job puts an
   `approved` row back with `writes_off`; the branch deletion does nothing. The read hides the
   action with the same sentence in its place (FR10c).
3. **The product passes only the recorded go:** each entry point takes `go` with no default,
   and its caller passes `write_gate.WRITE_ACCESS_GO`, read at call time. A test forces the
   gate on by passing a `WriteGo` it builds, or by patching the constant inside one test.
4. **Structural tests hold it** (`test_fix_write_gate.py`): nothing in the product builds a
   `WriteGo`, nothing but `write_gate.py` assigns the constant, every `go` passed is that
   constant (or a function's own `go` handed on), no function defaults one, the gate imports
   no setting, environment or store, and every write path asks it.

## Consequences

As shipped, the fake's request log stays empty for a draft check and a draft job, which the
tests assert; the same tests, with a go passed, open a draft on the fake with exactly the
checked change. Turning writes on stays one reviewed commit to `write_gate.py` with
`status.md`'s T6.5 line (ADR-097). The `writes_off` sentence is new wording, the founder's
call (foundation decision 2; F5 status, open decision 90).

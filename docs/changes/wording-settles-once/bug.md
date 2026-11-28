# Bug: Wording settles once

Two defects in F3's AI wording (plan T5.3 to T5.5), found while investigating its spending-limit
test. Both reports were checked against the code before anything changed; both were confirmed.

## Reported behavior

1. **A late finish counts a run twice.** `store.finish_wording` moved a run's tokens from reserved
   to used without asking whether the run was still `started`. If F3's sweep had already called the
   run `died` and moved its whole reservation to used, a finish arriving afterwards counted its
   tokens again and subtracted its reservation a second time. `GREATEST(reserved - x, 0)` then
   erased *another* run's reservation, so a later run could reserve past the monthly limit.
2. **The wording suites' shared clock goes stale.** `wording_fixtures.NOW` was read once, at import.
   Its docstring said the stack's own AI sweep "never takes these rows before a test does". In a
   20-minute CI run that is false: the stack's sweep reads the real clock and calls any run older
   than `DIED_AFTER` (370 s) dead, so a run a test had in flight, or had left `started` on purpose,
   could be taken from under it.

## Reproduction

**Defect 1, deterministic.** The new live test
`test_insight_word_live.py::test_a_run_finishing_after_the_sweep_ended_it_changes_nothing`: one
run is killed and keeps its reservation (1542 tokens); a second run reaches its provider call, the
sweep runs (in its own thread and connection, as beat runs it) and calls that second run `died`,
then the call returns 400 + 60 tokens and the run finishes. Against the original `store.py` and
`word.py`:

```
>       assert await _month_totals(ai_install, ai_tenant) == (
            died.reserved_tokens,
            held.reserved_tokens,
        )
E       assert (2002, 0) == (1542, 1542)
```

Used is 1542 (the sweep's charge) + 460 (the late finish) = 2002, counted twice; reserved is 0, so
the killed run's 1542-token reservation was erased. With that reservation gone, a third run could
reserve 1542 tokens the month no longer had.

**Defect 2.** A harness (scratchpad only, not in the repository) runs the stack's sweep on the real
clock every 0.2 s for the whole session, as the CI stack's beat does every 300 s. With the
original suites and `NOW` set 20 minutes back, as it is 20 minutes into a run:

```
>       assert sorted(outcomes) == ["done", "stale", "stale", "stale"]
E       AssertionError: assert ['stale', 'st...ale', 'stale'] == ['done', 'sta...ale', 'stale']
FAILED tests/test_insight_word_live.py::test_four_parallel_jobs_of_one_version_make_one_call_and_one_result
1 failed, 42 passed        stack sweep took: {'died': 1, 'expired': 0}
```

The run this test holds at its provider for 0.5 s was taken as `died` mid-call. The tests that
leave a killed run `started` and assert on it before sweeping are exposed the same way, but lost
this race here because the window is milliseconds; in CI, over hundreds of runs, they need not.

## Root cause

1. `finish_wording` (`apps/scheduler/src/nexovue_scheduler/insight/store.py`) ran `_SETTLE` first and
   `_FINISH_RUN` second, and ignored `_FINISH_RUN`'s row count. `_FINISH_RUN` already required
   `outcome = 'started'`, so the run row was protected, but the money moved regardless. The sweep
   (`sweep_died`) is safe: it locks the `started` rows it selects and settles in the same
   transaction. The two also took their row locks in opposite orders (finish: the month row, then
   the run row; sweep: the run row, then the month row), so the two could deadlock. That last
   point is from reading, not observed.
2. `apps/scheduler/tests/wording_fixtures.py` bound `NOW` at import and used it as the default time
   of every row the helpers wrote (`connect`, `finding`, `version`, `run_word`). A run's age is
   measured against the stack sweep's real clock, so every test after the first six minutes of a
   session wrote runs the stack's sweep was entitled to take. The canary (`test_insight_canary.py`)
   derived its telemetry window from the same `NOW`.

   A related isolation gap, found reading the same tests: the two sweep tests ran the sweep (which
   reads every organization's rows) with a clock `DIED_AFTER` + 1 minute *ahead*, so their sweep
   could take another test's run in flight in the other xdist worker. This one was not reproduced.

## Specification impact

None to the specification. The design's *Limits* gains one sentence stating a rule it left
implicit: a run is settled once, and a finish after the sweep changes nothing. Nothing in the
spec or design said what a late finish does; this record decides it (see *Fix plan*).

**Customer impact, had there been customers.** Only reachable if a wording child outlives its hard
limit plus the lease's grace (the sweep's threshold), which the worker's hard kill should prevent:
a memory kill that leaves the process alive, or a stalled kill, would be enough. Then an
organization's month could show more used than was spent, and, worse, another in-flight run's
reservation could be erased, letting wording and chat (F4 reserves through the same
`reserve_month()`) together reserve past the limit the owner set. The limit is a money control the
owner relies on, so after the first customer this is a billing-trust defect, not a cosmetic one.
Defect 2 affects tests only: a CI run red at random on correct code, which teaches re-running
rather than reading.

## Regression test

- `test_a_run_finishing_after_the_sweep_ended_it_changes_nothing` (new, `test_insight_word_live.py`):
  asserts the died run's row is the sweep's (`died`, input = its reservation, estimated), the other
  run's reservation survives, the month's used and reserved sum to what the sweep left, no words
  land (`failed`/`died`, no wording), the job returns `stale`, and `word` logs one warning.
- `test_the_sweep_settles_a_dead_question_and_charges_what_it_held` (extended,
  `test_assistant_account_live.py`): F4 chat's late spend and late final write after its sweep move
  nothing. Chat was already correct (see *Fix plan*); this pins it.
- Defect 2 is covered by the suites themselves under the harness above; there is no separate test.

## Fix plan and validation

**Fix 1.** `finish_wording` now runs `_FINISH_RUN` first and stops when it changed no row; only then
`_SETTLE` and the words or state, all in one transaction. The run row's lock is taken first, the
same lock the sweep holds, so one of the two ends the run and the other finds it ended; both now
take the run row before the month row. It returns `"written"`, `"not_written"` or
`"already_ended"`; `word` logs `already_ended` as a warning (only a child that outlived its hard
limit gets there) and returns `stale`, so nothing is retried.

**What a late finish does: nothing.** The sweep's charge (the whole reservation, flagged
estimated) stands; the late tokens are not added; the words never land. Alternatives rejected:
counting the late finish's excess over the reservation (more exact, but the run row would then
disagree with the month, and F4's chat already drops a late spend the same way), and writing the
words (the design's rule is that a died run's explanation is `failed`, reason `died`, and the
sweep has already written that). The cost: a late finish whose actual passed its reservation is
under-counted by that excess. The reservation is the input estimate plus the most the run may
write, so the excess is the estimate's error at most.

**Other late writers checked.** F4 chat (`assistant_store.py`) moves money only through
`assistant_record_spend` and `assistant_finish_message` (migration 0073). Each locks the question
row, acts only while it is in flight or still holds a reservation, and subtracts at most what that
question holds, so a late write after chat's sweep moves nothing and can never erase another's
reservation. Chat's `finish_run` already requires `started` and moves no money. The connection
test's `finish_check` meters a late finish's tokens once, but a test reserves nothing (A51) and the
sweep charged it 0, so nothing is counted twice; left as it is.

**Fix 2.** A `now` fixture in `wording_fixtures.py` reads the clock when each test starts;
`connect`, `finding` and `version` take it as a required keyword, `Connected` carries it, and
`run_word` uses it unless a test names another time. Every assertion is unchanged in strength;
the one that read `NOW` (`run.started_at == NOW`) reads `now`. The canary's window is derived from
the test's `now`. The two sweep tests (and the new one) start their run 3 minutes back, inside
`DIED_AFTER`, and sweep 1 second past it, so the stack's sweep cannot take the run first and the
test's sweep reaches no other test's run in flight. The docstring says what is true.
`docs/engineering/testing.md` gains the rule (proposed; see `status.md`).

**No migration.** Validation is in [`status.md`](status.md).

## Status

Implemented, awaiting review. See [`status.md`](status.md).

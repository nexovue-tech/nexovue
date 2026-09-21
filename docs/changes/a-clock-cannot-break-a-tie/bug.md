# Bug: lists a human reads could reorder themselves between two loads

## Classification

Bug — non-deterministic ordering in a read a customer and an operator both look at.

## Symptom

The admin's member list for an organization returned `[viewer, owner]` where it had returned
`[owner, viewer]` for weeks, on the same commit and the same data. Nothing had changed but the query
plan.

## Reproduction

`just check` on 2026-09-21, with the Python suite running six workers:

```
>       assert [(member.role, member.status) for member in page.members] == [
E       AssertionError: assert [('viewer', '...r', 'active')] == [('owner', 'a...', 'invited')]
E         At index 0 diff: ('viewer', 'invited') != ('owner', 'active')
```

## Root cause

`CreatedAt` in `data/postgres/base.py` defaults to `server_default=text("now()")`, and Postgres
`now()` is **transaction start time** — not statement time, and not wall-clock at insert. Every row
written inside one transaction therefore carries an identical `created_at`, to the microsecond.

An owner and the teammate they invite are created in one request, so they tie. `ORDER BY created_at`
alone leaves their relative order to the planner, and the planner is free to change its mind.

The parallel gate did not cause this; it revealed it. The rows had been tied since the feature was
written, and a serial run happened to keep producing the same plan.

`members/queries.members_of` — the customer-facing list — already ordered by `(created_at, id)`.
The admin copy of the same query was written without the tiebreak, and two more orderings had the
same shape.

## Customer impact

nexovue is pre-deployment, so no customer has seen it. After the first one: a member list that
reorders itself between two loads of the same page, and an incident's notification list that shows
three channels in a different order each time it is opened. Neither loses data; both make a reader
distrust the screen, which on an audit-adjacent surface is the expensive kind of doubt. It is also
the class of defect that hides worse ones — an intermittent difference nobody can reproduce gets
blamed on the reader.

## Fix

A stable tiebreak on the three orderings that lacked one:

- `admin/queries.members_of` — `(created_at, id)`
- `admin/queries` projects — `(created_at, id)`
- `alerts/queries` notifications — `(created_at, id)`

Ids are **uuidv7**, so the tiebreak is itself time-ordered: it agrees with the order of creation
rather than merely being repeatable.

## Regression coverage

`test_the_member_list_holds_its_order_when_a_clock_cannot_separate_two_rows` asserts both halves:
that the two rows really do share a `created_at` — without which the test would prove nothing — and
that the order is the one creation produced. **Seen red** by reversing the tiebreak to `id.desc()`.

It fails loudly if the premise ever stops holding, rather than passing quietly on a tie that no
longer exists.

## Status

See `status.md`.

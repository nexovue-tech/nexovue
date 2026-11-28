# ADR-088: A case note is chat's knowledge by copy, gone with its note, inert, and ranked above nexovue's own lines

## Status

Accepted 2026-11-21 with F4 plan Step 7 (T7.1, T7.2), inside the approved F4 design
([`design.md`](../features/ai-chatbot/design.md), section 6, *Writers* and *Why three nullable foreign
keys*; D58; FR28 to FR30, FR41; BR7, BR16) and
[ADR-067](ADR-067-knowledge-is-chunks-in-postgres-found-by-keyword-always.md), which it extends to
people's case notes. Built against fake providers only (foundation item 31). Two choices made while
building, which the founder has not yet answered, are named under *Open* and not decided here.

## Context

F22 gives a case its notes (`finding_notes`): people's own words, deletable by their author or an
admin, gone with their finding at retention. F4's knowledge must find them for "how did we fix this
last time?" without F22's note service learning anything about chat, without a note surviving its own
deletion in a chat index, and without a teammate's words becoming a command the panel offers to run.
The specification also asks that a person's note outrank a hundred composed fact lines (FR29, AC83).

## Decision

1. **A note is copied, not referenced at query time.** The scheduler's knowledge sweep, in
   organizations with chat on, copies each note with no chunk into one chunk of its whole text (FR30,
   D58). F22's note service is unchanged; a note never depends on this table; the gap is at most one
   sweep.
2. **The copy goes with its note by the database's own cascade.** `knowledge_chunks.note_id` keys
   `(org_id, note_id)` to `finding_notes (org_id, id)` `ON DELETE CASCADE` (migration 0080), so
   deleting a note, its finding by any path, or its organization takes the chunk in the same statement
   (BR16, AC38). A chunk cannot name another organization's note.
3. **A note always saves.** The sweep writes a note's chunk while its project holds fewer than the
   5,000-chunk cap of people's words, evicting fact lines first; past it the note is skipped, never
   refused, and the Chat section counts what is not searchable (FR32, AC83).
4. **A note's words are the team's, and inert.** Search returns a note's row with the date it was
   written and its case, never its author's name; a sentence citing one carries the notes label; its
   quote is read again from Postgres, labelled "A teammate wrote this", folded when it reads as a link
   or a command, and never one a sentence may tell the reader to run (BR7, AC40, AC63). Only a
   runbook's quote may be.
5. **What people wrote comes before what nexovue composed.** Keyword and meaning search each order
   runbooks' and notes' chunks before fact lines, and the reciprocal-rank merge keeps that order.
6. **A resolved case writes and keeps a fact line**, composed from the finding's columns alone
   (`nexovue_platform.knowledge_facts`), which the API's *Save as runbook* copies with a note into a
   runbook that outlives the finding (FR41, AC95).

## Consequences

- A note's text reaches the AI provider, in search results and to be embedded, once an owner agreed to
  chat at *facts and text*; the consent's disclosure version rises to 3, and F22's *What leaves this
  server* and note box say so while chat is on.
- A deleted note is gone from every later answer at once; an earlier answer quoting it reads "gone".
- A note chunk stays when chat is later turned off, as fact lines do, until its note goes.
- The AI account reads a note chunk's text only to embed it, through its existing column grants; it
  cannot read which note or case a chunk is.

## Open (status.md, open decisions 39 and 41)

- Which case resolutions keep a fact line: built as a person's resolve and a linked tracker issue's
  close, not a false alarm, a new normal, no activity, or recovery untouched.
- Point 5 orders runbooks, not only notes, above fact lines; FR29 names notes alone.

## Alternatives rejected

- **Writing the chunk in F22's note transaction.** It would make F22's service import chat code and
  write a copy for organizations that never turn chat on (D58).
- **Searching `finding_notes` directly.** It would put a second free-text search and its own
  ranking, labelling and embedding beside the chunk table, and leave meaning search without notes.
- **Ranking by text score alone.** A hundred composed lines naming the same service outrank one note
  by `ts_rank`, which is what AC83 forbids.

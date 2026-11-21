# ADR-079: Embedding is metered spending through F3's month, proved by a check, and never stands between chat and keyword search

## Status

Accepted 2026-11-21 with F4 plan Step 6 (T6.0 to T6.3), inside the approved F4 design
([`design.md`](../features/ai-chatbot/design.md), section 6, *Embedding*; D62, D81) and
[ADR-067](ADR-067-knowledge-is-chunks-in-postgres-found-by-keyword-always.md)'s point 5, which this
records as built. Built against fake providers only; no real embedding call has been made (foundation
item 31).

## Context

ADR-067 decided meaning search in outline: a `vector` column with no size, added by `setup`, an exact
scan filtered by the model, RRF beside keyword search. Building it raised four questions the design
answered only in part: how an embedding call is paid for and counted, what proves a model works before
anything is sent, what the AI account may touch, and what a question's own vector is and when it goes.
Every embedding call sends a team's text to a provider, so it is spending and disclosure at once; and
an install without the extension, or with a provider that has no embedding model, must lose nothing
but meaning search (BR17, AC43).

## Decision

1. **`setup` provisions meaning search** (`provision_vector()`): `CREATE EXTENSION IF NOT EXISTS
   vector`, then `knowledge_chunks.embedding`, `assistant_steps.query_embedding` and
   `query_embedding_model`, each added only if missing, under a 5-second lock timeout. A refusal is one
   line and `setup` goes on. The AI account's grants on these columns are applied only when they exist.
2. **Every embedding call is an `ai_runs` row, purpose `embed`,** written before the call. The sweep's
   calls reserve their estimate from F3's month under chat's share (`reserve_month`,
   `limits.chat_ceiling`) in the same transaction, and settle through `store.settle_run`, so a run
   F3's sweep already called `died` settles nothing and writes no vector. A search phrase's call is
   paid from its question's own reservation (`assistant_record_spend`), as chat's calls are.
3. **Nothing is embedded until a check passes on exactly the saved values** (AC85): the model, the
   connection used, its address, format and key id, fingerprinted. The check is a job on the AI
   container, one call with nexovue's own words, and its result lands only if those values still stand.
   Chat must also be on at *facts and text* with an owner's consent, AI on with a monthly limit, and the
   organization live. All of it is read again before each call.
4. **The AI account reads a chunk's text and whose it is, and writes its vector,** never reads one.
   Opting a project out nulls its vectors at the next sweep (AC70); deleted projects and organizations
   are never sent.
5. **A question's phrase is embedded by the worker and its vector stored on its step,** with the model
   that made it. The search route is told the step, reads the vector in SQL, refuses a step of another
   question, and compares only chunks of that model and size. `assistant_finish_message` nulls both
   columns when the question ends, by dynamic SQL so it runs on an install without them.
6. **Failure is keyword search.** A failed or refused call leaves its chunks due and charges only what
   may have been billed; a failed phrase embedding never ends the question. The result and the step
   say `keyword` or `hybrid`, and the panel says "Searched by keyword only."

## Consequences

- Embedding counts against the same month as wording and chat; a busy month delays it, never the
  reverse.
- A model chosen after the check needs a new check; changing the model re-embeds in the background,
  and meaning search returns less until done (FR31).
- Titan embeds one text a call, so with 8 calls a sweep a Bedrock install embeds slowly; the batch
  sizes are guesses for T6.4 and the bed to move.
- The bulk count in the consent preview, and the chat disclosure version raised to 2, make the wider
  sending an owner's decision again.

## Alternatives rejected

- **Embedding with no reservation, as F3's connection test counts.** The sweep sends every chunk; an
  unreserved sweep could pass the month's limit by a whole backlog.
- **Trusting the setting without a check.** A wrong model name would fail every sweep and every phrase
  silently, while the screen said meaning search was on.
- **Sending the phrase's vector over HTTP.** 1,536 numbers do not fit a GET; storing it on the step
  keeps the one-door read (ADR-055).
- **A migration for the columns.** A missing extension would stop `setup`, and every service with it.

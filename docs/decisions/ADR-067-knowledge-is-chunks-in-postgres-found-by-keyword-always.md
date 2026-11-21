# ADR-067: Chat's knowledge is chunks in Postgres, found by keyword always, each chunk tied to its source by a cascade

## Status

Accepted 2026-11-02 by the founder with the F4 design ([`design.md`](../features/ai-chatbot/design.md),
*ADR proposal: knowledge in Postgres with no vector index, with keyword search always available*,
which refines decision 0004, pgvector inside Postgres). Filed 2026-11-21 with F4 plan Step 5, which
built the table, its writers and keyword search (migration 0076). The vector half, meaning search with
an exact scan and RRF, is Step 6's and is not built yet; this record covers it as decided, and Step 6
amends it if its measurement (T6.4) moves anything.

## Context

Chat answers "what do we do when…" and "how did we fix this before" from what a team wrote and from
what nexovue recorded, not from telemetry, which it reads live through the API
([ADR-055](ADR-055-chat-reads-through-the-api-as-the-person-on-a-ticket.md)). A self-hosted install
has one Postgres of 1 GB and no spare store. Customers' providers differ: Anthropic has no embedding
model, and some installs cannot create the `vector` extension, so keyword search must stand alone.
Text a team writes is a way in for a planted instruction, so the writers must be few and known, and a
deleted source must leave nothing behind for chat to find.

## Decision

1. **One table, `knowledge_chunks`, in Postgres,** beside `runbooks`. Each row is a runbook's piece or
   a fact line nexovue composed from a recovered finding (notes join in Step 7). A generated
   `content_tsv` column, indexed with GIN, gives keyword search with no writer of its own.
2. **One nullable foreign key per source, each `ON DELETE CASCADE`,** and a check that exactly the key
   its kind names is set. Deleting a runbook, or a finding by any path including the expiry sweep's
   bulk deletion, takes its chunks in the same statement. The runbook and finding keys carry the
   project, so the database refuses a chunk in another project than its source.
3. **Three writers only, each held by a test:** the runbook service, in the runbook's own transaction,
   splitting by heading under a ceiling of 18 chunks a runbook whatever its headings; the scheduler's
   knowledge sweep on the shared queue, writing a line composed from a finding's columns alone, at
   most one per service, signal and day, and dropping it if the finding stops being `recovered`; and
   (Step 7) notes. Model words, titles, logs and names failing the strict pattern never enter.
4. **Keyword search is always there:** `content_tsv @@ websearch_to_tsquery('english', q)`, ranked,
   20 at most, read by chat's loop alone through `GET /assistant/knowledge/search` on the question's
   ticket (D31), never by a token or through MCP (FR37), and not offered at data level *facts only*.
5. **Meaning search, when Step 6 builds it,** is an exact scan over a `vector` column of no fixed size
   that `setup` adds (never a migration, so an install without the extension loses nothing else), with
   the model's name beside each vector and on the question's own vector, so two models' vectors are
   never compared; merged with keyword results by RRF. No HNSW until a measurement over 200 ms asks for
   one (D84).
6. **One cap, one enforcer:** 5,000 chunks a project, of which fact lines at most 1,000, the oldest
   evicted first by the knowledge sweep. Runbooks fit by construction (200 at 18 chunks is 3,600), so
   a person's writing never fails because of chat's index.

## Consequences

- No memory at rest and nothing to tune for keyword search; it costs one GIN index per install.
- Search quality with keywords alone is plain: every word must match, alternatives need `OR`, and a
  question's words used as they are may find nothing. The bed (T5.7, T6.6) measures it.
- A runbook's text is the team's: shown only as an inert quote, labelled with its age, the one quote
  a sentence may point at as something to run, and never folded. A planted instruction in it is
  fenced, masked for credential forms, and causes no write and no way out (AC40).
- Runbooks and chunks of a deleted project or organization wait for the next expiry sweep, as the rest
  of a deleted project's rows do.
- Fact lines of a project finding, and of a service whose name fails the strict pattern, share one
  unnamed line a signal and day.

## Alternatives rejected

- **A separate vector store.** A second store to run, back up and secure on a 1 GB install.
- **A polymorphic `source_id`.** No cascade could enforce it, so a deletion path could forget it.
- **Meaning search only.** Leaves Anthropic customers and installs without the extension with nothing.
- **HNSW from day one, or a fixed vector size.** Memory at rest and a schema change per model, for a
  set small enough to scan.
- **Indexing model words or raw telemetry.** Model words would launder a guess into a fact, and
  telemetry is read live.

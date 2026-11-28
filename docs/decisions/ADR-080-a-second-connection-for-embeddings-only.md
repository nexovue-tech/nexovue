# ADR-080: An organization may add a second connection for embeddings only, with its own key, agreement and check

## Status

Accepted 2026-11-21 with F4 plan Step 6 (T6.1), building the approved F4 decision D61 (spec, D15
changed in review; design, *Differences* 7a). Migration 0078.

## Context

F3 gives an organization one AI connection. Anthropic has no embedding model, so a Claude customer
would never get meaning search; other customers may want a cheap or local embedding model beside a
large chat model. Text sent to a second address is a disclosure the chat consent did not name.

## Decision

- `assistant_settings` holds the embedding model and, optionally, a second connection: wire format
  (OpenAI-style or Bedrock, the two that embed), address and key, all or none. The address is held to
  F3's rules (FR23) on save and again at call time; the key is sealed under `AI_SECRET_KEY`, purpose
  `assistant-embedding-key`, bound to the organization and a hash of the address, and joins
  `keys.SEALED_COLUMNS`. A moved address drops a key not given again.
- **An owner agrees to it separately** (`CONSENT_AI_DISCLOSURE`), to F3's consent fingerprint of that
  connection; a new address or key ends the agreement. Saving is an admin's (`MANAGE_AI`). Both are
  audited with field names and the host, never the key.
- With no second connection, the chat connection embeds, and a model on a connection that cannot
  embed is refused in a sentence.

## Consequences

- Two keys to rotate; the reseal covers both.
- The second connection has no preview of its own beyond the Chat section's sentence and the bulk
  count (status, open decision 31).

## Alternatives rejected

- **Later, after Step 6.** Leaves Claude customers with keyword search only.
- **Reusing F3's connection table with a second row.** F3's row is one per organization, with its own
  switch, limit and test; an embedding-only connection has none of those.

# Dead letters

What to do when telemetry has been set aside as unprocessable. Written from a run on the development
stack on 2026-10-11 (`docs/changes/remaining-recommendations/`, C5).

## What a dead letter is

A record the worker could not process is **parked** on the signal's dead-letter topic (`spans-dlq`,
`logs-dlq`, `metrics-dlq`, `profiles-dlq`) instead of blocking its partition. Nothing drains these
topics: a parked record waits for a person, for the topics' 7-day retention, after which it is gone.

Two kinds:

- **A raw record** — it never decoded, or a store refused it whole. The value is the original bytes,
  **unmasked**: masking runs after decoding, so treat it as customer data.
- **A masked row** (`nexovue-parked-payload: row-json`) — one stored row the store refused after
  masking. It cannot go back on a raw topic.

## How you know

`nexovue_worker_dead_letter_records{topic}` on the worker's `/metrics` (`monitoring.md`). Any increase is
worth a look.

## Procedure

1. **List.** `just dlq list -topic logs-dlq` (newest 50; `-limit` for more). On a deployed install:
   `docker compose exec worker /dlq list -topic logs-dlq`. Each line is the dead-letter offset, when it
   was parked, by which consumer group, where it came from (`topic[partition]@offset`), the tenant,
   and the reason (cut to 200 characters — a store's refusal can quote the value it refused).
2. **Find the cause in the reason.** A decode failure is a sender or receiver problem; a store refusal
   names what the store objected to.
3. **Fix the cause first.** A record replayed before its cause is fixed is parked again — on the dev
   run, a malformed record replayed from `logs-dlq` offset 2 came straight back as offset 3, which is
   exactly what should happen.
4. **Replay.** `just dlq replay -topic logs-dlq -from 12 -to 14` puts each record's original key and
   bytes back on its source topic, where it goes through every stage again, masking first. Masked rows
   are skipped and named.
5. **List again** to confirm nothing came back.

## Not yet established

- No replay has been run against a deployed install.
- A parked record's tenant is erased with the topic's retention, not with the organization (ADR-014,
  *Consequences*).

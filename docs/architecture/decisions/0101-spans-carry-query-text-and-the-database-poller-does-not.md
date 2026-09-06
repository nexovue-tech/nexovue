# 0101 — Spans carry query text and the database poller does not

- **Status:** Accepted
- **Date:** 2026-09-06
- **Extends:** [0059](0059-masking-defaults-are-high-confidence-only.md), whose rule decides this
  and had simply never been applied to this attribute.

## Context

Two doors handle the same data differently, and until now neither difference was chosen.

**Door 4b refuses to send SQL**, and `postgres_stats.py` argues it well:

> *"`pg_stat_statements` normalizes literals out, but not always — a query built by string
> concatenation carries its parameters into `query`, and those are customer data we have no
> business storing."*

It sends `db.query.hash` and nothing else.

**Door 1 stores the SQL.** A span read out of the dev store on 2026-09-06:

```json
{"db": {"namespace": "astronomy_db",
        "query": {"text": "INSERT INTO accounting.\"order\" (order_id) VALUES (@p0); …"}}}
```

Same database, same statements. That one is parameterised — `@p0` — but that is the .NET SDK's
behaviour, not a guarantee. Concatenated SQL has no parameters to strip, other SDKs differ, and
sanitisation is usually configurable.

**Masking does not close the gap, and this was read rather than assumed.** Of the five default
rules, `Rules.masksValues()` is true only for `card_numbers` and `authorization_credentials`, so
only a Luhn-passing number or a `Bearer`/`Basic` credential inside the SQL is caught.
`credential_keys` matches the attribute *key* against `password`, `secret`, `token`, `api_key` —
and the key here is `db.query.text`, which contains none of them. An email address, a phone number
or a national ID inside a `WHERE` clause is stored verbatim for the retention period.

## Decision

**The asymmetry is deliberate, and it is asymmetric because the cost of refusing differs.**

**Door 1 keeps `db.query.text`.** An engineer looking at a slow database span needs the statement.
A hash alone means leaving the tool to look it up somewhere else, which is the moment the tool
stops helping — and the span is often the only place the statement appears at all, because a
one-off slow query may never reach `pg_stat_statements`' top hundred.

**Door 4b keeps refusing it**, because refusing costs it nothing. It reads
`pg_stat_statements`, where the hash *is* the identity and the customer can resolve it in their own
database. A door that gains nothing from carrying customer data should not carry it.

**The default masking rules do not change.** [ADR 0059](0059-masking-defaults-are-high-confidence-only.md)
already settled the test: on by default only for what *can never be needed*. Query text can be
needed — it is the most useful attribute on a database span. Masking it by default would make every
customer's traces worse to protect the few who want it gone, and masking is irreversible.

**The control already exists and is documented instead.** `masking_config` accepts
`mask_keys`, so a project sets:

```json
{"mask_keys": ["db.query.text"]}
```

and the worker removes it before the write. No code was added for this decision; what was missing
was the decision and somewhere for a customer to read it.

## Alternatives considered

**Mask `db.query.text` by default.** Rejected on ADR 0059's own test. It is a value that is
routinely needed for debugging, the masking is irreversible, and a customer who discovers their
traces are less useful cannot get the data back.

**Make door 4b send the text too, for consistency.** Rejected. Consistency is not the goal; not
holding data we do not need is. Door 4b has a hash that identifies the statement exactly, so
carrying the text would be gratuitous.

**Add a per-record redaction flag like Grafana's `disable_query_redaction`.** Rejected as the wrong
shape here. `mask_keys` is the existing, general mechanism and it already covers this; a second,
attribute-specific switch would be a parallel system for one key.

## Consequences

**A security questionnaire has an answer.** *"Do you store the contents of our SQL queries?"* is
now: yes on traces, because that is how a slow query is debugged; no from the database connector,
which never needed it; and it is removable per project with one line of configuration.

**The customer-facing note lives in
[docs/product/database-monitoring-setup.md](../../product/database-monitoring-setup.md)**, under
what we collect — including that most SDKs parameterise but none guarantee it.

**The same key-versus-value gap exists elsewhere and is not closed here.** `url.full` on RUM
records carries the query string and fragment for exactly the same reason: `credential_keys`
matches keys, and `url.full` names no credential. That one is open in
[open-decisions §1c](../../delivery/open-decisions.md) and is a different decision, because unlike a
SQL statement a URL's query string has a dedicated attribute that is already off by default.

**What this does not do is make SDK-side sanitisation our problem.** A customer who wants values
never to leave their process should configure it in their SDK; masking here is the second line,
applied after the data has already crossed the wire.

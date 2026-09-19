"""F21: deciding who is told when something breaks, and telling them.

Evaluation runs where the moment happens — a check's state change is judged in
the transaction that records it — and delivery reads a Postgres outbox written in
that same transaction (ADR-012, ADR-013). Nothing here sends inside a transaction
that decided something.
"""

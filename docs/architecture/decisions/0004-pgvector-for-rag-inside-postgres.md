# 4. pgvector for RAG, inside Postgres

- **Status:** Accepted
- **Date:** 2026-08

## Context

The chatbot and insight engine need embeddings of past findings, resolved cases and runbooks. A
dedicated vector database is the obvious alternative.

## Decision

Use pgvector inside the Postgres we already run.

## Consequences

One less system to operate, back up and secure. If vector volume ever outgrows what Postgres does
comfortably, this becomes a migration rather than a configuration change.

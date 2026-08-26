# 28. Frontend router = TanStack Router

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

In an observability UI the URL carries state that matters: time ranges, filters, service names,
trace IDs. Every shared link and every back-button press depends on those being correct. React
Router v8 was the alternative.

## Decision

TanStack Router. It types search params end-to-end and pairs natively with TanStack Query.

React Router v8 has the larger ecosystem but weaker search-param inference and a yearly major
cadence with a migration history.

## Consequences

Search-param bugs — the most likely class of bug in this UI — become type errors. Cost: a smaller
ecosystem, and route definitions are more ceremonious than React Router's.

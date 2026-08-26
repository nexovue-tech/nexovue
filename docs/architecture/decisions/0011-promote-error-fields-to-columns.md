# 11. Promote error fields to real columns

- **Status:** Accepted
- **Date:** 2026-08

## Context

Error grouping is a headline feature. If exception type and message live inside a JSON blob, every
grouping query pays parse cost on every row.

## Decision

`exception_type` and `exception_message` become real columns — or a dedicated errors materialized
view. Span events stay as a JSON string in v1.

## Consequences

Error grouping filters and aggregates at column speed. The cost is schema surface: these two fields
are now fixed, and normalization must populate them from span events for every door.

-- Refills the rebuilt table. Runs after the view exists, not before: readers
-- aggregate min/max, so an overlap between backfill and live writes is
-- idempotent while a gap is not.
INSERT INTO span_trace_lookup
SELECT tenant_id, trace_id, min(timestamp), max(timestamp), min(received_at)
FROM spans GROUP BY tenant_id, trace_id

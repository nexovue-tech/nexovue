-- Refills the table 000013 rebuilt. Without this, opening any trace older than
-- the rebuild returns "no such trace" until `spans` ages out beneath it.
--
-- Runs *after* the view is back, not before, because readers aggregate
-- min(span_start)/max(span_end): a trace counted by both the view and this
-- statement yields the same window, so overlap is harmless and a gap is not.
-- (The same ordering would be wrong for a summing rollup — see 000018.)
--
-- The spill threshold is not a tuning knob: one group per trace over the whole
-- retention window is an unbounded aggregation, and on a 2.5 GiB dev server it
-- exhausts memory outright rather than spilling.
INSERT INTO span_trace_lookup
SELECT
    tenant_id,
    trace_id,
    min(timestamp) AS span_start,
    max(timestamp) AS span_end
FROM spans
GROUP BY tenant_id, trace_id
SETTINGS max_bytes_before_external_group_by = 500000000

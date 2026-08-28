-- The MV writes into `span_trace_lookup`, which 000013 has to recreate: a
-- partition key cannot be altered. Dropping the view first keeps it from
-- writing into a table that is being exchanged underneath it.
DROP VIEW IF EXISTS span_trace_lookup_mv

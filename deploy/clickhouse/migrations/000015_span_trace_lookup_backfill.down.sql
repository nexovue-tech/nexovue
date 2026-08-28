-- The rows this added cannot be told apart from the view's, so the inverse of
-- filling the table is emptying it. 000013's down replaces the table anyway;
-- this only matters when rolling back a single step.
TRUNCATE TABLE span_trace_lookup

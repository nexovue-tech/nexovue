-- The table is derived: every row is recomputable from `spans`, and the trace
-- list falls back to grouping the window when it is absent. One line is the
-- whole rollback.
DROP TABLE IF EXISTS trace_summaries

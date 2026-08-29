-- The second materialized view on `spans`. Plain MergeTree, so a re-fire
-- duplicates rows rather than corrupting a sum — harmless to readers, which
-- look a trace up rather than aggregate it. Guarded anyway: it is the same
-- root, the cost is one window, and "harmless today" is a reason that stops
-- being true the day someone counts these rows.
ALTER TABLE span_trace_lookup MODIFY SETTING non_replicated_deduplication_window = 1000

-- `insert logs` retries the same way spans do and had the same gap, so a lost
-- acknowledgement wrote the batch twice. No materialized view reads this table,
-- so the window alone is the whole fix here.
ALTER TABLE logs MODIFY SETTING non_replicated_deduplication_window = 1000

-- 000044's down has already swapped the tables back, so the rows are in
-- metrics_sum_rebuild. Copy them back before 000042 drops it: a rollback that
-- discards the contents is not a rollback.
INSERT INTO metrics_sum (`timestamp`, `start_timestamp`, `tenant_id`, `project_id`, `service_name`, `scope_name`, `scope_version`, `metric_name`, `metric_unit`, `metric_description`, `resource_attrs`, `attrs`, `flags`, `value`, `aggregation_temporality`, `is_monotonic`, `exemplars.timestamp`, `exemplars.value`, `exemplars.trace_id`, `exemplars.span_id`)
SELECT `timestamp`, `start_timestamp`, `tenant_id`, `project_id`, `service_name`, `scope_name`, `scope_version`, `metric_name`, `metric_unit`, `metric_description`, `resource_attrs`, `attrs`, `flags`, `value`, `aggregation_temporality`, `is_monotonic`, `exemplars.timestamp`, `exemplars.value`, `exemplars.trace_id`, `exemplars.span_id` FROM metrics_sum_rebuild
SETTINGS max_threads = 1, max_insert_threads = 1, max_block_size = 8192,
         max_insert_block_size = 8192, min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432

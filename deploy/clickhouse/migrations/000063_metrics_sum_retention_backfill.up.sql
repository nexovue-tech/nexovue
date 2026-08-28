INSERT INTO metrics_sum_rebuild (`timestamp`, `start_timestamp`, `received_at`, `retention_days`, `tenant_id`, `project_id`, `service_name`, `scope_name`, `scope_version`, `metric_name`, `metric_unit`, `metric_description`, `resource_attrs`, `attrs`, `flags`, `value`, `aggregation_temporality`, `is_monotonic`, `exemplars.timestamp`, `exemplars.value`, `exemplars.trace_id`, `exemplars.span_id`)
SELECT `timestamp`, `start_timestamp`, `received_at`, 395, `tenant_id`, `project_id`, `service_name`, `scope_name`, `scope_version`, `metric_name`, `metric_unit`, `metric_description`, `resource_attrs`, `attrs`, `flags`, `value`, `aggregation_temporality`, `is_monotonic`, `exemplars.timestamp`, `exemplars.value`, `exemplars.trace_id`, `exemplars.span_id`
FROM metrics_sum
SETTINGS max_threads = 1, max_insert_threads = 1, max_block_size = 8192,
         max_insert_block_size = 8192, min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432

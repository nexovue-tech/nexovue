INSERT INTO spans (`timestamp`, `received_at`, `tenant_id`, `project_id`, `trace_id`, `span_id`, `parent_span_id`, `service_name`, `span_name`, `span_kind`, `duration_ms`, `status_code`, `status_message`, `http_method`, `http_route`, `http_status`, `deployment_version`, `resource_attrs`, `span_attrs`, `events`)
SELECT `timestamp`, `received_at`, `tenant_id`, `project_id`, `trace_id`, `span_id`, `parent_span_id`, `service_name`, `span_name`, `span_kind`, `duration_ms`, `status_code`, `status_message`, `http_method`, `http_route`, `http_status`, `deployment_version`, `resource_attrs`, `span_attrs`, `events` FROM spans_rebuild
SETTINGS max_threads = 1, max_insert_threads = 1, max_block_size = 8192,
         max_insert_block_size = 8192, min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432

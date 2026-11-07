-- Derived: every row is recounted from the spans the worker reads next, and
-- nothing outside F28 reads it. One line is the whole rollback.
DROP TABLE IF EXISTS service_versions_1m

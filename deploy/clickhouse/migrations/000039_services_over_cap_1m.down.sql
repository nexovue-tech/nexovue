-- Derived: a record of drains that capped a tenant-minute. Dropping it loses only
-- that record; the rollups beside it are unaffected. One line is the whole rollback.
DROP TABLE IF EXISTS services_over_cap_1m

-- The three columns the Nested expanded into, named one by one: measured,
-- `DROP COLUMN IF EXISTS links` is a silent no-op, because with
-- flatten_nested = 1 no column of that bare name exists to match.
ALTER TABLE spans
    DROP COLUMN IF EXISTS links.trace_id,
    DROP COLUMN IF EXISTS links.span_id,
    DROP COLUMN IF EXISTS links.attributes

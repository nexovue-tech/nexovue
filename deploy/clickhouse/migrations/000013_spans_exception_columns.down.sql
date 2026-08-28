ALTER TABLE spans
    DROP COLUMN IF EXISTS exception_type,
    DROP COLUMN IF EXISTS exception_message

ALTER TABLE logs
    DROP COLUMN IF EXISTS exception_type,
    DROP COLUMN IF EXISTS exception_message

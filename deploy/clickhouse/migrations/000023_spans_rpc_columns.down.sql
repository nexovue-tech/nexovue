ALTER TABLE spans
    DROP COLUMN IF EXISTS rpc_system,
    DROP COLUMN IF EXISTS rpc_method,
    DROP COLUMN IF EXISTS rpc_status

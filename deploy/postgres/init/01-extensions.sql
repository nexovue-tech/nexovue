-- Runs once, on an empty data directory, before any migration. The bundled image's account
-- is a superuser, so this succeeds here; pgvector is not a trusted extension.
-- `setup` creates `vector` too (`provision_vector()`, F4 Step 6), with meaning search's columns,
-- on every start, and an install whose account cannot create it gets keyword search only
-- (F4 AC43). No migration depends on it.
CREATE EXTENSION IF NOT EXISTS vector;

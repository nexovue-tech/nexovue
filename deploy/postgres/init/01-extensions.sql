-- Runs once, on an empty data directory, before any migration.
-- Extensions need superuser, which Alembic's application role must not have.
CREATE EXTENSION IF NOT EXISTS vector;

# Database engineering standard

- Postgres schema changes use Alembic; ClickHouse schema changes use the checked-in migration system.
- Tenant-scoped Postgres access takes `org_id`; ClickHouse queries go through the tenancy repository chokepoint.
- Use explicit transactions where related control-plane writes must succeed together.
- Do not create a combined cross-store migration/rollback abstraction: the stores have independent lifecycle and recovery behavior.
- Test forward migration, rollback/recovery implications, indexes, and compatibility for material schema changes.

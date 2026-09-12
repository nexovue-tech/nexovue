# Cloud/database integrations and deploy events

## Status

**Verified current-state baseline.** Related catalog features: F24 and F28. The complete release
analysis UI and GitHub setup workflow are not implemented.

## Implemented behavior

- Projects can create/list/delete supported integrations and see integration health.
- AWS CloudWatch polling collects configured RDS metrics through cross-account role assumption with
an external ID; cloud credentials are not stored.
- Postgres polling collects `pg_stat_statements` and connection statistics via IAM-authenticated,
read-only access; query text is not stored.
- The API accepts signed deploy events, lists them by project, and manages project webhook secrets.

## Code evidence

`apps/api/src/nexovue_api/integrations/router.py` and `deploys/router.py`; scheduler `pollers/aws.py`,
`cloudwatch.py`, `postgres_connect.py`, `postgres_stats.py`, and `tasks.py`; and the deploy-event
migrations are authoritative.

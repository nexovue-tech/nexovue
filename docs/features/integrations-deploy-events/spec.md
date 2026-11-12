# Cloud/database integrations and deploy events

## Status

**Verified current-state baseline.** Related catalog features: F24 and F28. Deploy events are now
F28's: their envelope, secrets, releases and every screen that shows them are specified and built
there ([deployment-release-tracking](../deployment-release-tracking/spec.md)); this page keeps the
integrations half.

## Implemented behavior

- Projects can create/list/delete supported integrations and see integration health.
- AWS CloudWatch polling collects configured RDS metrics through cross-account role assumption with
an external ID; cloud credentials are not stored.
- Postgres polling collects `pg_stat_statements` and connection statistics via IAM-authenticated,
read-only access; query text is not stored.
- The API accepts signed deploy batches (`POST /deploys/webhook/{project_id}`, each item signing its own
  `delivery_id` and time, ADR-033), files each under a release, and manages project webhook secrets,
  which are sealed and shown once. `GET /deploys` lists a project's deploys with no sender text:
  `metadata`, `url` and `source` are served by `GET /releases/{id}/sender-details` alone, to a person
  (F28 FR54).

## Code evidence

`apps/api/src/nexovue_api/integrations/router.py` and `deploys/router.py`; scheduler `pollers/aws.py`,
`cloudwatch.py`, `postgres_connect.py`, `postgres_stats.py`, and `tasks.py`; and the deploy-event
migrations are authoritative.

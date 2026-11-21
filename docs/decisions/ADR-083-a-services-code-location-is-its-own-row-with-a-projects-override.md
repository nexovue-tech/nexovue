# ADR-083: A service's code location is its own row, for the organization with a project's override

## Status

Accepted 2026-11-21: decided in the F5 design (`service_code_locations`, spec FR5b) and approved with it;
filed with F5 Step 3.

## Context

A suggested fix must know which repository and folder hold a service's code, and how a stack's paths map
to that folder. F22's `service_repositories` already maps a service to one place per project, but that
place may be a Jira project, it carries no folder, and it is per project, while a project is usually an
environment (staging would repeat every mapping).

## Decision

1. **A table of its own, `service_code_locations`:** a service, a repository the Fix App reaches, a folder
   (null for the whole repository) and an optional path in production, keyed by organization, project
   (null for the whole organization) and service, `UNIQUE NULLS NOT DISTINCT`.
2. **A project's row wins over the organization's** (AC12a). Composite keys bind the repository to the
   organization, so a row cannot cross tenants (AC30).
3. **Set by an admin (`MANAGE_CONNECTIONS`), many at once, every row audited** (`fix.code_location_changed`);
   a bulk form saves every row or none, naming the one refused.

## Consequences

F22's table stays as it is. A folder outside every owner's consent stops new source for that service until
an owner agrees, and the settings screen says so beside the row.

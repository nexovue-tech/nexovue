# Data lifecycle and source-map controls

## Status

**Verified current-state baseline.** Related catalog feature: F27. Full customer export, archival
tiers, and all GDPR workflow surfaces are not implemented.

## Implemented behavior

- Projects have replaceable masking and sampling configuration; workers mask before downstream use.
- Retention derives from plan/project configuration.
- Tenant purge/data-erasure paths exist for stored telemetry and control-plane data.
  **Decided, not built:** organization erasure moves from per request to a nightly batch — see
  [ADR-014](../../decisions/ADR-014-telemetry-erasure-runs-as-a-nightly-batch.md).
- Source maps are uploaded as metadata plus object-store bytes and can be listed and used to
symbolicate minified frames.

## Code evidence

`apps/api/src/nexovue_api/projects/router.py`, `apps/scheduler/src/nexovue_scheduler/erasure/`, `sourcemaps/router.py`,
`apps/workers/internal/pipeline/mask/`, and `projectconfig/` are authoritative. Migration
`0017_source_maps_are_metadata_the_bytes_live_in_object_storage.py` records source-map storage
ownership.

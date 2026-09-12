# Data lifecycle and source-map controls

## Status

**Verified current-state baseline.** Related catalog feature: F27. Full customer export, archival
tiers, and all GDPR workflow surfaces are not implemented.

## Implemented behavior

- Projects have replaceable masking and sampling configuration; workers mask before downstream use.
- Retention derives from plan/project configuration.
- Tenant purge/data-erasure paths exist for stored telemetry and control-plane data.
- Source maps are uploaded as metadata plus object-store bytes and can be listed and used to
symbolicate minified frames.

## Code evidence

`apps/api/src/nexovue_api/projects/router.py`, `purge/`, `sourcemaps/router.py`,
`apps/workers/internal/pipeline/mask/`, and `projectconfig/` are authoritative. Migration
`0017_source_maps_are_metadata_the_bytes_live_in_object_storage.py` records source-map storage
ownership.

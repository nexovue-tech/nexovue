# API engineering standard

- Keep routes under `/api/v1/` unless they are operational probes.
- Validate external input and expose typed RFC 9457 errors, not framework/internal exceptions.
- Preserve authorization and tenant scoping in every read/write path.
- Treat pagination, filtering, idempotency, timeouts, and rate limits as interface behavior; specify them for new public endpoints.
- Do not expose credentials, internal database details, or unmasked telemetry values.

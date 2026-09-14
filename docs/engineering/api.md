# API engineering standard

- Keep routes under `/api/v1/` unless they are operational probes.
- Validate external input and expose typed RFC 9457 errors, not framework/internal exceptions.
- Preserve authorization and tenant scoping in every read/write path.
- Treat pagination, filtering, idempotency, timeouts, and rate limits as interface behavior; specify them for new public endpoints.
- Do not expose credentials, internal database details, or unmasked telemetry values.
- Address a non-JSON response by **suffixing the path with its extension** — `/billing/history.csv`,
  `/traces/export.csv` — never with `?format=` or a separate `/export` route. The extension is
  self-describing to a browser and to a person, and it cannot be accidentally omitted the way a
  query parameter can. Serve it with `Content-Disposition: attachment` and let the browser save it;
  a page in a sandboxed frame cannot hand itself a blob.
- Declare such a route **above** any sibling wildcard. `/traces/{trace_id}` would otherwise match
  `export.csv` as a trace id and answer a 404 that names neither mistake.
- Carry the filters on the URL rather than behind a "download what I last asked for" token, so the
  link is the same question somebody can paste to a colleague and no server-side search can go stale
  between looking and downloading.

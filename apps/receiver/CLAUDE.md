# receiver — the ingestion edge (Go)

Front door for every data door. Terminates OTLP, authenticates, tags tenancy, normalizes, publishes to Kafka.

Gates run in order and the order is load-bearing:
`auth` → `origin` → `allowance` → `ratelimit` → `decode` → `normalize` → `produce`

Nothing downstream of `normalize` knows which door the data came from.

**`allowance` is the plan gate** (ADR 0093). An organization whose plan includes a
monthly volume, and has spent it, is refused until the 1st. It sits above `ratelimit`
because it answers from an in-process cache rather than a Valkey round trip, so the
customer this gate exists for — one sending hard and being refused for all of it — costs
nothing to turn away. **Only a plan with a ceiling on its billed services has an
allowance**: `max_billed_services IS NULL` on pro and scale, so nothing that pays is ever
refused for volume. The refusal is `RESOURCE_EXHAUSTED` **without** `RetryInfo`, so a
compliant client drops the batch rather than resending it until the month turns, and it
is counted as `allowance_spent` rather than `throttled` — both are the same rpc code and
they are entirely different conversations. A control-plane read that fails admits the
request: losing the protection costs money, and refusing costs the customer their data.

**`origin` is the browser gate** (ADR 0087). It sits after `auth` because the allowlist belongs to
the key, and before `ratelimit` because a request from an origin the key does not cover must not
spend the owner's budget — otherwise a scraped public key becomes a denial of service against its
owner. The preflight `OPTIONS` is answered permissively and *unauthenticated*, because a preflight
carries no credential; enforcement is on the POST behind it. Every reply to a request carrying an
`Origin` gets CORS headers, **refusals included** — a 403 the page cannot read is a support ticket.

**The browser door also reads `text/plain`, as OTLP/JSON** (ADR 0089). It is the only body
`navigator.sendBeacon` can deliver: the Beacon spec fixes credentials mode to `include`, so anything
outside the CORS safelist preflights and then fails against our refusal to send
`Access-Control-Allow-Credentials`. The body is parsed as JSON like any other, so a beacon that is
not JSON is still refused 400. **The server door has no `text/plain` entry and cannot be given one** —
each entrance is mounted with its own `contentTypes` map and `Options.accepts` is unexported.

Trace batches are **split one envelope per `trace_id`** before publishing, because trace assembly depends on Kafka partitioning by trace id. Logs and metrics forward as received.

Errors follow **OTLP semantics, not problem+json** — SDK clients expect the protocol's own status codes. HTTP answers a failure in the request's own encoding (protobuf in, protobuf out); gRPC answers the mirrored `google.rpc.Code`.

One asymmetry is deliberate. An oversized gRPC export is refused `RESOURCE_EXHAUSTED` by grpc-go itself, before any handler exists, where the HTTP door answers 413. That is safe because the spec makes `RESOURCE_EXHAUSTED` **non-retryable unless the server attaches `RetryInfo`** — grpc-go's size error carries none, so a compliant client drops the batch rather than resending it forever. Our *rate-limit* refusal does attach `RetryInfo`, because that one genuinely is recoverable.

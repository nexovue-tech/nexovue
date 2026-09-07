# receiver — the ingestion edge (Go)

Front door for every data door. Terminates OTLP, authenticates, tags tenancy, normalizes, publishes to Kafka.

Gates run in order and the order is load-bearing:
`auth` → `origin` → `allowance` → `ratelimit` → `decode` → `normalize` → `produce`

Downstream of `normalize` sees one payload format whatever door the data came from. The
envelope's `door` names the entrance — `DOOR_OTLP` for a secret key, `DOOR_RUM` for a public
site key — and it is decided in exactly one place, `ingest.DoorFor(identity)`, from the kind of
key that opened the door (ADR 0097). The usage meter bills the browser door to its own budget
by that field, so a browser export stamped as the server door spends the budget the split
exists to protect.

**`allowance` is the plan gate** (ADR 0093, split per door by ADR 0097). An organization whose
plan includes a monthly volume, and has spent it, is refused until the 1st. It sits above
`ratelimit` because it answers from an in-process cache rather than a Valkey round trip, so the
customer this gate exists for — one sending hard and being refused for all of it — costs
nothing to turn away. **Only a plan with a ceiling on its billed services has an
allowance**: `max_billed_services IS NULL` on pro and scale, so nothing that pays is ever
refused for volume. **Where the plan row records `browser_share_percent`, that is a cap on the
browser door inside the one allowance, not a partition of it.** The browser's spend comes out of
the month only as it is actually spent, so an unused cap stays the server's — reserving it
withdrew a quarter of the free tier for a door nobody could open. Both doors are then judged on
the charged total: server bytes plus the browser's spend **up to its cap and not past it**. That
`min` is what stops a scraped key's overshoot closing the server door, so **a customer's backend
keeps reporting whatever happens to their website** while nothing sums past the plan.
A row with no share is pooled, which is what every row before 0097 meant.

**A deleted organization is refused**, and that is a different verdict from one the gate cannot
find: a missing row is a broken control plane and is admitted, a `deleted_at` is not. Reading the
two as one thing turned the cap off entirely, because the statement filtered `deleted_at IS NULL`
while the key lookups never join `organizations`. The key still authenticates — revoking it
belongs with account deletion in M8. One checker serves
both doors and both transports, reading the month once and asking about the door the envelope
will carry. The refusal is `RESOURCE_EXHAUSTED` **without** `RetryInfo`, so a compliant client
drops the batch rather than resending it until the month turns, and it is counted as
`allowance_spent`, `browser_allowance_spent`, `organization_gone` or `ingest_suspended` rather
than `throttled` — all the same rpc code and four different conversations. `allowance.Verdict`
carries one `Cause` rather than a flag per refusal, so it cannot say two of them at once. A
control-plane read that fails admits the request: losing the protection costs money, and
refusing costs the customer their data.

**`organizations.ingest_suspended_at` is the operator's stop valve** (P1-52), read on the same
row and in the same statement as the allowance, so closing it costs the ingest path no extra
query. **It is operational, not billing**: it stops a tenant that is well inside its allowance —
including a paying one the allowance never touches — and it is the only refusal here that a
person has to lift by hand. NULL is open; any recorded moment closes the valve at every door,
and the timestamp is deliberately not compared with now. It takes up to
`RECEIVER_ALLOWANCE_CACHE_TTL` (30s by default) to bite on each instance, and a control plane
that cannot be read admits the request, so the valve cannot be closed during a Postgres outage.
The keys still authenticate, exactly as they do for a deleted organization.

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

**The limiter fails open when Valkey errors, and bounds itself in process while it does** (ADR
0104). `ratelimit.AllowWhileCounterDown` is a token bucket per key prefix, engaged only on the
error path, sized at `fallbackBurstMultiple` (10) times `RECEIVER_RATE_LIMIT_PER_SECOND` — a
multiple, not a copy, because each instance sees only its share of a key's traffic and matching
the configured number would refuse traffic Valkey would have allowed. **It is per receiver
instance**: the effective ceiling is that number times the fleet, and it grows as the fleet
grows. The map is bounded for the reason `cache.go` gives — the key prefix on an invalid request
is attacker-chosen. `limiter_unavailable` stays the one degraded line per window; a refusal from
the fallback is counted as `throttled_in_process`, not `throttled`, and its log line names the
per-instance number that refused, because it is not the configured one.

**`net/http/pprof` mounts on the admin listener when `RECEIVER_PPROF_ENABLED` is set**, and only
there — never an OTLP port. Off by default: the endpoints are unauthenticated. Checked
2026-09-07 that :8081 is published by none of the three compose files and routed to by nothing
in the Caddyfile; anything that changes which ports are published has to check that again.

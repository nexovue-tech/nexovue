# receiver — the ingestion edge (Go)

Front door for every data door. Terminates OTLP, authenticates, tags tenancy, normalizes, publishes to Kafka.

Gates run in order and the order is load-bearing:
`auth` → `ratelimit` → `decode` → `normalize` → `produce`

Nothing downstream of `normalize` knows which door the data came from.

Trace batches are **split one envelope per `trace_id`** before publishing, because trace assembly depends on Kafka partitioning by trace id. Logs and metrics forward as received.

Errors follow **OTLP semantics, not problem+json** — SDK clients expect the protocol's own status codes.

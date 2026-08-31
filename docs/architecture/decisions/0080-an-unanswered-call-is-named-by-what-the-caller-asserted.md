# 0080 — An unanswered call's callee is named by what the caller asserted, and never by an IP or a transport

- **Status:** Accepted. Its open question — should a bare IP be allowed to name a node? — is answered by [0082](0082-ip-named-peers-collapse-in-the-map-not-in-storage.md): the address is still stored, and the map collapses addresses of one kind into one node. Nothing else here is changed.
- **Date:** 2026-08-30

## Context

`service_edges_1m` draws the service map from paired spans: a CLIENT answered by a SERVER, or a
PRODUCER whose message reached a CONSUMER ([0078](0078-queue-edges-are-derived-from-span-links.md)).
When the callee never reports for itself, the call is still real — something answered it and the
caller waited on the answer — so the edge was kept and pointed at one reserved name,
`(uninstrumented)`.

Measured against the OpenTelemetry Demo 3.0.0, that one node absorbed **cart→Redis,
product-catalog→Postgres, load-generator→frontend, and the unknowns of `ad`, `payment` and
`frontend`** — six unrelated dependencies drawn as one node with six arrows into it.

The failure mode is worse than lost detail. A hub is the most visually dominant thing on a
dependency graph: it is where the eye goes, and it is what a reader takes to be the shared
component everything depends on. Merging six unrelated peers therefore does not merely omit
information, it **asserts an architecture the customer does not have**. The rendering
([0031](0031-service-map-rendering-with-react-flow.md)) makes this concrete — the node was already
styled dashed and italic to say "not a service", and that styling does nothing about a fan-in of six.

The comment that put every unanswered call in one bucket was right about the constraint it named:
naming the callee ourselves would be an invention. But one shared bucket is not the only
alternative, because **a caller-side span usually says what it called**.

### What the conventions say a client span carries about its peer

Checked against the semantic-conventions repository's own model files, not the rendered docs:

| Attribute | Status | Source |
|---|---|---|
| `db.system` → **`db.system.name`** | renamed in 1.33.0 | `model/db/deprecated/registry-deprecated.yaml`: `deprecated: {reason: renamed, renamed_to: db.system.name}` |
| `peer.service` → **`service.peer.name`** | renamed | `model/peer/deprecated.yaml`: `renamed_to: service.peer.name`; the current one "SHOULD be equal to the actual `service.name` resource attribute of the remote service if any" |
| `messaging.system` | Required on producer spans, never renamed | [messaging spans](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/) |
| `server.address` | Recommended on database client spans | [database spans](https://opentelemetry.io/docs/specs/semconv/db/database-spans/) |
| `network.peer.address` | Recommended — "Peer address of the network connection — IP address or UNIX domain socket name" | [general attributes](https://opentelemetry.io/docs/specs/semconv/general/attributes/) |

The **value** renames within `db.system.name` (`mssql`→`microsoft.sql_server`, `oracle`→`oracle.db`,
`dynamodb`→`aws.dynamodb`, sixteen in all) come from the spec's own
[migration table](https://opentelemetry.io/docs/specs/semconv/non-normative/db-migration/).

**Both generations are live in one fleet**, which is the trap this project has now hit three times —
after HTTP and RPC. Confirmed in ClickHouse on demo telemetry: `cart` writes
`db.system="redis"` while `accounting` and `product-catalog` write `db.system.name="postgresql"`.
Reading only the current spelling would have left `cart` in the bucket it was supposed to leave.

## Decision

A callee that never reported for itself is named from what the **caller's own span** asserted, most
specific first:

1. `service.peer.name`, else `peer.service` — the peer's own name, as declared
2. `server.address` — the host the caller dialled
3. `db.system.name` / `db.system`, else `messaging.system` — the datastore or broker it reached
4. `(uninstrumented)` — only when the span said none of the above

The name keeps parentheses, which already mark "a name we were told, not one that reported for
itself"; a real `service.name` is a resource attribute and cannot contain them. Every reserved name
is now recognised **by its parentheses** rather than by a fixed list, in the pipeline, the API
(`is_reserved_service`) and the web (`isPlaceholderService`), because the set of them is open.

Nothing is invented: every branch is a value the caller wrote on its own span.

A new column `server_peer_kind`
([000027](../../../deploy/clickhouse/migrations/000027_service_edges_peer_kind.up.sql)) carries the
technology beside the name — `redis`, `postgresql`, `grpc` — so a node reads `(valkey-cart)`
labelled `redis` rather than making a reader guess from a hostname. It is empty whenever the callee
reported for itself: a service that names itself needs no label, and what the caller *believed*
about it is not evidence about the callee.

Live, read through the API's own service layer against demo telemetry:

```
cart            -> (valkey-cart)     kind='redis'       calls=466
product-catalog -> (astronomy-db)    kind='postgresql'  calls=10
accounting      -> (astronomy-db)    kind='postgresql'  calls=2
cart            -> (flagd)           kind='grpc'        calls=19
frontend        -> (uninstrumented)  kind='grpc'        calls=289
```

`load-generator → frontend` left the bucket entirely, for an unrelated reason: `frontend-proxy` now
reports, so that edge pairs properly.

### An IP never names a node

`network.peer.address` is **not read at all**. The conventions define it as the socket's own peer —
an IP. An address that changes on restart makes a node whose history is unqueryable across the
395-day derived retention ([0072](0072-retention-class-partitioning.md)): the same dependency
becomes a new node tomorrow, and a 13-month rollup that cannot be grouped over time is the one thing
that rollup exists for.

### `rpc.system` never names one either

Tried, shipped nothing, and the measurement is why. With `rpc.system` in the identity chain the demo
produced **`frontend → (grpc)` at 104 calls and `recommendation → (grpc)` at 16, merged into one
node** — the same false hub this ADR exists to break apart, wearing a new name. Nobody depends on
gRPC; they depend on what is behind it. `rpc.system` is *how a call travelled*, not *what answered
it*, so it is carried as `Peer.Transport` and can only ever label an edge, never name a node. The
type keeps `System` (datastore or broker, which is a dependency in its own right) and `Transport`
as separate fields so the distinction cannot be lost by a later edit.

### `server_peer_kind` is out of the sorting key

Measured: `cart` calls `flagd` over **both plain HTTP and gRPC** — the same span name family, two
different transports. The kind is therefore a property of the **call**, not of the caller→callee
pair, and putting it in the sorting key would split one dependency into two edges that the graph
then has to merge back — and two edges sharing a React Flow id silently become one, which is a bug
the edge id already carries a comment about.

Outside the key, rows differing only in kind merge and their counts still sum, which is the answer
the map asks. The read picks one with `max(server_peer_kind)`, which is reproducible across queries
rather than whichever row a merge happened to keep. It **is** part of the accumulator's in-memory
grouping key, because otherwise whichever span landed in the bucket first would set it: the same
offsets would produce a different block on replay, and the deduplication token
([0075](0075-block-deduplication-replaces-the-read-before-retry.md),
[0060](0060-counter-idempotency-via-clickhouse-dedup.md)) would stop recognising a retry.

## Alternatives considered

**Technology-first naming — rejected.** Name the node `(redis)` and `(postgresql)`, using
`server.address` only when no technology is present. It is inherently low-cardinality, it is a
closed enum from the spec, it sidesteps the IP question entirely, and it is how a person talks
("cart depends on Redis"). It was rejected because it **merges two Redis clusters into one node
forever**: a customer running a session cache and a rate-limit store sees one dependency, and no
later change can separate them, because the data that would have distinguished them was never
stored. Storage should carry the most specific truth available; a presentation layer can always
aggregate upward, and cannot ever disaggregate. This is the alternative most likely to be re-opened,
and this paragraph is the answer.

**Keep the single bucket — rejected.** The status quo. It is honest, and it is the reason for the
`(uninstrumented)` name in the first place. Rejected because honesty about *one* call is not honesty
about *six*: the hub it draws is a positive false claim about the architecture, not an absence.

**Filter IP literals out of `server.address` — rejected.** Detect a bare IPv4/IPv6 address and fall
through to the technology instead. Rejected because it invents a policy the caller did not state:
dialling an IP is a real dependency, a hostname that happens to look like an IP would be dropped by
the heuristic, and this codebase does not apply that kind of guess to `client_service` either. See
the open question below — this may yet be the right answer, but it is Raj's call.

**Encode the technology inside the node name — rejected.** `(redis valkey-cart)` needs no migration
and no API field. Rejected because it makes a display string carry two facts that the API and the
web would then have to parse apart.

**Put `server_peer_kind` in the sorting key — rejected**, for the `cart`→`flagd` measurement above.
The reverse also turned out to be expensive: `MODIFY ORDER BY` can only *extend* a key, and undoing
that needs two statements while the migration runner is configured `x-multi-statement=false`, so the
migration would not have been reversible in one file.

## Consequences

**A SERVER span that merely lost the pairing race now draws a shadow node.** Previously every
unanswered call joined one bucket; now a call whose partner was evicted from a full pairing buffer,
or arrived after the 10s window ([0010](0010-tail-sampling-defaults.md)), can draw `(product-catalog)`
beside the real `product-catalog`. This is a behaviour change under load and it is the consequence
most likely to be reported as a bug. It is accepted as **more diagnostic than misleading** — a
shadow node says "some calls to this are not pairing", where the old bucket said nothing — but the
rate must be visible rather than inferred, so `edges_unanswered` and `edges_unidentified` are now on
the pipeline heartbeat. The gap between them is how many unanswered calls the peer attributes were
able to name; a shadow node appearing while `edges_unanswered` climbs is a pairing problem, not a
naming one.

**`(uninstrumented)` still exists and is still a bucket**, now only for spans that asserted nothing.
Measured live at `frontend → (uninstrumented)` 289 calls with kind `grpc` — Go gRPC clients that
carry `rpc.system` and no address, whose callees were restarting. That is the honest answer, and it
now carries a transport label, so "289 gRPC calls to something we cannot name" is readable where
"289 calls" was not.

**Node count on a busy map goes up.** Every distinct datastore, broker and dialled host is now its
own node where they were one. That is the point, but it costs canvas: the label lanes added in the
same change help and do not solve it, and label placement is not collision-tested against node boxes.

**The insert had to start naming its columns.** Adding a column to `service_edges_1m` broke the
worker immediately: the INSERT mapped `input()` expressions to table columns **positionally**, and
an `ALTER … ADD COLUMN` appends, so `server_peer_kind` landed on `latency_quantiles` and the insert
failed `CANNOT_CONVERT_TYPE (70)` for twenty retries. No data was lost — offsets do not commit until
the insert acknowledges, which is [0079](0079-span-rows-are-idempotent-across-a-restart.md)'s
property working — but the statement now names its target columns, and any future column addition to
a derived table should assume the same trap.

**`server_service` cardinality is now driven by customer network topology**, not only by their
service names. See the open question.

## Open question — should a bare IP be allowed to name a node?

Left exactly as built, deliberately, because the trade is a product call rather than an engineering
one.

Measured live: `checkout → (172.19.0.11)` and `checkout → (172.19.0.19)`, two container IPs, two
calls each. **They are one dependency wearing two names**, and after the next restart they will be
two more. For a customer that is a real defect, not a cosmetic one.

Against filtering them: it is what the caller asserted, so removing it is us inventing a policy;
`client_service` already carries the same unbounded exposure through an arbitrary `service.name`,
so a cap here and not there would be inconsistent; and a legitimate fixed-IP dependency would
disappear from the map.

For filtering them, beyond the retention argument already made: the billing unit is **distinct
service names per project per day**
([0077](0077-the-billing-unit-is-a-table-and-the-price-is-a-dated-row.md),
[0056](0056-per-service-pricing.md)). Unbounded names are therefore not only a storage and
readability surface but a **billing** one. That connection was not visible when the naming chain was
designed, and it changes who the decision belongs to.

The options are: leave it; reject IP literals as an identity and fall through to the technology; or
cap distinct peer names per project per window and fold the overflow back into `(uninstrumented)`.
Whichever is chosen applies to `client_service` too, or it is not a rule.

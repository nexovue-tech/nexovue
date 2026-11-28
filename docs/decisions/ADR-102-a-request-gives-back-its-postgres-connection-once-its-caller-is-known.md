# ADR-102: A request gives back its Postgres connection once its caller is known, and a full pool answers busy

## Status

Accepted 2026-11-22, with the change [request holds one connection](../changes/request-holds-one-connection/bug.md),
which fixes F20 open decision 40. Awaiting the founder's review with that change's diff.

## Context

The API has one SQLAlchemy session per request (`deps.get_db_session`), committed by the
dependency when the route ends. A session takes a pooled connection on its first statement and
keeps it until its transaction ends. The caller's lookup (`auth/dependencies.get_current_identity`,
and the token and ticket branches of `get_current_reader`) was the first statement of nearly every
request, so every request held a connection from that lookup to its last byte, `idle in
transaction` through every wait that was not Postgres.

F20's measurement (2026-11-22, `docs/research/f20-measurement-2026-11-22.md`) then found the worse
consequence: a request that asks for both a person and a reader (the dashboard read does) needs the
session lookup a second time, and `SessionStore` reads on a session of its own, so a *second*
connection, taken while the first is held. Twenty such requests on a pool of 15 each held one and
waited for another: nobody could move until the pool's 10-second timeout, and the timeout reached the
caller as HTTP 500 (`QueuePool limit of size 10 overflow 5 reached`). Reproduced with one request on
a pool of one.

## Decision

1. **The caller's lookup ends its own transaction before it returns** (`_release_connection` in
   `auth/dependencies.py`). A route therefore starts holding no connection, and takes one afresh on
   its own first statement.
2. **It ends it with a commit.** Nothing in the lookup writes except a support view's audit line,
   which is already committed there. A rollback would expire the person and organization just
   loaded (they are read after, and an async session cannot load lazily); closing the session would
   detach them, and routes change `identity.user` (a name, a second factor) and rely on the request's
   commit to keep it. With `expire_on_commit=False` a commit keeps both loaded and attached.
3. **A full pool answers 503 `service-busy` with `Retry-After`** (`errors/handlers._busy`), from the
   one function both FastAPI's handler and the outermost middleware call, because a full pool is also
   met in middleware (the rate limiter reads sessions before any route).

## Alternatives rejected

- **Look the caller up on a session of its own.** Releases the connection as well, but detaches the
  person from the request's session, so a route's change to `identity.user` would silently not be
  saved.
- **A larger pool.** It moves the queue rather than shortening it (`docs/engineering/performance.md`),
  and a nested checkout deadlocks at any size once enough requests arrive together.
- **End the request's transaction before every ClickHouse read, at the tenancy chokepoint.** It would
  also free the connection a route holds between its own Postgres reads and its telemetry reads, but
  it commits a write route's work part-way through the request, which changes what a failed request
  leaves behind for the 13 write routes that read telemetry. Not decided here; open in the change
  record.

## Consequences

- A request's Postgres work is now at least two transactions, the caller's lookup and the route's.
  At Postgres's default `READ COMMITTED` each statement already read its own snapshot, so no read
  loses a guarantee; the lookup takes no lock and sets nothing transaction-local, which this relies
  on. A future lookup that must hold a lock into the route breaks this and must say so here.
- A connection is checked out twice for a route that reads Postgres, and `pool_pre_ping` pings each
  checkout: one more round trip, measured as no visible cost at 20 viewers.
- Load beyond the pool now answers 503 after `POSTGRES_POOL_TIMEOUT_SECONDS`, which the console
  already retries as a busy answer (`app/query-client.ts`). The timeout and the `Retry-After` value
  are proposals, not measurements.

# demo-app

A fake shop that emits real OpenTelemetry traces, so the stack always has
something to consume and the customer onboarding path is walked every day.

Three roles, one binary. They call each other over HTTP, so a trace crosses real
process boundaries and the waterfall has genuine depth:

```
storefront  GET /browse · POST /checkout
  └── checkout  POST /checkout      + cart.query   ← the database span faults act on
        └── payments  POST /charge  + payment.authorize
```

## It configures OTel exactly as a customer does

Nothing in `internal/telemetry` is nexovue-specific. The exporter reads the
standard variables — the same two lines our onboarding asks for:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:14318
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_devlocaldevlocaldevloc
```

If that setup is awkward, it is awkward for customers too, and we find out here
first.

## Breaking it on purpose

Faults are named, off by default, and toggled at runtime — no restart, so a
single trace stream contains the before and the after.

```bash
curl -X POST   localhost:18091/faults/checkout-db-slow   # the unindexed-query story
curl -X DELETE localhost:18091/faults/checkout-db-slow   # fixed
curl           localhost:18091/faults                    # what is broken now
```

| Fault | Role | What it does |
|---|---|---|
| `checkout-db-slow` | checkout | Adds 900ms to `cart.query` |
| `checkout-db-down` | checkout | Fails `cart.query` outright |
| `payments-errors` | payments | Fails 35% of charges |
| `payments-slow` | payments | Adds 900ms to `payment.authorize` |

They are deliberately specific rather than generic ("5% error rate"), because a
real incident is always something in particular — and because the insight engine
will eventually have to explain exactly these.

## Settings

`DEMO_ROLE` · `DEMO_ADDR` · `DEMO_CHECKOUT_URL` · `DEMO_PAYMENTS_URL` ·
`DEMO_TRAFFIC_RATE` (requests/second the storefront drives against itself; `0`
turns the generator off).

## Note on semconv

`internal/telemetry` imports a semconv version that must match the schema
`resource.Default()` carries, or `resource.Merge` fails at boot with a
conflicting-schema error. Bumping the OTel SDK means bumping that import in the
same commit.

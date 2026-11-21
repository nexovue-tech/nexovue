"""`/slo-readings`: F19's ClickHouse reads, on the telemetry tier (design, *API*; D59).

Apart from `/slos`, which reads Postgres only, so the list answers while ClickHouse is
down (AC40). The tier follows a static prefix (`ratelimit/policy.py`), hence two routers.
"""

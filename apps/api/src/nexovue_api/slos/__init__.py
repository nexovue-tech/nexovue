"""Reliability targets (F19): `/slos`, read from Postgres alone (design, *API*).

`router.py` names no telemetry repository, so it stays on the default rate-limit tier
and answers while ClickHouse is down (AC40, D59). The ClickHouse reads live under
`/slo-readings` from Step 2.
"""

"""F6: anomaly detection — judging each service's settled minutes against its own normal.

`settle` decides which minute every part of the pipeline has finished writing.
`store` is detection's only way into the telemetry store: reads bound to one
project, and the one writer of `detection_readings` (ADR-023).
"""

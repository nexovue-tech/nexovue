"""The dashboard batch read (F20): every panel of a saved dashboard in one request, each
real scan charged, and a state per panel. A package of its own so its router is the one
on the telemetry tier (design section 2, D41). It imports `dashboards`, never the reverse.
"""

"""Probes: the checks nexovue runs from its own infrastructure.

Deliberately empty of imports. `result` and `http_check` are pure — one is data,
the other needs only libcurl — and pulling the Celery app in here would mean a
dataclass could not be imported without a broker, a database and a Redis to
point at. The task itself lives in `tasks.py`.
"""

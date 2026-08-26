# scheduler — doors 2 and 4b (Python, Celery)

Synthetic probes and cloud/database pollers. Tasks normalize to the envelope and publish to Kafka themselves; there is no result backend for data.

Two queues, deliberately separated: `light` (HTTP checks, SSL, polls — high concurrency) and `browser` (Playwright — low concurrency, larger containers, its own image).

Schedules live in Postgres and are read by **our own Beat scheduler**, so customers edit checks in the UI without a redeploy.

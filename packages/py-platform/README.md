# py-platform

The three modules `apps/api` and `apps/scheduler` both need, held once.

`telemetry/context.py` binds the correlation and tenant ids a request or a task
carries. `telemetry/logging.py` configures structlog the same way in both.
`telemetry/redaction.py` strips credentials from anything about to be logged.

**Why a package rather than two copies.** They were duplicated — `context.py`
and `redaction.py` byte-identical, `logging.py` differing in two import lines —
and the plan's §7b named the root fix. The cost of the copies was never the 299
lines: it was that a redaction rule fixed in one and not the other is a privacy
bug that passes every test in both services.

**Why only these three.** A shared package grows into a dumping ground unless
something says what belongs. The rule here: a module belongs when both services
need the *same behaviour*, not merely a similar one. Settings, models and
database access are deliberately not here — they are similar between the two and
identical in neither, and forcing them together would couple two services that
deploy separately.

Nothing in this package may import `nexovue_api` or `nexovue_scheduler`. The
dependency runs one way, which is what keeps a control-plane change from
stopping a scheduler.

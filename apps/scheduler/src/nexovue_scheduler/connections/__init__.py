"""Tracker connections (F22): every GitHub call, and later every Jira call, runs here,
in `scheduler-connections`, which holds `CONNECTION_SECRET_KEY` and nothing else (D80).

Kind-agnostic by design (foundation section 6, item 29): `tasks.py` loads a connection of
any tracker kind and writes what it learns; `trackers.py` is the per-kind boundary; and
`github.py` is the one adapter built. Jira later is a second adapter behind the same
boundary, not a rework. Deliberately empty of imports, so the narrow task set loads only
what its tasks need.
"""

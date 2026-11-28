"""The AI account's connection limit covers the AI role at its worst (F3 A58).

The limit was 20, worked out for four children. F4 and F5 raised the role to eight (F5 D56),
so the arithmetic no longer held: eight children at up to two connections each, a parent's
claim pool of up to four, and its listener come to 21, and the 21st would be refused by
Postgres in the middle of a wording run. No database is needed to see it.
"""

from __future__ import annotations

from nexovue_api import insight_account, roles


def test_the_limit_covers_every_child_the_role_runs_at_its_worst() -> None:
    arguments = roles.ROLES["scheduler-insight"]
    children = int(arguments[arguments.index("--concurrency") + 1])
    worst = children * insight_account.CHILD_CONNECTIONS + insight_account.PARENT_CONNECTIONS

    spare = insight_account.CONNECTION_LIMIT - worst

    assert spare >= insight_account.RESTART_OVERLAP

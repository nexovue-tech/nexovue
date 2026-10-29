"""Which off-host snapshots are kept, and what a snapshot may name (ready-to-deploy, T20).

The newest of each of the last 7 days that have a snapshot, and of each of the last 4 ISO weeks
(design.md §8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexovue_api.backups import SNAPSHOT_FORMAT, BackupError, _parse_checksums, snapshots_to_keep


def _name(moment: datetime) -> str:
    return moment.strftime(SNAPSHOT_FORMAT)


START = datetime(2026, 10, 28, 2, 30, tzinfo=UTC)  # a Wednesday


def test_a_daily_backup_for_sixty_days_keeps_seven_days_and_four_weeks() -> None:
    names = [_name(START - timedelta(days=days)) for days in range(60)]

    kept = snapshots_to_keep(names)

    days = {_name(START - timedelta(days=days)) for days in range(7)}
    # The newest of each of four ISO weeks: today, then three Sundays — the first of which is
    # already one of the seven days.
    sundays = {_name(START - timedelta(days=days)) for days in (3, 10, 17)}
    assert kept == days | sundays
    assert len(kept) == 9


def test_several_backups_in_one_day_keep_only_the_newest() -> None:
    names = [_name(START + timedelta(hours=hours)) for hours in range(5)]

    assert snapshots_to_keep(names) == {_name(START + timedelta(hours=4))}


def test_a_host_that_was_off_for_weeks_keeps_its_last_backups() -> None:
    """Counted in days that have a snapshot, so a gap does not age everything out."""
    names = [_name(START - timedelta(days=40 + days)) for days in range(7)]

    assert snapshots_to_keep(names) == set(names)


def test_a_name_that_is_not_a_snapshot_is_never_deleted() -> None:
    assert snapshots_to_keep(["notes", "2026-13-99T999999Z"]) == {"notes", "2026-13-99T999999Z"}


@pytest.mark.parametrize("name", ["../../etc/cron.d/x", "/etc/passwd", ".hidden", "a/b"])
def test_a_snapshot_names_plain_files_only(name: str) -> None:
    """Names come back from the off-host bucket on a restore; a path would leave the snapshot."""
    with pytest.raises(BackupError, match="not a snapshot's file"):
        _parse_checksums(f"{'0' * 64}  {name}\n")


def test_sha256sum_s_own_lines_are_read() -> None:
    assert _parse_checksums(f"{'a' * 64}  postgres.dump\n{'b' * 64} *clickhouse.zip\n") == {
        "postgres.dump": "a" * 64,
        "clickhouse.zip": "b" * 64,
    }

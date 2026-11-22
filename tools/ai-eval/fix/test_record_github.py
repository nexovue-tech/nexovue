"""The GitHub recorder, offline (F5 plan T2.10): it repeats every recording the fake is
compared with, cleans what it writes, and calls nothing without its tokens. It is never
run against GitHub here; that is the founder's release check."""

from __future__ import annotations

import pytest

from fix.record_github import RECORDINGS, STEPS, Session, cleaned, main, record


def session() -> Session:
    return Session(
        repository="real-org/scratch",
        first="a" * 40,
        person_login="real-person",
        person_id=1,
        large_tree=None,
        merged_pull=None,
        read_token="",
        write_token="",
    )


def test_the_recorder_repeats_every_recording() -> None:
    assert set(STEPS) == {path.stem for path in RECORDINGS.glob("*.json")}


def test_a_dry_run_calls_nothing() -> None:
    done = record(session(), dry_run=True)

    assert len(done) == len(STEPS)
    assert all(line.endswith("would call GitHub") for line in done)


def test_without_tokens_it_stops_before_any_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NEXOVUE_RECORD_READ_TOKEN", raising=False)
    monkeypatch.delenv("NEXOVUE_RECORD_WRITE_TOKEN", raising=False)
    arguments = [
        "--repository",
        "o/r",
        "--first",
        "a" * 40,
        "--person-login",
        "p",
        "--person-id",
        "1",
    ]

    assert main(arguments) == 2
    assert "nothing was called" in capsys.readouterr().out


def test_cleaning_replaces_real_names_and_drops_ids_and_tokens() -> None:
    body = {
        "full_name": "real-org/scratch",
        "node_id": "R_kgDO",
        "user": {"login": "real-person", "node_id": "U_x"},
        "note": "ghs_" + "Q" * 36,
    }

    assert cleaned(body, session()) == {
        "full_name": "acme/api",
        "user": {"login": "octo-dev"},
        "note": "<token removed>",
    }

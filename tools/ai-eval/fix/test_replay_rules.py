"""K6 and K8 over commit history (F5 plan T6.3; spec AC63), on a repository this test makes:
planted lines meant to be refused are, planted lines meant to pass do, files a suggestion
could not change are skipped, and the checked-in report is the skeleton until the founder's
real run. Nothing but the test's own repository is read."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from fix import replay_rules
from fix.replay_rules import added_lines_of, git_in, replay

GIT_SECONDS = 30
# A 40-character hex run: what K8's hex threshold refuses (status, decision 5).
HEX = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"


def _git(repository: Path, *arguments: str) -> None:
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "bed",
        "GIT_AUTHOR_EMAIL": "bed@example.invalid",
        "GIT_COMMITTER_NAME": "bed",
        "GIT_COMMITTER_EMAIL": "bed@example.invalid",
    }
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        env=environment,
        capture_output=True,
        check=True,
        timeout=GIT_SECONDS,
    )


def _commit(repository: Path, files: dict[str, str], message: str) -> None:
    for path, text in files.items():
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", message)


@pytest.fixture
def history(tmp_path: Path) -> Path:
    repository = tmp_path / "history"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _commit(repository, {"app/client.py": 'BASE = "https://api.example.com/v1"\n'}, "first")
    _commit(
        repository,
        {
            "app/client.py": 'BASE = "https://api.example.com/v1"\n'
            'RETRY = "https://api.example.com/v1"\n'
            'MIRROR = "https://mirror.example.net/v1"\n',
        },
        "known and new addresses",
    )
    _commit(
        repository,
        {
            "app/build.py": f'COMMIT = "{HEX}"\nname = "plain words here"\n',
            "app/settings.json": '{"url": "https://elsewhere.example.org"}\n',
            "README.md": "See https://docs.example.io\n",
        },
        "an opaque string, and files a suggestion could not change",
    )
    return repository


def test_only_planted_refusals_are_counted(history: Path) -> None:
    result = replay(git_in(history, timeout=GIT_SECONDS))

    assert result.commits == 3
    assert result.lines == 5  # 1 + 2 + 2; the JSON and Markdown lines are skipped
    # K6: the first file's address (new to an empty file) and the mirror; K8: the hex run.
    assert result.refused == {"K6": 2, "K8": 1}
    assert result.commits_refused == {"K6": 2, "K8": 1}


def test_an_address_already_in_the_file_passes_k6(history: Path) -> None:
    result = replay(git_in(history, timeout=GIT_SECONDS), commits=2)

    # `RETRY` repeats an address the file held, whole: only `MIRROR` is refused.
    assert result.refused["K6"] == 1


def test_added_lines_are_read_from_hunks_only() -> None:
    diff = "\n".join(
        [
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1,0 +2,2 @@",
            "+one = 1",
            "+++ b/looks_like_a_header.py",
            "diff --git a/gone.py b/gone.py",
            "--- a/gone.py",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-old = 1",
        ]
    )

    # An added line reading `++ b/...` shows as `+++ b/...`: inside a hunk it is content.
    assert list(added_lines_of(diff)) == [
        ("x.py", "one = 1"),
        ("x.py", "++ b/looks_like_a_header.py"),
    ]


def test_a_git_command_past_its_time_stops_the_replay(history: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        git_in(history, timeout=0.000001)(["log"])


def test_the_checked_in_report_is_the_skeleton_until_the_founders_run() -> None:
    text = replay_rules.skeleton()

    assert replay_rules.REPORT.read_text(encoding="utf-8") == text, (
        "run `PYTHONPATH=tools/ai-eval uv run python -m fix.replay_rules`"
    )
    assert "Not yet run" in text and "|" not in text
    assert "no admin notice" in text


def test_the_filled_report_holds_counts_only(history: Path) -> None:
    result = replay(git_in(history, timeout=GIT_SECONDS))

    text = replay_rules.render(result, "a test repository")

    assert "| K6 | 2 of 5 | 2 of 3 |" in text and "| K8 | 1 of 5 | 1 of 3 |" in text
    assert "mirror.example.net" not in text and HEX not in text

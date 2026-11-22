"""K6 and K8 replayed over the added lines of a repository's real commit history (F5 plan
T6.3; spec AC63).

Each commit's added lines, in files a suggestion could change (a source file not on the
never-changed list), go through the very code K6 and K8 run in every room
(`checks.added_line_breaks`), with the file as it stood before the commit giving K6 its
addresses already in the file. People wrote this history, so **every refusal is a false
refusal**: the report counts them, and holds no line of the repository's code.

**Run only on a repository whose owner consented** (T6.3 names "at least one consenting
repository"); the choice is the founder's, after Phase 2. It reads a local clone with
`git` and sends nothing anywhere. K6 and K8 raise no admin notice (UR12) until the founder
has read the report.

    PYTHONPATH=tools/ai-eval uv run python -m fix.replay_rules \\
        --repository <a local clone> --label "<name, and where its consent is recorded>"

The checked-in report is the skeleton until then; `test_replay_rules.py` fails while it is
out of date.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from nexovue_platform.fixes.checks import added_line_breaks, addresses_in_file
from nexovue_platform.fixes.policy import has_source_extension, is_never_changed

REPORT: Final = (
    Path(__file__).resolve().parents[3] / "docs" / "research" / "f5-k6-k8-replay-2026-11.md"
)
# How long one `git` command may run before the replay stops (a guess for a large history).
GIT_SECONDS: Final = 120
# The newest commits replayed when no count is given (a guess: a few months of a team's work).
DEFAULT_COMMITS: Final = 2000
REPLAYED_RULES: Final = ("K6", "K8")

Git = Callable[[Sequence[str]], str]


def git_in(repository: Path, *, timeout: float = GIT_SECONDS) -> Git:
    """`git` in `repository`, its output as text. A command still running at `timeout`
    raises `subprocess.TimeoutExpired`; a failing one raises `CalledProcessError`."""

    def run(arguments: Sequence[str]) -> str:
        done = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        return done.stdout.decode("utf-8", errors="replace")

    return run


@dataclass(frozen=True, slots=True)
class AddedLine:
    commit: str
    path: str
    text: str


def _changeable(path: str) -> bool:
    return has_source_extension(path) and not is_never_changed(path)


def added_lines_of(diff: str) -> Iterator[tuple[str, str]]:
    """`(path, line)` for each added line of a `git show -U0` diff. A file's path is the
    `+++ b/` header's; a deleted file's lines are not added lines."""
    path: str | None = None
    in_hunk = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif not in_hunk and line.startswith("+++ "):
            target = line[4:]
            path = target[2:] if target.startswith("b/") else None
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and path is not None and line.startswith("+"):
            yield path, line[1:]


@dataclass(slots=True)
class Replay:
    commits: int = 0
    files: int = 0
    lines: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    commits_refused: Counter[str] = field(default_factory=Counter)


def replay(git: Git, *, commits: int = DEFAULT_COMMITS) -> Replay:
    """K6 and K8 over the newest `commits` non-merge commits' added lines."""
    result = Replay()
    for sha in git(["log", "--no-merges", "--format=%H", f"-n{commits}"]).split():
        result.commits += 1
        diff = git(["show", "--no-color", "--no-renames", "--no-ext-diff", "-U0", "--format=", sha])
        by_path: dict[str, list[str]] = {}
        for path, text in added_lines_of(diff):
            if _changeable(path):
                by_path.setdefault(path, []).append(text)
        refused_here: set[str] = set()
        for path, lines in by_path.items():
            result.files += 1
            known = addresses_in_file(_before(git, sha, path))
            for text in lines:
                result.lines += 1
                rules = {rule for rule, _ in added_line_breaks(text, known)} & set(REPLAYED_RULES)
                result.refused.update(rules)
                refused_here |= rules
        result.commits_refused.update(refused_here)
    return result


def _before(git: Git, sha: str, path: str) -> str:
    """The file as it stood before `sha`; empty for a new file or a first commit."""
    try:
        return git(["show", f"{sha}^:{path}"])
    except subprocess.CalledProcessError:
        return ""


def render(result: Replay, label: str) -> str:
    rows = "\n".join(
        f"| {rule} | {result.refused[rule]} of {result.lines} | "
        f"{result.commits_refused[rule]} of {result.commits} |"
        for rule in REPLAYED_RULES
    )
    return f"""\
# F5: K6 and K8 over real commit history

Written by `tools/ai-eval/fix/replay_rules.py` (plan T6.3; spec AC63), over **{label}**.
People wrote this history, so every refusal is a false refusal. Counts only; no line of the
repository's code is copied here. Not the gate: K6 and K8 raise no admin notice until the
founder has read this.

Replayed: {result.commits} commits, {result.files} changed source files, {result.lines}
added lines, in files a suggestion could change.

| Rule | Added lines refused | Commits with a refusal |
|---|---|---|
{rows}
"""


def skeleton() -> str:
    return """\
# F5: K6 and K8 over real commit history

**Not yet run: it needs the founder's choice of one consenting repository** (plan T6.3;
spec AC63). This page is replaced, whole, by the report `tools/ai-eval/fix/replay_rules.py`
writes. **No number below is a result: there is none yet.** K6 and K8 raise no admin notice
(UR12) until the founder has read the real report.

## What it does

Each commit's added lines, in files a suggestion could change (a source file not on the
never-changed list), go through the code K6 and K8 run in every room, with the file as it
stood before the commit giving K6 its addresses already in the file. People wrote the
history, so every refusal is a false refusal. It reads a local clone with `git`, sends
nothing anywhere, and the report holds counts only, never the repository's code.

It was tested on a repository made by its test, with lines planted to be refused and lines
planted to pass; nothing else was read.

## What the run needs

- One repository whose owner consented, chosen by the founder. Nexovue's own history is a
  candidate; it is a real repository, so the choice is still his.
- `PYTHONPATH=tools/ai-eval uv run python -m fix.replay_rules --repository <a local clone>
  --label "<name, and where its consent is recorded>"`.

## What it will say

For K6 and K8, added lines refused of all replayed, and commits with a refusal, with no
pass mark. The founder reads it before either rule raises a notice.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repository", type=Path, help="a local clone of a consenting repository")
    parser.add_argument("--label", help="its name, and where its owner's consent is recorded")
    parser.add_argument("--commits", type=int, default=DEFAULT_COMMITS)
    arguments = parser.parse_args(argv)
    if arguments.repository is None:
        REPORT.write_text(skeleton(), encoding="utf-8")
    elif not arguments.label:
        parser.error("--label names the repository and where its consent is recorded")
    else:
        result = replay(git_in(arguments.repository), commits=arguments.commits)
        REPORT.write_text(render(result, arguments.label), encoding="utf-8")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

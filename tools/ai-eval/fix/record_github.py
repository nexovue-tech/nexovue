"""Record GitHub's real answers to F5's routes, as the simulated GitHub's fixtures (plan
T2.10; a release check, not in the gate).

**Not run yet, and never by an agent.** The founder runs it after Phase 2 (foundation
item 28), on a scratch repository, with the founder's approval for each use of real
GitHub (plan, *Validation plan*). Until then the files in
`apps/scheduler/tests/fixtures/github_fix/` are shaped from GitHub's documents, and
`test_fake_github_matches_recordings.py` compares the fake with those.

It replays the same steps that suite drives the fake through, against
`https://api.github.com`, and rewrites each fixture's `status` and `body` with what
GitHub said, keeping its `compared` and `values`. Bodies are cleaned before they are
written: the owner, repository and person's names become the fixtures' made-up ones,
and every `node_id` and token-shaped value is dropped. Tokens are read from the
environment, never from the command line, and are never printed.

    NEXOVUE_RECORD_READ_TOKEN=…  NEXOVUE_RECORD_WRITE_TOKEN=…  \\
      uv run python tools/ai-eval/fix/record_github.py \\
        --repository owner/scratch --first <older commit sha> \\
        --person-login someone --person-id 123 [--large-tree <sha>] [--merged-pull <n>] \\
        [--dry-run]

The read token is an installation token minted with `contents: read, metadata: read`;
the write token with `contents: write, pull_requests: write, metadata: read`. It
creates and then deletes one branch, `nexovue/suggestion-record`, and opens one draft
pull request, which deleting the branch closes. `read_tree_truncated` needs a tree past
GitHub's limit (`--large-tree`), and is left as it is without one; `read_pull_merged` (Step 8)
needs a pull request merged by hand in the scratch repository (`--merged-pull`), likewise.
Run `pnpm exec biome format --write` on the folder afterwards, as the gate formats JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

GITHUB: Final = "https://api.github.com"
RECORDINGS: Final = (
    Path(__file__).resolve().parents[3] / "apps" / "scheduler" / "tests" / "fixtures" / "github_fix"
)
READ_TOKEN_VARIABLE: Final = "NEXOVUE_RECORD_READ_TOKEN"
WRITE_TOKEN_VARIABLE: Final = "NEXOVUE_RECORD_WRITE_TOKEN"
BRANCH: Final = "nexovue/suggestion-record"
# The fixtures' made-up names, which real ones are replaced by.
FIXTURE_OWNER: Final = "acme"
FIXTURE_REPOSITORY: Final = "api"
FIXTURE_PERSON: Final = "octo-dev"
_TOKEN_SHAPED: Final = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+")


@dataclass
class Session:
    repository: str
    first: str
    person_login: str
    person_id: int
    large_tree: str | None
    # Step 8: a pull request merged by hand in the scratch repository, for the poller's shape.
    merged_pull: int | None
    read_token: str
    write_token: str
    # What earlier steps learned: the head, its tree, a blob, the branch's commit.
    known: dict[str, str] = field(default_factory=dict)

    def call(
        self, method: str, path: str, *, write: bool = False, body: Any = None
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            GITHUB + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.write_token if write else self.read_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                raw = answer.read()
                return answer.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as refused:
            raw = refused.read()
            return refused.code, json.loads(raw) if raw else None


def _repo(session: Session) -> str:
    return f"/repos/{session.repository}"


def _learn_head(session: Session) -> tuple[int, Any]:
    status, body = session.call("GET", f"{_repo(session)}/git/ref/heads/main")
    session.known["head"] = body["object"]["sha"]
    return status, body


def _learn_commit(session: Session) -> tuple[int, Any]:
    status, body = session.call("GET", f"{_repo(session)}/git/commits/{session.known['head']}")
    session.known["tree"] = body["tree"]["sha"]
    return status, body


def _learn_tree(session: Session) -> tuple[int, Any]:
    status, body = session.call(
        "GET", f"{_repo(session)}/git/trees/{session.known['tree']}?recursive=1"
    )
    session.known["blob"] = next(e["sha"] for e in body["tree"] if e["type"] == "blob")
    return status, body


def _new_commit(session: Session, message: str) -> str:
    _, body = session.call(
        "POST",
        f"{_repo(session)}/git/commits",
        write=True,
        body={
            "message": message,
            "tree": session.known["tree"],
            "parents": [session.known["head"]],
        },
    )
    return str(body["sha"])


def _branch(session: Session) -> tuple[int, Any]:
    session.known["branch_commit"] = _new_commit(session, "Suggested change (recording)")
    return session.call(
        "POST",
        f"{_repo(session)}/git/refs",
        write=True,
        body={"ref": f"refs/heads/{BRANCH}", "sha": session.known["branch_commit"]},
    )


def _draft(session: Session) -> tuple[int, Any]:
    owner = session.repository.split("/")[0]
    return session.call(
        "POST",
        f"{_repo(session)}/pulls",
        write=True,
        body={
            "title": "Recording a draft (Nexovue fixture run)",
            "head": f"{owner}:{BRANCH}",
            "base": "main",
            "body": "Opened by record_github.py; deleted with its branch.",
            "draft": True,
        },
    )


def _same_tree_child(session: Session) -> tuple[int, Any]:
    _, body = session.call(
        "POST",
        f"{_repo(session)}/git/commits",
        write=True,
        body={
            "message": "Run CI (recording)",
            "tree": session.known["tree"],
            "parents": [session.known["branch_commit"]],
        },
    )
    return session.call(
        "PATCH",
        f"{_repo(session)}/git/refs/heads/{BRANCH}",
        write=True,
        body={"sha": body["sha"], "force": False},
    )


Step = Callable[[Session], "tuple[int, Any] | None"]

# In order: later steps use what earlier ones learned. Names are the fixtures' names.
STEPS: Final[dict[str, Step]] = {
    "read_ref": _learn_head,
    "read_commit": _learn_commit,
    "read_tree_recursive": _learn_tree,
    "read_tree_truncated": lambda s: (
        None
        if s.large_tree is None
        else s.call("GET", f"{_repo(s)}/git/trees/{s.large_tree}?recursive=1")
    ),
    "read_pull_merged": lambda s: (
        None if s.merged_pull is None else s.call("GET", f"{_repo(s)}/pulls/{s.merged_pull}")
    ),
    "read_blob": lambda s: s.call("GET", f"{_repo(s)}/git/blobs/{s.known['blob']}"),
    "compare_ahead": lambda s: s.call("GET", f"{_repo(s)}/compare/{s.first}...main"),
    "compare_unknown": lambda s: s.call("GET", f"{_repo(s)}/compare/{'f' * 40}...main"),
    "permission_write": lambda s: s.call(
        "GET", f"{_repo(s)}/collaborators/{s.person_login}/permission"
    ),
    "read_user": lambda s: s.call("GET", f"/user/{s.person_id}"),
    "write_with_read_token": lambda s: s.call(
        "POST", f"{_repo(s)}/git/blobs", body={"content": "x", "encoding": "utf-8"}
    ),
    "create_blob": lambda s: s.call(
        "POST", f"{_repo(s)}/git/blobs", write=True, body={"content": "x\n", "encoding": "utf-8"}
    ),
    "create_tree": lambda s: s.call(
        "POST", f"{_repo(s)}/git/trees", write=True, body={"base_tree": s.known["tree"], "tree": []}
    ),
    "create_commit": lambda s: s.call(
        "POST",
        f"{_repo(s)}/git/commits",
        write=True,
        body={
            "message": "Recording a commit",
            "tree": s.known["tree"],
            "parents": [s.known["head"]],
        },
    ),
    "create_ref": _branch,
    "create_ref_exists": lambda s: s.call(
        "POST",
        f"{_repo(s)}/git/refs",
        write=True,
        body={"ref": f"refs/heads/{BRANCH}", "sha": s.known["head"]},
    ),
    "update_ref": _same_tree_child,
    "update_ref_not_fast_forward": lambda s: s.call(
        "PATCH",
        f"{_repo(s)}/git/refs/heads/{BRANCH}",
        write=True,
        body={"sha": s.first, "force": False},
    ),
    "create_pull_draft": _draft,
    "create_pull_second": _draft,
    "list_pulls_by_head": lambda s: s.call(
        "GET", f"{_repo(s)}/pulls?head={s.repository.split('/')[0]}:{BRANCH}&state=all", write=True
    ),
    "read_pull": lambda s: s.call(
        "GET", f"{_repo(s)}/pulls/{s.known.get('pull', '1')}", write=True
    ),
    "delete_ref": lambda s: s.call("DELETE", f"{_repo(s)}/git/refs/heads/{BRANCH}", write=True),
}


def cleaned(body: Any, session: Session) -> Any:
    """A body with the real names made the fixtures' ones, and no node id or token."""
    owner, name = session.repository.split("/")
    text = json.dumps(body)
    text = _TOKEN_SHAPED.sub("<token removed>", text)
    for real, made_up in (
        (session.repository, f"{FIXTURE_OWNER}/{FIXTURE_REPOSITORY}"),
        (session.person_login, FIXTURE_PERSON),
        (owner, FIXTURE_OWNER),
        (name, FIXTURE_REPOSITORY),
    ):
        text = text.replace(real, made_up)
    return _without_node_ids(json.loads(text))


def _without_node_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_node_ids(v) for k, v in value.items() if k != "node_id"}
    if isinstance(value, list):
        return [_without_node_ids(each) for each in value]
    return value


def record(session: Session, *, dry_run: bool) -> list[str]:
    """Run each step and rewrite its fixture; the lines say what was done."""
    done: list[str] = []
    for name, step in STEPS.items():
        if dry_run:
            done.append(f"{name}: would call GitHub")
            continue
        answer = step(session)
        if answer is None:
            done.append(f"{name}: left as it is (no input for it)")
            continue
        status, body = answer
        if name == "create_pull_draft" and isinstance(body, dict) and "number" in body:
            session.known["pull"] = str(body["number"])
        path = RECORDINGS / f"{name}.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["_source"] = "Recorded from GitHub by record_github.py, cleaned of names and ids."
        fixture["status"], fixture["body"] = status, cleaned(body, session)
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        done.append(f"{name}: recorded, {status}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record GitHub's answers to F5's routes.")
    parser.add_argument("--repository", required=True, help="owner/name of a scratch repository")
    parser.add_argument("--first", required=True, help="an older commit on main")
    parser.add_argument("--person-login", required=True)
    parser.add_argument("--person-id", required=True, type=int)
    parser.add_argument("--large-tree", help="a tree sha past GitHub's recursive limit")
    parser.add_argument("--merged-pull", type=int, help="a pull request merged by hand")
    parser.add_argument("--dry-run", action="store_true", help="list the steps, call nothing")
    arguments = parser.parse_args(argv)
    tokens = {
        name: os.environ.get(name, "") for name in (READ_TOKEN_VARIABLE, WRITE_TOKEN_VARIABLE)
    }
    if not arguments.dry_run and not all(tokens.values()):
        print(f"set {READ_TOKEN_VARIABLE} and {WRITE_TOKEN_VARIABLE}; nothing was called")
        return 2
    session = Session(
        repository=arguments.repository,
        first=arguments.first,
        person_login=arguments.person_login,
        person_id=arguments.person_id,
        large_tree=arguments.large_tree,
        merged_pull=arguments.merged_pull,
        read_token=tokens[READ_TOKEN_VARIABLE],
        write_token=tokens[WRITE_TOKEN_VARIABLE],
    )
    for line in record(session, dry_run=arguments.dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

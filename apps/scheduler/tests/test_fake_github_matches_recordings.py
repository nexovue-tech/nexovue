"""The simulated GitHub answers F5's routes as the recordings say GitHub does (F5 plan
T2.10).

Each file in `fixtures/github_fix/` holds one request, the status GitHub gives, a body,
the body's fields F5's code relies on (`compared`: present, with the same JSON type) and
the fields whose values decide behaviour (`values`: equal). This suite drives the fake
into the same situation and compares.

**The recordings are not recordings yet.** They are shaped from GitHub's documents,
because the founder decided no step waits on a real GitHub (foundation item 28), so
this proves the fake agrees with the documents as read, not with GitHub. Once the
founder runs `tools/ai-eval/fix/record_github.py` against a scratch repository, the
same suite compares the fake with what GitHub really said.

Not a live suite: it needs only this machine's private address, which the fake listens
on, as F22's suites do.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from tracker_fake import FakeGitHub, MintedToken, serve_fake_github

RECORDINGS: Final = Path(__file__).parent / "fixtures" / "github_fix"
REPOSITORY: Final = "acme/api"
BRANCH: Final = "nexovue/suggestion-1"
READER: Final = "ghs_fake_read_only_token"
WRITER: Final = "ghs_fake_write_token"
PERSON_ID: Final = 9000001
PERSON: Final = "octo-dev"


def recording(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((RECORDINGS / f"{name}.json").read_text())
    return document


@dataclass
class Scene:
    fake: FakeGitHub
    client: httpx.Client
    first: str
    head: str
    tree: str
    blob: str

    def call(
        self, method: str, path: str, *, token: str = WRITER, body: Any = None
    ) -> httpx.Response:
        return self.client.request(
            method,
            self.fake.base_url + path,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    def new_commit(self) -> str:
        """A commit on top of main, for a branch to point at."""
        blob = self.call(
            "POST",
            f"/repos/{REPOSITORY}/git/blobs",
            body={"content": "print(2)\n", "encoding": "utf-8"},
        ).json()["sha"]
        tree = self.call(
            "POST",
            f"/repos/{REPOSITORY}/git/trees",
            body={
                "base_tree": self.tree,
                "tree": [
                    {
                        "path": "services/checkout/pay.py",
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob,
                    }
                ],
            },
        ).json()["sha"]
        return str(
            self.call(
                "POST",
                f"/repos/{REPOSITORY}/git/commits",
                body={
                    "message": "Suggested change",
                    "tree": tree,
                    "parents": [self.head],
                },
            ).json()["sha"]
        )

    def branch(self) -> str:
        sha = self.new_commit()
        self.call(
            "POST",
            f"/repos/{REPOSITORY}/git/refs",
            body={"ref": f"refs/heads/{BRANCH}", "sha": sha},
        )
        return sha

    def draft(self) -> httpx.Response:
        return self.call(
            "POST",
            f"/repos/{REPOSITORY}/pulls",
            body={
                "title": "Suggested change",
                "head": f"acme:{BRANCH}",
                "base": "main",
                "body": "From Nexovue",
                "draft": True,
            },
        )


@pytest.fixture
def scene() -> Iterator[Scene]:
    with serve_fake_github() as fake, httpx.Client(trust_env=False, timeout=10) as client:
        fake.add_repository(4000001, REPOSITORY)
        fake.tokens[READER] = MintedToken(3000001, {"contents": "read", "metadata": "read"}, None)
        fake.tokens[WRITER] = MintedToken(
            3000001, {"contents": "write", "metadata": "read", "pull_requests": "write"}, None
        )
        fake.add_user(PERSON_ID, PERSON)
        fake.grant(REPOSITORY, PERSON, "write")
        first = fake.seed_files(REPOSITORY, {"README.md": "# api\n"})
        head = fake.seed_files(
            REPOSITORY,
            {
                "services/checkout/pay.py": "print(1)\n",
                "bin/run.sh": ("100755", "#!/bin/sh\n"),
            },
        )
        data = fake.git_of(REPOSITORY)
        tree = data.commits[head].tree
        blob = dict(data.flat(tree))["services/checkout/pay.py"][1]
        yield Scene(fake, client, first, head, tree, blob)


# Each recording, and how the scene reaches the moment it records.
Step = Callable[[Scene], httpx.Response]
REPO: Final = f"/repos/{REPOSITORY}"
STEPS: Final[dict[str, Step]] = {
    "read_ref": lambda s: s.call("GET", f"{REPO}/git/ref/heads/main", token=READER),
    "read_commit": lambda s: s.call("GET", f"{REPO}/git/commits/{s.head}", token=READER),
    "read_tree_recursive": lambda s: s.call(
        "GET", f"{REPO}/git/trees/{s.tree}?recursive=1", token=READER
    ),
    "read_tree_truncated": lambda s: _truncated_tree(s),
    "read_blob": lambda s: s.call("GET", f"{REPO}/git/blobs/{s.blob}", token=READER),
    "compare_ahead": lambda s: s.call("GET", f"{REPO}/compare/{s.first}...main", token=READER),
    "compare_unknown": lambda s: s.call("GET", f"{REPO}/compare/{'f' * 40}...main", token=READER),
    "permission_write": lambda s: s.call(
        "GET", f"{REPO}/collaborators/{PERSON}/permission", token=READER
    ),
    "read_user": lambda s: s.call("GET", f"/user/{PERSON_ID}", token=READER),
    "create_blob": lambda s: s.call(
        "POST", f"{REPO}/git/blobs", body={"content": "x\n", "encoding": "utf-8"}
    ),
    "create_tree": lambda s: s.call(
        "POST",
        f"{REPO}/git/trees",
        body={
            "base_tree": s.tree,
            "tree": [
                {
                    "path": "services/checkout/pay.py",
                    "mode": "100644",
                    "type": "blob",
                    "content": "print(3)\n",
                }
            ],
        },
    ),
    "create_commit": lambda s: s.call(
        "POST",
        f"{REPO}/git/commits",
        body={"message": "Suggested change", "tree": s.tree, "parents": [s.head]},
    ),
    "create_ref": lambda s: s.call(
        "POST", f"{REPO}/git/refs", body={"ref": f"refs/heads/{BRANCH}", "sha": s.new_commit()}
    ),
    "create_ref_exists": lambda s: (
        s.branch(),
        s.call("POST", f"{REPO}/git/refs", body={"ref": f"refs/heads/{BRANCH}", "sha": s.head}),
    )[1],
    "update_ref": lambda s: s.call(
        "PATCH",
        f"{REPO}/git/refs/heads/{BRANCH}",
        body={"sha": _same_tree_child(s, s.branch()), "force": False},
    ),
    "update_ref_not_fast_forward": lambda s: (
        s.branch(),
        s.call("PATCH", f"{REPO}/git/refs/heads/{BRANCH}", body={"sha": s.first, "force": False}),
    )[1],
    "delete_ref": lambda s: (s.branch(), s.call("DELETE", f"{REPO}/git/refs/heads/{BRANCH}"))[1],
    "create_pull_draft": lambda s: (s.branch(), s.draft())[1],
    "create_pull_second": lambda s: (s.branch(), s.draft(), s.draft())[2],
    "list_pulls_by_head": lambda s: (
        s.branch(),
        s.draft(),
        s.call("GET", f"{REPO}/pulls?head=acme:{BRANCH}&state=all", token=WRITER),
    )[2],
    "read_pull": lambda s: (s.branch(), s.draft(), s.call("GET", f"{REPO}/pulls/1"))[2],
    "read_pull_merged": lambda s: (
        s.branch(),
        s.draft(),
        s.fake.merge_pull(REPOSITORY, 1, at="2026-11-22T10:00:00Z"),
        s.call("GET", f"{REPO}/pulls/1"),
    )[3],
    "write_with_read_token": lambda s: s.call(
        "POST", f"{REPO}/git/blobs", token=READER, body={"content": "x", "encoding": "utf-8"}
    ),
}


def _truncated_tree(scene: Scene) -> httpx.Response:
    scene.fake.tree_entry_limit = 2
    return scene.call("GET", f"{REPO}/git/trees/{scene.tree}?recursive=1", token=READER)


def _same_tree_child(scene: Scene, parent: str) -> str:
    """Run CI's commit: the same tree, one commit on."""
    tree = scene.fake.git_of(REPOSITORY).commits[parent].tree
    return str(
        scene.call(
            "POST",
            f"{REPO}/git/commits",
            body={"message": "Run CI", "tree": tree, "parents": [parent]},
        ).json()["sha"]
    )


def _at(body: Any, path: str) -> Any:
    for part in path.split("."):
        body = body[int(part)] if isinstance(body, list) else body[part]
    return body


def _json_type(value: Any) -> str:
    return (
        "number"
        if isinstance(value, int | float) and not isinstance(value, bool)
        else type(value).__name__
    )


def test_every_recording_has_a_step_and_every_step_a_recording() -> None:
    assert {path.stem for path in RECORDINGS.glob("*.json")} == set(STEPS)


@pytest.mark.parametrize("name", sorted(STEPS))
def test_the_fake_matches_the_recording(name: str, scene: Scene) -> None:
    recorded = recording(name)
    answer = STEPS[name](scene)

    assert answer.status_code == recorded["status"]
    if recorded["body"] is None:
        assert answer.content == b""
        return
    body = answer.json()
    for path in recorded["compared"]:
        assert _json_type(_at(body, path)) == _json_type(_at(recorded["body"], path)), path
    for path in recorded["values"]:
        assert _at(body, path) == _at(recorded["body"], path), path


def test_a_blob_id_is_git_s_so_f5s_digest_names_the_same_bytes(scene: Scene) -> None:
    from nexovue_platform.fixes.diff import git_blob_id

    assert scene.blob == git_blob_id("print(1)\n")


def test_a_branch_carries_the_mode_it_was_given(scene: Scene) -> None:
    tree = scene.call("GET", f"{REPO}/git/trees/{scene.tree}?recursive=1", token=READER).json()
    modes = {entry["path"]: entry["mode"] for entry in tree["tree"]}

    assert modes["bin/run.sh"] == "100755"
    assert modes["services/checkout/pay.py"] == "100644"


def _open_draft_dropped(scene: Scene) -> httpx.Response:
    scene.branch()
    scene.fake.drop_after_commit = {"create_pull"}
    return scene.draft()


DROPPABLE: Final[dict[str, Step]] = {
    "create_blob": lambda s: s.call(
        "POST", f"{REPO}/git/blobs", body={"content": "y\n", "encoding": "utf-8"}
    ),
    "create_tree": lambda s: s.call(
        "POST",
        f"{REPO}/git/trees",
        body={
            "base_tree": s.tree,
            "tree": [{"path": "new.py", "mode": "100644", "content": "z\n"}],
        },
    ),
    "create_commit": lambda s: s.call(
        "POST",
        f"{REPO}/git/commits",
        body={"message": "Suggested change", "tree": s.tree, "parents": [s.head]},
    ),
    "create_ref": lambda s: s.call(
        "POST", f"{REPO}/git/refs", body={"ref": f"refs/heads/{BRANCH}", "sha": s.head}
    ),
    "create_pull": _open_draft_dropped,
}


@pytest.mark.parametrize("route", sorted(DROPPABLE))
def test_each_write_can_be_applied_and_its_answer_dropped(route: str, scene: Scene) -> None:
    """A crash at each step of the draft job: GitHub did the work, the job never heard."""
    scene.fake.drop_after_commit = {route}
    data = scene.fake.git_of(REPOSITORY)
    objects_before = len(data.blobs) + len(data.trees) + len(data.commits)

    with pytest.raises(httpx.HTTPError):
        DROPPABLE[route](scene)

    applied = {
        "create_blob": len(data.blobs) + len(data.trees) + len(data.commits) > objects_before,
        "create_tree": len(data.blobs) + len(data.trees) + len(data.commits) > objects_before,
        "create_commit": len(data.blobs) + len(data.trees) + len(data.commits) > objects_before,
        "create_ref": BRANCH in data.refs,
        "create_pull": len(scene.fake.pulls_in(REPOSITORY)) == 1,
    }
    assert applied[route]


def test_a_failure_can_be_put_on_any_route(scene: Scene) -> None:
    scene.fake.fail("read_blob", 502)

    assert scene.call("GET", f"{REPO}/git/blobs/{scene.blob}", token=READER).status_code == 502

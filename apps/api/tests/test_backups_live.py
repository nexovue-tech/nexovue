"""`nexovue backup` against a real S3 store, the dev stack's SeaweedFS (ready-to-deploy, T20).

A snapshot shipped off the host comes back byte for byte; one that changes on its way out is refused
and leaves nothing behind; retention prunes after each ship; and the install's bucket survives an
export into a snapshot and an import into an empty bucket.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import hashlib
import io
import os
import socket
import tarfile
import uuid
from collections.abc import Iterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack

from nexovue_api import backups

S3_ENDPOINT: Final = os.environ.get("NEXOVUE_TEST_S3_ENDPOINT", "http://localhost:18333")
S3_KEY: Final = os.environ.get("NEXOVUE_TEST_S3_KEY", "nexovue_dev_access")
S3_SECRET: Final = os.environ.get("NEXOVUE_TEST_S3_SECRET", "nexovue_dev_secret")


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 80), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(S3_ENDPOINT),
    suite="apps/api/tests/test_backups_live.py",
    remedy="the S3 store is not reachable — run `just up`",
)


@pytest.fixture
def bucket() -> Iterator[backups.Bucket]:
    client = backups._client(S3_ENDPOINT, S3_KEY, S3_SECRET, "us-east-1")
    name = f"backup-test-{uuid.uuid4().hex[:12]}"
    client.create_bucket(Bucket=name)
    yield backups.Bucket(client=client, name=name, prefix="nexovue-backups")
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", ())]
        if keys:
            client.delete_objects(Bucket=name, Delete={"Objects": keys})
    client.delete_bucket(Bucket=name)


FILES: Final = {
    "manifest.txt": b"taken_at=2026-10-28T020000Z\n",
    "postgres.dump": os.urandom(300_000),
    "clickhouse.zip": os.urandom(9_000_000),
}


def _snapshot_tar(files: dict[str, bytes], checksums: dict[str, bytes] | None = None) -> io.BytesIO:
    summed = checksums or files
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in summed.items())
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in [(backups.CHECKSUMS, sums.encode()), *files.items()]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    stream.seek(0)
    return stream


def _untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as archive:
        return {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive
            if member.isfile()
        }


def test_a_shipped_snapshot_comes_back_byte_for_byte(bucket: backups.Bucket) -> None:
    backups.ship(bucket, "2026-10-28T020000Z", _snapshot_tar(FILES))

    fetched = io.BytesIO()
    backups.fetch(bucket, "2026-10-28T020000Z", fetched)

    restored = _untar(fetched.getvalue())
    assert {name: restored[name] for name in FILES} == FILES
    assert backups.snapshot_names(bucket) == ["2026-10-28T020000Z"]


def test_a_file_that_changed_on_its_way_out_is_refused_and_nothing_is_left(
    bucket: backups.Bucket,
) -> None:
    tampered = dict(FILES, **{"postgres.dump": b"not what was summed"})

    with pytest.raises(backups.BackupError, match=r"postgres\.dump changed on its way out"):
        backups.ship(bucket, "2026-10-28T020000Z", _snapshot_tar(tampered, checksums=FILES))

    assert backups.snapshot_names(bucket) == []


def test_each_ship_prunes_to_the_retention(bucket: backups.Bucket) -> None:
    small = {"manifest.txt": b"x"}
    for day in range(1, 10):
        backups.ship(bucket, f"2026-10-{day:02d}T020000Z", _snapshot_tar(small))

    names = backups.snapshot_names(bucket)

    assert names[-7:] == [f"2026-10-{day:02d}T020000Z" for day in range(3, 10)]
    assert "2026-10-01T020000Z" not in names, "older than seven days and not a week's newest"


def test_the_bucket_survives_an_export_and_an_import(bucket: backups.Bucket) -> None:
    objects = {"org/1/sourcemaps/app.js.map": os.urandom(70_000), "checks/a/file.txt": b"hello"}
    for key, data in objects.items():
        bucket.client.put_object(Bucket=bucket.name, Key=key, Body=data)
    exported = io.BytesIO()
    assert backups.bucket_export(bucket, exported) == len(objects)

    empty = backups.Bucket(client=bucket.client, name=f"{bucket.name}-restore")
    bucket.client.create_bucket(Bucket=empty.name)
    try:
        exported.seek(0)
        assert backups.bucket_import(empty, exported) == len(objects)
        for key, data in objects.items():
            assert bucket.client.get_object(Bucket=empty.name, Key=key)["Body"].read() == data
    finally:
        for key in objects:
            bucket.client.delete_object(Bucket=empty.name, Key=key)
        bucket.client.delete_bucket(Bucket=empty.name)

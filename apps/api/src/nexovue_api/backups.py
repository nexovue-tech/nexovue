"""Backups off the host, and the bucket in and out of a snapshot (ready-to-deploy, T20).

`nexovue backup <command>`, run by `deploy/nexovue backup` and `restore` inside the install's own
network, so it reaches the bundled object store and needs nothing installed on the host. Snapshots
travel as a tar stream on stdin or stdout — the host never shares a directory with the container,
whose user cannot write the host's files.

    ship <name>        a snapshot's tar on stdin → the off-host store, each file checked, then prune
    fetch <name>       the off-host store → a snapshot's tar on stdout, each file checked
    list               the snapshots off the host, oldest first
    bucket-export      every object in the install's bucket → a tar on stdout
    bucket-import      a tar on stdin → the install's bucket

The off-host store is any S3-compatible bucket the operator owns, never the install's own:
`BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, and optionally
`BACKUP_S3_ENDPOINT` (unset for AWS), `BACKUP_S3_REGION` and `BACKUP_S3_PREFIX`.

Kept: the newest snapshot of each of the last 7 days that have one, and of each of the last 4 weeks
(design.md §8). A snapshot whose upload failed its check is deleted, never kept half-written.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Any, Final

import boto3
from botocore.config import Config

# backup.sh's directory name for a snapshot, in UTC.
SNAPSHOT_FORMAT: Final = "%Y-%m-%dT%H%M%SZ"
CHECKSUMS: Final = "SHA256SUMS"
KEEP_DAILY: Final = 7
KEEP_WEEKLY: Final = 4


class BackupError(Exception):
    """A backup step that cannot be completed; the message names what to fix."""


@dataclass(frozen=True)
class Bucket:
    """One S3-compatible bucket and where its objects live in it."""

    client: Any
    name: str
    prefix: str = ""

    def key(self, *parts: str) -> str:
        """`prefix/part/part`; the prefix alone with no parts."""
        return "/".join(part for part in (self.prefix.strip("/"), *parts) if part)


def _client(endpoint: str | None, access_key: str, secret_key: str, region: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        # SeaweedFS and most S3-compatible stores serve the bucket as a path.
        config=Config(
            retries={"mode": "standard", "max_attempts": 5}, s3={"addressing_style": "path"}
        ),
    )


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise BackupError(f"{name} is not set")
    return value


def offsite_bucket() -> Bucket:
    """The operator's off-host bucket, from BACKUP_S3_*."""
    return Bucket(
        client=_client(
            os.environ.get("BACKUP_S3_ENDPOINT"),
            _required("BACKUP_S3_ACCESS_KEY_ID"),
            _required("BACKUP_S3_SECRET_ACCESS_KEY"),
            os.environ.get("BACKUP_S3_REGION") or "us-east-1",
        ),
        name=_required("BACKUP_S3_BUCKET"),
        prefix=os.environ.get("BACKUP_S3_PREFIX") or "nexovue-backups",
    )


def install_bucket() -> Bucket:
    """The install's own bucket — source maps and check files — from S3_*."""
    return Bucket(
        client=_client(
            _required("S3_ENDPOINT"),
            _required("S3_ACCESS_KEY_ID"),
            _required("S3_SECRET_ACCESS_KEY"),
            os.environ.get("S3_REGION") or "us-east-1",
        ),
        name=_required("S3_BUCKET"),
    )


def snapshots_to_keep(
    names: Iterable[str], *, daily: int = KEEP_DAILY, weekly: int = KEEP_WEEKLY
) -> set[str]:
    """The newest snapshot of each of the last `daily` days and `weekly` ISO weeks that have one.

    Counted in days and weeks that hold a snapshot, not calendar days back from today, so a host
    that was down for a fortnight still keeps its last week of backups. A name that is not a
    snapshot's is kept: pruning deletes only what it recognises.
    """
    taken: dict[str, datetime] = {}
    kept: set[str] = set()
    for name in names:
        try:
            taken[name] = datetime.strptime(name, SNAPSHOT_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            kept.add(name)
    newest_first = sorted(taken, key=taken.__getitem__, reverse=True)
    for period, count in ((_day, daily), (_week, weekly)):
        seen: set[tuple[int, ...]] = set()
        for name in newest_first:
            bucket = period(taken[name])
            if bucket in seen:
                continue
            if len(seen) == count:
                break
            seen.add(bucket)
            kept.add(name)
    return kept


def _day(moment: datetime) -> tuple[int, ...]:
    return (moment.year, moment.month, moment.day)


def _week(moment: datetime) -> tuple[int, ...]:
    year, week, _ = moment.isocalendar()
    return (year, week)


def _parse_checksums(text: str) -> dict[str, str]:
    """`sha256sum` output: `<hex>  <file>` per line.

    A snapshot holds plain file names only. Anything else is refused: these names come back from
    the off-host bucket on a restore, and one like `../x` would be written outside the snapshot.
    """
    sums: dict[str, str] = {}
    for line in text.splitlines():
        digest, _, file_name = line.strip().partition(" ")
        file_name = file_name.strip().lstrip("*")
        if not digest or not file_name:
            continue
        if "/" in file_name or "\\" in file_name or file_name.startswith("."):
            raise BackupError(f"{CHECKSUMS} names {file_name!r}, which is not a snapshot's file")
        sums[file_name] = digest
    return sums


class _HashingReader:
    """A file object that hashes what is read through it, so a stream is checked as it uploads."""

    def __init__(self, source: IO[bytes]) -> None:
        self._source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        self.digest.update(chunk)
        return chunk


def snapshot_names(bucket: Bucket) -> list[str]:
    """The complete snapshots in the bucket, oldest first.

    A snapshot is complete once its SHA256SUMS is there, which `ship` writes last. Listed by object
    rather than by folder: SeaweedFS keeps a folder after its objects are deleted.
    """
    names: set[str] = set()
    pages = bucket.client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket.name, Prefix=f"{bucket.key()}/" if bucket.prefix else ""
    )
    for page in pages:
        for item in page.get("Contents", ()):
            folder, _, file_name = item["Key"].rpartition("/")
            if file_name == CHECKSUMS:
                names.add(folder.rsplit("/", 1)[-1])
    return sorted(names)


def _delete_snapshot(bucket: Bucket, name: str) -> None:
    pages = bucket.client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket.name, Prefix=bucket.key(name) + "/"
    )
    for page in pages:
        keys = [{"Key": item["Key"]} for item in page.get("Contents", ())]
        if keys:
            bucket.client.delete_objects(Bucket=bucket.name, Delete={"Objects": keys})


def ship(bucket: Bucket, name: str, stream: IO[bytes]) -> list[str]:
    """Upload a snapshot's tar, checking every file against its SHA256SUMS; prune; the deleted.

    SHA256SUMS must come first in the stream, which `deploy/nexovue backup` guarantees, so each
    file is checked as it passes rather than after the whole snapshot is held somewhere. It is
    uploaded last, so a snapshot cut off halfway is never mistaken for a complete one.
    """
    # Refuse a name pruning would never recognise.
    datetime.strptime(name, SNAPSHOT_FORMAT).replace(tzinfo=UTC)
    expected: dict[str, str] | None = None
    sums = b""
    shipped: set[str] = set()
    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                file_name = member.name.removeprefix("./")
                source = archive.extractfile(member)
                if source is None:
                    continue
                if expected is None:
                    if file_name != CHECKSUMS:
                        raise BackupError(f"{CHECKSUMS} must come first in the snapshot")
                    sums = source.read()
                    expected = _parse_checksums(sums.decode())
                    continue
                if file_name not in expected:
                    raise BackupError(f"{file_name} is not in {CHECKSUMS}")
                reader = _HashingReader(source)
                bucket.client.upload_fileobj(reader, bucket.name, bucket.key(name, file_name))
                if reader.digest.hexdigest() != expected[file_name]:
                    raise BackupError(f"{file_name} changed on its way out: checksum mismatch")
                shipped.add(file_name)
        if expected is None or set(expected) != shipped:
            missing = sorted(set(expected or {}) - shipped) or [CHECKSUMS]
            raise BackupError(f"the snapshot is incomplete; missing {', '.join(missing)}")
        # Last: until it is there, the snapshot does not exist to `snapshot_names` or to pruning.
        bucket.client.put_object(Bucket=bucket.name, Key=bucket.key(name, CHECKSUMS), Body=sums)
    except BaseException:
        _delete_snapshot(bucket, name)
        raise
    return prune(bucket)


def prune(bucket: Bucket) -> list[str]:
    """Delete the snapshots retention does not keep; the deleted, oldest first."""
    names = snapshot_names(bucket)
    keep = snapshots_to_keep(names)
    deleted = [name for name in names if name not in keep]
    for name in deleted:
        _delete_snapshot(bucket, name)
    return deleted


def fetch(bucket: Bucket, name: str, out: IO[bytes]) -> None:
    """A snapshot as a tar on `out`, each file checked against SHA256SUMS as it streams.

    A mismatch raises after the file is written, so the caller must treat a failed exit as a
    corrupt snapshot; the restore scripts check SHA256SUMS again before touching a store.
    """
    try:
        sums_body = bucket.client.get_object(Bucket=bucket.name, Key=bucket.key(name, CHECKSUMS))
    except bucket.client.exceptions.NoSuchKey as missing:
        raise BackupError(f"no snapshot {name} off the host") from missing
    sums_bytes: bytes = sums_body["Body"].read()
    with tarfile.open(fileobj=out, mode="w|") as archive:
        _add(archive, CHECKSUMS, io.BytesIO(sums_bytes), len(sums_bytes))
        for file_name, digest in _parse_checksums(sums_bytes.decode()).items():
            body = bucket.client.get_object(Bucket=bucket.name, Key=bucket.key(name, file_name))
            reader = _HashingReader(body["Body"])
            _add(archive, file_name, reader, body["ContentLength"])
            if reader.digest.hexdigest() != digest:
                raise BackupError(f"{file_name} is corrupt off the host: checksum mismatch")


def _add(
    archive: tarfile.TarFile, name: str, source: IO[bytes] | _HashingReader, size: int
) -> None:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    archive.addfile(info, source)


def bucket_export(bucket: Bucket, out: IO[bytes]) -> int:
    """Every object in the bucket as a tar on `out`; how many."""
    count = 0
    with tarfile.open(fileobj=out, mode="w|") as archive:
        for page in bucket.client.get_paginator("list_objects_v2").paginate(Bucket=bucket.name):
            for item in page.get("Contents", ()):
                body = bucket.client.get_object(Bucket=bucket.name, Key=item["Key"])
                _add(archive, item["Key"], body["Body"], body["ContentLength"])
                count += 1
    return count


def bucket_import(bucket: Bucket, stream: IO[bytes]) -> int:
    """Every file in the tar on `stream` into the bucket, under its own name; how many."""
    count = 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            source = archive.extractfile(member) if member.isfile() else None
            if source is None:
                continue
            # Through the reader: boto3 probes a tar member's seek, which a stream cannot answer.
            bucket.client.upload_fileobj(_HashingReader(source), bucket.name, member.name)
            count += 1
    return count


USAGE: Final = (
    "usage: nexovue backup ship <name> | fetch <name> | list | bucket-export | bucket-import"
)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    command, rest = (arguments[0], arguments[1:]) if arguments else ("", [])
    try:
        if command == "ship" and len(rest) == 1:
            for name in ship(offsite_bucket(), rest[0], sys.stdin.buffer):
                print(f"pruned {name}", file=sys.stderr)
            print(f"shipped {rest[0]}", file=sys.stderr)
        elif command == "fetch" and len(rest) == 1:
            fetch(offsite_bucket(), rest[0], sys.stdout.buffer)
        elif command == "list" and not rest:
            print("\n".join(snapshot_names(offsite_bucket())))
        elif command == "bucket-export" and not rest:
            count = bucket_export(install_bucket(), sys.stdout.buffer)
            print(f"bucket: {count} object(s) exported", file=sys.stderr)
        elif command == "bucket-import" and not rest:
            count = bucket_import(install_bucket(), sys.stdin.buffer)
            print(f"bucket: {count} object(s) imported", file=sys.stderr)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except BackupError as error:
        print(f"backup: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

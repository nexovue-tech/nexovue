"""The object store, over the generic S3 API only.

ADR 0037 fixes the store as the S3 API rather than any one vendor's: SeaweedFS
locally, AWS S3 in deployment. Nothing here may use a feature outside that API,
because the two have to be the same store from this side.

boto3 rather than an async wrapper. `aiobotocore` pins `botocore<1.43.57` and
`aioboto3` pins `aiobotocore==2.25.1`, either of which drags the whole
workspace's AWS SDK backwards and couples the scheduler's STS code (ADR 0076) to
a third party's release cadence. boto3's calls are blocking, so every one of
them runs on a worker thread through anyio — which Starlette already ships —
rather than on the event loop where it would stall every other request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from nexovue_api.settings import ObjectStoreSettings

# What a caller may not do with a key it did not build. A traversal in a stored
# key would let one project's upload name another's object, so keys are built
# here and validated before use rather than trusted from a row.
_FORBIDDEN_IN_KEY: Final = ("..", "//")

# Retries are botocore's, not ours: it knows which S3 error codes are worth
# repeating. The cap is here so a store that is down fails the request rather
# than holding a worker thread for the client's whole patience.
_MAX_ATTEMPTS: Final = 3
_CONNECT_TIMEOUT_SECONDS: Final = 5
_READ_TIMEOUT_SECONDS: Final = 30

# The S3 API accepts at most a thousand keys in one DeleteObjects request, and
# lists at most a thousand at a time. One number, because a page is deleted as
# it is listed.
_KEYS_PER_REQUEST: Final = 1000

# A prefix names a folder, never half a key. Without this, `source-maps/<id>`
# would also match `source-maps/<id-of-someone-else>…`, so a deletion aimed at
# one tenant could reach another whose id happened to start with the same text.
_PREFIX_SUFFIX: Final = "/"


class ObjectStoreError(Exception):
    """The store refused or could not be reached. Carries no credentials."""


@dataclass(frozen=True)
class StoredObject:
    """Where an object landed, and how big it turned out to be."""

    key: str
    size_bytes: int


class ObjectStore:
    """Reads and writes bytes by key. One per process; the client is thread-safe."""

    def __init__(self, settings: ObjectStoreSettings) -> None:
        self._bucket = settings.bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region,
            config=Config(
                retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                # SeaweedFS serves the bucket as a path, not as a subdomain, and
                # so does S3 for a bucket name that is not DNS-safe.
                s3={"addressing_style": "path"},
            ),
        )

    async def put(self, key: str, body: bytes, *, content_type: str) -> StoredObject:
        """Write one object, replacing anything already at that key."""
        _refuse_unsafe(key)
        await self._off_the_loop(
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType=content_type
            ),
            f"store {len(body)} bytes",
        )
        return StoredObject(key=key, size_bytes=len(body))

    async def get(self, key: str) -> bytes:
        """Read one object whole. Callers store only objects they can hold."""
        _refuse_unsafe(key)

        def read() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        return await self._off_the_loop(read, "read an object")

    async def delete(self, key: str) -> None:
        """Remove one object. Deleting what is not there is not an error in S3."""
        _refuse_unsafe(key)
        await self._off_the_loop(
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key),
            "delete an object",
        )

    async def delete_prefix(self, prefix: str) -> int:
        """Remove every object under one prefix. Returns how many went.

        A prefix rather than a list of keys taken from rows, because an object
        whose row was never written is still the customer's data: uploads store
        the bytes first, so a row-driven deletion would leave exactly those
        behind. The prefix is built by the caller from ids it owns.
        """
        _refuse_unsafe(prefix)
        if not prefix.endswith(_PREFIX_SUFFIX):
            raise ObjectStoreError("refused a prefix that does not name a folder")
        return await self._off_the_loop(
            lambda: self._delete_everything_under(prefix), "delete objects under a prefix"
        )

    def _delete_everything_under(self, prefix: str) -> int:
        """List and delete a page at a time, so a large tenant needs no big list."""
        deleted = 0
        pages = self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self._bucket,
            Prefix=prefix,
            PaginationConfig={"PageSize": _KEYS_PER_REQUEST},
        )
        for page in pages:
            keys = [{"Key": stored["Key"]} for stored in page.get("Contents", ())]
            if not keys:
                continue
            refused = self._client.delete_objects(
                Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True}
            ).get("Errors", ())
            # Reported per key rather than raised, so a partial failure would
            # otherwise be counted as a complete erasure.
            if refused:
                raise ObjectStoreError(f"the store kept {len(refused)} object(s) under the prefix")
            deleted += len(keys)
        return deleted

    async def ensure_bucket(self) -> None:
        """Create the bucket when it does not exist, so a fresh install works.

        `head_bucket` first because creating one that exists is an error on AWS
        and a success on SeaweedFS, and the two must not behave differently.
        """

        def create_if_missing() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await self._off_the_loop(create_if_missing, "prepare the bucket")

    async def _off_the_loop[T](self, call: Callable[[], T], describe: str) -> T:
        """Run one blocking S3 call on a worker thread, with its failure named.

        The cause is not chained into the message: botocore puts the endpoint,
        and sometimes the signed URL, into its own string.
        """
        try:
            return await anyio.to_thread.run_sync(call)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStoreError(f"could not {describe}") from error


def _refuse_unsafe(key: str) -> None:
    """Reject a key that could name an object outside the one it describes."""
    if not key or key.startswith("/") or any(part in key for part in _FORBIDDEN_IN_KEY):
        raise ObjectStoreError("refused an unsafe object key")

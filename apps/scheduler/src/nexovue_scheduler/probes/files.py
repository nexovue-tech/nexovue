"""Reading the files a check sends, from the API's object store, into memory.

A small cache per worker process: a check running every minute would otherwise
download the same bytes every minute. A file never changes under its id — a
replaced file is a new upload with a new id — so the cache cannot serve stale
bytes, only evict them.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from nexovue_platform import erasure

from nexovue_scheduler.probes.failures import MisconfiguredError, ProberError
from nexovue_scheduler.settings import Settings

MAX_CACHED_FILES: Final = 64
MAX_CACHED_BYTES: Final = 32 * 1024 * 1024
_MISSING_CODES: Final = frozenset({"NoSuchKey", "404"})
# One page of keys per delete request; the S3 API's own maximum.
_KEYS_PER_REQUEST: Final = 1000


def object_key(org_id: str, file_id: str) -> str:
    """The API's key for a check file. `checkfiles/service.py` builds the same."""
    return f"{erasure.CHECK_FILES_ROOT}/{org_id}/files/{file_id}"


class ObjectStoreFiles:
    """File bytes by id, cached, from the S3 API."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._bucket = settings.s3_bucket
        self._client = client
        self._settings = settings
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cached_bytes = 0

    def read(self, org_id: str, file_id: str) -> bytes:
        key = object_key(org_id, file_id)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        body = self._download(key, file_id)
        self._remember(key, body)
        return body

    def delete(self, org_id: str, file_id: str) -> None:
        self._cache.pop(object_key(org_id, file_id), None)
        try:
            self._store().delete_object(Bucket=self._bucket, Key=object_key(org_id, file_id))
        except (BotoCoreError, ClientError) as refused:
            raise ProberError("a file could not be deleted from storage") from refused

    def delete_prefix(self, prefix: str) -> int:
        """Every object under one tenant prefix, a page at a time; how many went.

        For the nightly erasure (ADR-014). A prefix rather than keys from rows,
        because the request already deleted the rows, and an object whose row was
        never written is still the customer's.
        """
        if not prefix.endswith("/") or prefix.count("/") < 2:
            raise ProberError(f"refused a prefix that does not name one tenant's folder: {prefix}")
        store = self._store()
        deleted = 0
        try:
            pages = store.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket,
                Prefix=prefix,
                PaginationConfig={"PageSize": _KEYS_PER_REQUEST},
            )
            for page in pages:
                keys = [{"Key": stored["Key"]} for stored in page.get("Contents", ())]
                if not keys:
                    continue
                refused = store.delete_objects(
                    Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True}
                ).get("Errors", ())
                # Reported per key rather than raised by the client.
                if refused:
                    raise ProberError(f"the store kept {len(refused)} object(s) under {prefix}")
                deleted += len(keys)
        except (BotoCoreError, ClientError) as failed:
            raise ProberError(f"objects under {prefix} could not be deleted") from failed
        for key in [cached for cached in self._cache if cached.startswith(prefix)]:
            self._cached_bytes -= len(self._cache.pop(key))
        return deleted

    def _download(self, key: str, file_id: str) -> bytes:
        try:
            response = self._store().get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
        except ClientError as refused:
            code = str(refused.response.get("Error", {}).get("Code", ""))
            if code in _MISSING_CODES:
                raise MisconfiguredError(
                    f"the file {file_id} is no longer stored, so the request was not sent"
                ) from refused
            raise ProberError("the file storage refused a read") from refused
        except BotoCoreError as unreachable:
            raise ProberError("the file storage could not be reached") from unreachable
        return body

    def _remember(self, key: str, body: bytes) -> None:
        self._cache[key] = body
        self._cached_bytes += len(body)
        while len(self._cache) > MAX_CACHED_FILES or self._cached_bytes > MAX_CACHED_BYTES:
            _, evicted = self._cache.popitem(last=False)
            self._cached_bytes -= len(evicted)

    def _store(self) -> Any:
        if self._client is None:
            settings = self._settings
            if not (
                settings.s3_endpoint
                and settings.s3_bucket
                and settings.s3_access_key_id
                and settings.s3_secret_access_key
            ):
                raise ProberError("no file storage is configured on this prober")
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
                region_name=settings.s3_region,
                config=Config(
                    retries={"max_attempts": 2, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=10,
                    s3={"addressing_style": "path"},
                ),
            )
        return self._client

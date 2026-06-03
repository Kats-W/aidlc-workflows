"""S3-backed raw content store for crawled chunks.

:class:`S3ContentStore` persists the plain-text body of each chunk to the
crawl-content bucket so the EmbedderLambda (and future re-embedding jobs) can
retrieve the source text without re-crawling. boto3 is synchronous, so blocking
calls are dispatched to a worker thread to keep the public API ``async``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ObjectNotFoundError, S3AccessError

logger = Logger()


class S3ContentStore:
    """Stores and retrieves chunk text in the crawl-content S3 bucket.

    Object keys follow ``content/{source_url_hash}/{chunk_id}.txt``.
    """

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        """Args:
        bucket: Target S3 bucket name.
        client: Optional pre-built boto3 S3 client (injected in tests).
        """
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    @staticmethod
    def build_key(source_url_hash: str, chunk_id: str) -> str:
        """Return the canonical object key for a chunk."""
        # chunk_id may contain '#'; keep only the index suffix for the filename.
        safe_chunk = chunk_id.replace("#", "_")
        return f"content/{source_url_hash}/{safe_chunk}.txt"

    async def put(self, key: str, body: str) -> str:
        """Upload ``body`` to ``key`` and return the key."""

        def _put() -> None:
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body.encode("utf-8"),
                    ContentType="text/plain; charset=utf-8",
                )
            except ClientError as exc:
                raise S3AccessError(f"failed to put object {key}") from exc

        await asyncio.to_thread(_put)
        logger.debug("put object", extra={"bucket": self._bucket, "key": key})
        return key

    async def get(self, key: str) -> str:
        """Download and return the UTF-8 text body at ``key``.

        Raises:
            ObjectNotFoundError: If the object does not exist.
            S3AccessError: On any other S3 failure.
        """

        def _get() -> str:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
                return response["Body"].read().decode("utf-8")
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFoundError(f"object not found: {key}") from exc
                raise S3AccessError(f"failed to get object {key}") from exc

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        """Delete the object at ``key`` (idempotent)."""

        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                raise S3AccessError(f"failed to delete object {key}") from exc

        await asyncio.to_thread(_delete)
        logger.debug("deleted object", extra={"bucket": self._bucket, "key": key})

"""S3-backed combined vector cache for the cosine similarity searcher.

:class:`VectorCacheS3Store` persists the *entire* VectorStore corpus (embedding
matrix + chunk metadata) as a single S3 object. The EmbedderLambda (U-02)
rebuilds this cache once per crawl cycle (on the final diff batch);
:class:`CosineSimilaritySearcher`
(U-03) reads it on a cold ``/tmp`` cache instead of running a full DynamoDB
``Scan`` on the request path.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from typing import Any

import boto3
import msgpack
import numpy as np
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ObjectNotFoundError, S3AccessError

logger = Logger()

#: Object key for the combined-corpus cache. The matrix and metadata are
#: packed into a single object so a reader always observes one atomic
#: snapshot — never a mix of an old and a new write (which previously
#: happened with two independent PutObject calls and caused
#: ``matrix.shape[0] != len(meta)`` under concurrent rebuilds).
CACHE_KEY: str = "vector-cache/cache.msgpack"


def build_matrix_and_meta(items: list[dict[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Convert :meth:`VectorStore.scan_all` items into a matrix + metadata list."""
    meta = [
        {"chunkId": it["chunkId"], "sourceUrl": it.get("sourceUrl", ""), "text": it.get("text", "")}
        for it in items
    ]
    if items:
        # float32 halves the matrix/cache size versus float64 with no
        # meaningful loss of cosine-similarity precision, which matters both
        # for the EmbedderLambda rebuild's peak memory and the cache object
        # size read back by the searcher.
        matrix = np.asarray([it["embedding"] for it in items], dtype=np.float32)
    else:
        matrix = np.empty((0, 0), dtype=np.float32)
    return matrix, meta


class VectorCacheS3Store:
    """Reads/writes the combined embedding matrix + metadata to S3."""

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        """Args:
        bucket: Target S3 bucket name (the shared crawl-content bucket).
        client: Optional pre-built boto3 S3 client (injected in tests).
        """
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    async def write(self, matrix: np.ndarray, meta: list[dict[str, Any]]) -> None:
        """Upload the combined corpus matrix + metadata to S3 as one object."""

        def _write() -> None:
            try:
                vectors_buf = io.BytesIO()
                np.save(vectors_buf, matrix)
                body = msgpack.packb(
                    {"vectors": vectors_buf.getvalue(), "meta": meta}, use_bin_type=True
                )
                self._client.put_object(Bucket=self._bucket, Key=CACHE_KEY, Body=body)
            except ClientError as exc:
                raise S3AccessError(f"failed to write vector cache: {exc}") from exc

        await asyncio.to_thread(_write)
        logger.info("vector cache written to s3", extra={"rows": len(meta)})

    async def read(
        self,
        on_loaded: Callable[[np.ndarray, list[dict[str, Any]]], None] | None = None,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Download and parse the combined corpus matrix + metadata.

        Args:
            on_loaded: Optional callback invoked inside the download thread
                after a successful parse, *before* the result is returned.
                Because ``asyncio.to_thread`` threads survive coroutine
                cancellation, this guarantees the callback fires even when
                the awaiting coroutine is cancelled by a timeout — useful
                for persisting the result to ``/tmp`` on first load.

        Raises:
            ObjectNotFoundError: If the cache has not been built yet.
            S3AccessError: On any other S3 failure.
        """

        def _read() -> tuple[np.ndarray, list[dict[str, Any]]]:
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=CACHE_KEY)
                body = obj["Body"].read()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFoundError("vector cache not found in s3") from exc
                raise S3AccessError(f"failed to read vector cache: {exc}") from exc

            unpacked = msgpack.unpackb(body, raw=False)
            matrix = np.load(io.BytesIO(unpacked["vectors"]))
            meta: list[dict[str, Any]] = unpacked["meta"]
            if on_loaded is not None:
                on_loaded(matrix, meta)
            return matrix, meta

        return await asyncio.to_thread(_read)

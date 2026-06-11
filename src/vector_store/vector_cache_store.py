"""S3-backed combined vector cache for the cosine similarity searcher.

:class:`VectorCacheS3Store` persists the *entire* VectorStore corpus (embedding
matrix + chunk metadata) as a single S3 object pair. The EmbedderLambda (U-02)
rebuilds this cache once per crawl cycle (on the final diff batch);
:class:`CosineSimilaritySearcher`
(U-03) reads it on a cold ``/tmp`` cache instead of running a full DynamoDB
``Scan`` on the request path.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import boto3
import numpy as np
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ObjectNotFoundError, S3AccessError

logger = Logger()

#: Object keys for the combined-corpus cache.
VECTORS_KEY: str = "vector-cache/vectors.npy"
META_KEY: str = "vector-cache/vectors_meta.json"


def build_matrix_and_meta(items: list[dict[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Convert :meth:`VectorStore.scan_all` items into a matrix + metadata list."""
    meta = [
        {"chunkId": it["chunkId"], "sourceUrl": it.get("sourceUrl", ""), "text": it.get("text", "")}
        for it in items
    ]
    if items:
        matrix = np.asarray([it["embedding"] for it in items], dtype=np.float64)
    else:
        matrix = np.empty((0, 0), dtype=np.float64)
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
        """Upload the combined corpus matrix + metadata to S3."""

        def _write() -> None:
            try:
                buf = io.BytesIO()
                np.save(buf, matrix)
                self._client.put_object(Bucket=self._bucket, Key=VECTORS_KEY, Body=buf.getvalue())
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=META_KEY,
                    Body=json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                )
            except ClientError as exc:
                raise S3AccessError("failed to write vector cache") from exc

        await asyncio.to_thread(_write)
        logger.info("vector cache written to s3", extra={"rows": len(meta)})

    async def read(self) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Download and parse the combined corpus matrix + metadata.

        Raises:
            ObjectNotFoundError: If the cache has not been built yet.
            S3AccessError: On any other S3 failure.
        """

        def _read() -> tuple[np.ndarray, list[dict[str, Any]]]:
            try:
                vectors_obj = self._client.get_object(Bucket=self._bucket, Key=VECTORS_KEY)
                meta_obj = self._client.get_object(Bucket=self._bucket, Key=META_KEY)
                vectors_body = vectors_obj["Body"].read()
                meta_body = meta_obj["Body"].read()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFoundError("vector cache not found in s3") from exc
                raise S3AccessError("failed to read vector cache") from exc

            matrix = np.load(io.BytesIO(vectors_body))
            meta = json.loads(meta_body)
            return matrix, meta

        return await asyncio.to_thread(_read)

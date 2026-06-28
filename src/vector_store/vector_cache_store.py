"""S3-backed vector cache for the cosine similarity searcher.

:class:`VectorCacheS3Store` persists the embedding matrix and lightweight
metadata (chunkId + sourceUrl, **no text**) as two S3 objects — a numpy
``.npy`` file and a JSON file. Text is fetched from DynamoDB at query time
for the top-k hits only.

Serialization uses ``numpy.save`` and ``json.dump`` directly to /tmp files,
avoiding in-memory buffers entirely. This keeps the write-path peak memory
at ~520 MB (just the matrix + meta) instead of ~2,550 MB with the former
``msgpack.pack`` approach whose internal buffer-doubling caused OOM.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any

import boto3
import numpy as np
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import CacheConsistencyError, ObjectNotFoundError, S3AccessError

logger = Logger()

MATRIX_KEY: str = "vector-cache/matrix.npy"
META_KEY: str = "vector-cache/meta.json"


def _assert_consistent(matrix: np.ndarray, meta: list[dict[str, Any]], where: str) -> None:
    """Guard the matrix/meta row-count invariant before persisting.

    matrix and meta live in two separate S3 objects, so a drift between them
    (observed in production: 129,861 rows vs 129,863 meta entries) makes the
    searcher discard the whole corpus. Never write a pair that disagrees.
    """
    rows = int(matrix.shape[0]) if matrix.ndim == 2 else 0
    if rows != len(meta):
        raise CacheConsistencyError(
            f"vector cache row mismatch in {where}: matrix={rows} meta={len(meta)}"
        )


def build_matrix_and_meta(items: list[dict[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Convert :meth:`VectorStore.scan_all` items into a matrix + metadata list."""
    meta = [
        {"chunkId": it["chunkId"], "sourceUrl": it.get("sourceUrl", "")}
        for it in items
    ]
    if items:
        matrix = np.asarray([it["embedding"] for it in items], dtype=np.float32)
    else:
        matrix = np.empty((0, 0), dtype=np.float32)
    return matrix, meta


class VectorCacheS3Store:
    """Reads/writes the embedding matrix + metadata to S3."""

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    _TMP_MATRIX = "/tmp/cache_matrix.npy"
    _TMP_META = "/tmp/cache_meta.json"

    async def write(self, matrix: np.ndarray, meta: list[dict[str, Any]]) -> None:
        """Write matrix and metadata to S3 as separate objects.

        Writes to /tmp first, then uploads — no large in-memory buffers.
        """

        _assert_consistent(matrix, meta, "write")

        def _write() -> None:
            try:
                np.save(self._TMP_MATRIX, matrix)
                with open(self._TMP_META, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
                self._client.upload_file(self._TMP_MATRIX, self._bucket, MATRIX_KEY)
                self._client.upload_file(self._TMP_META, self._bucket, META_KEY)
            except ClientError as exc:
                raise S3AccessError(f"failed to write vector cache: {exc}") from exc
            finally:
                for p in (self._TMP_MATRIX, self._TMP_META):
                    if os.path.exists(p):
                        os.remove(p)

        await asyncio.to_thread(_write)
        logger.info("vector cache written to s3", extra={"rows": len(meta)})

    async def patch(
        self,
        upserts: list[tuple[str, str, np.ndarray]],
        deletes: list[str],
    ) -> None:
        """Apply incremental updates to the S3 cache without a full scan.

        ``upserts`` is a list of ``(chunkId, sourceUrl, embedding)`` tuples.
        ``deletes`` is a list of ``chunkId`` strings to remove.

        The method downloads the existing cache, applies the delta, and
        re-uploads.  If no cache exists yet, it starts from an empty state.
        """

        def _patch() -> None:
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=MATRIX_KEY)
                matrix = np.load(io.BytesIO(obj["Body"].read()))
                obj = self._client.get_object(Bucket=self._bucket, Key=META_KEY)
                meta: list[dict[str, Any]] = json.loads(obj["Body"].read())
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    matrix = np.empty((0, 0), dtype=np.float32)
                    meta = []
                else:
                    raise S3AccessError(f"failed to read vector cache for patch: {exc}") from exc

            # Never patch on top of an already-drifted base — it would only
            # propagate the corruption. A full rebuild must heal it instead.
            base_rows = int(matrix.shape[0]) if matrix.ndim == 2 else 0
            if base_rows != len(meta):
                raise CacheConsistencyError(
                    f"base vector cache drifted: matrix={base_rows} meta={len(meta)}"
                )

            delete_set = set(deletes)
            if delete_set:
                keep = [i for i, m in enumerate(meta) if m["chunkId"] not in delete_set]
                if len(keep) < len(meta):
                    meta = [meta[i] for i in keep]
                    matrix = matrix[keep] if matrix.size > 0 else matrix

            # Rebuild the index AFTER deletes so update-vs-append decisions use
            # current positions (the old code kept stale pre-delete indices,
            # which could append duplicates and drift matrix/meta apart).
            id_to_idx: dict[str, int] = {m["chunkId"]: i for i, m in enumerate(meta)}
            new_rows: list[np.ndarray] = []
            for chunk_id, source_url, embedding in upserts:
                idx = id_to_idx.get(chunk_id)
                if idx is not None:
                    meta[idx] = {"chunkId": chunk_id, "sourceUrl": source_url}
                    matrix[idx] = embedding
                else:
                    id_to_idx[chunk_id] = len(meta)
                    meta.append({"chunkId": chunk_id, "sourceUrl": source_url})
                    new_rows.append(np.asarray(embedding, dtype=np.float32))
            if new_rows:
                rows = np.asarray(new_rows, dtype=np.float32)
                matrix = rows if matrix.size == 0 else np.vstack([matrix, rows])

            _assert_consistent(matrix, meta, "patch")

            try:
                np.save(self._TMP_MATRIX, matrix)
                with open(self._TMP_META, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
                self._client.upload_file(self._TMP_MATRIX, self._bucket, MATRIX_KEY)
                self._client.upload_file(self._TMP_META, self._bucket, META_KEY)
            except ClientError as exc:
                raise S3AccessError(f"failed to write vector cache: {exc}") from exc
            finally:
                for p in (self._TMP_MATRIX, self._TMP_META):
                    if os.path.exists(p):
                        os.remove(p)

        await asyncio.to_thread(_patch)
        logger.info(
            "vector cache patched",
            extra={"upserts": len(upserts), "deletes": len(deletes)},
        )

    async def read(self) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Download the matrix and metadata from S3.

        Raises:
            ObjectNotFoundError: If the cache has not been built yet.
            S3AccessError: On any other S3 failure.
        """

        def _read() -> tuple[np.ndarray, list[dict[str, Any]]]:
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=MATRIX_KEY)
                matrix = np.load(io.BytesIO(obj["Body"].read()))
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFoundError("vector cache not found in s3") from exc
                raise S3AccessError(f"failed to read vector cache: {exc}") from exc

            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=META_KEY)
                meta: list[dict[str, Any]] = json.loads(obj["Body"].read())
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFoundError("vector cache meta not found in s3") from exc
                raise S3AccessError(f"failed to read vector cache meta: {exc}") from exc

            return matrix, meta

        return await asyncio.to_thread(_read)

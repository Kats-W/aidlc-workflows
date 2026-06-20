"""Cosine-similarity vector search with a /tmp warm cache.

:class:`CosineSimilaritySearcher` loads the full corpus from the VectorStore
table once per cold start, then caches the embedding matrix + metadata under
``/tmp`` for ``CACHE_TTL_SECONDS``. The cache is persisted as a numpy ``.npy``
file plus JSON metadata — **never pickle** — to avoid arbitrary-code-execution
risk on cache load.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from aws_lambda_powertools import Logger

from src.common.errors import ObjectNotFoundError, S3AccessError, SearchError
from src.vector_store.store import VectorStore
from src.vector_store.vector_cache_store import VectorCacheS3Store, build_matrix_and_meta

logger = Logger()

#: Cache file locations (Lambda's writable /tmp).
CACHE_VECTORS: str = "/tmp/vectors.npy"
CACHE_META: str = "/tmp/vectors_meta.json"
CACHE_TS: str = "/tmp/vectors_ts.txt"


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single search result.

    Attributes:
        chunk_id: Identifier of the matched chunk.
        source_url: Page the chunk came from.
        text: The chunk's text (for RAG context assembly).
        score: Cosine similarity in ``[-1.0, 1.0]``.
    """

    chunk_id: str
    source_url: str
    text: str
    score: float


class CosineSimilaritySearcher:
    """Cosine top-k search over the VectorStore corpus with /tmp caching."""

    CACHE_TTL_SECONDS: int = 900  # 15 minutes

    def __init__(self, store: VectorStore, cache_store: VectorCacheS3Store | None = None) -> None:
        self._store = store
        self._cache_store = cache_store

    async def search(self, query_vec: list[float], top_k: int = 5) -> list[SearchHit]:
        """Return the ``top_k`` most cosine-similar chunks to ``query_vec``.

        Raises:
            SearchError: If the query is malformed or the corpus is empty.
        """
        if not query_vec:
            raise SearchError("query vector is empty")

        matrix, meta = await self._load_vectors()
        if matrix.size == 0:
            return []

        query = np.asarray(query_vec, dtype=np.float64)
        if query.shape[0] != matrix.shape[1]:
            raise SearchError(
                f"query dim {query.shape[0]} != corpus dim {matrix.shape[1]}"
            )

        top_indices = self._cosine_top_k(matrix, query, top_k)
        scores = self._cosine_scores(matrix, query)
        hits = [
            SearchHit(
                chunk_id=meta[i]["chunkId"],
                source_url=meta[i].get("sourceUrl", ""),
                text=meta[i].get("text", ""),
                score=float(scores[i]),
            )
            for i in top_indices
        ]
        logger.info("search complete", extra={"top_k": top_k, "hits": len(hits)})
        return hits

    async def _load_vectors(self) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Load the corpus from /tmp cache when valid, else S3, else DynamoDB."""
        if self._is_cache_valid():
            try:
                matrix = np.load(CACHE_VECTORS)
                with open(CACHE_META, encoding="utf-8") as fh:
                    meta = json.load(fh)
                logger.debug("vector cache hit", extra={"rows": len(meta)})
                return matrix, meta
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("vector cache load failed, refreshing", extra={"error": str(exc)})

        if self._cache_store is not None:
            # The download thread (asyncio.to_thread) survives coroutine
            # cancellation. Pass _write_cache as on_loaded so the thread
            # persists the result to /tmp even when the pipeline timeout
            # cancels this coroutine mid-download — subsequent warm
            # invocations then hit the fast /tmp path above.
            def _persist(m: np.ndarray, meta: list[dict[str, Any]]) -> None:
                if m.shape[0] == len(meta):
                    self._write_cache(m, meta)

            try:
                matrix, meta = await self._cache_store.read(
                    on_loaded=_persist,
                )
            except ObjectNotFoundError:
                logger.info("s3 vector cache not found, returning empty corpus")
                return np.empty((0, 0), dtype=np.float64), []
            except S3AccessError as exc:
                logger.warning(
                    "s3 vector cache read failed, returning empty corpus",
                    extra={"error": str(exc)},
                )
                return np.empty((0, 0), dtype=np.float64), []
            else:
                if matrix.shape[0] == len(meta):
                    logger.info("vector cache loaded from s3", extra={"rows": len(meta)})
                    return matrix, meta
                logger.warning(
                    "vector cache row mismatch, returning empty corpus",
                    extra={"matrix_rows": int(matrix.shape[0]), "meta_rows": len(meta)},
                )
                return np.empty((0, 0), dtype=np.float64), []

        items = await self._store.scan_all()
        matrix, meta = build_matrix_and_meta(items)
        self._write_cache(matrix, meta)
        return matrix, meta

    def _is_cache_valid(self) -> bool:
        """Return True when all cache files exist and are within the TTL."""
        if not (
            os.path.exists(CACHE_TS)
            and os.path.exists(CACHE_VECTORS)
            and os.path.exists(CACHE_META)
        ):
            return False
        try:
            with open(CACHE_TS, encoding="utf-8") as fh:
                ts = float(fh.read().strip())
        except (OSError, ValueError):
            return False
        return (time.time() - ts) < self.CACHE_TTL_SECONDS

    def _write_cache(self, matrix: np.ndarray, meta: list[dict[str, Any]]) -> None:
        """Persist the corpus to /tmp as .npy + JSON (never pickle)."""
        try:
            np.save(CACHE_VECTORS, matrix)
            with open(CACHE_META, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False)
            with open(CACHE_TS, "w", encoding="utf-8") as fh:
                fh.write(str(time.time()))
        except OSError as exc:
            # A cache write failure must not break search; just log it.
            logger.warning("failed to write vector cache", extra={"error": str(exc)})

    @staticmethod
    def _cosine_scores(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
        """Return the cosine similarity of every row in ``matrix`` to ``query``."""
        query_norm = np.linalg.norm(query)
        row_norms = np.linalg.norm(matrix, axis=1)
        denom = row_norms * query_norm
        # Avoid divide-by-zero: zero-norm rows score 0.
        with np.errstate(divide="ignore", invalid="ignore"):
            scores = np.where(denom > 0, (matrix @ query) / denom, 0.0)
        return scores

    def _cosine_top_k(self, matrix: np.ndarray, query: np.ndarray, k: int) -> list[int]:
        """Return the indices of the ``k`` highest-scoring rows (descending)."""
        scores = self._cosine_scores(matrix, query)
        k = min(k, scores.shape[0])
        if k <= 0:
            return []
        # argpartition for top-k, then sort that slice descending.
        partition = np.argpartition(scores, -k)[-k:]
        ordered = partition[np.argsort(scores[partition])[::-1]]
        return [int(i) for i in ordered]

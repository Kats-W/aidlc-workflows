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

from src.common.errors import SearchError
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
    title: str = ""


#: Per-source relevance weights applied on top of cosine similarity when ranking.
#: Canonical rate/fee/product/FAQ pages carry the authoritative, dated figures and
#: should outrank general explanatory column articles and time-bound campaign
#: pages for factual questions (rates, fees, procedures).
_SOURCE_WEIGHT_RULES: tuple[tuple[str, float], ...] = (
    ("/interest_and_commission/", 1.20),
    ("/products/", 1.12),
    ("help.jibunbank.co.jp", 1.10),
    ("/campaign/", 0.70),
    # Time-bound announcements / news (e.g. historical rate-change notices) must
    # not outrank the current rate pages for factual questions.
    ("/announcement/", 0.68),
    ("/corporate/news/", 0.70),
    ("/column/", 0.88),
)


def source_weight(source_url: str) -> float:
    """Return the ranking weight for ``source_url`` (1.0 if no rule matches)."""
    for needle, weight in _SOURCE_WEIGHT_RULES:
        if needle in source_url:
            return weight
    return 1.0


class CosineSimilaritySearcher:
    """Cosine top-k search over the VectorStore corpus with /tmp caching."""

    CACHE_TTL_SECONDS: int = 900  # 15 minutes

    def __init__(self, store: VectorStore, cache_store: VectorCacheS3Store | None = None) -> None:
        self._store = store
        self._cache_store = cache_store
        self._mem_matrix: np.ndarray | None = None
        self._mem_meta: list[dict[str, Any]] | None = None
        self._mem_ts: float = 0.0

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

        query = np.asarray(query_vec, dtype=np.float32)
        if query.shape[0] != matrix.shape[1]:
            raise SearchError(
                f"query dim {query.shape[0]} != corpus dim {matrix.shape[1]}"
            )

        # Rank by cosine score reweighted by source authority so the canonical
        # rate/product/FAQ pages outrank general column articles and campaign
        # pages for factual questions. The reported hit score stays the raw
        # cosine (used by the pipeline's MIN_HIT_SCORE relevance filter).
        scores = self._cosine_scores(matrix, query)
        weights = np.fromiter(
            (source_weight(m.get("sourceUrl", "")) for m in meta),
            dtype=np.float32,
            count=len(meta),
        )
        top_indices = self._top_k_indices(scores * weights, top_k)

        chunk_ids = [meta[i]["chunkId"] for i in top_indices]
        texts = await self._store.batch_get_texts(chunk_ids)

        hits = [
            SearchHit(
                chunk_id=meta[i]["chunkId"],
                source_url=meta[i].get("sourceUrl", ""),
                text=texts.get(meta[i]["chunkId"], {}).get("text", ""),
                score=float(scores[i]),
                title=texts.get(meta[i]["chunkId"], {}).get("title", ""),
            )
            for i in top_indices
        ]
        logger.info("search complete", extra={"top_k": top_k, "hits": len(hits)})
        return hits

    async def _load_vectors(self) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Load the corpus from memory, /tmp, S3, or DynamoDB (in priority order)."""
        if self._mem_matrix is not None and self._mem_meta is not None:
            if (time.time() - self._mem_ts) < self.CACHE_TTL_SECONDS:
                return self._mem_matrix, self._mem_meta
            self._mem_matrix = None
            self._mem_meta = None

        if self._is_cache_valid():
            try:
                matrix = np.load(CACHE_VECTORS)
                with open(CACHE_META, encoding="utf-8") as fh:
                    meta = json.load(fh)
                logger.debug("vector cache hit", extra={"rows": len(meta)})
                self._mem_matrix = matrix
                self._mem_meta = meta
                self._mem_ts = time.time()
                return matrix, meta
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("vector cache load failed, refreshing", extra={"error": str(exc)})

        if self._cache_store is not None:
            logger.info("s3 vector cache not in /tmp, returning empty corpus")
            return np.empty((0, 0), dtype=np.float32), []

        items = await self._store.scan_all()
        matrix, meta = build_matrix_and_meta(items)
        self._write_cache(matrix, meta)
        self._mem_matrix = matrix
        self._mem_meta = meta
        self._mem_ts = time.time()
        return matrix, meta

    async def ensure_cache_loaded(self) -> None:
        """Download the S3 vector cache to ``/tmp`` if not already present.

        Call this *outside* the pipeline timeout so the download runs to
        completion on cold start (~15-20 s). Subsequent warm invocations
        find ``/tmp`` valid and skip this entirely.
        """
        if self._is_cache_valid() or self._cache_store is None:
            return
        try:
            matrix, meta = await self._cache_store.read()
            if matrix.shape[0] == len(meta):
                self._write_cache(matrix, meta)
                self._mem_matrix = matrix
                self._mem_meta = meta
                self._mem_ts = time.time()
                logger.info("cache pre-warmed to /tmp", extra={"rows": len(meta)})
            else:
                logger.warning("cache pre-warm row mismatch")
        except Exception as exc:
            logger.warning("cache pre-warm failed", extra={"error": str(exc)})

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
        """Return the indices of the ``k`` highest cosine-scoring rows (descending)."""
        return self._top_k_indices(self._cosine_scores(matrix, query), k)

    @staticmethod
    def _top_k_indices(scores: np.ndarray, k: int) -> list[int]:
        """Return the indices of the ``k`` highest values in ``scores`` (descending)."""
        k = min(k, scores.shape[0])
        if k <= 0:
            return []
        # argpartition for top-k, then sort that slice descending.
        partition = np.argpartition(scores, -k)[-k:]
        ordered = partition[np.argsort(scores[partition])[::-1]]
        return [int(i) for i in ordered]

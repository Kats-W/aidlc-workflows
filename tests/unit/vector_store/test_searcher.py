"""Unit tests for :mod:`src.vector_store.searcher` (cosine search + /tmp cache)."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.common.errors import ObjectNotFoundError, S3AccessError, SearchError
from src.vector_store.searcher import CACHE_META, CACHE_TS, CACHE_VECTORS, CosineSimilaritySearcher
from src.vector_store.vector_cache_store import build_matrix_and_meta


@pytest.fixture(autouse=True)
def _clean_cache():  # type: ignore[no-untyped-def]
    for path in (CACHE_VECTORS, CACHE_META, CACHE_TS):
        if os.path.exists(path):
            os.remove(path)
    yield
    for path in (CACHE_VECTORS, CACHE_META, CACHE_TS):
        if os.path.exists(path):
            os.remove(path)


def _store_with(items: list[dict]) -> object:
    store = type("S", (), {})()
    store.scan_all = AsyncMock(return_value=items)  # type: ignore[attr-defined]
    texts = {
        it["chunkId"]: {"text": it.get("text", ""), "title": it.get("title", "")}
        for it in items
    }
    store.batch_get_texts = AsyncMock(  # type: ignore[attr-defined]
        side_effect=lambda ids: {cid: texts[cid] for cid in ids if cid in texts}
    )
    return store


def _cache_store_with(read_result: object) -> object:
    """Build a fake :class:`VectorCacheS3Store` whose ``read`` returns or raises."""
    cache_store = type("CS", (), {})()
    if isinstance(read_result, BaseException):
        cache_store.read = AsyncMock(side_effect=read_result)  # type: ignore[attr-defined]
    else:
        cache_store.read = AsyncMock(return_value=read_result)  # type: ignore[attr-defined]
    return cache_store


CORPUS = [
    {"chunkId": "a", "sourceUrl": "u-a", "text": "alpha", "embedding": [1.0, 0.0, 0.0]},
    {"chunkId": "b", "sourceUrl": "u-b", "text": "beta", "embedding": [0.0, 1.0, 0.0]},
    {"chunkId": "c", "sourceUrl": "u-c", "text": "gamma", "embedding": [0.9, 0.1, 0.0]},
]


async def test_search_top_k_order() -> None:
    s = CosineSimilaritySearcher(_store_with(CORPUS))  # type: ignore[arg-type]
    hits = await s.search([1.0, 0.0, 0.0], top_k=2)
    assert [h.chunk_id for h in hits] == ["a", "c"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].source_url == "u-a"
    assert hits[0].text == "alpha"


async def test_empty_corpus_returns_empty() -> None:
    s = CosineSimilaritySearcher(_store_with([]))  # type: ignore[arg-type]
    assert await s.search([1.0, 0.0, 0.0], top_k=5) == []


async def test_empty_query_raises() -> None:
    s = CosineSimilaritySearcher(_store_with(CORPUS))  # type: ignore[arg-type]
    with pytest.raises(SearchError):
        await s.search([], top_k=5)


async def test_dim_mismatch_raises() -> None:
    s = CosineSimilaritySearcher(_store_with(CORPUS))  # type: ignore[arg-type]
    with pytest.raises(SearchError):
        await s.search([1.0, 0.0], top_k=5)


async def test_cache_is_written_and_reused() -> None:
    store = _store_with(CORPUS)
    s = CosineSimilaritySearcher(store)  # type: ignore[arg-type]
    await s.search([1.0, 0.0, 0.0], top_k=1)
    # Cache files exist.
    assert os.path.exists(CACHE_VECTORS)
    assert os.path.exists(CACHE_META)
    assert os.path.exists(CACHE_TS)
    # Verify cache is JSON (never pickle).
    with open(CACHE_META, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta[0]["chunkId"] == "a"
    # Second search must not re-scan the store (served from cache).
    store.scan_all.reset_mock()  # type: ignore[attr-defined]
    await s.search([0.0, 1.0, 0.0], top_k=1)
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


async def test_cache_ttl_expiry_refreshes() -> None:
    store = _store_with(CORPUS)
    s = CosineSimilaritySearcher(store)  # type: ignore[arg-type]
    await s.search([1.0, 0.0, 0.0], top_k=1)
    # Backdate both /tmp and in-memory timestamps beyond the TTL.
    expired = time.time() - CosineSimilaritySearcher.CACHE_TTL_SECONDS - 1
    with open(CACHE_TS, "w", encoding="utf-8") as fh:
        fh.write(str(expired))
    s._mem_ts = expired
    store.scan_all.reset_mock()  # type: ignore[attr-defined]
    await s.search([1.0, 0.0, 0.0], top_k=1)
    store.scan_all.assert_called_once()  # type: ignore[attr-defined]


async def test_ensure_cache_loaded_warms_tmp_for_search() -> None:
    """ensure_cache_loaded() downloads from S3 → /tmp; search uses /tmp."""
    matrix, meta = build_matrix_and_meta(CORPUS)
    store = _store_with(CORPUS)
    cache_store = _cache_store_with((matrix, meta))
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    await s.ensure_cache_loaded()
    assert os.path.exists(CACHE_VECTORS)
    assert os.path.exists(CACHE_META)
    # Search must use /tmp cache, not call S3 read again or DynamoDB scan.
    cache_store.read.reset_mock()  # type: ignore[attr-defined]
    hits = await s.search([1.0, 0.0, 0.0], top_k=1)
    assert hits[0].chunk_id == "a"
    cache_store.read.assert_not_called()  # type: ignore[attr-defined]
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


async def test_ensure_cache_loaded_skips_when_tmp_valid() -> None:
    """If /tmp cache is already valid, ensure_cache_loaded() is a no-op."""
    matrix, meta = build_matrix_and_meta(CORPUS)
    store = _store_with(CORPUS)
    cache_store = _cache_store_with((matrix, meta))
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    await s.ensure_cache_loaded()
    cache_store.read.reset_mock()  # type: ignore[attr-defined]
    await s.ensure_cache_loaded()
    cache_store.read.assert_not_called()  # type: ignore[attr-defined]


async def test_ensure_cache_loaded_handles_s3_missing() -> None:
    """S3 cache not found → ensure_cache_loaded logs warning, search returns empty."""
    store = _store_with(CORPUS)
    cache_store = _cache_store_with(ObjectNotFoundError("not built yet"))
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    await s.ensure_cache_loaded()
    assert not os.path.exists(CACHE_VECTORS)
    hits = await s.search([1.0, 0.0, 0.0], top_k=1)
    assert hits == []
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


async def test_ensure_cache_loaded_handles_row_mismatch() -> None:
    """Row mismatch in S3 cache → not written to /tmp, search returns empty."""
    matrix, meta = build_matrix_and_meta(CORPUS)
    mismatched = (matrix, meta[:-1])
    store = _store_with(CORPUS)
    cache_store = _cache_store_with(mismatched)
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    await s.ensure_cache_loaded()
    assert not os.path.exists(CACHE_VECTORS)
    hits = await s.search([1.0, 0.0, 0.0], top_k=1)
    assert hits == []
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


async def test_ensure_cache_loaded_handles_s3_error() -> None:
    """S3 access error → ensure_cache_loaded logs warning, search returns empty."""
    store = _store_with(CORPUS)
    cache_store = _cache_store_with(S3AccessError("boom"))
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    await s.ensure_cache_loaded()
    assert not os.path.exists(CACHE_VECTORS)
    hits = await s.search([1.0, 0.0, 0.0], top_k=1)
    assert hits == []
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


async def test_search_without_cache_warm_returns_empty_when_s3_configured() -> None:
    """If ensure_cache_loaded wasn't called, search returns empty (not slow scan)."""
    store = _store_with(CORPUS)
    cache_store = _cache_store_with((np.empty((0, 0)), []))
    s = CosineSimilaritySearcher(store, cache_store)  # type: ignore[arg-type]
    hits = await s.search([1.0, 0.0, 0.0], top_k=1)
    assert hits == []
    store.scan_all.assert_not_called()  # type: ignore[attr-defined]


def test_cosine_self_similarity_is_one() -> None:
    @settings(max_examples=30, deadline=None)
    @given(
        vec=st.lists(
            st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
            min_size=2,
            max_size=8,
        )
    )
    def _prop(vec: list[float]) -> None:
        v = np.asarray(vec, dtype=np.float64)
        if np.linalg.norm(v) == 0:
            return  # zero vector has no defined cosine
        s = CosineSimilaritySearcher(_store_with([]))  # type: ignore[arg-type]
        scores = s._cosine_scores(v.reshape(1, -1), v)
        assert scores[0] == pytest.approx(1.0, abs=1e-6)

    _prop()

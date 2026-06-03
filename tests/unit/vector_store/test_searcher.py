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

from src.common.errors import SearchError
from src.vector_store.searcher import CACHE_META, CACHE_TS, CACHE_VECTORS, CosineSimilaritySearcher


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
    return store


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
    # Backdate the timestamp beyond the TTL.
    with open(CACHE_TS, "w", encoding="utf-8") as fh:
        fh.write(str(time.time() - CosineSimilaritySearcher.CACHE_TTL_SECONDS - 1))
    store.scan_all.reset_mock()  # type: ignore[attr-defined]
    await s.search([1.0, 0.0, 0.0], top_k=1)
    store.scan_all.assert_called_once()  # type: ignore[attr-defined]


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

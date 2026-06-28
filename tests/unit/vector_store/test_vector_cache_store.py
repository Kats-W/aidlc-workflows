"""Unit tests for :mod:`src.vector_store.vector_cache_store` (moto S3)."""

from __future__ import annotations

import boto3
import numpy as np
import pytest
from moto import mock_aws

from src.common.errors import CacheConsistencyError, ObjectNotFoundError
from src.vector_store.vector_cache_store import VectorCacheS3Store, build_matrix_and_meta

BUCKET = "crawl-content-test"


@pytest.fixture()
def cache_store():  # type: ignore[no-untyped-def]
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )
        yield VectorCacheS3Store(bucket=BUCKET, client=client)


def test_build_matrix_and_meta() -> None:
    items = [
        {"chunkId": "a", "sourceUrl": "u-a", "text": "alpha", "embedding": [1.0, 0.0]},
        {"chunkId": "b", "sourceUrl": "u-b", "text": "beta", "embedding": [0.0, 1.0]},
    ]
    matrix, meta = build_matrix_and_meta(items)
    assert matrix.shape == (2, 2)
    assert meta == [
        {"chunkId": "a", "sourceUrl": "u-a"},
        {"chunkId": "b", "sourceUrl": "u-b"},
    ]


def test_build_matrix_and_meta_empty() -> None:
    matrix, meta = build_matrix_and_meta([])
    assert matrix.shape == (0, 0)
    assert meta == []


async def test_write_and_read_roundtrip(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    meta = [
        {"chunkId": "a", "sourceUrl": "u-a"},
        {"chunkId": "b", "sourceUrl": "u-b"},
    ]
    await cache_store.write(matrix, meta)
    loaded_matrix, loaded_meta = await cache_store.read()
    assert np.array_equal(loaded_matrix, matrix)
    assert loaded_meta == meta


async def test_read_missing_raises(cache_store: VectorCacheS3Store) -> None:
    with pytest.raises(ObjectNotFoundError):
        await cache_store.read()


async def test_patch_creates_cache_from_empty(cache_store: VectorCacheS3Store) -> None:
    upserts = [
        ("a", "u-a", np.array([1.0, 0.0], dtype=np.float32)),
        ("b", "u-b", np.array([0.0, 1.0], dtype=np.float32)),
    ]
    await cache_store.patch(upserts, [])
    matrix, meta = await cache_store.read()
    assert matrix.shape == (2, 2)
    assert meta == [{"chunkId": "a", "sourceUrl": "u-a"}, {"chunkId": "b", "sourceUrl": "u-b"}]


async def test_patch_appends_to_existing(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    meta = [{"chunkId": "a", "sourceUrl": "u-a"}]
    await cache_store.write(matrix, meta)

    upserts = [("b", "u-b", np.array([0.0, 1.0], dtype=np.float32))]
    await cache_store.patch(upserts, [])

    loaded_matrix, loaded_meta = await cache_store.read()
    assert loaded_matrix.shape == (2, 2)
    assert loaded_meta == [
        {"chunkId": "a", "sourceUrl": "u-a"},
        {"chunkId": "b", "sourceUrl": "u-b"},
    ]


async def test_patch_replaces_existing_chunk(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    meta = [{"chunkId": "a", "sourceUrl": "u-a"}]
    await cache_store.write(matrix, meta)

    upserts = [("a", "u-a-v2", np.array([0.5, 0.5], dtype=np.float32))]
    await cache_store.patch(upserts, [])

    loaded_matrix, loaded_meta = await cache_store.read()
    assert loaded_matrix.shape == (1, 2)
    assert loaded_meta == [{"chunkId": "a", "sourceUrl": "u-a-v2"}]
    assert np.allclose(loaded_matrix[0], [0.5, 0.5])


async def test_patch_deletes_chunks(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    meta = [{"chunkId": "a", "sourceUrl": "u-a"}, {"chunkId": "b", "sourceUrl": "u-b"}]
    await cache_store.write(matrix, meta)

    await cache_store.patch([], ["a"])

    loaded_matrix, loaded_meta = await cache_store.read()
    assert loaded_matrix.shape == (1, 2)
    assert loaded_meta == [{"chunkId": "b", "sourceUrl": "u-b"}]


async def test_patch_upsert_and_delete_combined(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    meta = [{"chunkId": "a", "sourceUrl": "u-a"}, {"chunkId": "b", "sourceUrl": "u-b"}]
    await cache_store.write(matrix, meta)

    upserts = [("c", "u-c", np.array([0.5, 0.5], dtype=np.float32))]
    await cache_store.patch(upserts, ["a"])

    loaded_matrix, loaded_meta = await cache_store.read()
    assert loaded_matrix.shape == (2, 2)
    assert loaded_meta == [
        {"chunkId": "b", "sourceUrl": "u-b"},
        {"chunkId": "c", "sourceUrl": "u-c"},
    ]


async def test_patch_update_after_delete_shifts_index_no_duplicate(
    cache_store: VectorCacheS3Store,
) -> None:
    """Regression: deleting an earlier chunk shifts indices; a later upsert of a
    surviving chunk must update in place, not append a duplicate (the stale
    pre-delete index bug that drifted matrix/meta apart)."""
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32)
    meta = [
        {"chunkId": "a", "sourceUrl": "u-a"},
        {"chunkId": "b", "sourceUrl": "u-b"},
        {"chunkId": "c", "sourceUrl": "u-c"},  # index 2 before delete
    ]
    await cache_store.write(matrix, meta)

    # Delete "a" (c shifts to index 1), then upsert "c" — must update, not append.
    await cache_store.patch([("c", "u-c-v2", np.array([0.2, 0.2], dtype=np.float32))], ["a"])

    loaded_matrix, loaded_meta = await cache_store.read()
    assert loaded_matrix.shape == (2, 2)  # b, c — no phantom 3rd row
    assert loaded_meta == [
        {"chunkId": "b", "sourceUrl": "u-b"},
        {"chunkId": "c", "sourceUrl": "u-c-v2"},
    ]
    assert np.allclose(loaded_matrix[1], [0.2, 0.2])


async def test_write_rejects_row_mismatch(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)  # 2 rows
    meta = [{"chunkId": "a", "sourceUrl": "u-a"}]  # 1 entry
    with pytest.raises(CacheConsistencyError):
        await cache_store.write(matrix, meta)


async def test_patch_rejects_drifted_base(cache_store: VectorCacheS3Store) -> None:
    # Seed a deliberately drifted cache by writing the two objects directly.
    import io
    import json

    buf = io.BytesIO()
    np.save(buf, np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))  # 2 rows
    cache_store._client.put_object(
        Bucket=BUCKET, Key="vector-cache/matrix.npy", Body=buf.getvalue()
    )
    cache_store._client.put_object(
        Bucket=BUCKET,
        Key="vector-cache/meta.json",
        Body=json.dumps([{"chunkId": "a", "sourceUrl": "u-a"}]).encode("utf-8"),  # 1 entry
    )
    with pytest.raises(CacheConsistencyError):
        await cache_store.patch([("b", "u-b", np.array([1.0, 1.0], dtype=np.float32))], [])

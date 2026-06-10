"""Unit tests for :mod:`src.vector_store.vector_cache_store` (moto S3)."""

from __future__ import annotations

import boto3
import numpy as np
import pytest
from moto import mock_aws

from src.common.errors import ObjectNotFoundError
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
        {"chunkId": "a", "sourceUrl": "u-a", "text": "alpha"},
        {"chunkId": "b", "sourceUrl": "u-b", "text": "beta"},
    ]


def test_build_matrix_and_meta_empty() -> None:
    matrix, meta = build_matrix_and_meta([])
    assert matrix.shape == (0, 0)
    assert meta == []


async def test_write_and_read_roundtrip(cache_store: VectorCacheS3Store) -> None:
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    meta = [
        {"chunkId": "a", "sourceUrl": "u-a", "text": "alpha"},
        {"chunkId": "b", "sourceUrl": "u-b", "text": "beta"},
    ]
    await cache_store.write(matrix, meta)
    loaded_matrix, loaded_meta = await cache_store.read()
    assert np.array_equal(loaded_matrix, matrix)
    assert loaded_meta == meta


async def test_read_missing_raises(cache_store: VectorCacheS3Store) -> None:
    with pytest.raises(ObjectNotFoundError):
        await cache_store.read()

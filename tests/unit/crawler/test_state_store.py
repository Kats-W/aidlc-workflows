"""Unit tests for :mod:`src.crawler.state_store` (moto S3)."""

from __future__ import annotations

from collections import deque

import boto3
import pytest
from moto import mock_aws

from src.crawler.state_store import _STATE_KEY, CrawlStateStore

BUCKET = "crawl-content-test"


@pytest.fixture()
def state_store():  # type: ignore[no-untyped-def]
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )
        yield CrawlStateStore(bucket=BUCKET, client=client)


async def test_load_returns_none_when_absent(state_store: CrawlStateStore) -> None:
    assert await state_store.load() is None


async def test_save_and_load_roundtrip(state_store: CrawlStateStore) -> None:
    queue = deque(["https://a/1", "https://a/2"])
    visited = {"https://a/0"}

    await state_store.save(queue, visited)
    loaded = await state_store.load()

    assert loaded is not None
    loaded_queue, loaded_visited = loaded
    assert list(loaded_queue) == ["https://a/1", "https://a/2"]
    assert loaded_visited == visited


async def test_load_returns_none_on_corrupt_json(state_store: CrawlStateStore) -> None:
    state_store._client.put_object(Bucket=BUCKET, Key=_STATE_KEY, Body=b"not json")
    assert await state_store.load() is None

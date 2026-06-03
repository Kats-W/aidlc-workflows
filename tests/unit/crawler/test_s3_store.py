"""Unit tests for :mod:`src.crawler.s3_store` (moto S3)."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.common.errors import ObjectNotFoundError
from src.crawler.s3_store import S3ContentStore

BUCKET = "crawl-content-test"


@pytest.fixture()
def s3_store():  # type: ignore[no-untyped-def]
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )
        yield S3ContentStore(bucket=BUCKET, client=client)


def test_build_key() -> None:
    key = S3ContentStore.build_key("abc123", "abc123#0")
    assert key == "content/abc123/abc123_0.txt"


async def test_put_and_get_roundtrip(s3_store: S3ContentStore) -> None:
    key = "content/h/c_0.txt"
    returned = await s3_store.put(key, "口座開設の本文")
    assert returned == key
    body = await s3_store.get(key)
    assert body == "口座開設の本文"


async def test_get_missing_raises(s3_store: S3ContentStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        await s3_store.get("content/none/missing.txt")


async def test_delete(s3_store: S3ContentStore) -> None:
    key = "content/h/c_1.txt"
    await s3_store.put(key, "to-delete")
    await s3_store.delete(key)
    with pytest.raises(ObjectNotFoundError):
        await s3_store.get(key)

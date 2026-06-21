"""Unit tests for :mod:`src.vector_store.handler` (Embedder Lambda, moto)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import boto3
import numpy as np
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.common.errors import S3AccessError
from src.vector_store import handler as h
from src.vector_store.vector_cache_store import MATRIX_KEY, META_KEY

TABLE_NAME = "vector-store-test"
BUCKET = "crawl-content-test"


def _read_cache_meta(s3: object, bucket: str) -> list[dict]:
    body = s3.get_object(Bucket=bucket, Key=META_KEY)["Body"].read()  # type: ignore[attr-defined]
    return json.loads(body)


def _read_cache_matrix(s3: object, bucket: str) -> np.ndarray:
    import io

    body = s3.get_object(Bucket=bucket, Key=MATRIX_KEY)["Body"].read()  # type: ignore[attr-defined]
    return np.load(io.BytesIO(body))


@pytest.fixture()
def aws_env(monkeypatch):  # type: ignore[no-untyped-def]
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "chunkId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "chunkId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()

        s3 = boto3.client("s3", region_name="ap-northeast-1")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )

        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("VECTOR_STORE_TABLE_NAME", TABLE_NAME)
        monkeypatch.setenv("CRAWL_CONTENT_BUCKET", BUCKET)
        yield s3


def _fake_bedrock() -> AsyncMock:
    bedrock = AsyncMock()
    bedrock.embed = AsyncMock(return_value=[1.0, 0.0])
    return bedrock


async def test_upsert_patches_vector_cache(aws_env) -> None:  # type: ignore[no-untyped-def]
    s3 = aws_env
    bedrock = _fake_bedrock()
    with patch.object(h, "BedrockClient", return_value=bedrock):
        result = await h.handler(
            {
                "upsert": [
                    {
                        "chunkId": "a#0",
                        "sourceUrl": "https://x/faq",
                        "text": "alpha",
                        "contentHash": "h1",
                    },
                ],
                "delete": [],
            },
            None,
        )
    assert result == {"upserted": 1, "deleted": 0}
    meta = _read_cache_meta(s3, BUCKET)
    assert meta == [{"chunkId": "a#0", "sourceUrl": "https://x/faq"}]
    matrix = _read_cache_matrix(s3, BUCKET)
    assert matrix.shape == (1, 2)


async def test_delete_patches_vector_cache(aws_env) -> None:  # type: ignore[no-untyped-def]
    s3 = aws_env
    bedrock = _fake_bedrock()
    with patch.object(h, "BedrockClient", return_value=bedrock):
        await h.handler(
            {
                "upsert": [
                    {
                        "chunkId": "a#0",
                        "sourceUrl": "https://x/faq",
                        "text": "alpha",
                        "contentHash": "h1",
                    },
                ],
                "delete": [],
            },
            None,
        )
        result = await h.handler(
            {"upsert": [], "delete": ["a#0"]}, None
        )
    assert result == {"upserted": 0, "deleted": 1}
    meta = _read_cache_meta(s3, BUCKET)
    assert meta == []


async def test_no_changes_skips_cache_patch(aws_env) -> None:  # type: ignore[no-untyped-def]
    s3 = aws_env
    bedrock = _fake_bedrock()
    with patch.object(h, "BedrockClient", return_value=bedrock):
        result = await h.handler({"upsert": [], "delete": []}, None)
    assert result == {"upserted": 0, "deleted": 0}
    with pytest.raises(ClientError):
        s3.get_object(Bucket=BUCKET, Key=META_KEY)


async def test_cache_patch_failure_is_logged_not_raised(aws_env) -> None:  # type: ignore[no-untyped-def]
    bedrock = _fake_bedrock()
    with (
        patch.object(h, "BedrockClient", return_value=bedrock),
        patch.object(h.VectorCacheS3Store, "patch", AsyncMock(side_effect=S3AccessError("boom"))),
    ):
        result = await h.handler(
            {
                "upsert": [
                    {
                        "chunkId": "a#0",
                        "sourceUrl": "https://x/faq",
                        "text": "alpha",
                        "contentHash": "h1",
                    },
                ],
                "delete": [],
            },
            None,
        )
    assert result == {"upserted": 1, "deleted": 0}


async def test_upsert_replaces_existing_in_cache(aws_env) -> None:  # type: ignore[no-untyped-def]
    """Upserting a chunk that already exists in the cache replaces it."""
    s3 = aws_env
    bedrock = _fake_bedrock()
    with patch.object(h, "BedrockClient", return_value=bedrock):
        await h.handler(
            {
                "upsert": [
                    {
                        "chunkId": "a#0",
                        "sourceUrl": "https://x/faq",
                        "text": "alpha",
                        "contentHash": "h1",
                    },
                ],
                "delete": [],
            },
            None,
        )
        await h.handler(
            {
                "upsert": [
                    {
                        "chunkId": "a#0",
                        "sourceUrl": "https://x/faq-v2",
                        "text": "alpha updated",
                        "contentHash": "h2",
                    },
                ],
                "delete": [],
            },
            None,
        )
    meta = _read_cache_meta(s3, BUCKET)
    assert len(meta) == 1
    assert meta[0] == {"chunkId": "a#0", "sourceUrl": "https://x/faq-v2"}

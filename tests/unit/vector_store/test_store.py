"""Unit tests for :mod:`src.vector_store.store` (moto DynamoDB)."""

from __future__ import annotations

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from src.crawler.parser import ContentChunk
from src.vector_store.store import VectorStore

TABLE_NAME = "vector-store-test"


@pytest.fixture()
def vector_table():  # type: ignore[no-untyped-def]
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "chunkId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "chunkId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield table


def _chunk(cid: str) -> ContentChunk:
    return ContentChunk(
        chunk_id=cid,
        source_url="https://x/faq",
        index=0,
        text=f"text-{cid}",
        content_hash="h",
    )


async def test_upsert_stores_decimal_embedding(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.upsert(_chunk("a#0"), [0.1, 0.2, 0.3])
    item = vector_table.get_item(Key={"chunkId": "a#0"})["Item"]
    assert all(isinstance(v, Decimal) for v in item["embedding"])
    assert item["embedding"] == [Decimal("0.1"), Decimal("0.2"), Decimal("0.3")]
    assert item["text"] == "text-a#0"


async def test_scan_all_returns_floats(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.upsert(_chunk("a#0"), [0.5, 0.5])
    await store.upsert(_chunk("a#1"), [0.1, 0.9])
    items = await store.scan_all()
    assert len(items) == 2
    by_id = {it["chunkId"]: it for it in items}
    assert by_id["a#0"]["embedding"] == [0.5, 0.5]
    assert all(isinstance(v, float) for v in by_id["a#1"]["embedding"])
    assert by_id["a#0"]["sourceUrl"] == "https://x/faq"


async def test_delete(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.upsert(_chunk("a#0"), [1.0])
    await store.delete("a#0")
    assert "Item" not in vector_table.get_item(Key={"chunkId": "a#0"})


async def test_delete_idempotent(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.delete("missing")  # no raise

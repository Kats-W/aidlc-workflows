"""Unit tests for :mod:`src.vector_store.store` (moto DynamoDB)."""

from __future__ import annotations

from decimal import Decimal

import boto3
import numpy as np
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
    assert np.array_equal(by_id["a#0"]["embedding"], [0.5, 0.5])
    assert by_id["a#1"]["embedding"].dtype == np.float32
    assert by_id["a#0"]["sourceUrl"] == "https://x/faq"


async def test_scan_all_embeddings_are_native_floats_not_decimal(  # type: ignore[no-untyped-def]
    vector_table,
) -> None:
    """The read path must avoid ``Decimal`` entirely (memory-pressure fix)."""
    store = VectorStore(table=vector_table)
    await store.upsert(_chunk("a#0"), [0.123, 0.456, 0.789])
    items = await store.scan_all()
    embedding = items[0]["embedding"]
    # A float32 numpy array, not Decimal (and not a Python list of Decimal).
    assert embedding.dtype == np.float32
    assert embedding == pytest.approx([0.123, 0.456, 0.789], abs=1e-6)
    assert not any(isinstance(v, Decimal) for v in embedding.tolist())
    assert type(items[0]["chunkId"]) is str
    assert type(items[0]["text"]) is str


async def test_scan_all_paginates_across_pages(vector_table) -> None:  # type: ignore[no-untyped-def]
    """Verify the low-level paginated scan returns every item."""
    store = VectorStore(table=vector_table)
    for i in range(25):
        await store.upsert(_chunk(f"a#{i}"), [float(i), float(i) + 0.5])
    items = await store.scan_all()
    assert len(items) == 25
    assert {it["chunkId"] for it in items} == {f"a#{i}" for i in range(25)}


async def test_scan_all_empty_table_returns_empty(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    assert await store.scan_all() == []


async def test_delete(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.upsert(_chunk("a#0"), [1.0])
    await store.delete("a#0")
    assert "Item" not in vector_table.get_item(Key={"chunkId": "a#0"})


async def test_delete_idempotent(vector_table) -> None:  # type: ignore[no-untyped-def]
    store = VectorStore(table=vector_table)
    await store.delete("missing")  # no raise

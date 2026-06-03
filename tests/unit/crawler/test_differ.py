"""Unit tests for :mod:`src.crawler.differ` (moto DynamoDB)."""

from __future__ import annotations

import asyncio

import boto3
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

from src.crawler.differ import DifferEngine
from src.crawler.parser import ContentChunk

TABLE_NAME = "content-diff-test"


@pytest.fixture()
def diff_table():  # type: ignore[no-untyped-def]
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


def _chunk(cid: str, text: str) -> ContentChunk:
    return ContentChunk(
        chunk_id=cid,
        source_url="https://x/faq",
        index=0,
        text=text,
        content_hash=DifferEngine.compute_hash(text),
    )


async def test_diff_all_added_on_empty_table(diff_table) -> None:  # type: ignore[no-untyped-def]
    differ = DifferEngine(diff_table)
    chunks = [_chunk("a#0", "alpha"), _chunk("a#1", "beta")]
    result = await differ.diff(chunks)
    assert len(result.added) == 2
    assert not result.changed
    assert not result.deleted


async def test_diff_detects_change_and_delete(diff_table) -> None:  # type: ignore[no-untyped-def]
    differ = DifferEngine(diff_table)
    # Seed prior state via commit.
    await differ.commit(
        await differ.diff([_chunk("a#0", "alpha"), _chunk("a#1", "beta")])
    )
    # New crawl: a#0 unchanged, a#1 changed, a#2 new, a#1 deletion via absence? no.
    new = [_chunk("a#0", "alpha"), _chunk("a#1", "beta-CHANGED"), _chunk("a#2", "gamma")]
    result = await differ.diff(new)
    assert {c.chunk_id for c in result.added} == {"a#2"}
    assert {c.chunk_id for c in result.changed} == {"a#1"}
    assert result.deleted == []


async def test_diff_detects_deletion(diff_table) -> None:  # type: ignore[no-untyped-def]
    differ = DifferEngine(diff_table)
    await differ.commit(await differ.diff([_chunk("a#0", "alpha"), _chunk("a#1", "beta")]))
    result = await differ.diff([_chunk("a#0", "alpha")])
    assert result.deleted == ["a#1"]


async def test_commit_writes_to_table(diff_table) -> None:  # type: ignore[no-untyped-def]
    differ = DifferEngine(diff_table)
    result = await differ.diff([_chunk("a#0", "alpha")])
    await differ.commit(result)
    item = diff_table.get_item(Key={"chunkId": "a#0"})["Item"]
    assert item["contentHash"] == DifferEngine.compute_hash("alpha")
    assert item["sourceUrl"] == "https://x/faq"


@settings(max_examples=20, deadline=None)
@given(texts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=6, unique=True))
def test_diff_idempotent(texts: list[str]) -> None:
    # Sync hypothesis test driving the async API via asyncio.run, with a fresh
    # moto table per example to keep examples independent.
    async def _run() -> None:
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
            table = ddb.create_table(
                TableName="content-diff-pbt",
                KeySchema=[{"AttributeName": "chunkId", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "chunkId", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            differ = DifferEngine(table)
            chunks = [_chunk(f"c#{i}", t) for i, t in enumerate(texts)]
            first = await differ.diff(chunks)
            await differ.commit(first)
            second = await differ.diff(chunks)
            assert second.is_empty

    asyncio.run(_run())

"""VectorStore DynamoDB persistence for embedded content chunks.

:class:`VectorStore` upserts/deletes embedded chunks and scans the full corpus
for the cosine-similarity searcher. Embeddings are stored as lists of
``Decimal`` because DynamoDB's number type does not accept native floats.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import boto3
import numpy as np
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError
from src.crawler.parser import ContentChunk

logger = Logger()


class _FloatDeserializer(TypeDeserializer):
    """``TypeDeserializer`` that maps DynamoDB Numbers straight to ``float``.

    The high-level DynamoDB *resource* API deserializes every Number ("N")
    attribute to a Python ``Decimal``. For a full ``scan_all`` of the corpus
    (~5,700 items x 1024-dim embeddings ~= 5.8M numbers) that materializes
    millions of short-lived ``Decimal`` objects simultaneously — each with
    ~100+ bytes of per-object overhead — easily approaching/exceeding the
    512MB Lambda limit and triggering severe GC thrashing. Returning ``float``
    directly avoids the ``Decimal`` intermediary entirely on the read path.
    """

    def _deserialize_n(self, value: str) -> float:
        return float(value)


def _to_decimal_list(vector: list[float]) -> list[Decimal]:
    """Convert a float vector to DynamoDB-safe ``Decimal`` values."""
    # Round-trip through str to avoid binary float artefacts in Decimal.
    return [Decimal(str(v)) for v in vector]


class VectorStore:
    """CRUD + scan over the VectorStore DynamoDB table."""

    def __init__(self, table: Any | None = None, table_name: str | None = None) -> None:
        """Args:
        table: Optional pre-built boto3 DynamoDB ``Table`` (tests).
        table_name: Table name; falls back to ``VECTOR_STORE_TABLE_NAME``.
        """
        if table is not None:
            self._table = table
            self._table_name = table.name
            # ``table.meta.client`` is the *resource-level* client: its ``scan``
            # auto-deserializes Numbers to ``Decimal`` and never exposes the raw
            # ``{"N": ...}`` wire format our ``_FloatDeserializer`` needs. Build a
            # genuine low-level client from the same session/region/endpoint so
            # the float read path works against the (moto) table in tests too.
            meta = table.meta.client.meta
            self._client = boto3.client(
                "dynamodb",
                region_name=meta.region_name,
                endpoint_url=meta.endpoint_url,
            )
        else:
            import os

            name = table_name or os.environ["VECTOR_STORE_TABLE_NAME"]
            resource = boto3.resource("dynamodb")
            self._table = resource.Table(name)
            self._table_name = name
            self._client = boto3.client("dynamodb")

    async def upsert(self, chunk: ContentChunk, vector: list[float]) -> None:
        """Insert or replace ``chunk`` with its ``vector`` embedding."""

        def _put() -> None:
            try:
                self._table.put_item(
                    Item={
                        "chunkId": chunk.chunk_id,
                        "sourceUrl": chunk.source_url,
                        "text": chunk.text,
                        "title": chunk.title,
                        "contentHash": chunk.content_hash,
                        "embedding": _to_decimal_list(vector),
                    }
                )
            except ClientError as exc:
                raise DynamoAccessError(f"failed to upsert {chunk.chunk_id}") from exc

        await asyncio.to_thread(_put)
        logger.debug("upserted vector", extra={"chunk_id": chunk.chunk_id})

    async def delete(self, chunk_id: str) -> None:
        """Delete the chunk identified by ``chunk_id`` (idempotent)."""

        def _delete() -> None:
            try:
                self._table.delete_item(Key={"chunkId": chunk_id})
            except ClientError as exc:
                raise DynamoAccessError(f"failed to delete {chunk_id}") from exc

        await asyncio.to_thread(_delete)
        logger.debug("deleted vector", extra={"chunk_id": chunk_id})

    async def warm_connection(self) -> None:
        """Establish the DynamoDB connection so subsequent calls skip cold-connect latency."""

        def _warm() -> None:
            self._client.get_item(
                TableName=self._table_name,
                Key={"chunkId": {"S": "__warmup__"}},
                ProjectionExpression="chunkId",
            )

        await asyncio.to_thread(_warm)

    async def batch_get_texts(self, chunk_ids: list[str]) -> dict[str, dict[str, str]]:
        """Fetch ``text`` and ``title`` for a small set of chunk IDs.

        Returns ``{chunkId: {"text": ..., "title": ...}}`` (title may be "").
        """

        if not chunk_ids:
            return {}

        def _get() -> dict[str, dict[str, str]]:
            try:
                response = self._client.batch_get_item(
                    RequestItems={
                        self._table_name: {
                            "Keys": [{"chunkId": {"S": cid}} for cid in chunk_ids],
                            "ProjectionExpression": "chunkId, #t, title",
                            "ExpressionAttributeNames": {"#t": "text"},
                        }
                    }
                )
            except ClientError as exc:
                raise DynamoAccessError("failed to batch_get_texts") from exc
            result: dict[str, dict[str, str]] = {}
            for item in response.get("Responses", {}).get(self._table_name, []):
                cid = item["chunkId"]["S"]
                result[cid] = {
                    "text": item.get("text", {}).get("S", ""),
                    "title": item.get("title", {}).get("S", ""),
                }
            return result

        return await asyncio.to_thread(_get)

    _PARALLEL_SCAN_SEGMENTS: int = 8

    async def scan_all(self) -> list[dict[str, Any]]:
        """Scan every item, returning embedding/sourceUrl/chunkId.

        Uses the low-level DynamoDB client with a :class:`_FloatDeserializer`
        so embedding Numbers deserialize directly to ``float`` instead of
        ``Decimal``, and a parallel scan (8 segments) to stay within the
        Lambda timeout at corpus scale (100K+ items).
        """

        deserializer = _FloatDeserializer()

        def _deser(attr: Any | None) -> Any:
            return deserializer.deserialize(attr) if attr is not None else None

        def _scan_segment(segment: int) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            try:
                kwargs: dict[str, Any] = {
                    "TableName": self._table_name,
                    "ProjectionExpression": "chunkId, sourceUrl, embedding",
                    "Segment": segment,
                    "TotalSegments": self._PARALLEL_SCAN_SEGMENTS,
                }
                while True:
                    response = self._client.scan(**kwargs)
                    for item in response.get("Items", []):
                        items.append(
                            {
                                "chunkId": _deser(item["chunkId"]),
                                "sourceUrl": _deser(item.get("sourceUrl")) or "",
                                "embedding": np.asarray(
                                    _deser(item.get("embedding")) or [], dtype=np.float32
                                ),
                            }
                        )
                    last_key = response.get("LastEvaluatedKey")
                    if not last_key:
                        break
                    kwargs["ExclusiveStartKey"] = last_key
            except ClientError as exc:
                raise DynamoAccessError(f"failed to scan VectorStore segment {segment}") from exc
            return items

        segment_results = await asyncio.gather(
            *[asyncio.to_thread(_scan_segment, seg) for seg in range(self._PARALLEL_SCAN_SEGMENTS)]
        )
        items = [item for segment in segment_results for item in segment]
        segs = self._PARALLEL_SCAN_SEGMENTS
        logger.info("scanned vectors", extra={"count": len(items), "segments": segs})
        return items

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
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError
from src.crawler.parser import ContentChunk

logger = Logger()


def _to_decimal_list(vector: list[float]) -> list[Decimal]:
    """Convert a float vector to DynamoDB-safe ``Decimal`` values."""
    # Round-trip through str to avoid binary float artefacts in Decimal.
    return [Decimal(str(v)) for v in vector]


def _to_float_list(vector: list[Any]) -> list[float]:
    """Convert a stored ``Decimal`` vector back to floats."""
    return [float(v) for v in vector]


class VectorStore:
    """CRUD + scan over the VectorStore DynamoDB table."""

    def __init__(self, table: Any | None = None, table_name: str | None = None) -> None:
        """Args:
        table: Optional pre-built boto3 DynamoDB ``Table`` (tests).
        table_name: Table name; falls back to ``VECTOR_STORE_TABLE_NAME``.
        """
        if table is not None:
            self._table = table
        else:
            import os

            name = table_name or os.environ["VECTOR_STORE_TABLE_NAME"]
            self._table = boto3.resource("dynamodb").Table(name)

    async def upsert(self, chunk: ContentChunk, vector: list[float]) -> None:
        """Insert or replace ``chunk`` with its ``vector`` embedding."""

        def _put() -> None:
            try:
                self._table.put_item(
                    Item={
                        "chunkId": chunk.chunk_id,
                        "sourceUrl": chunk.source_url,
                        "text": chunk.text,
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

    async def scan_all(self) -> list[dict[str, Any]]:
        """Scan every item, returning embedding/text/sourceUrl/chunkId.

        Embeddings are converted back to ``list[float]`` for the caller.
        """

        def _scan() -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            try:
                kwargs: dict[str, Any] = {
                    "ProjectionExpression": "chunkId, sourceUrl, #t, embedding",
                    "ExpressionAttributeNames": {"#t": "text"},
                }
                while True:
                    response = self._table.scan(**kwargs)
                    for item in response.get("Items", []):
                        items.append(
                            {
                                "chunkId": item["chunkId"],
                                "sourceUrl": item.get("sourceUrl", ""),
                                "text": item.get("text", ""),
                                "embedding": _to_float_list(item.get("embedding", [])),
                            }
                        )
                    last_key = response.get("LastEvaluatedKey")
                    if not last_key:
                        break
                    kwargs["ExclusiveStartKey"] = last_key
            except ClientError as exc:
                raise DynamoAccessError("failed to scan VectorStore") from exc
            return items

        items = await asyncio.to_thread(_scan)
        logger.debug("scanned vectors", extra={"count": len(items)})
        return items

"""Differential content detection against the ContentDiff DynamoDB table.

:class:`DifferEngine` compares the freshly crawled chunks against the hashes
persisted from the previous crawl (stored in the ``ContentDiff`` table) and
returns a :class:`DiffResult` describing which chunks were *added*, *changed*,
or *deleted*. :meth:`commit` then reconciles the table to match the new state.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError
from src.crawler.parser import ContentChunk

logger = Logger()


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Outcome of a diff between a fresh crawl and the stored state.

    Attributes:
        added: Chunks present now but not previously stored.
        changed: Chunks whose ``content_hash`` differs from the stored hash.
        deleted: ``chunk_id`` values previously stored but absent from the crawl.
    """

    added: list[ContentChunk] = field(default_factory=list)
    changed: list[ContentChunk] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Return True when nothing changed (no embedding work required)."""
        return not (self.added or self.changed or self.deleted)


class DifferEngine:
    """Detects and commits content diffs using the ContentDiff table."""

    def __init__(self, table: Any) -> None:
        """Args:
        table: A boto3 DynamoDB ``Table`` resource for ContentDiff.
        """
        self._table = table

    @staticmethod
    def compute_hash(text: str) -> str:
        """Return the SHA-256 hex digest of ``text`` (UTF-8 encoded)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def diff(self, new_chunks: list[ContentChunk]) -> DiffResult:
        """Compare ``new_chunks`` to the stored state and classify changes."""
        stored = await self._load_stored_hashes()
        seen: set[str] = set()
        added: list[ContentChunk] = []
        changed: list[ContentChunk] = []

        for chunk in new_chunks:
            seen.add(chunk.chunk_id)
            prior = stored.get(chunk.chunk_id)
            if prior is None:
                added.append(chunk)
            elif prior != chunk.content_hash:
                changed.append(chunk)

        deleted = sorted(stored.keys() - seen)
        result = DiffResult(added=added, changed=changed, deleted=deleted)
        logger.info(
            "diff computed",
            extra={
                "added": len(result.added),
                "changed": len(result.changed),
                "deleted": len(result.deleted),
            },
        )
        return result

    async def commit(self, result: DiffResult) -> None:
        """Reconcile the ContentDiff table to reflect ``result``.

        Added and changed chunks are upserted (hash + sourceUrl); deleted chunk
        ids are removed.
        """

        def _commit() -> None:
            try:
                with self._table.batch_writer() as batch:
                    for chunk in (*result.added, *result.changed):
                        batch.put_item(
                            Item={
                                "chunkId": chunk.chunk_id,
                                "sourceUrl": chunk.source_url,
                                "contentHash": chunk.content_hash,
                            }
                        )
                    for chunk_id in result.deleted:
                        batch.delete_item(Key={"chunkId": chunk_id})
            except ClientError as exc:
                raise DynamoAccessError("failed to commit diff to ContentDiff") from exc

        await asyncio.to_thread(_commit)
        logger.info(
            "diff committed",
            extra={
                "upserted": len(result.added) + len(result.changed),
                "deleted": len(result.deleted),
            },
        )

    async def _load_stored_hashes(self) -> dict[str, str]:
        """Scan the ContentDiff table and return ``{chunkId: contentHash}``."""

        def _scan() -> dict[str, str]:
            stored: dict[str, str] = {}
            try:
                kwargs: dict[str, Any] = {
                    "ProjectionExpression": "chunkId, contentHash",
                }
                while True:
                    response = self._table.scan(**kwargs)
                    for item in response.get("Items", []):
                        stored[item["chunkId"]] = item["contentHash"]
                    last_key = response.get("LastEvaluatedKey")
                    if not last_key:
                        break
                    kwargs["ExclusiveStartKey"] = last_key
            except ClientError as exc:
                raise DynamoAccessError("failed to scan ContentDiff") from exc
            return stored

        return await asyncio.to_thread(_scan)

"""EmbedderLambda entry point (US-2.2 diff embedding).

Receives a diff payload from the CrawlerLambda — chunks to upsert (with text)
and chunk ids to delete — embeds each upsert chunk with Titan v2, and
reconciles the VectorStore table. The payload may be passed directly (small
diffs) or reference S3 (large diffs); both forms are supported.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from aws_lambda_powertools import Logger

from src.common.bedrock_client import BedrockClient
from src.crawler.parser import ContentChunk
from src.crawler.s3_store import S3ContentStore
from src.vector_store.store import VectorStore

logger = Logger()


async def _resolve_payload(event: dict[str, Any], store: S3ContentStore | None) -> dict[str, Any]:
    """Return the diff payload, dereferencing an S3 pointer when present."""
    ref = event.get("s3Ref")
    if ref and store is not None:
        import json

        body = await store.get(ref)
        loaded: dict[str, Any] = json.loads(body)
        return loaded
    return event


def _to_chunk(item: dict[str, Any]) -> ContentChunk:
    """Build a :class:`ContentChunk` from a payload upsert entry."""
    return ContentChunk(
        chunk_id=item["chunkId"],
        source_url=item.get("sourceUrl", ""),
        index=int(item.get("index", 0)),
        text=item.get("text", ""),
        content_hash=item.get("contentHash", ""),
    )


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Async embedder entry point (invoked via :func:`lambda_handler`)."""
    bucket = os.environ.get("CRAWL_CONTENT_BUCKET")
    store = S3ContentStore(bucket=bucket) if bucket else None
    payload = await _resolve_payload(event, store)

    vector_store = VectorStore()
    bedrock = BedrockClient()

    upserts = [_to_chunk(i) for i in payload.get("upsert", [])]
    deletes = [str(c) for c in payload.get("delete", [])]

    upserted = 0
    for chunk in upserts:
        vector = await bedrock.embed(chunk.text)
        await vector_store.upsert(chunk, vector)
        upserted += 1

    for chunk_id in deletes:
        await vector_store.delete(chunk_id)

    summary = {"upserted": upserted, "deleted": len(deletes)}
    logger.info("embedder finished", extra=summary)
    return summary


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

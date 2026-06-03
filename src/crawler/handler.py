"""CrawlerLambda entry point (US-2.1 weekly crawl, US-2.2 diff update).

Triggered weekly by EventBridge Scheduler. The handler:

1. loads ``robots.txt`` per host and obeys disallow rules,
2. fetches each target URL with ``httpx`` honouring a 1-3s polite random delay,
3. parses + chunks the HTML, persists raw text to S3,
4. computes a content diff against the ContentDiff table,
5. commits the diff and asynchronously invokes the EmbedderLambda with it.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any

import boto3
import httpx
from aws_lambda_powertools import Logger

from src.common.errors import CrawlerError, FetchTimeoutError
from src.crawler.differ import DifferEngine, DiffResult
from src.crawler.parser import ContentChunk, ContentParser
from src.crawler.robots import USER_AGENT, RobotsTxtGuard
from src.crawler.s3_store import S3ContentStore

logger = Logger()

_FETCH_TIMEOUT_SECONDS: float = 30.0
_MIN_DELAY_SECONDS: float = 1.0
_MAX_DELAY_SECONDS: float = 3.0


def _target_urls() -> list[str]:
    """Parse the ``CRAWLER_TARGET_URLS`` JSON-list environment variable."""
    raw = os.environ.get("CRAWLER_TARGET_URLS", "[]")
    try:
        urls = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CrawlerError("CRAWLER_TARGET_URLS is not valid JSON") from exc
    if not isinstance(urls, list):
        raise CrawlerError("CRAWLER_TARGET_URLS must be a JSON list")
    return [str(u) for u in urls]


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a single URL's HTML, raising FetchTimeoutError on failure."""
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchTimeoutError(f"failed to fetch {url}") from exc
    return response.text


async def _crawl_url(
    client: httpx.AsyncClient,
    url: str,
    guard: RobotsTxtGuard,
    parser: ContentParser,
    store: S3ContentStore,
) -> list[ContentChunk]:
    """Crawl one URL: robots check, fetch, parse, persist text to S3."""
    if not guard.is_allowed(url):
        logger.info("robots disallowed, skipping", extra={"url": url})
        return []

    html = await _fetch(client, url)
    chunks = parser.parse(html, url)
    url_hash = parser.compute_hash(url)
    for chunk in chunks:
        key = store.build_key(url_hash, chunk.chunk_id)
        await store.put(key, chunk.text)
    return chunks


def _invoke_embedder(result: DiffResult) -> None:
    """Asynchronously invoke EmbedderLambda with the diff payload."""
    function_name = os.environ.get("EMBEDDER_FUNCTION_NAME")
    if not function_name:
        logger.warning("EMBEDDER_FUNCTION_NAME unset; skipping embedder invoke")
        return
    payload = {
        "upsert": [
            {
                "chunkId": c.chunk_id,
                "sourceUrl": c.source_url,
                "index": c.index,
                "text": c.text,
                "contentHash": c.content_hash,
            }
            for c in (*result.added, *result.changed)
        ],
        "delete": list(result.deleted),
    }
    boto3.client("lambda").invoke(
        FunctionName=function_name,
        InvocationType="Event",  # asynchronous
        Payload=json.dumps(payload).encode("utf-8"),
    )


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Async crawler entry point (invoked via :func:`lambda_handler`)."""
    urls = _target_urls()
    bucket = os.environ["CRAWL_CONTENT_BUCKET"]
    diff_table_name = os.environ["CONTENT_DIFF_TABLE_NAME"]

    parser = ContentParser()
    store = S3ContentStore(bucket=bucket)
    differ = DifferEngine(boto3.resource("dynamodb").Table(diff_table_name))

    all_chunks: list[ContentChunk] = []
    errors: list[str] = []
    crawled = 0

    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        # Group URLs by host so robots.txt is loaded once per host.
        guards: dict[str, RobotsTxtGuard] = {}
        for url in urls:
            host = httpx.URL(url).host
            guard = guards.get(host)
            if guard is None:
                guard = RobotsTxtGuard()
                try:
                    await guard.load(url)
                except FetchTimeoutError as exc:
                    errors.append(f"{url}: robots load failed: {exc.message}")
                    continue
                guards[host] = guard

            try:
                chunks = await _crawl_url(client, url, guard, parser, store)
                all_chunks.extend(chunks)
                crawled += 1
            except CrawlerError as exc:
                errors.append(f"{url}: {exc.code}: {exc.message}")
            await asyncio.sleep(random.uniform(_MIN_DELAY_SECONDS, _MAX_DELAY_SECONDS))

    result = await differ.diff(all_chunks)
    await differ.commit(result)
    if not result.is_empty:
        _invoke_embedder(result)

    summary = {
        "crawled": crawled,
        "added": len(result.added),
        "changed": len(result.changed),
        "deleted": len(result.deleted),
        "errors": errors,
    }
    logger.info("crawl finished", extra=summary)
    return summary


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

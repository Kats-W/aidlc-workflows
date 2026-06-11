"""CrawlerLambda entry point (US-2.1 weekly crawl, US-2.2 diff update).

Triggered weekly by EventBridge Scheduler. The handler performs a BFS crawl
starting from the seed URLs in ``CRAWLER_TARGET_URLS``, restricted to the
same hosts as the seeds. It:

1. loads ``robots.txt`` per host and obeys disallow rules,
2. fetches each URL with ``httpx`` honouring a 1-3s polite random delay,
3. parses + chunks the HTML, persists raw text to S3,
4. extracts same-host links and enqueues them for BFS continuation,
5. stops gracefully 60s before the Lambda deadline,
6. computes a content diff against the ContentDiff table,
7. commits the diff and asynchronously invokes the EmbedderLambda with it.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections import deque
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

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
# Stop enqueueing new pages this many ms before the Lambda deadline.
_TIMEOUT_MARGIN_MS: int = 60_000
# FAQ host: distinct articles are addressed via the `id` query parameter, so
# it must be preserved. All other query parameters (tracking/session params)
# are dropped to avoid a crawler-trap explosion of near-duplicate URLs.
_FAQ_HOSTS: frozenset[str] = frozenset({"help.jibunbank.co.jp", "www.help.jibunbank.co.jp"})


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


def _normalize_url(url: str) -> str:
    """Strip fragment; normalize the query string; ensure root path ends with '/'.

    On the FAQ host, only the `id` parameter (which selects the article) is
    kept; on all other hosts the query string is dropped entirely. This
    prevents tracking/session query parameters from causing a crawler-trap
    explosion of near-duplicate URLs.
    """
    p = urlparse(url)
    path = p.path if p.path else "/"
    if p.hostname in _FAQ_HOSTS:
        kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k == "id"]
        query = urlencode(kept)
    else:
        query = ""
    return p._replace(fragment="", path=path, query=query).geturl()


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
) -> tuple[list[ContentChunk], str]:
    """Fetch, parse, and store one URL. Returns (chunks, raw_html)."""
    if not guard.is_allowed(url):
        logger.info("robots disallowed, skipping", extra={"url": url})
        return [], ""

    html = await _fetch(client, url)
    chunks = parser.parse(html, url)
    url_hash = parser.compute_hash(url)
    for chunk in chunks:
        key = store.build_key(url_hash, chunk.chunk_id)
        await store.put(key, chunk.text)
    return chunks, html


_EMBEDDER_BATCH_SIZE = 50  # ~300 KB per batch; Event invocation limit is 1 MB


def _invoke_embedder(result: DiffResult) -> None:
    """Asynchronously invoke EmbedderLambda in batches to stay under the 1 MB Event limit."""
    function_name = os.environ.get("EMBEDDER_FUNCTION_NAME")
    if not function_name:
        logger.warning("EMBEDDER_FUNCTION_NAME unset; skipping embedder invoke")
        return

    upsert_items = [
        {
            "chunkId": c.chunk_id,
            "sourceUrl": c.source_url,
            "index": c.index,
            "text": c.text,
            "contentHash": c.content_hash,
        }
        for c in (*result.added, *result.changed)
    ]
    deletes = list(result.deleted)
    client = boto3.client("lambda")

    # Batch upserts; send deletes only in the first batch.
    batches = range(0, max(len(upsert_items), 1), _EMBEDDER_BATCH_SIZE)
    for batch_start in batches:
        upsert_batch = upsert_items[batch_start : batch_start + _EMBEDDER_BATCH_SIZE]
        delete_batch: list[str] = deletes if batch_start == 0 else []
        payload = {"upsert": upsert_batch, "delete": delete_batch}
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # asynchronous
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            "embedder batch invoked",
            extra={
                "batch_start": batch_start,
                "upsert_count": len(upsert_batch),
                "delete_count": len(delete_batch),
            },
        )


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """BFS crawler entry point (invoked via :func:`lambda_handler`)."""
    seeds = _target_urls()
    bucket = os.environ["CRAWL_CONTENT_BUCKET"]
    diff_table_name = os.environ["CONTENT_DIFF_TABLE_NAME"]

    parser = ContentParser()
    store = S3ContentStore(bucket=bucket)
    differ = DifferEngine(boto3.resource("dynamodb").Table(diff_table_name))

    allowed_hosts: set[str] = {httpx.URL(u).host for u in seeds}
    visited: set[str] = set()
    queue: deque[str] = deque(_normalize_url(u) for u in seeds)
    guards: dict[str, RobotsTxtGuard] = {}
    all_chunks: list[ContentChunk] = []
    errors: list[str] = []
    crawled = 0

    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        while queue:
            # Gracefully stop 60s before Lambda deadline so Embedder can be invoked.
            if context.get_remaining_time_in_millis() < _TIMEOUT_MARGIN_MS:
                logger.warning(
                    "Lambda timeout approaching — stopping BFS early",
                    extra={"remaining_queue": len(queue)},
                )
                break

            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

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

            if not guard.is_allowed(url):
                logger.info("robots disallowed, skipping", extra={"url": url})
                continue

            try:
                chunks, html = await _crawl_url(client, url, guard, parser, store)
                all_chunks.extend(chunks)
                crawled += 1

                # Enqueue discovered same-host links.
                for link in parser.extract_links(html, url):
                    norm = _normalize_url(link)
                    if httpx.URL(norm).host in allowed_hosts and norm not in visited:
                        queue.append(norm)
            except CrawlerError as exc:
                errors.append(f"{url}: {exc.code}: {exc.message}")

            await asyncio.sleep(random.uniform(_MIN_DELAY_SECONDS, _MAX_DELAY_SECONDS))

    result = await differ.diff(all_chunks)
    await differ.commit(result)
    if not result.is_empty:
        try:
            _invoke_embedder(result)
        except Exception:
            # Log but do not re-raise: diff is already committed, so the next
            # scheduled crawl will detect no changes and skip Embedder invocation.
            # Re-raising would cause Lambda to fail and AWS to retry from scratch,
            # which corrupts ContentDiff (prior-run chunks get deleted on retry).
            logger.exception("embedder invocation failed; diff committed but not forwarded")

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

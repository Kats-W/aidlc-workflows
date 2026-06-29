"""CrawlerLambda entry point (US-2.1 weekly crawl, US-2.2 diff update).

Triggered weekly by EventBridge Scheduler. The handler performs a BFS crawl
restricted to the same hosts as the seed URLs in ``CRAWLER_TARGET_URLS``. It:

1. resumes the BFS queue/visited set persisted from the previous invocation
   (or starts a fresh cycle from the seed URLs if none is persisted, or the
   previous cycle finished),
2. loads ``robots.txt`` per host and obeys disallow rules,
3. fetches each URL with ``httpx`` honouring a 1-3s polite random delay,
4. parses + chunks the HTML, persists raw text to S3,
5. extracts same-host links and enqueues them for BFS continuation,
6. stops gracefully 60s before the Lambda deadline and persists the remaining
   queue/visited set for the next invocation,
7. computes a content diff against the ContentDiff table, scoped to the pages
   crawled this invocation,
8. commits the diff and asynchronously invokes the EmbedderLambda with it.

A full BFS cycle may span many invocations for a large site. When the queue
empties (every reachable page has been visited at least once this cycle), the
next invocation starts a new cycle from the seed URLs with an empty visited
set, so previously-crawled pages are re-crawled to detect content changes.
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

from src.common.errors import CrawlerError, FetchTimeoutError, ParseError, S3AccessError
from src.crawler.differ import DifferEngine, DiffResult
from src.crawler.parser import ContentChunk, ContentParser
from src.crawler.robots import USER_AGENT, RobotsTxtGuard
from src.crawler.s3_store import S3ContentStore
from src.crawler.state_store import CrawlStateStore

logger = Logger()

_FETCH_TIMEOUT_SECONDS: float = 30.0
_MIN_DELAY_SECONDS: float = 1.0
_MAX_DELAY_SECONDS: float = 3.0
# Stop enqueueing new pages this many ms before the Lambda deadline.
_TIMEOUT_MARGIN_MS: int = 60_000
# --- TEMPORARY: one-time auto-continue drain (remove after initial crawl) ----
# Safety cap on chained self-invocations (~15 min each) so the drain can never
# loop forever. ~60 chains ≈ up to ~15h, well beyond the expected ~3-8h.
_AUTO_CONTINUE_MAX_CHAINS: int = 60
# FAQ host: distinct articles are addressed via the `id` query parameter, so
# it must be preserved. All other query parameters (tracking/session params)
# are dropped to avoid a crawler-trap explosion of near-duplicate URLs.
_FAQ_HOSTS: frozenset[str] = frozenset({"help.jibunbank.co.jp", "www.help.jibunbank.co.jp"})
# Only these Content-Types are treated as parseable pages. Non-HTML responses
# (e.g. PDFs linked from a page) are skipped: BeautifulSoup.get_text() on a
# PDF's raw bytes returns nearly the entire binary as "text", producing
# thousands of meaningless chunks for a single document.
_HTML_CONTENT_TYPES: tuple[str, ...] = ("text/html", "application/xhtml+xml")


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


def _initial_state(
    loaded: tuple[deque[str], set[str]] | None, seeds: list[str]
) -> tuple[deque[str], set[str]]:
    """Return the BFS ``(queue, visited)`` to resume from.

    Starts a fresh crawl cycle (seed URLs, empty visited set) when no state
    was persisted yet, or the previous cycle finished (empty queue) —
    restarting the cycle is what allows already-visited pages to be
    re-crawled for diff detection.
    """
    if loaded is None or not loaded[0]:
        return deque(_normalize_url(u) for u in seeds), set()
    return loaded


async def _load_state(
    state_store: CrawlStateStore, seeds: list[str]
) -> tuple[deque[str], set[str]]:
    """Load persisted BFS state, falling back to a fresh cycle on S3 failure.

    A failure to read the persisted state must not abort the whole crawl —
    it degrades to the same "start fresh from seeds" behaviour as if no
    state had ever been persisted.
    """
    try:
        loaded = await state_store.load()
    except S3AccessError:
        logger.exception("failed to load BFS state; starting a fresh crawl cycle")
        loaded = None
    return _initial_state(loaded, seeds)


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Fetch a single URL's body and Content-Type, raising FetchTimeoutError on failure."""
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchTimeoutError(f"failed to fetch {url}") from exc
    return response.text, response.headers.get("content-type", "")


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

    html, content_type = await _fetch(client, url)
    if not any(ct in content_type.lower() for ct in _HTML_CONTENT_TYPES):
        logger.info("non-HTML content, skipping", extra={"url": url, "content_type": content_type})
        return [], ""

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
    batches = list(range(0, max(len(upsert_items), 1), _EMBEDDER_BATCH_SIZE))
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
    state_store = CrawlStateStore(bucket=bucket)
    differ = DifferEngine(boto3.resource("dynamodb").Table(diff_table_name))

    allowed_hosts: set[str] = {httpx.URL(u).host for u in seeds}
    queue, visited = await _load_state(state_store, seeds)
    queued: set[str] = set(queue)
    guards: dict[str, RobotsTxtGuard] = {}
    all_chunks: list[ContentChunk] = []
    crawled_url_hashes: set[str] = set()
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
                crawled_url_hashes.add(parser.compute_hash(url))

                # Enqueue discovered same-host links.
                for link in parser.extract_links(html, url):
                    norm = _normalize_url(link)
                    if (
                        httpx.URL(norm).host in allowed_hosts
                        and norm not in visited
                        and norm not in queued
                    ):
                        queue.append(norm)
                        queued.add(norm)
            except ParseError as exc:
                # Page fetched successfully but yielded no extractable text:
                # treat as a definitive result for diff purposes (its previous
                # chunks, if any, are stale and should be marked deleted).
                crawled_url_hashes.add(parser.compute_hash(url))
                errors.append(f"{url}: {exc.code}: {exc.message}")
            except CrawlerError as exc:
                errors.append(f"{url}: {exc.code}: {exc.message}")

            await asyncio.sleep(random.uniform(_MIN_DELAY_SECONDS, _MAX_DELAY_SECONDS))

    try:
        await state_store.save(queue, visited)
    except S3AccessError:
        # Log but do not re-raise: the next invocation will simply fail to
        # resume and start a fresh cycle, same as today's behaviour.
        logger.exception("failed to save BFS state; next invocation will start a fresh cycle")

    result = await differ.diff(all_chunks, crawled_url_hashes)
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
        "remaining_queue": len(queue),
        "errors": errors,
    }
    logger.info("crawl finished", extra=summary)
    _maybe_self_continue(event, context, len(queue))
    return summary


def _maybe_self_continue(event: dict[str, Any], context: Any, remaining_queue: int) -> None:
    """TEMPORARY: one-time auto-continue drain of the BFS frontier.

    When invoked with ``{"autoContinue": true}`` and the queue is not yet empty,
    asynchronously re-invokes this same function to continue crawling, bounded by
    ``chainsLeft`` (default :data:`_AUTO_CONTINUE_MAX_CHAINS`). The weekly
    scheduled crawl sends no flag, so it never self-continues. Remove this block
    (and the self-invoke IAM grant) once the initial full crawl has completed.
    """
    if not event.get("autoContinue"):
        return
    if remaining_queue <= 0:
        logger.info("auto-continue: queue drained — initial crawl complete")
        return
    chains_left = int(event.get("chainsLeft", _AUTO_CONTINUE_MAX_CHAINS))
    if chains_left <= 0:
        logger.warning(
            "auto-continue: chain cap reached — stopping",
            extra={"remaining_queue": remaining_queue},
        )
        return
    boto3.client("lambda").invoke(
        FunctionName=context.function_name,
        InvocationType="Event",
        Payload=json.dumps({"autoContinue": True, "chainsLeft": chains_left - 1}).encode(),
    )
    logger.info(
        "auto-continue: re-invoked self",
        extra={"chains_left": chains_left - 1, "remaining_queue": remaining_queue},
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

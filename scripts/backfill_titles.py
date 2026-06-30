#!/usr/bin/env python
"""One-off: delete mojibake chunks and backfill page <title> onto the corpus.

Two fixes applied without a full re-crawl/re-embed:
  1. Delete chunks whose stored text contains the Unicode replacement char
     (legacy Shift_JIS /pc/business pages mis-decoded before the encoding fix).
     They self-heal on the next weekly crawl with the corrected parser.
  2. For every remaining unique sourceUrl, fetch the page once (bytes -> the
     encoding-aware parser), extract its <title>, and UpdateItem the ``title``
     attribute on all of that URL's chunks. Titles are metadata, so no
     re-embedding is needed.

Dry-run by default; pass --apply to write.

    export AWS_REGION=ap-northeast-1
    uv run python scripts/backfill_titles.py            # counts only
    uv run python scripts/backfill_titles.py --apply
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3
import httpx
from botocore.config import Config

from src.crawler.parser import ContentParser
from src.crawler.robots import USER_AGENT

VECTOR_STORE = "au-jibun-bank-dev-vector-store"
CONTENT_DIFF = "au-jibun-bank-dev-content-diff"
SCAN_SEGMENTS = 8
FETCH_CONCURRENCY = 6
WRITE_WORKERS = 3
_CFG = Config(retries={"max_attempts": 15, "mode": "adaptive"})


def _scan_segment(segment: int) -> list[tuple[str, str, bool]]:
    client = boto3.client("dynamodb")
    out: list[tuple[str, str, bool]] = []
    kwargs: dict = {
        "TableName": VECTOR_STORE,
        "ProjectionExpression": "chunkId, sourceUrl, #t",
        "ExpressionAttributeNames": {"#t": "text"},
        "Segment": segment,
        "TotalSegments": SCAN_SEGMENTS,
    }
    while True:
        resp = client.scan(**kwargs)
        for it in resp.get("Items", []):
            cid = it["chunkId"]["S"]
            url = it.get("sourceUrl", {}).get("S", "")
            mojibake = "�" in it.get("text", {}).get("S", "")
            out.append((cid, url, mojibake))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return out
        kwargs["ExclusiveStartKey"] = lek


def _scan_all() -> list[tuple[str, str, bool]]:
    with ThreadPoolExecutor(max_workers=SCAN_SEGMENTS) as ex:
        return [r for seg in ex.map(_scan_segment, range(SCAN_SEGMENTS)) for r in seg]


def _delete(table: str, chunk_ids: list[str]) -> None:
    tbl = boto3.resource("dynamodb", config=_CFG).Table(table)
    with tbl.batch_writer() as bw:
        for cid in chunk_ids:
            bw.delete_item(Key={"chunkId": cid})


def _delete_parallel(table: str, chunk_ids: list[str]) -> None:
    shards = [chunk_ids[i::WRITE_WORKERS] for i in range(WRITE_WORKERS)]
    with ThreadPoolExecutor(max_workers=WRITE_WORKERS) as ex:
        list(ex.map(lambda s: _delete(table, s), shards))


async def _fetch_titles(urls: list[str]) -> dict[str, str]:
    parser = ContentParser()
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    titles: dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:

        async def one(url: str) -> None:
            async with sem:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    titles[url] = parser._extract_title(r.content)
                except Exception:
                    titles[url] = ""

        await asyncio.gather(*(one(u) for u in urls))
    return titles


def _update_titles(items: list[tuple[str, str]]) -> None:
    """items: list of (chunkId, title)."""

    def worker(shard: list[tuple[str, str]]) -> None:
        tbl = boto3.resource("dynamodb", config=_CFG).Table(VECTOR_STORE)
        for cid, title in shard:
            tbl.update_item(
                Key={"chunkId": cid},
                UpdateExpression="SET title = :t",
                ExpressionAttributeValues={":t": title},
            )

    shards = [items[i::WRITE_WORKERS] for i in range(WRITE_WORKERS)]
    with ThreadPoolExecutor(max_workers=WRITE_WORKERS) as ex:
        list(ex.map(worker, shards))


def main() -> None:
    apply = "--apply" in sys.argv
    rows = _scan_all()
    mojibake = [cid for cid, _u, m in rows if m]
    clean = [(cid, u) for cid, u, m in rows if not m and u]
    by_url: dict[str, list[str]] = defaultdict(list)
    for cid, u in clean:
        by_url[u].append(cid)

    print(f"total chunks={len(rows)}  mojibake={len(mojibake)}  "
          f"clean={len(clean)}  unique URLs={len(by_url)}")
    if not apply:
        print("(dry-run; pass --apply to delete mojibake + backfill titles)")
        return

    if mojibake:
        print(f"deleting {len(mojibake)} mojibake chunks ...", flush=True)
        _delete_parallel(VECTOR_STORE, mojibake)
        _delete_parallel(CONTENT_DIFF, mojibake)

    print(f"fetching titles for {len(by_url)} URLs ...", flush=True)
    titles = asyncio.run(_fetch_titles(list(by_url)))
    got = sum(1 for t in titles.values() if t)
    print(f"  resolved {got}/{len(titles)} titles")

    updates = [(cid, titles.get(u, "")) for u, cids in by_url.items() for cid in cids]
    print(f"updating title on {len(updates)} chunks ...", flush=True)
    _update_titles(updates)
    print("done.")


if __name__ == "__main__":
    main()

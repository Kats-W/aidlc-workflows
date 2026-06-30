#!/usr/bin/env python
"""One-off: remove http:// chunks that have an https:// twin.

The crawler used to follow both http and https variants of the same page,
embedding each as separate chunks (chunk_id = hash(sourceUrl)#i, so the two
schemes produced distinct ids). That doubled much of the corpus and filled
search results with http/https twins. The crawler now canonicalizes to https;
this removes the already-stored http duplicates (keeping the https copy). Pages
that exist *only* over http are left untouched.

Dry-run by default; pass --apply to delete.

    export AWS_REGION=ap-northeast-1
    uv run python scripts/dedup_http_duplicates.py            # counts only
    uv run python scripts/dedup_http_duplicates.py --apply
"""

from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

VECTOR_STORE = "au-jibun-bank-dev-vector-store"
CONTENT_DIFF = "au-jibun-bank-dev-content-diff"
SCAN_SEGMENTS = 8
WRITE_WORKERS = 3
_CFG = Config(retries={"max_attempts": 15, "mode": "adaptive"})


def _scan_segment(segment: int) -> list[tuple[str, str]]:
    client = boto3.client("dynamodb")
    out: list[tuple[str, str]] = []
    kwargs: dict = {
        "TableName": VECTOR_STORE,
        "ProjectionExpression": "chunkId, sourceUrl",
        "Segment": segment,
        "TotalSegments": SCAN_SEGMENTS,
    }
    while True:
        resp = client.scan(**kwargs)
        for it in resp.get("Items", []):
            out.append((it["chunkId"]["S"], it.get("sourceUrl", {}).get("S", "")))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return out
        kwargs["ExclusiveStartKey"] = lek


def _scan_all() -> list[tuple[str, str]]:
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


def main() -> None:
    apply = "--apply" in sys.argv
    rows = _scan_all()
    # Group chunkIds by normalized (scheme-stripped) URL, tracking schemes.
    by_key: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"http": [], "https": []})
    for cid, url in rows:
        if "://" not in url:
            continue
        scheme, rest = url.split("://", 1)
        if scheme in ("http", "https"):
            by_key[rest.rstrip("/")][scheme].append(cid)

    # Delete http chunks only where an https twin exists.
    drop = [
        cid
        for grp in by_key.values()
        if grp["https"] and grp["http"]
        for cid in grp["http"]
    ]
    print(f"total chunks={len(rows)}  normalized pages={len(by_key)}  "
          f"http-dupes to drop={len(drop)}  keep={len(rows) - len(drop)}")
    if not apply:
        print("(dry-run; pass --apply to delete)")
        return
    print(f"deleting {len(drop)} http-duplicate chunks ...", flush=True)
    _delete_parallel(VECTOR_STORE, drop)
    _delete_parallel(CONTENT_DIFF, drop)
    print("done.")


if __name__ == "__main__":
    main()

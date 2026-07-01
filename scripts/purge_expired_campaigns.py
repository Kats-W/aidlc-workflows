#!/usr/bin/env python
"""One-off: delete expired campaign pages from the corpus.

Campaign pages (/campaign/YYYY/...) are time-bound; old ones (from years before
the current one) are expired and only add noise — they were being retrieved for
general questions where a campaign is not the right answer. Delete chunks whose
sourceUrl is a campaign page from a year earlier than the current year; keep the
current year's campaigns.

Dry-run by default; pass --apply to delete.

    export AWS_REGION=ap-northeast-1
    uv run python scripts/purge_expired_campaigns.py           # counts only
    uv run python scripts/purge_expired_campaigns.py --apply
"""

from __future__ import annotations

import datetime
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

VECTOR_STORE = "au-jibun-bank-dev-vector-store"
CONTENT_DIFF = "au-jibun-bank-dev-content-diff"
SCAN_SEGMENTS = 8
WRITE_WORKERS = 3
_CFG = Config(retries={"max_attempts": 15, "mode": "adaptive"})
CURRENT_YEAR = datetime.date.today().year
_CAMPAIGN_YEAR = re.compile(r"/campaign/(\d{4})(?:/|_)")


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


def _expired_year(url: str) -> int | None:
    m = _CAMPAIGN_YEAR.search(url)
    if not m:
        return None
    year = int(m.group(1))
    return year if year < CURRENT_YEAR else None


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
    drop, years = [], Counter()
    for cid, url in rows:
        y = _expired_year(url)
        if y is not None:
            drop.append(cid)
            years[y] += 1
    print(f"total={len(rows)}  expired-campaign chunks to drop={len(drop)}  "
          f"keep={len(rows) - len(drop)}  (current year={CURRENT_YEAR})")
    print("  by campaign year:", dict(sorted(years.items())))
    if not apply:
        print("(dry-run; pass --apply to delete)")
        return
    print(f"deleting {len(drop)} expired-campaign chunks ...", flush=True)
    _delete_parallel(VECTOR_STORE, drop)
    _delete_parallel(CONTENT_DIFF, drop)
    print("done.")


if __name__ == "__main__":
    main()

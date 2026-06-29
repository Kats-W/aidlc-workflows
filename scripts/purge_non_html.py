#!/usr/bin/env python
"""One-off: purge non-HTML (PDF/image/binary) chunks from the RAG corpus.

The crawler now skips non-HTML at fetch time, but PDF/PNG chunks embedded before
that skip remain orphaned in DynamoDB (the incremental pipeline never revisits a
skipped URL, so it never deletes them). They make up ~84% of the corpus (mostly
FX app-manual PDFs) and drown out the useful HTML pages. This removes them from
both the vector-store and the content-diff tables, keyed by chunkId.

Dry-run by default; pass --apply to actually delete.

    export AWS_REGION=ap-northeast-1
    uv run python scripts/purge_non_html.py            # dry run (counts only)
    uv run python scripts/purge_non_html.py --apply     # delete
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import boto3
from botocore.config import Config

VECTOR_STORE = "au-jibun-bank-dev-vector-store"
CONTENT_DIFF = "au-jibun-bank-dev-content-diff"
SEGMENTS = 8
#: Fewer parallel writers + adaptive (client-side rate-limited) retries so the
#: bulk delete stays under the on-demand table's auto-scaling ceiling instead of
#: spiking into ThrottlingException.
DELETE_WORKERS = 3
_CFG = Config(retries={"max_attempts": 15, "mode": "adaptive"})

# Binary / non-article extensions to remove. Anything not listed (html, htm,
# php, or no extension = directory pages) is kept.
DROP_EXTS = {
    "pdf", "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp",
    "css", "js", "json", "xml", "zip", "gz", "csv",
    "xlsx", "xls", "doc", "docx", "ppt", "pptx",
    "mp4", "mp3", "mov", "woff", "woff2", "ttf", "eot",
}


def _ext(url: str) -> str:
    path = urlparse(url).path.lower()
    m = re.search(r"\.([a-z0-9]{1,6})$", path)
    return m.group(1) if m else "(none)"


def _scan_segment(table_name: str, segment: int) -> list[tuple[str, str]]:
    client = boto3.client("dynamodb")
    out: list[tuple[str, str]] = []
    kwargs: dict = {
        "TableName": table_name,
        "ProjectionExpression": "chunkId, sourceUrl",
        "Segment": segment,
        "TotalSegments": SEGMENTS,
    }
    while True:
        resp = client.scan(**kwargs)
        for it in resp.get("Items", []):
            out.append((it["chunkId"]["S"], it.get("sourceUrl", {}).get("S", "")))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return out
        kwargs["ExclusiveStartKey"] = lek


def _scan_all(table_name: str) -> list[tuple[str, str]]:
    with ThreadPoolExecutor(max_workers=SEGMENTS) as ex:
        results = ex.map(lambda s: _scan_segment(table_name, s), range(SEGMENTS))
    return [row for seg in results for row in seg]


def _delete(table_name: str, chunk_ids: list[str]) -> None:
    res = boto3.resource("dynamodb", config=_CFG)
    table = res.Table(table_name)
    with table.batch_writer() as bw:
        for cid in chunk_ids:
            bw.delete_item(Key={"chunkId": cid})


def _delete_parallel(table_name: str, chunk_ids: list[str]) -> None:
    shards = [chunk_ids[i::DELETE_WORKERS] for i in range(DELETE_WORKERS)]
    with ThreadPoolExecutor(max_workers=DELETE_WORKERS) as ex:
        list(ex.map(lambda s: _delete(table_name, s), shards))


def process(table_name: str, apply: bool) -> None:
    rows = _scan_all(table_name)
    by_ext = Counter(_ext(u) for _, u in rows)
    drop_ids = [cid for cid, u in rows if _ext(u) in DROP_EXTS]
    keep = len(rows) - len(drop_ids)
    print(f"\n=== {table_name} ===")
    print(f"  total={len(rows)}  drop={len(drop_ids)}  keep={keep}")
    print("  by extension:", dict(by_ext.most_common(12)))
    if not apply:
        print("  (dry-run; pass --apply to delete)")
        return
    print(f"  deleting {len(drop_ids)} items ...", flush=True)
    _delete_parallel(table_name, drop_ids)
    print("  done.")


def main() -> None:
    apply = "--apply" in sys.argv
    for t in (VECTOR_STORE, CONTENT_DIFF):
        process(t, apply)
    if not apply:
        print("\nNo changes made. Re-run with --apply to purge.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Diagnostic: show what the retriever actually returns for a question.

Prints the top-k retrieved chunks (cosine score, source URL, text snippet) so we
can tell whether a weak answer is a corpus-coverage problem (the fact isn't
retrieved) or a generation problem (it is retrieved but the model hedges).

    export VECTOR_STORE_TABLE_NAME=au-jibun-bank-dev-vector-store
    export CRAWL_CONTENT_BUCKET=au-jibun-bank-dev-crawl-content-568115736711
    export AWS_REGION=ap-northeast-1
    uv run python scripts/rag_eval/inspect_retrieval.py "住宅ローンの金利を教えて" 8
"""

from __future__ import annotations

import asyncio
import os
import sys

from src.common.bedrock_client import BedrockClient
from src.vector_store.searcher import CosineSimilaritySearcher
from src.vector_store.store import VectorStore
from src.vector_store.vector_cache_store import VectorCacheS3Store


async def main(question: str, k: int) -> None:
    bucket = os.environ["CRAWL_CONTENT_BUCKET"]
    searcher = CosineSimilaritySearcher(VectorStore(), VectorCacheS3Store(bucket=bucket))
    print("loading vector cache from S3 ...", flush=True)
    await searcher.ensure_cache_loaded()
    vec = await BedrockClient().embed(question)
    hits = await searcher.search(vec, top_k=k)
    print(f"\nQ: {question}\n{'=' * 70}")
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.text.split())[:220]
        print(f"\n[{i}] score={h.score:.3f}  {h.source_url}")
        print(f"    {snippet}")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "住宅ローンの金利を教えて"
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    asyncio.run(main(q, k))

#!/usr/bin/env python
"""LLM-as-judge RAG quality evaluation (honest faithfulness + usefulness).

Runs the full pipeline locally per question (embed -> search -> generate) and
then asks Claude Sonnet to score the answer against the *actual retrieved
context*:

  - faithfulness (1-5): is every claim supported by the context (no hallucination)?
  - usefulness   (1-5): does it actually answer the user's question?
  - verdict: good | hedge (faithful but unhelpful, e.g. corpus gap) |
             hallucination | miss

This separates "grounded" from "actually good": a hedge ("we don't have that
info") scores high faithfulness but low usefulness, exposing corpus gaps that a
hit-rate metric hides.

    export VECTOR_STORE_TABLE_NAME=au-jibun-bank-dev-vector-store
    export CRAWL_CONTENT_BUCKET=au-jibun-bank-dev-crawl-content-568115736711
    export AWS_REGION=ap-northeast-1
    uv run python scripts/rag_eval/judge_eval.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import boto3

from src.common.bedrock_client import ANSWER_MODEL_ID, ANTHROPIC_VERSION, BedrockClient
from src.rag_handler.handler import MIN_HIT_SCORE, TOP_K
from src.vector_store.searcher import CosineSimilaritySearcher
from src.vector_store.store import VectorStore
from src.vector_store.vector_cache_store import VectorCacheS3Store

QUESTIONS = Path(__file__).with_name("questions.json")

_JUDGE_PROMPT = """あなたは RAG システムの厳格な評価者です。
以下の「質問」「参考情報(検索で取得した文脈)」「回答」を読み、回答を採点してください。

# 質問
{question}

# 参考情報
{context}

# 回答
{answer}

# 採点基準
- faithfulness(忠実性, 1-5): 回答の主張がすべて参考情報で裏付けられているか。
  参考情報に無い事実を述べていれば低評価(ハルシネーション)。
- usefulness(有用性, 1-5): 質問に実際に役立つ回答ができているか。
  「情報が無い」と断るだけ・一般論に逃げるだけなら低評価。
- verdict: good(的確) / hedge(忠実だが情報不足で役に立たない) /
  hallucination(根拠の無い断定) / miss(無関係・回答不能)

以下の JSON のみで回答してください(説明文不要):
{{"faithfulness": <1-5>, "usefulness": <1-5>, "verdict": "<...>", "reason": "<30字以内>"}}"""


def _judge(client: Any, question: str, context: str, answer: str) -> dict[str, Any]:
    prompt = _JUDGE_PROMPT.format(question=question, context=context[:6000], answer=answer)
    body = json.dumps(
        {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            resp = client.invoke_model(modelId=ANSWER_MODEL_ID, body=body)
            blocks = json.loads(resp["body"].read())["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start : end + 1])  # type: ignore[no-any-return]
        except Exception as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"judge failed after retries: {last_exc}")


async def main() -> None:
    bucket = os.environ["CRAWL_CONTENT_BUCKET"]
    searcher = CosineSimilaritySearcher(VectorStore(), VectorCacheS3Store(bucket=bucket))
    print("loading vector cache ...", flush=True)
    await searcher.ensure_cache_loaded()
    bedrock = BedrockClient()
    judge_client = boto3.client("bedrock-runtime")

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []

    for q in questions:
        if q.get("expect_miss"):
            continue
        vec = await bedrock.embed(q["question"])
        all_hits = await searcher.search(vec, top_k=TOP_K)
        top = all_hits[0].score if all_hits else 0.0
        hits = [h for h in all_hits if h.score >= MIN_HIT_SCORE]
        if not hits:
            rows.append({"id": q["id"], "faithfulness": 5, "usefulness": 1, "verdict": "miss"})
            print(f"  {q['id']:14s} top={top:.3f} no usable hits -> miss")
            continue
        chunks = [{"text": h.text, "source_url": h.source_url} for h in hits]
        context = "\n\n".join(f"[{i + 1}] {c['text']}" for i, c in enumerate(chunks))
        answer, _ = await bedrock.generate_answer(q["question"], chunks, "", max_tokens=500)
        verdict = _judge(judge_client, q["question"], context, answer)
        rows.append({"id": q["id"], **verdict})
        print(f"  {q['id']:14s} top={top:.3f} faith={verdict['faithfulness']} "
              f"use={verdict['usefulness']} {verdict['verdict']:14s} {verdict.get('reason', '')}")

    _summary(rows)


def _summary(rows: list[dict[str, Any]]) -> None:
    faith = [r["faithfulness"] for r in rows]
    use = [r["usefulness"] for r in rows]
    verdicts: dict[str, int] = {}
    for r in rows:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    good = sum(1 for r in rows if r["usefulness"] >= 4 and r["faithfulness"] >= 4)
    print(f"\n=== LLM-judge Summary (n={len(rows)}) ===")
    print(f"  faithfulness mean : {statistics.mean(faith):.2f} / 5")
    print(f"  usefulness   mean : {statistics.mean(use):.2f} / 5")
    print(f"  good (faith>=4 & use>=4): {good}/{len(rows)} ({100 * good / len(rows):.0f}%)")
    print(f"  verdicts          : {verdicts}")


if __name__ == "__main__":
    asyncio.run(main())

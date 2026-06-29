#!/usr/bin/env python
"""RAG quality + latency evaluation harness (Phase C/D).

Runs a fixed question set against the deployed chat-api Function URL (the real
streaming SSE path) and reports, per question and in aggregate:

  - hit:          did the pipeline return a grounded answer (vs the fallback)?
  - sources:      number of cited sources, and how many are jibunbank.co.jp
  - keywords:     fraction of expected keywords present in the answer
  - ttft_ms:      time to first answer token (the latency users actually feel)
  - total_ms:     time to the final token

A negative-control question (``expect_miss``) checks the system declines to
answer when nothing relevant is retrieved (no hallucinated sources).

Usage:
    export CHAT_ENDPOINT=https://<id>.lambda-url.ap-northeast-1.on.aws
    export DEMO_KEY=$(aws secretsmanager get-secret-value \
        --secret-id au-jibun-bank-dev-chat-demo-key --query SecretString --output text)
    uv run python scripts/rag_eval/evaluate.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = os.environ.get("CHAT_ENDPOINT", "").rstrip("/")
DEMO_KEY = os.environ.get("DEMO_KEY", "")
QUESTIONS = Path(__file__).with_name("questions.json")


def _stream_one(client: httpx.Client, question: str) -> dict[str, Any]:
    """POST a question and consume the SSE stream, timing the first/last token."""
    sources: list[str] = []
    answer_parts: list[str] = []
    t0 = time.monotonic()
    ttft: float | None = None
    error: str | None = None

    headers = {"content-type": "application/json"}
    if DEMO_KEY:
        headers["x-demo-key"] = DEMO_KEY

    with client.stream(
        "POST",
        f"{ENDPOINT}/chat",
        headers=headers,
        json={"message": question, "sessionId": "rag-eval"},
        timeout=60.0,
    ) as resp:
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "ttft_ms": None, "total_ms": None,
                    "sources": [], "answer": ""}
        event = "message"
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "sources":
                    sources = data
                elif event == "token":
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    answer_parts.append(data)
                elif event == "error":
                    error = data.get("message", "error")
    total = time.monotonic() - t0
    return {
        "error": error,
        "ttft_ms": round(ttft * 1000) if ttft is not None else None,
        "total_ms": round(total * 1000),
        "sources": sources,
        "answer": "".join(answer_parts),
    }


def main() -> int:
    if not ENDPOINT:
        print("ERROR: set CHAT_ENDPOINT (and DEMO_KEY).", file=sys.stderr)
        return 2

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []

    print(f"Evaluating {len(questions)} questions against {ENDPOINT}\n")
    with httpx.Client() as client:
        for q in questions:
            r = _stream_one(client, q["question"])
            answer = r["answer"]
            hit = bool(answer) and "オペレーターにおつなぎ" not in answer
            kw = q.get("keywords", [])
            kw_hits = sum(1 for k in kw if k in answer)
            # sources are {"url","title"} objects (older runs returned bare URLs).
            jibun = sum(
                1
                for s in r["sources"]
                if "jibunbank.co.jp" in (s.get("url", "") if isinstance(s, dict) else s)
            )
            ok = (not q.get("expect_miss")) == hit  # negative control should miss
            rows.append({
                "id": q["id"], "hit": hit, "expect_miss": q.get("expect_miss", False),
                "n_src": len(r["sources"]), "jibun_src": jibun,
                "kw": f"{kw_hits}/{len(kw)}" if kw else "-",
                "ttft_ms": r["ttft_ms"], "total_ms": r["total_ms"],
                "chars": len(answer), "control_ok": ok, "error": r["error"],
            })
            flag = "✓" if ok else "✗"
            print(f"  {flag} {q['id']:14s} hit={hit!s:5s} src={len(r['sources'])} "
                  f"kw={rows[-1]['kw']:>4s} ttft={r['ttft_ms']}ms total={r['total_ms']}ms")

    _summary(rows)
    return 0


def _summary(rows: list[dict[str, Any]]) -> None:
    answered = [r for r in rows if not r["expect_miss"]]
    hits = [r for r in answered if r["hit"]]
    ttfts = [r["ttft_ms"] for r in hits if r["ttft_ms"] is not None]
    totals = [r["total_ms"] for r in hits if r["total_ms"] is not None]

    def pct(n: int, d: int) -> str:
        return f"{(100 * n / d):.0f}%" if d else "n/a"

    def p(vals: list[int], q: float) -> str:
        if not vals:
            return "n/a"
        s = sorted(vals)
        return f"{s[min(len(s) - 1, int(q * len(s)))]}ms"

    grounded = [r for r in hits if r["jibun_src"] > 0]
    controls = [r for r in rows if r["expect_miss"]]
    controls_ok = sum(1 for r in controls if r["control_ok"])
    print("\n=== Summary ===")
    print(f"  answerable hit-rate : {pct(len(hits), len(answered))} ({len(hits)}/{len(answered)})")
    print(f"  source-grounded     : {pct(len(grounded), len(hits))} (>=1 jibunbank source)")
    print(f"  negative control    : {pct(controls_ok, len(controls))} declined as expected")
    print(f"  TTFT  p50/p95       : {p(ttfts, 0.5)} / {p(ttfts, 0.95)}")
    print(f"  total p50/p95       : {p(totals, 0.5)} / {p(totals, 0.95)}")
    if totals:
        print(f"  total mean          : {round(statistics.mean(totals))}ms")


if __name__ == "__main__":
    raise SystemExit(main())

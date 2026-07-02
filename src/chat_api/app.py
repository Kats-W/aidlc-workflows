"""FastAPI streaming chat app (U-08).

Reuses the RAG pipeline collaborators from the voice path
(:mod:`src.rag_handler.handler`) — mask → personalize → embed → search — and
streams the answer tokens with ``BedrockClient.generate_answer_stream`` over
Server-Sent Events. Unlike the Connect voice path there is no 6-second budget;
the web client simply renders tokens as they arrive.

Run locally::

    uvicorn src.chat_api.app:app --port 8080

Behind the Lambda Web Adapter the same ``app`` is served by uvicorn and exposed
on a Function URL in ``RESPONSE_STREAM`` mode.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.common.bedrock_client import BedrockClient
from src.common.errors import AppError
from src.common.pii_masker import PiiMasker
from src.rag_handler.handler import (
    ANONYMOUS,
    MIN_HIT_SCORE,
    TOP_K,
)
from src.rag_handler.personalizer import Personalizer
from src.session_manager.history import ConversationTurn, HistoryRepository
from src.vector_store.searcher import CosineSimilaritySearcher
from src.vector_store.store import VectorStore
from src.vector_store.vector_cache_store import VectorCacheS3Store

logger = Logger()

#: Max answer tokens for the chat channel. Higher than the voice path's 400
#: (no 6s budget here) but still bounded so answers stay concise and fast.
CHAT_ANSWER_MAX_TOKENS: int = 700

#: No-hit fallback for the web chat. Unlike the voice path's FALLBACK_ANSWER it
#: does not promise an operator handoff (the demo has no escalation channel).
CHAT_FALLBACK: str = (
    "申し訳ございません。ご質問に正確にお答えできる情報が見つかりませんでした。"
    "お手数ですが、表現を変えてもう一度お試しください。"
)

#: Shared searcher (holds the in-memory / S3 vector cache). Built once at
#: startup so warm invocations skip the multi-hundred-MB cache load.
_searcher: CosineSimilaritySearcher | None = None


def _make_searcher() -> CosineSimilaritySearcher:
    bucket = os.environ.get("CRAWL_CONTENT_BUCKET")
    cache_store = VectorCacheS3Store(bucket=bucket) if bucket else None
    return CosineSimilaritySearcher(VectorStore(), cache_store)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Warm the vector cache once when the worker starts."""
    global _searcher
    _searcher = _make_searcher()
    try:
        await _searcher.ensure_cache_loaded()
        logger.info("chat_api startup: vector cache loaded")
    except Exception:
        logger.exception("chat_api startup: vector cache warm failed")
    yield


# CORS is handled at the edge by the Lambda Function URL's own CORS config
# (see ChatStack). Adding FastAPI's CORSMiddleware too emits a *second*
# Access-Control-Allow-Origin header, which browsers reject ("Failed to fetch").
app = FastAPI(title="au Jibun Bank Chat API", lifespan=lifespan)


class ChatRequest(BaseModel):
    """A single chat turn from the web client."""

    message: str
    sessionId: str | None = None


def build_collaborators() -> tuple[PiiMasker, Personalizer, BedrockClient, HistoryRepository]:
    """Construct the per-request pipeline collaborators (patched out in tests)."""
    history = HistoryRepository()
    return PiiMasker(), Personalizer(history), BedrockClient(), history


def _verify_key(x_demo_key: str | None = Header(default=None)) -> None:
    """Reject requests without the shared demo key, when one is configured.

    If ``DEMO_API_KEY`` is unset (local development) the check is skipped.
    """
    expected = os.environ.get("DEMO_API_KEY")
    if expected and x_demo_key != expected:
        raise HTTPException(status_code=401, detail="invalid demo key")


def _sse(event: str, data: Any) -> str:
    """Format one Server-Sent Event with a JSON ``data`` payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(message: str, session_id: str | None) -> AsyncIterator[str]:
    """Run the RAG pipeline and yield SSE frames: sources → token* → done."""
    masker, personalizer, bedrock, history = build_collaborators()
    searcher = _searcher if _searcher is not None else _make_searcher()
    customer_id = (session_id or ANONYMOUS).strip() or ANONYMOUS
    try:
        masked, _entities = await masker.mask(message)
        history_text = await personalizer.build_context(customer_id)
        # Rewrite terse follow-ups into a standalone query so multi-turn
        # drill-down (Phase ③) retrieves the right topic; no-op on the first turn.
        search_query = await bedrock.condense_query(masked, history_text)
        query_vec = await bedrock.embed(search_query)
        hits = await searcher.search(query_vec, top_k=TOP_K)

        usable = [h for h in hits if h.score >= MIN_HIT_SCORE]
        if not usable:
            yield _sse("sources", [])
            yield _sse("token", CHAT_FALLBACK)
            yield _sse("done", {"hit": False})
            return

        chunks = [{"text": h.text, "source_url": h.source_url} for h in usable]
        # Emit sources as {url, title} so the UI shows a human-readable page
        # title. Dedupe across http/https of the same page (prefer https) so a
        # page never appears twice.
        seen: dict[str, dict[str, str]] = {}
        for h in usable:
            if not h.source_url:
                continue
            key = h.source_url.split("://", 1)[-1].rstrip("/")
            cur = seen.get(key)
            if cur is None or (
                h.source_url.startswith("https") and not cur["url"].startswith("https")
            ):
                seen[key] = {"url": h.source_url, "title": h.title or ""}
        yield _sse("sources", list(seen.values()))

        parts: list[str] = []
        async for delta in bedrock.generate_answer_stream(
            masked,
            chunks,
            history_text,
            max_tokens=CHAT_ANSWER_MAX_TOKENS,
            allow_clarifying=True,
        ):
            parts.append(delta)
            yield _sse("token", delta)

        answer = "".join(parts)
        now = datetime.now(UTC).isoformat()
        await history.append_turn(
            customer_id,
            ConversationTurn(
                role="user",
                text=masked,
                timestamp=now,
                contact_id=session_id or "",
                channel="chat",
            ),
        )
        await history.append_turn(
            customer_id,
            ConversationTurn(
                role="assistant",
                text=answer,
                timestamp=datetime.now(UTC).isoformat(),
                contact_id=session_id or "",
                channel="chat",
            ),
        )
        yield _sse("done", {"hit": True})
    except AppError as exc:
        logger.warning("chat pipeline failed", extra={"code": exc.code})
        yield _sse("error", {"code": exc.code, "message": "一時的なエラーが発生しました"})
    except Exception:
        logger.exception("chat pipeline crashed")
        yield _sse("error", {"message": "内部エラーが発生しました"})


@app.get("/health")
async def health() -> dict[str, Any]:
    """Readiness probe used by the Lambda Web Adapter."""
    return {"status": "ok", "cacheReady": _searcher is not None}


@app.post("/chat")
async def chat(req: ChatRequest, _: None = Depends(_verify_key)) -> StreamingResponse:
    """Stream a RAG answer for ``req.message`` as Server-Sent Events."""
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    return StreamingResponse(
        _event_stream(message, req.sessionId),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

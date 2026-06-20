"""RagHandlerLambda — Amazon Connect RAG answer hook (US-1.1 / US-1.2).

Invoked from a Connect contact-flow Lambda block for each customer utterance.
The pipeline, bounded by a 6-second time budget (well inside Connect's 8s limit):

1. mask PII in the user input (Comprehend),
2. build personalization context from prior turns,
3. embed the masked input (Titan v2),
4. cosine-search the vector store for the top-k chunks,
5. generate an answer with Claude Haiku 4.5,
6. append the user + assistant turns to CustomerHistory.

If the pipeline exceeds its time budget, or Bedrock/Comprehend fail, the handler
returns a safe fallback response with ``hit=False`` (it never raises to Connect).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger

from src.common.bedrock_client import BedrockClient
from src.common.errors import AppError, TimeoutBudgetExceeded
from src.common.pii_masker import PiiMasker
from src.rag_handler.personalizer import Personalizer
from src.session_manager.history import ConversationTurn, HistoryRepository
from src.vector_store.searcher import CosineSimilaritySearcher, SearchHit
from src.vector_store.store import VectorStore
from src.vector_store.vector_cache_store import VectorCacheS3Store

logger = Logger()

#: Time budget for the whole RAG pipeline (Connect allows 8s; keep headroom).
PIPELINE_BUDGET_SECONDS: float = 6.0
#: Number of context chunks to retrieve.
TOP_K: int = 5
#: Minimum cosine score for a hit to count as a usable match.
MIN_HIT_SCORE: float = 0.3
#: Max tokens for the RAG answer. Kept short for voice-channel latency (the
#: 6s pipeline budget) and TTS readability.
ANSWER_MAX_TOKENS: int = 400
#: Fallback message returned when the pipeline cannot produce an answer.
FALLBACK_ANSWER: str = (
    "申し訳ございません。ただいまお答えをご用意できませんでした。"
    "オペレーターにおつなぎいたします。"
)

#: Sentinel for unidentified callers.
ANONYMOUS: str = "anonymous"


def _build_dependencies() -> tuple[
    PiiMasker, Personalizer, BedrockClient, CosineSimilaritySearcher, HistoryRepository
]:
    """Construct the live pipeline collaborators (patched out in tests)."""
    history = HistoryRepository()
    bucket = os.environ.get("CRAWL_CONTENT_BUCKET")
    cache_store = VectorCacheS3Store(bucket=bucket) if bucket else None
    return (
        PiiMasker(),
        Personalizer(history),
        BedrockClient(),
        CosineSimilaritySearcher(VectorStore(), cache_store),
        history,
    )


async def _rag_pipeline(
    customer_id: str,
    user_input: str,
    channel: str,
    contact_id: str,
) -> dict[str, Any]:
    """Run the full RAG pipeline and return the Connect response payload."""
    masker, personalizer, bedrock, searcher, history = _build_dependencies()

    step_start = time.monotonic()

    def _log_step(step: str) -> None:
        nonlocal step_start
        now = time.monotonic()
        elapsed_ms = round((now - step_start) * 1000)
        logger.info(
            "pipeline step timing",
            extra={"contact_id": contact_id, "step": step, "elapsed_ms": elapsed_ms},
        )
        step_start = now

    masked_input, _entities = await masker.mask(user_input)
    _log_step("mask")
    history_text = await personalizer.build_context(customer_id)
    _log_step("personalize")

    query_vec = await bedrock.embed(masked_input)
    _log_step("embed")
    hits: list[SearchHit] = await searcher.search(query_vec, top_k=TOP_K)
    _log_step("search")

    usable = [h for h in hits if h.score >= MIN_HIT_SCORE]
    if not usable:
        logger.info("no usable hits", extra={"contact_id": contact_id})
        return {"answer": FALLBACK_ANSWER, "sources": [], "hit": False}

    chunks = [{"text": h.text, "source_url": h.source_url} for h in usable]
    answer, sources = await bedrock.generate_answer(
        masked_input, chunks, history_text, max_tokens=ANSWER_MAX_TOKENS
    )
    _log_step("generate_answer")

    now = datetime.now(UTC).isoformat()
    await history.append_turn(
        customer_id,
        ConversationTurn(
            role="user",
            text=masked_input,
            timestamp=now,
            contact_id=contact_id,
            channel=channel,
        ),
    )
    await history.append_turn(
        customer_id,
        ConversationTurn(
            role="assistant",
            text=answer,
            timestamp=datetime.now(UTC).isoformat(),
            contact_id=contact_id,
            channel=channel,
        ),
    )
    _log_step("history_append")
    return {"answer": answer, "sources": sources, "hit": True}


_searcher_singleton: CosineSimilaritySearcher | None = None


async def _ensure_cache_warmed() -> None:
    """Download the S3 vector cache to ``/tmp`` on cold start.

    Runs *outside* the pipeline timeout so the download completes in
    full (~15-20 s for 877 MB). Connect will time out on the first
    cold-start call, but subsequent warm invocations find ``/tmp``
    ready and respond within the 6 s budget.
    """
    global _searcher_singleton
    if _searcher_singleton is None:
        bucket = os.environ.get("CRAWL_CONTENT_BUCKET")
        cache_store = VectorCacheS3Store(bucket=bucket) if bucket else None
        _searcher_singleton = CosineSimilaritySearcher(VectorStore(), cache_store)
    await _searcher_singleton.ensure_cache_loaded()


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Connect contact-flow entry point.

    Never raises to Connect: any failure (timeout, Bedrock, Comprehend) collapses
    into a ``hit=False`` fallback response so the flow can escalate gracefully.
    """
    customer_id = str(event.get("customerId") or ANONYMOUS).strip() or ANONYMOUS
    user_input = str(event.get("userInput") or "").strip()
    channel = str(event.get("channel") or "voice")
    contact_id = str(event.get("contactId") or "")

    if not user_input:
        logger.warning("empty userInput", extra={"contact_id": contact_id})
        return {"answer": FALLBACK_ANSWER, "sources": [], "hit": False}

    await _ensure_cache_warmed()

    try:
        return await asyncio.wait_for(
            _rag_pipeline(customer_id, user_input, channel, contact_id),
            timeout=PIPELINE_BUDGET_SECONDS,
        )
    except TimeoutError as exc:
        budget = TimeoutBudgetExceeded("RAG pipeline exceeded 6s budget")
        logger.warning("rag timeout", extra={"code": budget.code, "error": str(exc)})
        return {"answer": FALLBACK_ANSWER, "sources": [], "hit": False}
    except AppError as exc:
        logger.warning(
            "rag pipeline failed", extra={"code": exc.code, "detail": exc.message}
        )
        return {"answer": FALLBACK_ANSWER, "sources": [], "hit": False}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`).

    Normalises the Amazon Connect contact-flow event envelope so the inner
    ``handler`` always receives a flat dict with ``userInput``, ``customerId``,
    ``channel``, and ``contactId`` at the top level.  Direct test invocations
    (which already use the flat format) pass through unchanged.
    """
    if "Details" in event:
        # Connect wraps everything inside Details.{ContactData,Parameters}.
        details: dict[str, Any] = event.get("Details", {})
        params: dict[str, Any] = details.get("Parameters", {})
        contact_data: dict[str, Any] = details.get("ContactData", {})
        attrs: dict[str, Any] = contact_data.get("Attributes", {})
        normalized: dict[str, Any] = {
            "userInput": params.get("userInput", ""),
            "customerId": params.get("customerId") or attrs.get("customerId", ""),
            "channel": contact_data.get("Channel", "VOICE").lower(),
            "contactId": contact_data.get("ContactId", ""),
        }
        return asyncio.run(handler(normalized, context))
    return asyncio.run(handler(event, context))

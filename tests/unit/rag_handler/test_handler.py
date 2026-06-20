"""Unit tests for :mod:`src.rag_handler.handler` (collaborators mocked)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from src.common.errors import BedrockError
from src.rag_handler import handler as h
from src.vector_store.searcher import SearchHit


def _make_deps(
    *,
    masked: str = "[MASKED] の残高は",
    entities: list[dict] | None = None,
    hits: list[SearchHit] | None = None,
    answer: str = "残高は10万円です。",
    sources: list[str] | None = None,
):
    masker = AsyncMock()
    masker.mask = AsyncMock(return_value=(masked, entities or []))
    personalizer = AsyncMock()
    personalizer.build_context = AsyncMock(return_value="")
    bedrock = AsyncMock()
    bedrock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    bedrock.generate_answer = AsyncMock(
        return_value=(answer, sources or ["https://x/faq"])
    )
    searcher = AsyncMock()
    searcher.search = AsyncMock(return_value=hits if hits is not None else [])
    history = AsyncMock()
    history.append_turn = AsyncMock()
    return masker, personalizer, bedrock, searcher, history


def _hit(score: float) -> SearchHit:
    return SearchHit(chunk_id="c1", source_url="https://x/faq", text="残高照会方法", score=score)


_noop_warm = patch.object(h, "_ensure_cache_warmed", new_callable=AsyncMock)


async def test_happy_path_hit_true() -> None:
    deps = _make_deps(hits=[_hit(0.9)])
    with _noop_warm, patch.object(h, "_build_dependencies", return_value=deps):
        result = await h.handler(
            {"customerId": "cust-1", "userInput": "残高は?", "contactId": "ct1"}, None
        )
    assert result["hit"] is True
    assert result["answer"] == "残高は10万円です。"
    assert result["sources"] == ["https://x/faq"]
    # user + assistant turns persisted.
    assert deps[4].append_turn.await_count == 2


async def test_no_usable_hits_returns_hit_false() -> None:
    # Hit below MIN_HIT_SCORE -> treated as no match.
    deps = _make_deps(hits=[_hit(0.05)])
    with _noop_warm, patch.object(h, "_build_dependencies", return_value=deps):
        result = await h.handler(
            {"customerId": "cust-1", "userInput": "天気は?", "contactId": "ct1"}, None
        )
    assert result["hit"] is False
    assert result["sources"] == []
    assert result["answer"] == h.FALLBACK_ANSWER
    deps[2].generate_answer.assert_not_awaited()


async def test_pii_is_masked_before_embed() -> None:
    deps = _make_deps(masked="[MASKED] です", entities=[{"Type": "NAME"}], hits=[_hit(0.9)])
    with _noop_warm, patch.object(h, "_build_dependencies", return_value=deps):
        await h.handler(
            {"customerId": "cust-1", "userInput": "私は山田太郎です", "contactId": "ct1"},
            None,
        )
    # embed receives the masked text, not the raw PII input.
    deps[2].embed.assert_awaited_once_with("[MASKED] です")


async def test_timeout_returns_fallback() -> None:
    masker, personalizer, bedrock, searcher, history = _make_deps(hits=[_hit(0.9)])

    async def _slow_embed(_text: str) -> list[float]:
        await asyncio.sleep(10)
        return [0.0]

    bedrock.embed = AsyncMock(side_effect=_slow_embed)
    deps = (masker, personalizer, bedrock, searcher, history)
    with (
        _noop_warm,
        patch.object(h, "_build_dependencies", return_value=deps),
        patch.object(h, "PIPELINE_BUDGET_SECONDS", 0.05),
    ):
        result = await h.handler(
            {"customerId": "cust-1", "userInput": "残高は?", "contactId": "ct1"}, None
        )
    assert result == {"answer": h.FALLBACK_ANSWER, "sources": [], "hit": False}


async def test_bedrock_error_returns_fallback() -> None:
    masker, personalizer, bedrock, searcher, history = _make_deps(hits=[_hit(0.9)])
    bedrock.generate_answer = AsyncMock(side_effect=BedrockError("boom"))
    deps = (masker, personalizer, bedrock, searcher, history)
    with _noop_warm, patch.object(h, "_build_dependencies", return_value=deps):
        result = await h.handler(
            {"customerId": "cust-1", "userInput": "残高は?", "contactId": "ct1"}, None
        )
    assert result["hit"] is False
    assert result["answer"] == h.FALLBACK_ANSWER


async def test_empty_input_short_circuits() -> None:
    with patch.object(h, "_build_dependencies") as build:
        result = await h.handler({"customerId": "cust-1", "userInput": "  "}, None)
    assert result["hit"] is False
    build.assert_not_called()

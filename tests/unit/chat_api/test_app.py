"""Unit tests for the chat_api FastAPI streaming app (U-08)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import src.chat_api.app as app_module
from src.chat_api.app import app
from src.common.errors import BedrockError
from src.vector_store.searcher import SearchHit

# --- fakes ------------------------------------------------------------------


class _FakeMasker:
    async def mask(self, text: str) -> tuple[str, list[Any]]:
        return text, []


class _FakePersonalizer:
    async def build_context(self, customer_id: str) -> str:
        return ""


class _FakeHistory:
    def __init__(self) -> None:
        self.turns: list[Any] = []

    async def append_turn(self, customer_id: str, turn: Any) -> None:
        self.turns.append((customer_id, turn))


class _FakeBedrock:
    def __init__(self, deltas: list[str], *, raise_mid: bool = False) -> None:
        self._deltas = deltas
        self._raise_mid = raise_mid

    async def embed(self, text: str) -> list[float]:
        return [0.1] * 1024

    def sources_for(self, chunks: list[dict[str, Any]]) -> list[str]:
        seen: list[str] = []
        for c in chunks:
            url = str(c.get("source_url") or "")
            if url and url not in seen:
                seen.append(url)
        return seen

    async def generate_answer_stream(
        self, query: str, chunks: list[dict[str, Any]], history_text: str, max_tokens: int = 700
    ) -> AsyncIterator[str]:
        for d in self._deltas:
            yield d
        if self._raise_mid:
            raise BedrockError("stream broke")


class _FakeSearcher:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    async def ensure_cache_loaded(self) -> None:
        return None

    async def search(self, query_vec: list[float], top_k: int) -> list[SearchHit]:
        return self._hits


def _hit(score: float, url: str = "https://jibun/loan") -> SearchHit:
    return SearchHit(
        chunk_id="c1", source_url=url, text="住宅ローンの金利情報", score=score,
        title="住宅ローン - じぶん銀行",
    )


def _install(
    monkeypatch: pytest.MonkeyPatch, *, hits: list[SearchHit], bedrock: _FakeBedrock
) -> _FakeHistory:
    history = _FakeHistory()
    searcher = _FakeSearcher(hits)
    monkeypatch.setattr(
        app_module,
        "build_collaborators",
        lambda: (_FakeMasker(), _FakePersonalizer(), bedrock, history),
    )
    # Patch both the global and its factory so the FastAPI lifespan (which runs
    # on TestClient __enter__ and rebuilds the searcher) keeps using the fake.
    monkeypatch.setattr(app_module, "_searcher", searcher)
    monkeypatch.setattr(app_module, "_make_searcher", lambda: searcher)
    return history


def _parse_sse(text: str) -> list[tuple[str, Any]]:
    """Parse raw SSE text into a list of (event, json-decoded-data)."""
    events: list[tuple[str, Any]] = []
    event = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event is not None:
            events.append((event, json.loads(line[len("data: ") :])))
            event = None
    return events


# --- tests ------------------------------------------------------------------


def test_chat_streams_sources_tokens_done(monkeypatch: pytest.MonkeyPatch) -> None:
    bedrock = _FakeBedrock(["住宅ローン", "の金利は", "変動と固定"])
    history = _install(monkeypatch, hits=[_hit(0.8)], bedrock=bedrock)

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "金利を教えて", "sessionId": "s1"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "sources"
    assert kinds[-1] == "done"
    assert dict(events)["sources"] == [
        {"url": "https://jibun/loan", "title": "住宅ローン - じぶん銀行"}
    ]
    tokens = "".join(d for e, d in events if e == "token")
    assert tokens == "住宅ローンの金利は変動と固定"
    assert dict(events)["done"] == {"hit": True}
    # user + assistant turns persisted.
    assert len(history.turns) == 2


def test_chat_no_usable_hits_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    bedrock = _FakeBedrock(["unused"])
    _install(monkeypatch, hits=[_hit(0.1)], bedrock=bedrock)  # below MIN_HIT_SCORE

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "未知の質問"})

    events = _parse_sse(resp.text)
    assert ("sources", []) in events
    assert dict(events)["done"] == {"hit": False}
    tokens = "".join(d for e, d in events if e == "token")
    assert "情報が見つかりませんでした" in tokens  # CHAT_FALLBACK (no operator promise)


def test_chat_midstream_error_emits_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    bedrock = _FakeBedrock(["途中まで"], raise_mid=True)
    _install(monkeypatch, hits=[_hit(0.9)], bedrock=bedrock)

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "金利"})

    kinds = [e for e, _ in _parse_sse(resp.text)]
    assert "error" in kinds


def test_chat_empty_message_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, hits=[_hit(0.9)], bedrock=_FakeBedrock([]))
    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_requires_demo_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_API_KEY", "secret")
    _install(monkeypatch, hits=[_hit(0.9)], bedrock=_FakeBedrock(["ok"]))
    with TestClient(app) as client:
        unauthorized = client.post("/chat", json={"message": "金利"})
        authorized = client.post(
            "/chat", json={"message": "金利"}, headers={"x-demo-key": "secret"}
        )
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, hits=[_hit(0.9)], bedrock=_FakeBedrock([]))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

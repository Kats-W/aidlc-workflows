"""Unit tests for :mod:`src.rag_handler.escalation`."""

from __future__ import annotations

from src.rag_handler import escalation

_QUEUE = "arn:aws:connect:ap-northeast-1:111122223333:instance/abc/queue/xyz"


async def test_escalation_returns_queue_and_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ESCALATION_QUEUE_ARN", _QUEUE)
    result = await escalation.handler(
        {"contactId": "ct1", "reason": "no_knowledge_match"}, None
    )
    assert result == {
        "escalate": True,
        "queue_arn": _QUEUE,
        "reason": "no_knowledge_match",
    }


async def test_escalation_default_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ESCALATION_QUEUE_ARN", _QUEUE)
    result = await escalation.handler({"contactId": "ct1"}, None)
    assert result["reason"] == "no_knowledge_match"
    assert result["escalate"] is True

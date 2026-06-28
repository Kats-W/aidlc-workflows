"""Unit tests for :mod:`src.common.bedrock_client`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.common.bedrock_client import EMBED_DIMENSIONS, BedrockClient
from src.common.errors import BedrockError, BedrockThrottledError, EmbeddingError


def _mock_body(payload: dict) -> MagicMock:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode("utf-8")
    return body


def _client_returning(embedding: list[float]) -> MagicMock:
    client = MagicMock()
    client.invoke_model.return_value = {"body": _mock_body({"embedding": embedding})}
    return client


async def test_embed_success() -> None:
    vec = [0.1] * EMBED_DIMENSIONS
    client = _client_returning(vec)
    bedrock = BedrockClient(client=client)
    result = await bedrock.embed("口座開設について")
    assert result == vec
    # Correct model + request shape.
    kwargs = client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
    sent = json.loads(kwargs["body"])
    assert sent["inputText"] == "口座開設について"
    assert sent["dimensions"] == EMBED_DIMENSIONS


async def test_embed_empty_text_raises() -> None:
    bedrock = BedrockClient(client=MagicMock())
    with pytest.raises(EmbeddingError):
        await bedrock.embed("   ")


async def test_embed_throttling_raises_retryable() -> None:
    client = MagicMock()
    client.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeModel"
    )
    bedrock = BedrockClient(client=client)
    with pytest.raises(BedrockThrottledError) as exc:
        await bedrock.embed("hello")
    assert exc.value.retryable is True


async def test_embed_other_client_error_raises_embedding_error() -> None:
    client = MagicMock()
    client.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad"}}, "InvokeModel"
    )
    bedrock = BedrockClient(client=client)
    with pytest.raises(EmbeddingError):
        await bedrock.embed("hello")


async def test_embed_malformed_response_raises() -> None:
    client = MagicMock()
    client.invoke_model.return_value = {"body": _mock_body({"not_embedding": []})}
    bedrock = BedrockClient(client=client)
    with pytest.raises(EmbeddingError):
        await bedrock.embed("hello")


async def test_embed_empty_vector_raises() -> None:
    client = _client_returning([])
    bedrock = BedrockClient(client=client)
    with pytest.raises(EmbeddingError):
        await bedrock.embed("hello")


# --- generate_answer_stream -------------------------------------------------


def _delta_event(text: str) -> dict:
    """A Bedrock content_block_delta stream event carrying a text delta."""
    payload = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


def _stream_client(events: list[dict]) -> MagicMock:
    client = MagicMock()
    client.invoke_model_with_response_stream.return_value = {"body": iter(events)}
    return client


_CHUNKS = [{"text": "住宅ローンの金利は変動型と固定型があります", "source_url": "https://x/loan"}]


async def _collect(agen) -> list[str]:
    return [chunk async for chunk in agen]


async def test_generate_answer_stream_yields_text_deltas() -> None:
    events = [
        {"chunk": {"bytes": json.dumps({"type": "message_start"}).encode("utf-8")}},
        _delta_event("住宅ローン"),
        _delta_event("の金利は"),
        _delta_event("変動と固定があります"),
        {"chunk": {"bytes": json.dumps({"type": "message_stop"}).encode("utf-8")}},
    ]
    client = _stream_client(events)
    bedrock = BedrockClient(client=client)
    out = await _collect(
        bedrock.generate_answer_stream("金利を教えて", _CHUNKS, history_text="")
    )
    assert "".join(out) == "住宅ローンの金利は変動と固定があります"
    kwargs = client.invoke_model_with_response_stream.call_args.kwargs
    assert kwargs["modelId"] == "jp.anthropic.claude-haiku-4-5-20251001-v1:0"


async def test_generate_answer_stream_empty_raises() -> None:
    # No text deltas at all -> empty answer is an error.
    events = [{"chunk": {"bytes": json.dumps({"type": "message_stop"}).encode("utf-8")}}]
    bedrock = BedrockClient(client=_stream_client(events))
    with pytest.raises(BedrockError):
        await _collect(bedrock.generate_answer_stream("q", _CHUNKS, history_text=""))


async def test_generate_answer_stream_empty_query_raises() -> None:
    bedrock = BedrockClient(client=_stream_client([]))
    with pytest.raises(BedrockError):
        await _collect(bedrock.generate_answer_stream("  ", _CHUNKS, history_text=""))


async def test_generate_answer_stream_throttle_on_open_raises_retryable() -> None:
    client = MagicMock()
    client.invoke_model_with_response_stream.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "InvokeModelWithResponseStream",
    )
    bedrock = BedrockClient(client=client)
    with pytest.raises(BedrockThrottledError):
        await _collect(bedrock.generate_answer_stream("q", _CHUNKS, history_text=""))


async def test_generate_answer_stream_midstream_error_raises() -> None:
    events = [
        _delta_event("部分的な回答"),
        {"modelStreamErrorException": {"message": "stream broke"}},
    ]
    bedrock = BedrockClient(client=_stream_client(events))
    with pytest.raises(BedrockError):
        await _collect(bedrock.generate_answer_stream("q", _CHUNKS, history_text=""))


def test_sources_for_dedupes() -> None:
    chunks = [
        {"text": "a", "source_url": "https://x/1"},
        {"text": "b", "source_url": "https://x/2"},
        {"text": "c", "source_url": "https://x/1"},
        {"text": "d", "source_url": ""},
    ]
    bedrock = BedrockClient(client=MagicMock())
    assert bedrock.sources_for(chunks) == ["https://x/1", "https://x/2"]

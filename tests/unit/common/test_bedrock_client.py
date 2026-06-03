"""Unit tests for :mod:`src.common.bedrock_client`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.common.bedrock_client import EMBED_DIMENSIONS, BedrockClient
from src.common.errors import BedrockThrottledError, EmbeddingError


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

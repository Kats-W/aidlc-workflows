"""Amazon Bedrock client wrapper.

For U-02 this exposes only :meth:`BedrockClient.embed`, which calls Titan Text
Embeddings v2 to produce a 1024-dimension vector for a piece of text. The class
is structured so that U-03 can add ``generate_answer`` and other model calls
without changing the constructor contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import BedrockThrottledError, EmbeddingError

logger = Logger()

#: Titan Text Embeddings v2 model id.
EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
#: Output embedding dimensionality.
EMBED_DIMENSIONS: int = 1024
#: Bedrock error codes that indicate throttling (retryable).
_THROTTLE_CODES: frozenset[str] = frozenset(
    {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}
)


class BedrockClient:
    """Thin async wrapper over the ``bedrock-runtime`` invoke_model API."""

    def __init__(self, client: Any | None = None) -> None:
        """Args:
        client: Optional pre-built boto3 ``bedrock-runtime`` client (tests).
        """
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime")
        self._client = client

    async def embed(self, text: str) -> list[float]:
        """Return the 1024-dim Titan v2 embedding for ``text``.

        Raises:
            EmbeddingError: If the request fails or the response is malformed.
            BedrockThrottledError: If Bedrock throttles the request (retryable).
        """
        if not text or not text.strip():
            raise EmbeddingError("cannot embed empty text")

        payload = json.dumps(
            {"inputText": text, "dimensions": EMBED_DIMENSIONS, "normalize": True}
        )

        def _invoke() -> list[float]:
            try:
                response = self._client.invoke_model(
                    modelId=EMBED_MODEL_ID,
                    accept="application/json",
                    contentType="application/json",
                    body=payload,
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(f"Bedrock throttled embed: {code}") from exc
                raise EmbeddingError(f"Bedrock embed failed: {code}") from exc

            try:
                body = json.loads(response["body"].read())
                vector = body["embedding"]
            except (KeyError, ValueError, TypeError) as exc:
                raise EmbeddingError("malformed Bedrock embedding response") from exc

            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("Bedrock returned an empty embedding")
            return [float(v) for v in vector]

        result = await asyncio.to_thread(_invoke)
        logger.debug("embedded text", extra={"chars": len(text), "dims": len(result)})
        return result

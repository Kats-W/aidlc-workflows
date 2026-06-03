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

from src.common.errors import BedrockError, BedrockThrottledError, EmbeddingError

logger = Logger()

#: Titan Text Embeddings v2 model id.
EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
#: Claude Sonnet 4.6 model id (used for RAG answer generation, U-03).
ANSWER_MODEL_ID: str = "anthropic.claude-sonnet-4-6-20250514-v1:0"
#: Anthropic Messages API version required by Bedrock.
ANTHROPIC_VERSION: str = "bedrock-2023-05-31"
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

    async def generate_answer(
        self,
        query: str,
        context_chunks: list[dict[str, Any]],
        history_text: str,
        max_tokens: int = 1024,
    ) -> tuple[str, list[str]]:
        """Generate a RAG answer with Claude Sonnet 4.6 over the retrieved context.

        Args:
            query: The (PII-masked) customer question.
            context_chunks: Retrieved chunks, each a mapping with at least
                ``text`` and ``source_url`` keys (``SearchHit``-shaped dicts).
            history_text: Pre-formatted prior-conversation text (may be empty).
            max_tokens: Maximum tokens to generate.

        Returns:
            A tuple of ``(answer_text, source_urls)`` where ``source_urls`` is the
            de-duplicated list of source URLs that backed the answer.

        Raises:
            BedrockThrottledError: If Bedrock throttles the request (retryable).
            BedrockError: If the request fails or the response is malformed.
        """
        if not query or not query.strip():
            raise BedrockError("cannot generate an answer for empty query")

        source_urls = self._dedupe_sources(context_chunks)
        prompt = self._build_prompt(query, context_chunks, history_text)
        payload = json.dumps(
            {
                "anthropic_version": ANTHROPIC_VERSION,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        )

        def _invoke() -> str:
            try:
                response = self._client.invoke_model(
                    modelId=ANSWER_MODEL_ID,
                    accept="application/json",
                    contentType="application/json",
                    body=payload,
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(
                        f"Bedrock throttled generate_answer: {code}"
                    ) from exc
                raise BedrockError(f"Bedrock generate_answer failed: {code}") from exc

            try:
                body = json.loads(response["body"].read())
                blocks = body["content"]
                text = "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise BedrockError("malformed Bedrock answer response") from exc

            if not text.strip():
                raise BedrockError("Bedrock returned an empty answer")
            return text

        answer = await asyncio.to_thread(_invoke)
        logger.info(
            "generated answer",
            extra={"chunks": len(context_chunks), "sources": len(source_urls)},
        )
        return answer, source_urls

    async def analyze_gap(self, prompt: str) -> dict[str, Any]:
        """Analyse a knowledge gap (U-06).

        Placeholder implementation returning a structured stub. The full
        implementation belongs to U-06; it is declared here so the shared
        ``BedrockClient`` surface is stable.
        """
        logger.debug("analyze_gap called (placeholder)", extra={"chars": len(prompt)})
        return {"gap": "", "suggestion": "", "confidence": 0.0}

    @staticmethod
    def _dedupe_sources(context_chunks: list[dict[str, Any]]) -> list[str]:
        """Return the source URLs in first-seen order, without duplicates."""
        seen: dict[str, None] = {}
        for chunk in context_chunks:
            url = str(chunk.get("source_url") or "").strip()
            if url and url not in seen:
                seen[url] = None
        return list(seen)

    @staticmethod
    def _build_prompt(
        query: str, context_chunks: list[dict[str, Any]], history_text: str
    ) -> str:
        """Assemble the Japanese RAG prompt: history + references + question."""
        references = "\n\n".join(
            f"[参考{i + 1}] {str(c.get('text') or '').strip()}"
            for i, c in enumerate(context_chunks)
            if str(c.get("text") or "").strip()
        )
        parts: list[str] = [
            "あなたは au じぶん銀行のカスタマーサポート AI です。"
            "以下の参考情報のみに基づき、お客さまの質問に丁寧な日本語で回答してください。"
            "参考情報に答えが含まれない場合は、推測せず「わかりかねます」と回答してください。",
        ]
        if history_text.strip():
            parts.append(f"# 過去の会話\n{history_text.strip()}")
        parts.append(f"# 参考情報\n{references or '参考情報なし'}")
        parts.append(f"# 質問\n{query.strip()}")
        return "\n\n".join(parts)

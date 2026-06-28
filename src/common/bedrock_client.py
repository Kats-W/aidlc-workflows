"""Amazon Bedrock client wrapper.

For U-02 this exposes only :meth:`BedrockClient.embed`, which calls Titan Text
Embeddings v2 to produce a 1024-dimension vector for a piece of text. The class
is structured so that U-03 can add ``generate_answer`` and other model calls
without changing the constructor contract.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import (
    BedrockError,
    BedrockThrottledError,
    EmbeddingError,
    ResponseParseError,
)

logger = Logger()

#: Titan Text Embeddings v2 model id.
EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
#: Claude Sonnet 4.6 JP geographic inference profile (used for the
#: latency-insensitive background generation in U-06: generate_suggestion,
#: analyze_gap). ap-northeast-1 does not support on-demand invocation of the
#: bare foundation-model id for this model; the jp.* inference profile
#: (routes to Tokyo/Osaka) must be used instead. Unlike earlier Claude models,
#: Sonnet 4.6 dropped the date/version suffix (no "-20250514-v1:0").
ANSWER_MODEL_ID: str = "jp.anthropic.claude-sonnet-4-6"
#: Claude Haiku 4.5 JP geographic inference profile (used for the
#: voice/chat RAG answer in U-03: generate_answer). Sonnet 4.6 was too slow
#: to reliably finish within Amazon Connect's 8s Lambda budget; Haiku 4.5
#: trades some answer quality for the latency headroom this path needs.
#: Like earlier Claude models (and unlike Sonnet 4.6), Haiku 4.5 keeps the
#: "-20251001-v1:0" date/version suffix.
RAG_ANSWER_MODEL_ID: str = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
#: Anthropic Messages API version required by Bedrock.
ANTHROPIC_VERSION: str = "bedrock-2023-05-31"
#: Output embedding dimensionality.
EMBED_DIMENSIONS: int = 1024
#: Bedrock error codes that indicate throttling (retryable).
_THROTTLE_CODES: frozenset[str] = frozenset(
    {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}
)
#: Sentinel yielded by the streaming helper when the event stream is exhausted.
_STREAM_END: object = object()


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
                error = exc.response.get("Error", {})
                code = error.get("Code", "")
                detail = error.get("Message", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(f"Bedrock throttled embed: {code}") from exc
                raise EmbeddingError(f"Bedrock embed failed: {code}: {detail}") from exc

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
        """Generate a RAG answer with Claude Haiku 4.5 over the retrieved context.

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
                    modelId=RAG_ANSWER_MODEL_ID,
                    accept="application/json",
                    contentType="application/json",
                    body=payload,
                )
            except ClientError as exc:
                error = exc.response.get("Error", {})
                code = error.get("Code", "")
                detail = error.get("Message", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(
                        f"Bedrock throttled generate_answer: {code}"
                    ) from exc
                raise BedrockError(
                    f"Bedrock generate_answer failed: {code}: {detail}"
                ) from exc

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

    def sources_for(self, context_chunks: list[dict[str, Any]]) -> list[str]:
        """Return the de-duplicated source URLs backing ``context_chunks``.

        Exposed so streaming callers (chat API) can emit the sources up front,
        before any answer tokens, without re-implementing the dedupe logic.
        """
        return self._dedupe_sources(context_chunks)

    async def generate_answer_stream(
        self,
        query: str,
        context_chunks: list[dict[str, Any]],
        history_text: str,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Yield Claude Haiku 4.5 answer text deltas over the retrieved context.

        Same prompt and model as :meth:`generate_answer`, but uses Bedrock's
        ``invoke_model_with_response_stream`` so callers can forward tokens to a
        web client as they arrive (lower time-to-first-token). Source URLs are
        not yielded here; obtain them up front via :meth:`sources_for`.

        Raises:
            BedrockThrottledError: If Bedrock throttles the request (retryable).
            BedrockError: If the request fails or the stream errors.
        """
        if not query or not query.strip():
            raise BedrockError("cannot generate an answer for empty query")

        prompt = self._build_prompt(query, context_chunks, history_text)
        payload = json.dumps(
            {
                "anthropic_version": ANTHROPIC_VERSION,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        )

        def _open() -> Any:
            try:
                response = self._client.invoke_model_with_response_stream(
                    modelId=RAG_ANSWER_MODEL_ID,
                    accept="application/json",
                    contentType="application/json",
                    body=payload,
                )
            except ClientError as exc:
                error = exc.response.get("Error", {})
                code = error.get("Code", "")
                detail = error.get("Message", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(
                        f"Bedrock throttled generate_answer_stream: {code}"
                    ) from exc
                raise BedrockError(
                    f"Bedrock generate_answer_stream failed: {code}: {detail}"
                ) from exc
            return iter(response["body"])

        stream = await asyncio.to_thread(_open)

        def _next() -> Any:
            try:
                return next(stream)
            except StopIteration:
                return _STREAM_END

        emitted = False
        while True:
            event = await asyncio.to_thread(_next)
            if event is _STREAM_END:
                break
            text = self._parse_stream_event(event)
            if text:
                emitted = True
                yield text

        if not emitted:
            raise BedrockError("Bedrock stream returned an empty answer")
        logger.info(
            "streamed answer",
            extra={"chunks": len(context_chunks)},
        )

    @staticmethod
    def _parse_stream_event(event: dict[str, Any]) -> str:
        """Extract a text delta from one Bedrock stream event.

        Returns the delta text for ``content_block_delta`` events and ``""`` for
        non-text events (message_start/stop, etc.). Raises on mid-stream error
        events surfaced by Bedrock.

        Raises:
            BedrockThrottledError: On a mid-stream throttling event.
            BedrockError: On any other mid-stream error event or malformed chunk.
        """
        for err_key, retryable in (
            ("throttlingException", True),
            ("modelStreamErrorException", False),
            ("internalServerException", False),
            ("validationException", False),
        ):
            if err_key in event:
                msg = event[err_key].get("message", err_key)
                if retryable:
                    raise BedrockThrottledError(f"Bedrock stream throttled: {msg}")
                raise BedrockError(f"Bedrock stream error: {msg}")

        chunk = event.get("chunk")
        if not chunk:
            return ""
        try:
            data = json.loads(chunk["bytes"])
        except (KeyError, ValueError, TypeError) as exc:
            raise BedrockError("malformed Bedrock stream chunk") from exc
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return str(delta.get("text", ""))
        return ""

    async def generate_suggestion(self, category: str, max_chars: int = 200) -> str:
        """Generate a concise (<= ``max_chars``) improvement suggestion (U-06).

        Asks Claude Sonnet 4.6 for a short, actionable Japanese website / FAQ
        improvement suggestion for a confusing topic ``category``. The result is
        truncated defensively to ``max_chars`` characters.

        Raises:
            BedrockThrottledError: If Bedrock throttles the request (retryable).
            BedrockError: If the request fails or returns no text.
        """
        prompt = (
            "あなたは au じぶん銀行のナレッジ改善担当です. "
            f"以下のトピックについて, 顧客が理解しやすくなるための"
            f"ウェブサイト/FAQ の改善案を{max_chars}字以内の日本語で1つ提案してください"
            "(前置き・箇条書き記号は不要, 改善案の本文のみ). \n\n"
            f"トピック: {category}"
        )
        payload = json.dumps(
            {
                "anthropic_version": ANTHROPIC_VERSION,
                "max_tokens": 512,
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
                error = exc.response.get("Error", {})
                code = error.get("Code", "")
                detail = error.get("Message", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(
                        f"Bedrock throttled generate_suggestion: {code}"
                    ) from exc
                raise BedrockError(
                    f"Bedrock generate_suggestion failed: {code}: {detail}"
                ) from exc

            try:
                body = json.loads(response["body"].read())
                blocks = body["content"]
                text = "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise BedrockError(
                    "malformed Bedrock generate_suggestion response"
                ) from exc

            if not text.strip():
                raise BedrockError("Bedrock returned an empty suggestion")
            return text.strip()

        text = await asyncio.to_thread(_invoke)
        return text[:max_chars]

    async def analyze_gap(self, summaries: list[str]) -> dict[str, Any]:
        """Analyse knowledge gaps from PII-masked conversation summaries (U-06).

        Sends only the (already PII-masked) conversation summaries to Claude
        Sonnet 4.6 and asks it to classify the topics customers struggled to
        understand. The model is required to answer with a strict JSON object::

            {"categories": [{"name": str, "count": int, "avg_difficulty": float}]}

        Args:
            summaries: PII-masked conversation summaries (raw transcripts are
                never passed). At most the first 50 are forwarded to the model.

        Returns:
            The parsed ``{"categories": [...]}`` mapping.

        Raises:
            BedrockThrottledError: If Bedrock throttles the request (retryable).
            ResponseParseError: If the model response is not valid JSON of the
                expected shape.
            BedrockError: If the request otherwise fails or returns no text.
        """
        prompt = self._build_gap_prompt(summaries)
        payload = json.dumps(
            {
                "anthropic_version": ANTHROPIC_VERSION,
                "max_tokens": 1024,
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
                error = exc.response.get("Error", {})
                code = error.get("Code", "")
                detail = error.get("Message", "")
                if code in _THROTTLE_CODES:
                    raise BedrockThrottledError(
                        f"Bedrock throttled analyze_gap: {code}"
                    ) from exc
                raise BedrockError(
                    f"Bedrock analyze_gap failed: {code}: {detail}"
                ) from exc

            try:
                body = json.loads(response["body"].read())
                blocks = body["content"]
                text = "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise BedrockError("malformed Bedrock analyze_gap response") from exc

            if not text.strip():
                raise BedrockError("Bedrock returned an empty analyze_gap response")
            return text

        text = await asyncio.to_thread(_invoke)
        parsed = self._parse_gap_json(text)
        logger.info(
            "analyzed knowledge gaps",
            extra={
                "summaries": len(summaries),
                "categories": len(parsed.get("categories", [])),
            },
        )
        return parsed

    @staticmethod
    def _build_gap_prompt(summaries: list[str]) -> str:
        """Assemble the Japanese gap-analysis prompt from PII-masked summaries."""
        joined = "\n".join(f"- {s}" for s in summaries[:50])
        return (
            "以下の会話サマリー群(PII除去済み)を分析し, "
            "顧客が理解しにくかったトピックを分類してください. \n\n"
            f"会話サマリー:\n{joined or '(サマリーなし)'}\n\n"
            "以下のJSON形式のみで回答してください(説明文不要):\n"
            '{"categories": [{"name": "トピック名", "count": 件数, '
            '"avg_difficulty": 1.0-5.0スコア}]}'
        )

    @staticmethod
    def _parse_gap_json(text: str) -> dict[str, Any]:
        """Parse the model's JSON response, tolerating surrounding prose.

        Raises:
            ResponseParseError: If no valid JSON object can be extracted.
        """
        candidate = text.strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ResponseParseError("analyze_gap response contained no JSON object")
        try:
            parsed = json.loads(candidate[start : end + 1])
        except (ValueError, TypeError) as exc:
            raise ResponseParseError("analyze_gap response was not valid JSON") from exc
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("categories"), list
        ):
            raise ResponseParseError(
                "analyze_gap JSON missing a 'categories' list"
            )
        return parsed

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
            "あなたは au じぶん銀行のカスタマーサポート AI です. "
            "以下の参考情報のみに基づき, お客さまの質問に丁寧な日本語で回答してください. "
            "参考情報に答えが含まれない場合は, 推測せず「わかりかねます」と回答してください. ",
        ]
        if history_text.strip():
            parts.append(f"# 過去の会話\n{history_text.strip()}")
        parts.append(f"# 参考情報\n{references or '参考情報なし'}")
        parts.append(f"# 質問\n{query.strip()}")
        return "\n\n".join(parts)

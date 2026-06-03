"""PII masking via Amazon Comprehend ``detect_pii_entities``.

:class:`PiiMasker` detects personally identifiable information in free text and
replaces every detected span with the literal ``[MASKED]`` token. All text that
flows into logs, vector search, the LLM prompt, or persisted conversation
history MUST first pass through :meth:`PiiMasker.mask` (U-03 business rule).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ComprehendError

if TYPE_CHECKING:
    from mypy_boto3_comprehend.literals import LanguageCodeType

logger = Logger()

#: Replacement token written in place of every detected PII span.
MASK_TOKEN: str = "[MASKED]"
#: Comprehend caps DetectPiiEntities at 100 KB of UTF-8 bytes per request.
_MAX_BYTES: int = 100_000


class PiiMasker:
    """Detect and mask PII spans in customer-supplied text."""

    def __init__(self, client: Any | None = None) -> None:
        """Args:
        client: Optional pre-built boto3 ``comprehend`` client (tests).
        """
        if client is None:
            client = boto3.client("comprehend")
        self._client = client

    async def mask(
        self, text: str, lang: str = "ja"
    ) -> tuple[str, list[dict[str, Any]]]:
        """Mask every PII entity Comprehend detects in ``text``.

        Args:
            text: Raw input that may contain PII.
            lang: Comprehend language code (default ``"ja"``).

        Returns:
            ``(masked_text, detected_entities)`` where ``detected_entities`` is
            the raw list of Comprehend entity dicts (offsets/type/score).

        Raises:
            ComprehendError: If the Comprehend call fails.
        """
        if not text or not text.strip():
            return text, []

        # Comprehend offsets are byte-based; guard the request-size limit.
        if len(text.encode("utf-8")) > _MAX_BYTES:
            raise ComprehendError("text exceeds Comprehend 100KB limit")

        def _detect() -> list[dict[str, Any]]:
            try:
                response = self._client.detect_pii_entities(
                    Text=text, LanguageCode=cast("LanguageCodeType", lang)
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                raise ComprehendError(f"Comprehend detect_pii failed: {code}") from exc
            entities = cast("list[dict[str, Any]]", response.get("Entities", []))
            return list(entities)

        entities = await asyncio.to_thread(_detect)
        masked = self._apply_mask(text, entities)
        logger.info("pii masked", extra={"entities": len(entities)})
        return masked, entities

    def contains_pii(self, entities: list[dict[str, Any]]) -> bool:
        """Return ``True`` if any PII entity was detected."""
        return len(entities) > 0

    @staticmethod
    def _apply_mask(text: str, entities: list[dict[str, Any]]) -> str:
        """Replace each entity span (by byte offset) with :data:`MASK_TOKEN`."""
        if not entities:
            return text

        raw = text.encode("utf-8")
        # Apply replacements right-to-left so earlier offsets stay valid.
        spans = sorted(
            (
                (int(e["BeginOffset"]), int(e["EndOffset"]))
                for e in entities
                if "BeginOffset" in e and "EndOffset" in e
            ),
            reverse=True,
        )
        token = MASK_TOKEN.encode("utf-8")
        for begin, end in spans:
            if 0 <= begin <= end <= len(raw):
                raw = raw[:begin] + token + raw[end:]
        return raw.decode("utf-8", errors="replace")

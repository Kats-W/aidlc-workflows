"""PII masking via Amazon Comprehend ``detect_pii_entities``.

:class:`PiiMasker` detects personally identifiable information in free text and
replaces every detected span with the literal ``[MASKED]`` token. All text that
flows into logs, vector search, the LLM prompt, or persisted conversation
history MUST first pass through :meth:`PiiMasker.mask` (U-03 business rule).

Comprehend's ``DetectPiiEntities`` only supports ``LanguageCode`` values ``en``
and ``es``; calling it with ``ja`` raises ``ValidationException``. Japanese
input (the default and primary language for this agent) is therefore masked via
:func:`src.common.ja_pii_patterns.mask_japanese_pii` instead, without ever
calling Comprehend.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ComprehendError
from src.common.ja_pii_patterns import MASK_TOKEN, mask_japanese_pii

if TYPE_CHECKING:
    from mypy_boto3_comprehend.literals import LanguageCodeType

logger = Logger()

#: Comprehend caps DetectPiiEntities at 100 KB of UTF-8 bytes per request.
_MAX_BYTES: int = 100_000

__all__ = ["MASK_TOKEN", "PiiMasker"]


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
        """Mask PII spans detected in ``text``.

        Args:
            text: Raw input that may contain PII.
            lang: Language code (default ``"ja"``). ``"ja"`` is masked via
                regex patterns (see module docstring); any other value is
                passed to Comprehend ``DetectPiiEntities`` as ``LanguageCode``.

        Returns:
            ``(masked_text, detected_entities)``. For non-``"ja"`` input,
            ``detected_entities`` is the raw list of Comprehend entity dicts
            (byte offsets/type/score); for ``"ja"`` input it mirrors that shape
            using character offsets (see :func:`mask_japanese_pii`).

        Raises:
            ComprehendError: If the Comprehend call fails (non-``"ja"`` only).
        """
        if not text or not text.strip():
            return text, []

        if lang == "ja":
            # Comprehend's DetectPiiEntities does not support ja (ValidationException);
            # mask Japanese input via regex patterns instead.
            masked, entities = mask_japanese_pii(text)
            logger.info("pii masked", extra={"entities": len(entities), "method": "regex"})
            return masked, entities

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

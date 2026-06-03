"""Personalization context builder (US-6.2).

:class:`Personalizer` reads a customer's recent (PII-masked) conversation turns
from the CustomerHistory table and renders them as a compact text block to seed
the RAG prompt. Anonymous callers get an empty context.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aws_lambda_powertools import Logger

from src.session_manager.history import HistoryRepository

logger = Logger()

#: Sentinel customer id for callers that cannot be identified.
ANONYMOUS: str = "anonymous"


class Personalizer:
    """Build prior-conversation context text for a customer."""

    def __init__(self, history: HistoryRepository) -> None:
        self._history = history

    async def build_context(self, customer_id: str, limit: int = 5) -> str:
        """Return formatted recent-conversation text for ``customer_id``.

        Anonymous callers (or a missing id) yield an empty string. Turns are
        rendered oldest-first as ``顧客: ...`` / ``エージェント: ...`` lines.
        """
        if not customer_id or customer_id == ANONYMOUS:
            return ""

        turns = await self._history.get_recent(customer_id, limit=limit)
        if not turns:
            return ""

        # get_recent returns newest-first; render oldest-first for readability.
        lines: list[str] = []
        for turn in reversed(turns):
            speaker = "顧客" if turn.role == "user" else "エージェント"
            lines.append(f"{speaker}: {turn.text}")
        logger.debug(
            "built personalization context",
            extra={"customer_id": customer_id, "turns": len(lines)},
        )
        return "\n".join(lines)


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Standalone entry point: build context for ``event['customerId']``."""
    customer_id = str(event.get("customerId") or ANONYMOUS)
    limit = int(event.get("limit", 5))
    personalizer = Personalizer(HistoryRepository())
    context_text = await personalizer.build_context(customer_id, limit=limit)
    return {"customerId": customer_id, "context": context_text}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

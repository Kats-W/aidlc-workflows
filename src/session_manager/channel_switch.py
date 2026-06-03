"""ChannelSwitchLambda — voice<->chat handover within one contact (US-4.1/4.2).

The contact flow invokes this when a customer switches channel mid-session (e.g.
escalates from the chatbot to a voice call, or vice versa) while keeping the same
Amazon Connect ``ContactId``. The function reconstructs the in-session context from
the CustomerHistory ``SESSION#`` item and returns a short handover summary so the
target channel can resume the conversation without losing thread.

CustomerHistory ``SESSION#`` schema (shared table):
    PK  ``customerId`` — the ``contactId`` (anonymous sessions use ``ANON#<id>``).
    SK  ``SESSION#<contactId>``
    attributes: ``turns`` (L of ConversationTurn JSON maps), ``channel`` (S),
                ``updatedAt`` (S), ``expiresAt`` (N, 90-day TTL).

All ``text`` persisted here is assumed PII-masked by the caller (U-03 rule).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError, SessionNotFoundError, ValidationError
from src.session_manager.history import ConversationTurn

logger = Logger()

#: Maximum number of turns retained on a SESSION# item (newest kept).
MAX_TURNS: int = 20
#: Seconds in a day (TTL arithmetic).
_SECONDS_PER_DAY: int = 86_400
#: Retention window for SESSION# items.
_TTL_DAYS: int = 90
#: Default channel recorded for a brand-new session.
_DEFAULT_CHANNEL: str = "voice"


@dataclass(frozen=True)
class SessionContext:
    """In-session conversation context for one contact.

    Attributes:
        contact_id: Amazon Connect contact id (also the partition key).
        turns: Ordered conversation turns, oldest first (max :data:`MAX_TURNS`).
        channel: Current channel — ``"voice"`` or ``"chat"``.
        summary: Optional pre-computed handover summary, or ``None``.
    """

    contact_id: str
    turns: list[ConversationTurn]
    channel: str
    summary: str | None


def _turn_to_map(turn: ConversationTurn) -> dict[str, Any]:
    """Serialise a :class:`ConversationTurn` to a DynamoDB-friendly map."""
    return {
        "role": turn.role,
        "text": turn.text,
        "timestamp": turn.timestamp,
        "contactId": turn.contact_id,
        "channel": turn.channel,
    }


def _format_turns(turns: list[ConversationTurn]) -> str:
    """Render turns as ``顧客:``/``AI:`` prefixed lines (oldest first)."""
    lines: list[str] = []
    for turn in turns:
        speaker = "顧客" if turn.role == "user" else "AI"
        lines.append(f"{speaker}: {turn.text}")
    return "\n".join(lines)


def _map_to_turn(item: dict[str, Any]) -> ConversationTurn:
    """Deserialise a DynamoDB map into a :class:`ConversationTurn`."""
    return ConversationTurn(
        role=str(item.get("role", "")),
        text=str(item.get("text", "")),
        timestamp=str(item.get("timestamp", "")),
        contact_id=str(item.get("contactId", "")),
        channel=str(item.get("channel", "")),
    )


class SessionContextManager:
    """Read/append the per-contact ``SESSION#`` context in CustomerHistory."""

    def __init__(self, table: Any | None = None, table_name: str | None = None) -> None:
        """Args:
        table: Optional pre-built boto3 DynamoDB ``Table`` (tests).
        table_name: Table name; falls back to ``CUSTOMER_HISTORY_TABLE_NAME``.
        """
        if table is not None:
            self._table = table
        else:
            name = table_name or os.environ["CUSTOMER_HISTORY_TABLE_NAME"]
            self._table = boto3.resource("dynamodb").Table(name)

    @staticmethod
    def _sk(contact_id: str) -> str:
        return f"SESSION#{contact_id}"

    def _ttl(self) -> int:
        return int(time.time()) + _TTL_DAYS * _SECONDS_PER_DAY

    async def get(self, contact_id: str) -> SessionContext:
        """Return the current :class:`SessionContext` for ``contact_id``.

        Raises:
            ValidationError: If ``contact_id`` is empty.
            SessionNotFoundError: If no ``SESSION#`` item exists.
            DynamoAccessError: If the DynamoDB read fails.
        """
        if not contact_id.strip():
            raise ValidationError("contact_id is required")

        def _get() -> dict[str, Any] | None:
            try:
                response = self._table.get_item(
                    Key={"customerId": contact_id, "sk": self._sk(contact_id)}
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to read session for {contact_id}"
                ) from exc
            item: dict[str, Any] | None = response.get("Item")
            return item

        item = await asyncio.to_thread(_get)
        if item is None:
            raise SessionNotFoundError(f"no session for {contact_id}")

        turns = [_map_to_turn(t) for t in item.get("turns", [])]
        ctx = SessionContext(
            contact_id=contact_id,
            turns=turns,
            channel=str(item.get("channel", _DEFAULT_CHANNEL)),
            summary=(str(item["summary"]) if item.get("summary") else None),
        )
        logger.debug(
            "loaded session", extra={"contact_id": contact_id, "turns": len(turns)}
        )
        return ctx

    async def update(self, contact_id: str, turn: ConversationTurn) -> None:
        """Append ``turn`` to the session, keeping at most :data:`MAX_TURNS`.

        Creates the ``SESSION#`` item if it does not yet exist. The item's
        ``channel`` is set to the appended turn's channel.

        Raises:
            ValidationError: If ``contact_id`` is empty.
            DynamoAccessError: If the DynamoDB read/write fails.
        """
        if not contact_id.strip():
            raise ValidationError("contact_id is required")

        def _read_existing() -> list[dict[str, Any]]:
            try:
                response = self._table.get_item(
                    Key={"customerId": contact_id, "sk": self._sk(contact_id)}
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to read session for {contact_id}"
                ) from exc
            item = response.get("Item")
            return list(item.get("turns", [])) if item else []

        existing = await asyncio.to_thread(_read_existing)
        existing.append(_turn_to_map(turn))
        # Retain only the newest MAX_TURNS turns.
        trimmed = existing[-MAX_TURNS:]
        now = datetime.now(UTC).isoformat()

        def _put() -> None:
            try:
                self._table.put_item(
                    Item={
                        "customerId": contact_id,
                        "sk": self._sk(contact_id),
                        "turns": trimmed,
                        "channel": turn.channel,
                        "updatedAt": now,
                        "expiresAt": self._ttl(),
                    }
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to update session for {contact_id}"
                ) from exc

        await asyncio.to_thread(_put)
        logger.debug(
            "updated session", extra={"contact_id": contact_id, "turns": len(trimmed)}
        )

    async def summarize(self, contact_id: str, last_n: int = 5) -> str:
        """Build a plain-text handover summary from the last ``last_n`` turns.

        The summary joins each turn as ``顧客: ...`` (user) or ``AI: ...``
        (assistant), oldest first, one turn per line.

        Raises:
            SessionNotFoundError: If no session exists for ``contact_id``.
        """
        ctx = await self.get(contact_id)
        recent = ctx.turns[-last_n:] if last_n > 0 else []
        return _format_turns(recent)


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Channel-switch handover handler (US-4.1/4.2).

    Expects ``contactId``, ``channelFrom`` and ``channelTo`` on the event. Loads
    the in-session context and returns a handover summary for the target channel.
    When no session exists yet the handover starts fresh with an empty summary.

    Returns:
        ``{"handover_summary": str, "channel_from": str, "channel_to": str,
        "turn_count": int}``

    Raises:
        ValidationError: If ``contactId`` is missing.
    """
    contact_id = str(event.get("contactId", "")).strip()
    channel_from = str(event.get("channelFrom", "")).strip()
    channel_to = str(event.get("channelTo", "")).strip()
    if not contact_id:
        raise ValidationError("contactId is required")

    manager = SessionContextManager()
    last_n = int(event.get("lastN", 5))
    try:
        ctx = await manager.get(contact_id)
        summary = _format_turns(ctx.turns[-last_n:] if last_n > 0 else [])
        turn_count = len(ctx.turns)
        logger.info(
            "channel handover",
            extra={
                "contact_id": contact_id,
                "channel_from": channel_from,
                "channel_to": channel_to,
                "turn_count": turn_count,
            },
        )
    except SessionNotFoundError:
        # New session — no prior context to carry over.
        summary = ""
        turn_count = 0
        logger.info(
            "channel handover (new session)",
            extra={
                "contact_id": contact_id,
                "channel_from": channel_from,
                "channel_to": channel_to,
            },
        )

    return {
        "handover_summary": summary,
        "channel_from": channel_from,
        "channel_to": channel_to,
        "turn_count": turn_count,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

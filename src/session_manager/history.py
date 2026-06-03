"""Customer conversation-history persistence (CustomerHistory table).

:class:`HistoryRepository` appends conversation turns, reads recent turns for
personalization, and stores per-contact summaries / CSAT into the shared
CustomerHistory DynamoDB table. Items are keyed by ``customerId`` (partition)
and ``sk`` (sort) and expire after :data:`HistoryRepository.TTL_DAYS` days via
the table's ``expiresAt`` TTL attribute.

All ``text`` written here is assumed PII-masked by the caller (U-03 rule).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError

logger = Logger()

#: Seconds in a day (TTL arithmetic).
_SECONDS_PER_DAY: int = 86_400


@dataclass(frozen=True)
class ConversationTurn:
    """A single turn in a customer conversation.

    Attributes:
        role: ``"user"`` or ``"assistant"``.
        text: Turn text — PII-masked before construction.
        timestamp: ISO8601 timestamp of the turn.
        contact_id: Amazon Connect contact id this turn belongs to.
        channel: ``"voice"`` or ``"chat"``.
    """

    role: str
    text: str
    timestamp: str
    contact_id: str
    channel: str


class HistoryRepository:
    """Read/write conversation turns, summaries, and CSAT in CustomerHistory."""

    #: Retention window for every history item.
    TTL_DAYS: int = 90

    def __init__(self, table: Any | None = None, table_name: str | None = None) -> None:
        """Args:
        table: Optional pre-built boto3 DynamoDB ``Table`` (tests).
        table_name: Table name; falls back to ``CUSTOMER_HISTORY_TABLE_NAME``.
        """
        if table is not None:
            self._table = table
        else:
            import os

            name = table_name or os.environ["CUSTOMER_HISTORY_TABLE_NAME"]
            self._table = boto3.resource("dynamodb").Table(name)

    def _ttl(self) -> int:
        """Return the epoch-second TTL (now + :data:`TTL_DAYS` days)."""
        return int(time.time()) + self.TTL_DAYS * _SECONDS_PER_DAY

    async def append_turn(self, customer_id: str, turn: ConversationTurn) -> None:
        """Append ``turn`` for ``customer_id`` under ``TURN#<timestamp>``."""

        def _put() -> None:
            try:
                self._table.put_item(
                    Item={
                        "customerId": customer_id,
                        "sk": f"TURN#{turn.timestamp}",
                        "role": turn.role,
                        "text": turn.text,
                        "timestamp": turn.timestamp,
                        "contactId": turn.contact_id,
                        "channel": turn.channel,
                        "expiresAt": self._ttl(),
                    }
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to append turn for {customer_id}"
                ) from exc

        await asyncio.to_thread(_put)
        logger.debug("appended turn", extra={"customer_id": customer_id})

    async def get_recent(
        self, customer_id: str, limit: int = 5
    ) -> list[ConversationTurn]:
        """Return the most recent ``limit`` turns, newest first."""

        def _query() -> list[ConversationTurn]:
            try:
                response = self._table.query(
                    KeyConditionExpression=Key("customerId").eq(customer_id)
                    & Key("sk").begins_with("TURN#"),
                    ScanIndexForward=False,  # descending => newest first
                    Limit=limit,
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to query history for {customer_id}"
                ) from exc
            return [
                ConversationTurn(
                    role=item.get("role", ""),
                    text=item.get("text", ""),
                    timestamp=item.get("timestamp", ""),
                    contact_id=item.get("contactId", ""),
                    channel=item.get("channel", ""),
                )
                for item in response.get("Items", [])
            ]

        turns = await asyncio.to_thread(_query)
        logger.debug(
            "fetched recent turns", extra={"customer_id": customer_id, "count": len(turns)}
        )
        return turns

    async def save_summary(
        self, customer_id: str, summary: str, contact_id: str
    ) -> None:
        """Persist a per-contact conversation summary under ``SUMMARY#<id>``."""

        def _put() -> None:
            try:
                self._table.put_item(
                    Item={
                        "customerId": customer_id,
                        "sk": f"SUMMARY#{contact_id}",
                        "summary": summary,
                        "contactId": contact_id,
                        "expiresAt": self._ttl(),
                    }
                )
            except ClientError as exc:
                raise DynamoAccessError(
                    f"failed to save summary for {customer_id}"
                ) from exc

        await asyncio.to_thread(_put)
        logger.debug("saved summary", extra={"customer_id": customer_id})

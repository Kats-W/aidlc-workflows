"""CsatHandlerLambda — persist post-contact CSAT scores (US-1.4).

Invoked at the end of a contact flow (or via a survey callback). Validates that
the score is within ``1..5`` and writes it to the CustomerHistory table under a
``CSAT#<contactId>`` sort key with the standard 90-day TTL.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError, ValidationError

logger = Logger()

_MIN_SCORE: int = 1
_MAX_SCORE: int = 5
_SECONDS_PER_DAY: int = 86_400
_TTL_DAYS: int = 90


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Persist a CSAT score for a contact.

    Expects ``customerId``, ``contactId`` and ``score`` (1-5) on the event.

    Raises:
        ValidationError: If the score is missing or outside ``1..5``.
        DynamoAccessError: If the DynamoDB write fails.
    """
    customer_id = str(event.get("customerId", "")).strip()
    contact_id = str(event.get("contactId", "")).strip()
    if not customer_id or not contact_id:
        raise ValidationError("customerId and contactId are required")

    raw_score: Any = event.get("score")
    try:
        score = int(raw_score)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"score must be an integer, got {raw_score!r}") from exc
    if not _MIN_SCORE <= score <= _MAX_SCORE:
        raise ValidationError(f"score {score} out of range [{_MIN_SCORE},{_MAX_SCORE}]")

    table_name = os.environ["CUSTOMER_HISTORY_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    expires_at = int(time.time()) + _TTL_DAYS * _SECONDS_PER_DAY

    def _put() -> None:
        try:
            table.put_item(
                Item={
                    "customerId": customer_id,
                    "sk": f"CSAT#{contact_id}",
                    "score": score,
                    "contactId": contact_id,
                    "expiresAt": expires_at,
                }
            )
        except ClientError as exc:
            raise DynamoAccessError(f"failed to save CSAT for {contact_id}") from exc

    await asyncio.to_thread(_put)
    logger.info("csat saved", extra={"contact_id": contact_id, "score": score})
    return {"saved": True, "contact_id": contact_id, "score": score}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

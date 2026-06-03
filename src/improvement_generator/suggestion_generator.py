"""SuggestionGeneratorLambda — weekly improvement-suggestion generation (US-3.3).

Invoked (asynchronously) by the GapAnalyzerLambda with the week's ranked
knowledge gaps. For the highest-confusion gaps it generates a concise (<= 200
character) Japanese improvement suggestion via Claude Sonnet 4.6 and writes it to
the ImprovementSuggestions table.

Rules:
  - At most :data:`MAX_SUGGESTIONS` (10) suggestions are written per run, taken
    from the highest-confusion gaps first.
  - A gap whose ``targetUrl`` already has a ``status == "pending"`` suggestion is
    skipped (no duplicate pending work for the dashboard reviewer). Existing
    pending suggestions are discovered via the ``gsi_status`` GSI
    (PK=``status``, SK=``priorityScore``).
  - Each new item is written with a ``uuid4`` ``suggestionId``,
    ``status = "pending"``, a 90-day ``ttl``, the ISO ``weekStart`` label, the
    ``targetUrl``, ``improvementText``, ``priorityScore`` and ``createdAt``.
  - Writes use a conditional ``PutItem`` (``attribute_not_exists(suggestionId)``)
    so retries never clobber an existing item.

Returns ``{"generated": int}`` where ``generated`` is in ``[0, 10]``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.bedrock_client import BedrockClient
from src.common.errors import DynamoAccessError

logger = Logger()

#: Maximum number of suggestions generated per weekly run.
MAX_SUGGESTIONS: int = 10

#: Suggestion time-to-live: 90 days.
TTL_DAYS: int = 90

#: Maximum suggestion length in characters.
MAX_SUGGESTION_CHARS: int = 200

#: GSI on ImprovementSuggestions keyed by ``status`` for pending lookups.
STATUS_GSI: str = "gsi_status"

#: Pending status value.
STATUS_PENDING: str = "pending"


def _gap_target_url(category: str) -> str:
    """Derive a stable FAQ target URL for a gap ``category``.

    The dashboard reviewer refines the exact destination; this provides a
    deterministic, category-scoped anchor used for pending de-duplication.
    """
    slug = category.strip().replace(" ", "-").replace("/", "-").lower()
    return f"https://www.jibunbank.co.jp/faq/#{slug}" if slug else ""


def _existing_pending_urls() -> set[str]:
    """Return the set of ``targetUrl`` values that already have a pending item."""
    table_name = os.environ["IMPROVEMENT_SUGGESTIONS_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    urls: set[str] = set()
    try:
        response = table.query(
            IndexName=STATUS_GSI,
            KeyConditionExpression=Key("status").eq(STATUS_PENDING),
        )
    except ClientError as exc:
        raise DynamoAccessError("failed to query pending suggestions") from exc
    for item in response.get("Items", []):
        url = str(item.get("targetUrl") or "")
        if url:
            urls.add(url)
    return urls


def _ttl_epoch(now: datetime) -> int:
    """Return the epoch-seconds TTL value (now + 90 days)."""
    return int((now + timedelta(days=TTL_DAYS)).timestamp())


def _put_suggestion(item: dict[str, Any]) -> bool:
    """Conditionally write one suggestion. Returns ``False`` if it already exists."""
    table_name = os.environ["IMPROVEMENT_SUGGESTIONS_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(suggestionId)",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return False
        raise DynamoAccessError("failed to write improvement suggestion") from exc
    return True


def _select_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the highest-confusion gaps, capped at :data:`MAX_SUGGESTIONS`."""
    ranked = sorted(gaps, key=lambda g: float(g.get("score", 0.0)), reverse=True)
    return ranked[:MAX_SUGGESTIONS]


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for improvement-suggestion generation.

    Args:
        event: ``{"weekStart": str, "gaps": [{"category", "score", "count"}]}``.

    Returns::

        {"generated": int}   # 0..MAX_SUGGESTIONS
    """
    week_start = str(event.get("weekStart") or "")
    gaps = event.get("gaps") or []
    if not gaps:
        logger.info("no gaps to generate suggestions for", extra={"weekStart": week_start})
        return {"generated": 0}

    selected = _select_gaps(gaps)
    existing_urls = await asyncio.to_thread(_existing_pending_urls)

    client = BedrockClient()
    now = datetime.now(UTC)
    ttl = _ttl_epoch(now)
    created_at = now.isoformat()
    generated = 0

    for gap in selected:
        category = str(gap.get("category", ""))
        target_url = _gap_target_url(category)
        if not target_url:
            continue
        if target_url in existing_urls:
            logger.info("skipping duplicate pending suggestion", extra={"url": target_url})
            continue

        text = await client.generate_suggestion(category, MAX_SUGGESTION_CHARS)
        item = {
            "suggestionId": str(uuid.uuid4()),
            "status": STATUS_PENDING,
            "weekStart": week_start,
            "targetUrl": target_url,
            "improvementText": text,
            "priorityScore": _decimalize(gap.get("score", 0.0)),
            "createdAt": created_at,
            "ttl": ttl,
        }
        written = await asyncio.to_thread(_put_suggestion, item)
        if written:
            generated += 1
            existing_urls.add(target_url)

    logger.info(
        "generated improvement suggestions",
        extra={"weekStart": week_start, "generated": generated},
    )
    return {"generated": generated}


def _decimalize(value: Any) -> Any:
    """Convert a float score to a DynamoDB-safe ``Decimal``."""
    from decimal import Decimal

    return Decimal(str(value))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

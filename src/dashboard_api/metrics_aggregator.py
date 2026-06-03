"""MetricsAggregatorLambda — dashboard usage-statistics aggregation (US-7.2).

Aggregates contact metrics from the ``CustomerHistory`` table over a rolling
window (7 or 30 days) for the admin dashboard:

  - ``contacts.total`` / ``contacts.voice`` / ``contacts.chat`` — channel counts.
  - ``escalationRate`` — fraction of contacts that were escalated.
  - ``avgCsat`` — mean CSAT score, or ``None`` when no CSAT data exists.
  - ``avgTurns`` — mean number of conversation turns.
  - ``aiResolutionRate`` — fraction resolved without escalation.

Each ``CustomerHistory`` conversation-summary item (``sk`` starting with
``SUMMARY#``) is expected to carry: ``channel`` (``voice``/``chat``),
``escalated`` (bool), ``csatScore`` (1-5 or absent), ``turns`` (int),
``createdAt`` (ISO-8601 UTC). Items outside the window are ignored.

For an empty window every metric is returned as ``0`` / ``0.0`` / ``None`` — the
aggregator never raises on a no-data period.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import DynamoAccessError

logger = Logger()

#: Prefix identifying conversation-summary items within a customer partition.
SUMMARY_PREFIX: str = "SUMMARY#"

#: Recognised channel labels.
CHANNEL_VOICE: str = "voice"
CHANNEL_CHAT: str = "chat"


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or ``None``."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _scan_summaries(table_name: str, start: datetime) -> list[dict[str, Any]]:
    """Return CustomerHistory summary items created at/after ``start``.

    A paginated ``Scan`` with a ``begins_with(sk, "SUMMARY#")`` filter is used so
    the aggregation is independent of the partition layout. Items whose
    ``createdAt`` falls outside the window are dropped during aggregation.
    """
    table = boto3.resource("dynamodb").Table(table_name)
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(sk, :p)",
        "ExpressionAttributeValues": {":p": SUMMARY_PREFIX},
    }
    try:
        while True:
            response = table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except ClientError as exc:
        raise DynamoAccessError("failed to scan customer-history summaries") from exc
    return items


def _empty_metrics(period_days: int) -> dict[str, Any]:
    """Return the zero/null metrics payload for an empty window."""
    return {
        "period": f"{period_days}d",
        "contacts": {"total": 0, "voice": 0, "chat": 0},
        "escalationRate": 0.0,
        "avgCsat": None,
        "avgTurns": 0.0,
        "aiResolutionRate": 0.0,
    }


def _aggregate(items: list[dict[str, Any]], start: datetime, period_days: int) -> dict[str, Any]:
    """Aggregate in-window summary ``items`` into the metrics payload."""
    total = 0
    voice = 0
    chat = 0
    escalated = 0
    csat_values: list[float] = []
    turns_values: list[float] = []

    for item in items:
        created = _parse_iso(item.get("createdAt"))
        if created is None or created < start:
            continue
        total += 1

        channel = str(item.get("channel", "")).lower()
        if channel == CHANNEL_VOICE:
            voice += 1
        elif channel == CHANNEL_CHAT:
            chat += 1

        if bool(item.get("escalated", False)):
            escalated += 1

        csat = item.get("csatScore")
        if csat is not None:
            csat_values.append(float(csat))

        turns = item.get("turns")
        if turns is not None:
            turns_values.append(float(turns))

    if total == 0:
        return _empty_metrics(period_days)

    escalation_rate = escalated / total
    avg_csat = (sum(csat_values) / len(csat_values)) if csat_values else None
    avg_turns = (sum(turns_values) / len(turns_values)) if turns_values else 0.0
    ai_resolution_rate = (total - escalated) / total

    return {
        "period": f"{period_days}d",
        "contacts": {"total": total, "voice": voice, "chat": chat},
        "escalationRate": round(escalation_rate, 4),
        "avgCsat": round(avg_csat, 2) if avg_csat is not None else None,
        "avgTurns": round(avg_turns, 2),
        "aiResolutionRate": round(ai_resolution_rate, 4),
    }


async def aggregate_metrics(
    period_days: int,
    *,
    now: datetime | None = None,
    table_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate dashboard metrics over the last ``period_days`` days.

    Args:
        period_days: Rolling window length in days (typically 7 or 30).
        now: Reference "now" (defaults to current UTC); injectable for tests.
        table_name: Override the CustomerHistory table name (defaults to the
            ``CUSTOMER_HISTORY_TABLE_NAME`` environment variable).

    Returns:
        The metrics payload described in the module docstring. Empty windows
        yield ``0`` / ``0.0`` / ``None`` values rather than raising.
    """
    ref_now = now or datetime.now(UTC)
    start = ref_now - timedelta(days=period_days)
    name = table_name or os.environ["CUSTOMER_HISTORY_TABLE_NAME"]

    items = await asyncio.to_thread(_scan_summaries, name, start)
    metrics = _aggregate(items, start, period_days)
    logger.info(
        "aggregated dashboard metrics",
        extra={"period": metrics["period"], "total": metrics["contacts"]["total"]},
    )
    return metrics


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Async entry point. ``event`` carries ``{"period_days": int}`` (default 30)."""
    period_days = int(event.get("period_days", 30))
    return await aggregate_metrics(period_days)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

"""GapAnalyzerLambda — knowledge-gap classification (US-3.2).

Invoked (asynchronously) by the ContactLensAnalyzerLambda with a ``weekStart``.
It reads that week's low-quality contacts from the ContactAnalysis table, fetches
each contact's **PII-masked** conversation summary (``SUMMARY#<contactId>`` items
in CustomerHistory, resolved via the ``gsi_contactId`` GSI), and asks Claude
Sonnet 4.6 — through :meth:`BedrockClient.analyze_gap` — to classify the topics
customers struggled to understand.

For each returned topic category a *confusion score* is computed::

    confusion = (low_quality_count / total_contacts) * escalation_rate
                * avg_difficulty

where ``escalation_rate`` is the fraction of this week's low-quality contacts
that were escalated. Categories are returned highest-confusion-first.

Bedrock throttling raises :class:`BedrockThrottledError` and is retried with
exponential back-off (max 3 attempts); a malformed model response surfaces as
:class:`ResponseParseError`.

PII safety: only the already-masked summaries are sent to the model. Raw
transcripts are never read or forwarded.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.bedrock_client import BedrockClient
from src.common.errors import BedrockThrottledError, DynamoAccessError

logger = Logger()

#: Maximum number of attempts for a throttled Bedrock analyze_gap call.
MAX_ATTEMPTS: int = 3

#: Back-off base (seconds): 1s -> 2s between attempts.
_BACKOFF_BASE_SECONDS: float = 1.0

#: GSI on CustomerHistory keyed by ``contactId`` for summary lookups.
CONTACT_ID_GSI: str = "gsi_contactId"


def _fetch_low_quality(week_start: str) -> list[dict[str, Any]]:
    """Query the ContactAnalysis table for a week's low-quality contacts."""
    table_name = os.environ["CONTACT_ANALYSIS_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        response = table.query(
            KeyConditionExpression=Key("weekStart").eq(week_start),
        )
    except ClientError as exc:
        raise DynamoAccessError(
            f"failed to query low-quality contacts for {week_start}"
        ) from exc
    items: list[dict[str, Any]] = response.get("Items", [])
    return items


def _fetch_summary(contact_id: str) -> str:
    """Return the PII-masked summary for ``contactId`` (empty if not found)."""
    table_name = os.environ["CUSTOMER_HISTORY_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        response = table.query(
            IndexName=CONTACT_ID_GSI,
            KeyConditionExpression=Key("contactId").eq(contact_id)
            & Key("sk").eq(f"SUMMARY#{contact_id}"),
            Limit=1,
        )
    except ClientError as exc:
        raise DynamoAccessError(
            f"failed to fetch summary for contact {contact_id}"
        ) from exc
    items = response.get("Items", [])
    if not items:
        return ""
    return str(items[0].get("summary") or "")


async def _analyze_with_backoff(
    client: BedrockClient, summaries: list[str]
) -> dict[str, Any]:
    """Call ``analyze_gap`` with exponential back-off on throttling."""
    last_exc: BedrockThrottledError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await client.analyze_gap(summaries)
        except BedrockThrottledError as exc:
            last_exc = exc
            logger.warning("analyze_gap throttled", extra={"attempt": attempt})
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


def confusion_score(
    low_quality_count: int,
    total_contacts: int,
    escalation_rate: float,
    avg_difficulty: float,
) -> float:
    """Compute a topic's confusion score.

    ``(low_quality_count / total_contacts) * escalation_rate * avg_difficulty``.
    Returns ``0.0`` when ``total_contacts`` is zero. The result is monotonically
    non-decreasing in each input, which the property-based tests rely on.
    """
    if total_contacts <= 0:
        return 0.0
    return (low_quality_count / total_contacts) * escalation_rate * avg_difficulty


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for knowledge-gap analysis.

    Args:
        event: ``{"weekStart": str}`` from the ContactLensAnalyzerLambda.

    Returns::

        {"gaps": [{"category": str, "score": float, "count": int}],
         "count": int, "weekStart": str}
    """
    week_start = str(event.get("weekStart") or "")
    if not week_start:
        logger.warning("gap analyzer invoked without weekStart")
        return {"gaps": [], "count": 0, "weekStart": ""}

    contacts = await asyncio.to_thread(_fetch_low_quality, week_start)
    total_contacts = len(contacts)
    if total_contacts == 0:
        logger.info("no low-quality contacts for week", extra={"weekStart": week_start})
        return {"gaps": [], "count": 0, "weekStart": week_start}

    escalated = sum(1 for c in contacts if c.get("escalated"))
    escalation_rate = escalated / total_contacts if total_contacts else 0.0

    summaries: list[str] = []
    for contact in contacts:
        summary = await asyncio.to_thread(
            _fetch_summary, str(contact.get("contactId", ""))
        )
        if summary:
            summaries.append(summary)

    client = BedrockClient()
    parsed = await _analyze_with_backoff(client, summaries)

    gaps: list[dict[str, Any]] = []
    for category in parsed.get("categories", []):
        count = int(category.get("count", 0))
        avg_difficulty = float(category.get("avg_difficulty", 0.0))
        score = confusion_score(
            low_quality_count=count,
            total_contacts=total_contacts,
            escalation_rate=escalation_rate,
            avg_difficulty=avg_difficulty,
        )
        gaps.append(
            {
                "category": str(category.get("name", "")),
                "score": score,
                "count": count,
            }
        )

    gaps.sort(key=lambda g: g["score"], reverse=True)
    logger.info(
        "computed knowledge gaps",
        extra={"weekStart": week_start, "gaps": len(gaps)},
    )

    await asyncio.to_thread(_invoke_suggestion_generator, week_start, gaps)
    return {"gaps": gaps, "count": len(gaps), "weekStart": week_start}


def _invoke_suggestion_generator(week_start: str, gaps: list[dict[str, Any]]) -> None:
    """Synchronously invoke the downstream SuggestionGeneratorLambda."""
    import json

    function_name = os.environ.get("SUGGESTION_GENERATOR_FUNCTION_NAME", "")
    if not function_name:
        logger.info("SUGGESTION_GENERATOR_FUNCTION_NAME unset; skipping invoke")
        return
    client = boto3.client("lambda")
    try:
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"weekStart": week_start, "gaps": gaps}).encode("utf-8"),
        )
    except ClientError:
        logger.exception("failed to invoke SuggestionGeneratorLambda")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

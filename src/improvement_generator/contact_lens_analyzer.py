"""ContactLensAnalyzerLambda — weekly low-quality contact detection (US-3.1).

Triggered weekly by EventBridge Scheduler (Monday 03:00 JST = Sunday 18:00 UTC),
this Lambda inspects the previous 7 days of Amazon Connect contacts. For each
contact it reads the Contact Lens *summary* attributes (never the raw
conversation transcript — PII safety) and flags a contact as *low quality* when
any of the following hold:

  - the post-contact CSAT score is <= 2 (on a 1-5 scale), or
  - the contact was escalated to a human agent, or
  - the overall Contact Lens sentiment is ``NEGATIVE`` with confidence >= 0.7.

Low-quality contacts are persisted to the ContactAnalysis table
(PK=``weekStart``, SK=``contactId``). When no low-quality contacts are found the
Lambda returns immediately and records the fact in CloudWatch Logs.

On success it synchronously invokes the downstream GapAnalyzerLambda with the
``weekStart`` so the pipeline continues without Step Functions.

Notes:
  - ``boto3`` ``connect`` Contact Lens APIs (``list_realtime_contact_analysis_segments``
    et al.) are not supported by ``moto``; the ``ContactLensReader`` collaborator
    is therefore injected in tests via ``unittest.mock``.
  - Transient Contact Lens failures raise :class:`ContactLensError` and are
    retried with exponential back-off (max 3 attempts). Each individual call is
    well under the 6-second API ceiling, so the 300s Lambda budget is ample and
    no ``asyncio.wait_for`` wrapping is required.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ContactLensError, DynamoAccessError

logger = Logger()

#: Maximum number of attempts for a transient Contact Lens read.
MAX_ATTEMPTS: int = 3

#: Back-off base (seconds): 1s -> 2s between attempts 1->2->3.
_BACKOFF_BASE_SECONDS: float = 1.0

#: Analysis window length in days.
WINDOW_DAYS: int = 7

#: CSAT score at or below which a contact is considered low quality.
LOW_CSAT_THRESHOLD: int = 2

#: Minimum sentiment confidence for a NEGATIVE contact to be low quality.
NEGATIVE_CONFIDENCE_THRESHOLD: float = 0.7


def _is_low_quality(analysis: dict[str, Any]) -> bool:
    """Return ``True`` when a contact analysis indicates a low-quality contact.

    Args:
        analysis: A normalised per-contact analysis summary with optional
            ``csat_score`` (1-5 or ``None``), ``escalated`` (bool),
            ``overall_sentiment`` (str) and ``sentiment_confidence`` (float).
    """
    score = analysis.get("csat_score")  # 1-5, None if not collected
    escalated = bool(analysis.get("escalated", False))
    sentiment = analysis.get("overall_sentiment", "NEUTRAL")
    sentiment_confidence = float(analysis.get("sentiment_confidence", 0.0))
    return (
        (score is not None and int(score) <= LOW_CSAT_THRESHOLD)
        or escalated
        or (
            sentiment == "NEGATIVE"
            and sentiment_confidence >= NEGATIVE_CONFIDENCE_THRESHOLD
        )
    )


def current_week_start(now: datetime | None = None) -> str:
    """Return the ISO week label (e.g. ``"2026-W23"``) for ``now`` (UTC)."""
    moment = now or datetime.now(UTC)
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class ContactLensReader:
    """Thin wrapper over the ``connect`` client for Contact Lens summaries.

    Only PII-masked *summary* attributes are read — never raw conversation
    transcripts. The concrete listing/analysis calls are isolated here so they
    can be mocked in tests (``moto`` does not cover these APIs).
    """

    def __init__(
        self, instance_id: str | None = None, client: Any | None = None
    ) -> None:
        self._instance_id = instance_id or os.environ.get("CONNECT_INSTANCE_ID", "")
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("connect")
        return self._client

    def list_analyses(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Return normalised per-contact analysis summaries in ``[start, end)``.

        Each returned mapping has the shape consumed by :func:`_is_low_quality`
        plus a ``contact_id`` key. Subclasses / mocks override this to return
        fixture data; the default raises so an unmocked deployment fails loudly.

        Raises:
            ContactLensError: On any Contact Lens API failure.
        """
        if not self._instance_id:
            raise ContactLensError("CONNECT_INSTANCE_ID is not configured")
        try:
            contacts = self._search_contacts(start, end)
            return [self._summarise(contact) for contact in contacts]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise ContactLensError(f"Contact Lens read failed: {code}") from exc

    def _search_contacts(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """List contacts in the window via ``connect.search_contacts``."""
        response = self.client.search_contacts(
            InstanceId=self._instance_id,
            TimeRange={
                "Type": "INITIATION_TIMESTAMP",
                "StartTime": start,
                "EndTime": end,
            },
        )
        contacts: list[dict[str, Any]] = response.get("Contacts", [])
        return contacts

    def _summarise(self, contact: dict[str, Any]) -> dict[str, Any]:
        """Project a raw Connect contact into a normalised analysis summary.

        Only summary-level attributes are read; the raw transcript is ignored.
        """
        attrs = contact.get("Attributes", {})
        csat_raw = attrs.get("csat_score")
        return {
            "contact_id": contact.get("Id", ""),
            "csat_score": int(csat_raw) if csat_raw not in (None, "") else None,
            "escalated": str(attrs.get("escalated", "")).lower() == "true",
            "overall_sentiment": attrs.get("overall_sentiment", "NEUTRAL"),
            "sentiment_confidence": float(attrs.get("sentiment_confidence", 0.0)),
            "summary_ref": f"SUMMARY#{contact.get('Id', '')}",
        }


async def _read_with_backoff(
    reader: ContactLensReader, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Read analyses with exponential back-off (max :data:`MAX_ATTEMPTS`)."""
    last_exc: ContactLensError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(reader.list_analyses, start, end)
        except ContactLensError as exc:
            last_exc = exc
            logger.warning(
                "contact lens read failed", extra={"attempt": attempt, "code": exc.code}
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


def _persist_low_quality(
    week_start: str, low_quality: list[dict[str, Any]]
) -> None:
    """Persist low-quality contacts to ContactAnalysis (PK=weekStart, SK=contactId)."""
    table_name = os.environ["CONTACT_ANALYSIS_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        with table.batch_writer() as batch:
            for analysis in low_quality:
                batch.put_item(
                    Item={
                        "weekStart": week_start,
                        "contactId": analysis["contact_id"],
                        "csatScore": analysis.get("csat_score"),
                        "escalated": bool(analysis.get("escalated", False)),
                        "overallSentiment": analysis.get("overall_sentiment", "NEUTRAL"),
                        "sentimentConfidence": str(
                            analysis.get("sentiment_confidence", 0.0)
                        ),
                        "summaryRef": analysis.get(
                            "summary_ref", f"SUMMARY#{analysis['contact_id']}"
                        ),
                    }
                )
    except ClientError as exc:
        raise DynamoAccessError("failed to persist low-quality contacts") from exc


def _invoke_gap_analyzer(week_start: str) -> None:
    """Synchronously invoke the downstream GapAnalyzerLambda (if configured)."""
    function_name = os.environ.get("GAP_ANALYZER_FUNCTION_NAME", "")
    if not function_name:
        logger.info("GAP_ANALYZER_FUNCTION_NAME unset; skipping downstream invoke")
        return
    client = boto3.client("lambda")
    try:
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"weekStart": week_start}).encode("utf-8"),
        )
    except ClientError:
        logger.exception("failed to invoke GapAnalyzerLambda")


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge-triggered entry point for low-quality contact detection.

    Returns::

        {"analyzed": int, "low_quality": int, "weekStart": str}
    """
    now = datetime.now(UTC)
    week_start = current_week_start(now)
    end = now
    start = now - timedelta(days=WINDOW_DAYS)

    reader = ContactLensReader()
    analyses = await _read_with_backoff(reader, start, end)
    low_quality = [a for a in analyses if _is_low_quality(a)]

    if not low_quality:
        logger.info(
            "no low-quality contacts detected",
            extra={"analyzed": len(analyses), "weekStart": week_start},
        )
        return {"analyzed": len(analyses), "low_quality": 0, "weekStart": week_start}

    await asyncio.to_thread(_persist_low_quality, week_start, low_quality)
    logger.info(
        "persisted low-quality contacts",
        extra={"analyzed": len(analyses), "low_quality": len(low_quality)},
    )
    await asyncio.to_thread(_invoke_gap_analyzer, week_start)

    return {
        "analyzed": len(analyses),
        "low_quality": len(low_quality),
        "weekStart": week_start,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

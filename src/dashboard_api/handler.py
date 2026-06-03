"""DashboardApiLambda — admin dashboard HTTP API backend (US-7.1, US-7.2).

Routes the API Gateway **HTTP API** (payload format 2.0) proxy event to the
dashboard operations. Cognito JWT authorization is enforced by the HTTP API's
JWT authorizer, so this Lambda does **not** re-validate the token.

Endpoints::

    GET   /suggestions?week=2026-W23&page=1&limit=10
    PATCH /suggestions/{suggestion_id}   body {"status": "...", "rejectReason": "..."}
    GET   /metrics?period=7d|30d
    GET   /suggestions/csv?week=2026-W23     -> text/csv

Errors are mapped to HTTP status codes::

    NotFoundError      -> 404
    ValidationError    -> 400
    UnauthorizedError  -> 403
    other AppError / * -> 500
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.errors import (
    AppError,
    DynamoAccessError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

logger = Logger()

#: GSI on ImprovementSuggestions keyed by ``weekStart`` for weekly listing.
WEEK_GSI: str = "gsi_week"

#: Allowed suggestion status transitions set by the reviewer.
ALLOWED_STATUSES: frozenset[str] = frozenset({"approved", "rejected", "hold"})

#: Default and maximum page size for suggestion listing.
DEFAULT_LIMIT: int = 10
MAX_LIMIT: int = 100

#: Maximum number of weeks (current + 11 past) the reviewer may browse.
MAX_WEEKS: int = 12

#: CSV export header row.
CSV_HEADER: list[str] = [
    "suggestionId",
    "targetUrl",
    "improvementText",
    "priorityScore",
    "status",
    "createdAt",
]

#: Characters that trigger CSV-injection neutralisation when leading a field.
_CSV_INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@")

_JSON_HEADERS: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def current_week_label(now: datetime | None = None) -> str:
    """Return the ISO-week label for ``now`` (e.g. ``"2026-W23"``)."""
    ref = now or datetime.now(UTC)
    iso_year, iso_week, _ = ref.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _table() -> Any:
    """Return the ImprovementSuggestions DynamoDB table resource."""
    name = os.environ["IMPROVEMENT_SUGGESTIONS_TABLE_NAME"]
    return boto3.resource("dynamodb").Table(name)


def _json_default(value: Any) -> Any:
    """JSON serializer for DynamoDB ``Decimal`` values."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _response(
    status: int, body: Any, *, extra_headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build an HTTP API proxy response with a JSON body."""
    headers = dict(_JSON_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body, default=_json_default, ensure_ascii=False),
    }


def _query_week(week: str) -> list[dict[str, Any]]:
    """Return all suggestions for ``week`` via the ``gsi_week`` GSI."""
    table = _table()
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "IndexName": WEEK_GSI,
        "KeyConditionExpression": Key("weekStart").eq(week),
    }
    try:
        while True:
            response = table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except ClientError as exc:
        raise DynamoAccessError("failed to query suggestions for week") from exc
    return items


def _sorted_by_priority(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``items`` sorted by ``priorityScore`` descending."""
    return sorted(items, key=lambda i: float(i.get("priorityScore", 0)), reverse=True)


def total_pages(total: int, limit: int) -> int:
    """Return the number of pages for ``total`` items of page size ``limit``.

    ``total == 0`` yields ``0`` pages. ``limit`` is assumed ``>= 1``.
    """
    if total <= 0:
        return 0
    return math.ceil(total / limit)


def _csv_safe(value: Any) -> str:
    """Neutralise CSV-injection by prefixing a quote to formula-leading fields."""
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_INJECTION_PREFIXES:
        return "'" + text
    return text


# --------------------------------------------------------------------------- #
# Route handlers
# --------------------------------------------------------------------------- #
async def _list_suggestions(query: dict[str, str]) -> dict[str, Any]:
    """Handle ``GET /suggestions`` (US-7.1)."""
    week = query.get("week") or current_week_label()
    try:
        page = int(query.get("page", "1"))
        limit = int(query.get("limit", str(DEFAULT_LIMIT)))
    except ValueError as exc:
        raise ValidationError("page and limit must be integers") from exc
    if page < 1:
        raise ValidationError("page must be >= 1")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")

    items = await asyncio.to_thread(_query_week, week)
    ordered = _sorted_by_priority(items)
    total = len(ordered)
    start = (page - 1) * limit
    page_items = ordered[start : start + limit]

    return _response(
        200,
        {
            "suggestions": page_items,
            "total": total,
            "page": page,
            "totalPages": total_pages(total, limit),
        },
    )


def _update_suggestion(
    suggestion_id: str, status: str, reject_reason: str | None
) -> dict[str, Any]:
    """Conditionally update one suggestion's status; raise if it does not exist."""
    table = _table()
    now = datetime.now(UTC).isoformat()
    expr = "SET #s = :s, updatedAt = :u"
    names = {"#s": "status"}
    values: dict[str, Any] = {":s": status, ":u": now}
    if status == "rejected" and reject_reason is not None:
        expr += ", rejectReason = :r"
        values[":r"] = reject_reason
    try:
        table.update_item(
            Key={"suggestionId": suggestion_id},
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(suggestionId)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            raise NotFoundError(f"suggestion not found: {suggestion_id}") from exc
        raise DynamoAccessError("failed to update suggestion") from exc
    return {"suggestionId": suggestion_id, "status": status, "updatedAt": now}


async def _patch_suggestion(suggestion_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Handle ``PATCH /suggestions/{id}`` (US-7.1)."""
    if not suggestion_id:
        raise ValidationError("missing suggestion id")
    status = str(body.get("status", ""))
    if status not in ALLOWED_STATUSES:
        raise ValidationError(
            f"status must be one of {sorted(ALLOWED_STATUSES)}; got {status!r}"
        )
    reject_reason = body.get("rejectReason")
    result = await asyncio.to_thread(
        _update_suggestion, suggestion_id, status, reject_reason
    )
    return _response(200, result)


async def _get_metrics(query: dict[str, str]) -> dict[str, Any]:
    """Handle ``GET /metrics?period=7d|30d`` (US-7.2)."""
    period = query.get("period", "7d")
    if period not in {"7d", "30d"}:
        raise ValidationError("period must be '7d' or '30d'")
    period_days = 7 if period == "7d" else 30

    function_name = os.environ["METRICS_AGGREGATOR_FUNCTION_NAME"]
    payload = json.dumps({"period_days": period_days}).encode("utf-8")
    try:
        invoke = await asyncio.to_thread(
            lambda: boto3.client("lambda").invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=payload,
            )
        )
        raw = invoke["Payload"].read()
        metrics = json.loads(raw)
    except (ClientError, json.JSONDecodeError, KeyError) as exc:
        raise AppError("failed to invoke metrics aggregator") from exc
    return _response(200, metrics)


async def _export_csv(query: dict[str, str]) -> dict[str, Any]:
    """Handle ``GET /suggestions/csv?week=`` (US-7.1)."""
    week = query.get("week") or current_week_label()
    items = await asyncio.to_thread(_query_week, week)
    ordered = _sorted_by_priority(items)

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_HEADER)
    for item in ordered:
        writer.writerow([_csv_safe(item.get(col)) for col in CSV_HEADER])

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/csv; charset=utf-8"},
        "body": buffer.getvalue(),
    }


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
_SUGGESTION_ID_RE = re.compile(r"^/suggestions/(?P<id>[^/]+)$")


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """Parse and return the JSON request body (empty dict when absent)."""
    raw = event.get("body")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("request body must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("request body must be a JSON object")
    return parsed


async def _route(event: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the HTTP API event to the matching route handler."""
    http = event["requestContext"]["http"]
    method = http["method"]
    path = http["path"]
    query = event.get("queryStringParameters") or {}

    if method == "GET" and path == "/suggestions":
        return await _list_suggestions(query)
    if method == "GET" and path == "/suggestions/csv":
        return await _export_csv(query)
    if method == "GET" and path == "/metrics":
        return await _get_metrics(query)
    if method == "PATCH":
        match = _SUGGESTION_ID_RE.match(path)
        if match and match.group("id") != "csv":
            body = _parse_body(event)
            return await _patch_suggestion(match.group("id"), body)

    raise NotFoundError(f"no route for {method} {path}")


def _error_status(exc: AppError) -> int:
    """Map an :class:`AppError` to an HTTP status code."""
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, ValidationError):
        return 400
    if isinstance(exc, UnauthorizedError):
        return 403
    return 500


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Async entry point for the dashboard HTTP API.

    Returns an HTTP API proxy response. Application errors are caught and mapped
    to the appropriate status code with a JSON ``{"error", "code"}`` body.
    """
    try:
        return await _route(event)
    except AppError as exc:
        status = _error_status(exc)
        logger.warning(
            "dashboard api error",
            extra={"code": exc.code, "status": status, "detail": exc.message},
        )
        return _response(status, {"error": exc.message, "code": exc.code})
    except Exception:  # convert any unexpected error to a 500 response.
        logger.exception("unhandled dashboard api error")
        return _response(500, {"error": "internal server error", "code": "INTERNAL_ERROR"})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

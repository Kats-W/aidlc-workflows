"""Unit tests for :mod:`src.dashboard_api.handler`.

Covers GET /suggestions, PATCH /suggestions/{id} and GET /metrics happy/error
paths, CSV export, plus a hypothesis property-based test asserting the
``total / limit -> totalPages`` consistency invariant.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from hypothesis import given
from hypothesis import strategies as st
from moto import mock_aws

from src.dashboard_api import handler as h

TABLE = "improvement-suggestions"


def _make_table() -> None:
    ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "suggestionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "suggestionId", "AttributeType": "S"},
            {"AttributeName": "weekStart", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi_week",
                "KeySchema": [{"AttributeName": "weekStart", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )


def _seed(week: str, n: int) -> None:
    table = boto3.resource("dynamodb", region_name="ap-northeast-1").Table(TABLE)
    for i in range(n):
        table.put_item(
            Item={
                "suggestionId": f"sug-{i}",
                "status": "pending",
                "weekStart": week,
                "targetUrl": f"https://www.jibunbank.co.jp/faq/#topic-{i}",
                "improvementText": f"topic-{i} を改善",
                "priorityScore": Decimal(str(i)),
                "createdAt": "2026-06-01T00:00:00+00:00",
            }
        )


def _evt(method: str, path: str, *, query=None, body=None) -> dict:
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("IMPROVEMENT_SUGGESTIONS_TABLE_NAME", TABLE)
    monkeypatch.setenv("METRICS_AGGREGATOR_FUNCTION_NAME", "metrics-aggregator")
    yield


# --------------------------------------------------------------------------- #
# GET /suggestions
# --------------------------------------------------------------------------- #
async def test_list_suggestions_sorted_and_paged() -> None:
    with mock_aws():
        _make_table()
        _seed("2026-W23", 12)
        out = await h.handler(
            _evt("GET", "/suggestions", query={"week": "2026-W23", "page": "1", "limit": "5"}),
            None,
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["total"] == 12
    assert body["page"] == 1
    assert body["totalPages"] == 3
    assert len(body["suggestions"]) == 5
    scores = [s["priorityScore"] for s in body["suggestions"]]
    assert scores == sorted(scores, reverse=True)  # descending


async def test_list_suggestions_defaults_to_current_week() -> None:
    with mock_aws():
        _make_table()
        out = await h.handler(_evt("GET", "/suggestions", query=None), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["total"] == 0
    assert body["totalPages"] == 0


async def test_list_suggestions_invalid_page() -> None:
    with mock_aws():
        _make_table()
        out = await h.handler(
            _evt("GET", "/suggestions", query={"page": "0"}), None
        )
    assert out["statusCode"] == 400


async def test_list_suggestions_invalid_limit() -> None:
    with mock_aws():
        _make_table()
        out = await h.handler(
            _evt("GET", "/suggestions", query={"limit": "999"}), None
        )
    assert out["statusCode"] == 400


# --------------------------------------------------------------------------- #
# PATCH /suggestions/{id}
# --------------------------------------------------------------------------- #
async def test_patch_approve() -> None:
    with mock_aws():
        _make_table()
        _seed("2026-W23", 1)
        out = await h.handler(
            _evt("PATCH", "/suggestions/sug-0", body={"status": "approved"}), None
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["suggestionId"] == "sug-0"
    assert body["status"] == "approved"
    assert "updatedAt" in body


async def test_patch_reject_with_reason() -> None:
    with mock_aws():
        _make_table()
        _seed("2026-W23", 1)
        out = await h.handler(
            _evt(
                "PATCH",
                "/suggestions/sug-0",
                body={"status": "rejected", "rejectReason": "重複"},
            ),
            None,
        )
        assert out["statusCode"] == 200
        item = (
            boto3.resource("dynamodb", region_name="ap-northeast-1")
            .Table(TABLE)
            .get_item(Key={"suggestionId": "sug-0"})["Item"]
        )
    assert item["status"] == "rejected"
    assert item["rejectReason"] == "重複"


async def test_patch_invalid_status() -> None:
    with mock_aws():
        _make_table()
        _seed("2026-W23", 1)
        out = await h.handler(
            _evt("PATCH", "/suggestions/sug-0", body={"status": "bogus"}), None
        )
    assert out["statusCode"] == 400


async def test_patch_not_found() -> None:
    with mock_aws():
        _make_table()
        out = await h.handler(
            _evt("PATCH", "/suggestions/missing", body={"status": "hold"}), None
        )
    assert out["statusCode"] == 404


async def test_patch_invalid_json_body() -> None:
    with mock_aws():
        _make_table()
        evt = _evt("PATCH", "/suggestions/sug-0")
        evt["body"] = "{not json"
        out = await h.handler(evt, None)
    assert out["statusCode"] == 400


# --------------------------------------------------------------------------- #
# GET /metrics
# --------------------------------------------------------------------------- #
async def test_get_metrics_invokes_aggregator() -> None:
    fake_metrics = {"period": "7d", "contacts": {"total": 3, "voice": 1, "chat": 2}}
    payload = MagicMock()
    payload.read.return_value = json.dumps(fake_metrics).encode("utf-8")
    fake_client = MagicMock()
    fake_client.invoke.return_value = {"Payload": payload}

    with patch.object(h.boto3, "client", return_value=fake_client):
        out = await h.handler(_evt("GET", "/metrics", query={"period": "7d"}), None)
    assert out["statusCode"] == 200
    assert json.loads(out["body"]) == fake_metrics
    fake_client.invoke.assert_called_once()


async def test_get_metrics_invalid_period() -> None:
    out = await h.handler(_evt("GET", "/metrics", query={"period": "1y"}), None)
    assert out["statusCode"] == 400


# --------------------------------------------------------------------------- #
# GET /suggestions/csv
# --------------------------------------------------------------------------- #
async def test_export_csv() -> None:
    with mock_aws():
        _make_table()
        _seed("2026-W23", 3)
        out = await h.handler(
            _evt("GET", "/suggestions/csv", query={"week": "2026-W23"}), None
        )
    assert out["statusCode"] == 200
    assert out["headers"]["Content-Type"] == "text/csv; charset=utf-8"
    lines = out["body"].strip().splitlines()
    assert lines[0] == "suggestionId,targetUrl,improvementText,priorityScore,status,createdAt"
    assert len(lines) == 4  # header + 3 rows


def test_csv_safe_neutralises_injection() -> None:
    assert h._csv_safe("=cmd()").startswith("'=")
    assert h._csv_safe("+1").startswith("'+")
    assert h._csv_safe("normal") == "normal"
    assert h._csv_safe(None) == ""


# --------------------------------------------------------------------------- #
# Routing / errors
# --------------------------------------------------------------------------- #
async def test_unknown_route_404() -> None:
    out = await h.handler(_evt("GET", "/nope"), None)
    assert out["statusCode"] == 404


def test_lambda_handler_sync() -> None:
    with mock_aws():
        _make_table()
        out = h.lambda_handler(
            _evt("GET", "/suggestions", query={"week": "2026-W23"}), None
        )
    assert out["statusCode"] == 200


def test_current_week_label_format() -> None:
    from datetime import UTC, datetime

    label = h.current_week_label(datetime(2026, 6, 3, tzinfo=UTC))
    assert label.startswith("2026-W")


# --------------------------------------------------------------------------- #
# Property-based: total / limit -> totalPages consistency
# --------------------------------------------------------------------------- #
@given(
    total=st.integers(min_value=0, max_value=10_000),
    limit=st.integers(min_value=1, max_value=100),
)
def test_total_pages_invariants(total: int, limit: int) -> None:
    pages = h.total_pages(total, limit)
    if total == 0:
        assert pages == 0
        return
    # Every item is covered and no page is empty.
    assert (pages - 1) * limit < total <= pages * limit
    assert pages >= 1

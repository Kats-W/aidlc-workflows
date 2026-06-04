"""Unit tests for :mod:`src.dashboard_api.metrics_aggregator`.

Includes the mandatory zero-data boundary test (empty window must return
``0`` / ``0.0`` / ``None`` rather than raising).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from src.dashboard_api import metrics_aggregator as ma

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
TABLE = "customer-history"


def _make_table() -> None:
    ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[
            {"AttributeName": "customerId", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "customerId", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _put(
    contact_id: str,
    *,
    channel: str,
    escalated: bool,
    csat: int | None,
    turns: int,
    days_ago: float,
) -> None:
    item: dict[str, object] = {
        "customerId": f"cust-{contact_id}",
        "sk": f"SUMMARY#{contact_id}",
        "channel": channel,
        "escalated": escalated,
        "turns": turns,
        "createdAt": (NOW - timedelta(days=days_ago)).isoformat(),
    }
    if csat is not None:
        item["csatScore"] = csat
    boto3.resource("dynamodb", region_name="ap-northeast-1").Table(TABLE).put_item(Item=item)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", TABLE)
    yield


# --------------------------------------------------------------------------- #
# Zero-data boundary (mandatory)
# --------------------------------------------------------------------------- #
async def test_empty_window_returns_zero_metrics() -> None:
    with mock_aws():
        _make_table()
        out = await ma.aggregate_metrics(7, now=NOW)
    assert out == {
        "period": "7d",
        "contacts": {"total": 0, "voice": 0, "chat": 0},
        "escalationRate": 0.0,
        "avgCsat": None,
        "avgTurns": 0.0,
        "aiResolutionRate": 0.0,
    }


async def test_no_csat_data_yields_none_avg() -> None:
    with mock_aws():
        _make_table()
        _put("a", channel="voice", escalated=False, csat=None, turns=3, days_ago=1)
        _put("b", channel="chat", escalated=False, csat=None, turns=5, days_ago=2)
        out = await ma.aggregate_metrics(7, now=NOW)
    assert out["avgCsat"] is None
    assert out["contacts"]["total"] == 2
    assert out["avgTurns"] == 4.0


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
async def test_channel_escalation_and_resolution() -> None:
    with mock_aws():
        _make_table()
        _put("a", channel="voice", escalated=True, csat=2, turns=4, days_ago=1)
        _put("b", channel="chat", escalated=False, csat=5, turns=2, days_ago=2)
        _put("c", channel="voice", escalated=False, csat=4, turns=6, days_ago=3)
        _put("d", channel="chat", escalated=False, csat=3, turns=8, days_ago=4)
        out = await ma.aggregate_metrics(7, now=NOW)

    assert out["contacts"] == {"total": 4, "voice": 2, "chat": 2}
    assert out["escalationRate"] == 0.25
    assert out["aiResolutionRate"] == 0.75
    assert out["avgCsat"] == 3.5
    assert out["avgTurns"] == 5.0
    assert out["period"] == "7d"


async def test_items_outside_window_excluded() -> None:
    with mock_aws():
        _make_table()
        _put("in", channel="voice", escalated=False, csat=5, turns=3, days_ago=2)
        _put("old", channel="chat", escalated=True, csat=1, turns=9, days_ago=20)
        out = await ma.aggregate_metrics(7, now=NOW)
    # Only the in-window contact counts.
    assert out["contacts"]["total"] == 1
    assert out["escalationRate"] == 0.0


async def test_30d_window_includes_more() -> None:
    with mock_aws():
        _make_table()
        _put("in", channel="voice", escalated=False, csat=5, turns=3, days_ago=2)
        _put("mid", channel="chat", escalated=True, csat=1, turns=9, days_ago=20)
        out = await ma.aggregate_metrics(30, now=NOW)
    assert out["period"] == "30d"
    assert out["contacts"]["total"] == 2


def test_parse_iso_handles_bad_value() -> None:
    assert ma._parse_iso("not-a-date") is None
    assert ma._parse_iso("") is None
    assert ma._parse_iso(None) is None


def test_lambda_handler_default_period() -> None:
    with mock_aws():
        _make_table()
        out = ma.lambda_handler({}, None)
    assert out["period"] == "30d"

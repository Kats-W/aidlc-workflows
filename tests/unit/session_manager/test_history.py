"""Unit tests for :mod:`src.session_manager.history` (moto DynamoDB)."""

from __future__ import annotations

import time

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from src.session_manager.history import ConversationTurn, HistoryRepository

TABLE_NAME = "customer-history-test"
_SECONDS_PER_DAY = 86_400


@pytest.fixture()
def history_table():  # type: ignore[no-untyped-def]
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
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
        table.wait_until_exists()
        yield table


def _turn(ts: str, role: str = "user") -> ConversationTurn:
    return ConversationTurn(
        role=role, text=f"text-{ts}", timestamp=ts, contact_id="c1", channel="chat"
    )


async def test_append_turn_sets_sk_and_ttl(history_table) -> None:  # type: ignore[no-untyped-def]
    repo = HistoryRepository(table=history_table)
    before = int(time.time())
    await repo.append_turn("cust-1", _turn("2026-06-03T10:00:00+00:00"))

    item = history_table.get_item(
        Key={"customerId": "cust-1", "sk": "TURN#2026-06-03T10:00:00+00:00"}
    )["Item"]
    assert item["sk"].startswith("TURN#")
    assert item["channel"] == "chat"
    expected = before + HistoryRepository.TTL_DAYS * _SECONDS_PER_DAY
    assert abs(int(item["expiresAt"]) - expected) < 60


async def test_get_recent_descending_and_limit(history_table) -> None:  # type: ignore[no-untyped-def]
    repo = HistoryRepository(table=history_table)
    for i in range(5):
        await repo.append_turn("cust-1", _turn(f"2026-06-03T10:0{i}:00+00:00"))

    recent = await repo.get_recent("cust-1", limit=3)
    assert len(recent) == 3
    timestamps = [t.timestamp for t in recent]
    assert timestamps == sorted(timestamps, reverse=True)  # newest first


async def test_save_summary_sk_format(history_table) -> None:  # type: ignore[no-untyped-def]
    repo = HistoryRepository(table=history_table)
    await repo.save_summary("cust-1", "要約テキスト", "contact-42")
    item = history_table.get_item(
        Key={"customerId": "cust-1", "sk": "SUMMARY#contact-42"}
    )["Item"]
    assert item["summary"] == "要約テキスト"
    assert item["sk"] == "SUMMARY#contact-42"


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(secs=st.integers(min_value=0, max_value=5))
async def test_ttl_is_now_plus_90_days(history_table, secs: int) -> None:  # type: ignore[no-untyped-def]
    repo = HistoryRepository(table=history_table)
    before = int(time.time())
    ts = f"2026-06-03T10:00:0{secs}+00:00"
    await repo.append_turn("cust-ttl", _turn(ts))
    item = history_table.get_item(
        Key={"customerId": "cust-ttl", "sk": f"TURN#{ts}"}
    )["Item"]
    after = int(time.time())
    ttl = int(item["expiresAt"])
    assert before + 90 * _SECONDS_PER_DAY - 60 <= ttl <= after + 90 * _SECONDS_PER_DAY + 60

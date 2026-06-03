"""Unit tests for :mod:`src.improvement_generator.suggestion_generator`."""

from __future__ import annotations

from unittest.mock import patch

import boto3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from src.improvement_generator import suggestion_generator as sg


class _FakeBedrock:
    async def generate_suggestion(self, category: str, max_chars: int = 200) -> str:
        return f"{category}の説明をFAQに追記してください"[:max_chars]


def _make_table() -> None:
    ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
    ddb.create_table(
        TableName="improvement-suggestions",
        KeySchema=[{"AttributeName": "suggestionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "suggestionId", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "priorityScore", "AttributeType": "N"},
            {"AttributeName": "weekStart", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi_status",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "priorityScore", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "gsi_week",
                "KeySchema": [{"AttributeName": "weekStart", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )


def _gaps(n: int) -> list[dict]:
    return [
        {"category": f"topic-{i}", "score": float(n - i), "count": n - i}
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def test_select_gaps_caps_at_max() -> None:
    selected = sg._select_gaps(_gaps(15))
    assert len(selected) == sg.MAX_SUGGESTIONS
    # Highest score first.
    assert selected[0]["score"] >= selected[-1]["score"]


def test_gap_target_url_slug() -> None:
    assert sg._gap_target_url("振込 手数料").endswith("#振込-手数料")
    assert sg._gap_target_url("") == ""


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
async def test_handler_no_gaps() -> None:
    out = await sg.handler({"weekStart": "2026-W23", "gaps": []}, None)
    assert out == {"generated": 0}


async def test_handler_generates_capped_at_10(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("IMPROVEMENT_SUGGESTIONS_TABLE_NAME", "improvement-suggestions")
        _make_table()
        with patch.object(sg, "BedrockClient", return_value=_FakeBedrock()):
            out = await sg.handler({"weekStart": "2026-W23", "gaps": _gaps(20)}, None)
        assert out["generated"] == 10

        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        items = ddb.Table("improvement-suggestions").scan()["Items"]
        assert len(items) == 10
        assert all(i["status"] == "pending" for i in items)
        assert all("ttl" in i for i in items)


async def test_handler_skips_pending_duplicate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("IMPROVEMENT_SUGGESTIONS_TABLE_NAME", "improvement-suggestions")
        _make_table()

        dup_url = sg._gap_target_url("topic-0")
        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        ddb.Table("improvement-suggestions").put_item(
            Item={
                "suggestionId": "existing",
                "status": "pending",
                "targetUrl": dup_url,
                "priorityScore": 99,
                "weekStart": "2026-W22",
            }
        )

        with patch.object(sg, "BedrockClient", return_value=_FakeBedrock()):
            out = await sg.handler({"weekStart": "2026-W23", "gaps": _gaps(3)}, None)
        # topic-0 is skipped (already pending) -> only 2 generated.
        assert out["generated"] == 2


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(n=st.integers(min_value=0, max_value=25))
async def test_generated_always_between_0_and_10(monkeypatch, n: int) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("IMPROVEMENT_SUGGESTIONS_TABLE_NAME", "improvement-suggestions")
        _make_table()
        with patch.object(sg, "BedrockClient", return_value=_FakeBedrock()):
            out = await sg.handler({"weekStart": "2026-W23", "gaps": _gaps(n)}, None)
        assert 0 <= out["generated"] <= sg.MAX_SUGGESTIONS


def test_ttl_is_90_days() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 6, 3, tzinfo=UTC)
    ttl = sg._ttl_epoch(now)
    assert ttl - int(now.timestamp()) == 90 * 24 * 3600


def test_lambda_handler_no_gaps() -> None:
    assert sg.lambda_handler({"weekStart": "2026-W23", "gaps": []}, None) == {
        "generated": 0
    }


def test_decimalize() -> None:
    from decimal import Decimal

    assert sg._decimalize(1.5) == Decimal("1.5")

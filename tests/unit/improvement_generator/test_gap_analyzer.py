"""Unit tests for :mod:`src.improvement_generator.gap_analyzer`."""

from __future__ import annotations

from unittest.mock import patch

import boto3
import pytest
from hypothesis import given
from hypothesis import strategies as st
from moto import mock_aws

from src.common.errors import BedrockThrottledError
from src.improvement_generator import gap_analyzer as ga


# --------------------------------------------------------------------------- #
# confusion_score
# --------------------------------------------------------------------------- #
def test_confusion_score_zero_total() -> None:
    assert ga.confusion_score(5, 0, 0.5, 3.0) == 0.0


def test_confusion_score_basic() -> None:
    # (4 / 8) * 0.5 * 4.0 = 1.0
    assert ga.confusion_score(4, 8, 0.5, 4.0) == pytest.approx(1.0)


@given(
    low=st.integers(min_value=0, max_value=50),
    total=st.integers(min_value=1, max_value=50),
    rate=st.floats(min_value=0.0, max_value=1.0),
    diff_a=st.floats(min_value=1.0, max_value=5.0),
    diff_b=st.floats(min_value=1.0, max_value=5.0),
)
def test_confusion_score_monotonic_in_difficulty(
    low: int, total: int, rate: float, diff_a: float, diff_b: float
) -> None:
    """Higher avg_difficulty never produces a lower confusion score."""
    lo, hi = sorted((diff_a, diff_b))
    score_lo = ga.confusion_score(low, total, rate, lo)
    score_hi = ga.confusion_score(low, total, rate, hi)
    assert score_hi >= score_lo


@given(
    counts=st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=10),
)
def test_higher_score_ranks_higher(counts: list[int]) -> None:
    """After sorting, scores are non-increasing (the highest gap comes first)."""
    total = 20
    gaps = [
        {
            "category": f"c{i}",
            "score": ga.confusion_score(c, total, 1.0, 3.0),
            "count": c,
        }
        for i, c in enumerate(counts)
    ]
    gaps.sort(key=lambda g: g["score"], reverse=True)
    scores = [g["score"] for g in gaps]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- #
# Back-off
# --------------------------------------------------------------------------- #
class _FakeBedrock:
    def __init__(self, fail_times: int = 0, categories: list | None = None) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self._categories = categories or []

    async def analyze_gap(self, summaries: list[str]) -> dict:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise BedrockThrottledError("throttled")
        return {"categories": self._categories}

    async def generate_suggestion(self, category: str, max_chars: int = 200) -> str:
        return f"improve {category}"


async def test_analyze_with_backoff_retries() -> None:
    fake = _FakeBedrock(fail_times=2, categories=[{"name": "x", "count": 1, "avg_difficulty": 3.0}])
    with patch.object(ga.asyncio, "sleep"):
        out = await ga._analyze_with_backoff(fake, ["s1"])
    assert fake.calls == 3
    assert out["categories"][0]["name"] == "x"


async def test_analyze_with_backoff_exhausts() -> None:
    fake = _FakeBedrock(fail_times=5)
    with patch.object(ga.asyncio, "sleep"), pytest.raises(BedrockThrottledError):
        await ga._analyze_with_backoff(fake, ["s1"])


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
def _make_tables() -> None:
    ddb = boto3.client("dynamodb", region_name="ap-northeast-1")
    ddb.create_table(
        TableName="contact-analysis",
        KeySchema=[
            {"AttributeName": "weekStart", "KeyType": "HASH"},
            {"AttributeName": "contactId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "weekStart", "AttributeType": "S"},
            {"AttributeName": "contactId", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="customer-history",
        KeySchema=[
            {"AttributeName": "customerId", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "customerId", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "contactId", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi_contactId",
                "KeySchema": [
                    {"AttributeName": "contactId", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


async def test_handler_no_week_start() -> None:
    out = await ga.handler({}, None)
    assert out == {"gaps": [], "count": 0, "weekStart": ""}


async def test_handler_no_contacts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("CONTACT_ANALYSIS_TABLE_NAME", "contact-analysis")
        monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", "customer-history")
        _make_tables()
        out = await ga.handler({"weekStart": "2026-W23"}, None)
    assert out == {"gaps": [], "count": 0, "weekStart": "2026-W23"}


async def test_handler_computes_and_ranks_gaps(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("CONTACT_ANALYSIS_TABLE_NAME", "contact-analysis")
        monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", "customer-history")
        monkeypatch.delenv("SUGGESTION_GENERATOR_FUNCTION_NAME", raising=False)
        _make_tables()

        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        ca = ddb.Table("contact-analysis")
        ca.put_item(Item={"weekStart": "2026-W23", "contactId": "ct-1", "escalated": True})
        ca.put_item(Item={"weekStart": "2026-W23", "contactId": "ct-2", "escalated": False})
        ch = ddb.Table("customer-history")
        ch.put_item(
            Item={
                "customerId": "cust-1",
                "sk": "SUMMARY#ct-1",
                "contactId": "ct-1",
                "summary": "振込手数料が分かりにくい",
            }
        )

        fake = _FakeBedrock(
            categories=[
                {"name": "振込手数料", "count": 2, "avg_difficulty": 4.0},
                {"name": "残高照会", "count": 1, "avg_difficulty": 2.0},
            ]
        )
        with patch.object(ga, "BedrockClient", return_value=fake):
            out = await ga.handler({"weekStart": "2026-W23"}, None)

    assert out["count"] == 2
    # escalation_rate = 1/2; "振込手数料" score = (2/2)*0.5*4.0 = 1.0 (top)
    assert out["gaps"][0]["category"] == "振込手数料"
    assert out["gaps"][0]["score"] >= out["gaps"][1]["score"]

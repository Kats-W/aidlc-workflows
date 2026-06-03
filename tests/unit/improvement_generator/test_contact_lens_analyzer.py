"""Unit tests for :mod:`src.improvement_generator.contact_lens_analyzer`."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.common.errors import ContactLensError
from src.improvement_generator import contact_lens_analyzer as cla


# --------------------------------------------------------------------------- #
# Low-quality classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        ({"csat_score": 2}, True),
        ({"csat_score": 1}, True),
        ({"csat_score": 3}, False),
        ({"csat_score": None}, False),
        ({"escalated": True}, True),
        ({"overall_sentiment": "NEGATIVE", "sentiment_confidence": 0.7}, True),
        ({"overall_sentiment": "NEGATIVE", "sentiment_confidence": 0.69}, False),
        ({"overall_sentiment": "POSITIVE", "sentiment_confidence": 0.99}, False),
        ({"csat_score": 5, "escalated": False}, False),
    ],
)
def test_is_low_quality(analysis: dict, expected: bool) -> None:
    assert cla._is_low_quality(analysis) is expected


def test_current_week_start_format() -> None:
    label = cla.current_week_start(datetime(2026, 6, 3, tzinfo=UTC))
    assert label == "2026-W23"


# --------------------------------------------------------------------------- #
# ContactLensReader
# --------------------------------------------------------------------------- #
def test_reader_missing_instance_id_raises() -> None:
    reader = cla.ContactLensReader(instance_id="", client=object())
    with pytest.raises(ContactLensError):
        reader.list_analyses(datetime.now(UTC), datetime.now(UTC))


def test_reader_normalises_contacts() -> None:
    class _FakeConnect:
        def search_contacts(self, **kwargs: object) -> dict:
            return {
                "Contacts": [
                    {
                        "Id": "ct-1",
                        "Attributes": {
                            "csat_score": "2",
                            "escalated": "false",
                            "overall_sentiment": "NEGATIVE",
                            "sentiment_confidence": "0.8",
                        },
                    }
                ]
            }

    reader = cla.ContactLensReader(instance_id="inst-1", client=_FakeConnect())
    out = reader.list_analyses(datetime.now(UTC), datetime.now(UTC))
    assert out[0]["contact_id"] == "ct-1"
    assert out[0]["csat_score"] == 2
    assert out[0]["overall_sentiment"] == "NEGATIVE"
    assert out[0]["summary_ref"] == "SUMMARY#ct-1"


def test_reader_wraps_client_error() -> None:
    class _BoomConnect:
        def search_contacts(self, **kwargs: object) -> dict:
            raise ClientError({"Error": {"Code": "InternalServiceError"}}, "SearchContacts")

    reader = cla.ContactLensReader(instance_id="inst-1", client=_BoomConnect())
    with pytest.raises(ContactLensError):
        reader.list_analyses(datetime.now(UTC), datetime.now(UTC))


# --------------------------------------------------------------------------- #
# Back-off
# --------------------------------------------------------------------------- #
async def test_read_with_backoff_retries_then_succeeds() -> None:
    calls = {"n": 0}

    class _FlakyReader(cla.ContactLensReader):
        def list_analyses(self, start, end):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] < 3:
                raise ContactLensError("transient")
            return [{"contact_id": "ct-1", "csat_score": 1}]

    reader = _FlakyReader(instance_id="inst-1", client=object())
    with patch.object(cla.asyncio, "sleep") as sleep:
        out = await cla._read_with_backoff(reader, datetime.now(UTC), datetime.now(UTC))
    assert calls["n"] == 3
    assert out[0]["contact_id"] == "ct-1"
    assert [c.args[0] for c in sleep.call_args_list] == [1.0, 2.0]


async def test_read_with_backoff_exhausts_and_raises() -> None:
    class _AlwaysFails(cla.ContactLensReader):
        def list_analyses(self, start, end):  # type: ignore[no-untyped-def]
            raise ContactLensError("down")

    reader = _AlwaysFails(instance_id="inst-1", client=object())
    with patch.object(cla.asyncio, "sleep"), pytest.raises(ContactLensError):
        await cla._read_with_backoff(reader, datetime.now(UTC), datetime.now(UTC))


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
def _make_table() -> None:
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


async def test_handler_no_low_quality_returns_early(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")

    async def _fake_read(reader, start, end):  # type: ignore[no-untyped-def]
        return [{"contact_id": "ct-1", "csat_score": 5}]

    with patch.object(cla, "_read_with_backoff", _fake_read):
        result = await cla.handler({}, None)
    assert result["analyzed"] == 1
    assert result["low_quality"] == 0


async def test_handler_persists_low_quality(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("CONTACT_ANALYSIS_TABLE_NAME", "contact-analysis")
        monkeypatch.delenv("GAP_ANALYZER_FUNCTION_NAME", raising=False)
        _make_table()

        async def _fake_read(reader, start, end):  # type: ignore[no-untyped-def]
            return [
                {"contact_id": "ct-1", "csat_score": 1, "escalated": True},
                {"contact_id": "ct-2", "csat_score": 5},
            ]

        with patch.object(cla, "_read_with_backoff", _fake_read):
            result = await cla.handler({}, None)

        assert result["analyzed"] == 2
        assert result["low_quality"] == 1

        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        items = ddb.Table("contact-analysis").scan()["Items"]
        assert len(items) == 1
        assert items[0]["contactId"] == "ct-1"
        assert items[0]["escalated"] is True


def test_lambda_handler_wraps(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")

    async def _fake_read(reader, start, end):  # type: ignore[no-untyped-def]
        return []

    with patch.object(cla, "_read_with_backoff", _fake_read):
        result = cla.lambda_handler({}, None)
    assert result["low_quality"] == 0

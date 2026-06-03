"""Unit tests for :mod:`src.session_manager.csat_handler` (moto DynamoDB)."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.common.errors import ValidationError
from src.session_manager import csat_handler

TABLE_NAME = "customer-history-csat-test"


@pytest.fixture()
def csat_env(monkeypatch):  # type: ignore[no-untyped-def]
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
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", TABLE_NAME)
        yield table


@pytest.mark.parametrize("score", [1, 2, 3, 4, 5])
async def test_csat_valid_scores(csat_env, score: int) -> None:  # type: ignore[no-untyped-def]
    event = {"customerId": "cust-1", "contactId": "contact-1", "score": score}
    result = await csat_handler.handler(event, None)
    assert result == {"saved": True, "contact_id": "contact-1", "score": score}
    item = csat_env.get_item(
        Key={"customerId": "cust-1", "sk": "CSAT#contact-1"}
    )["Item"]
    assert int(item["score"]) == score
    assert item["sk"] == "CSAT#contact-1"


@pytest.mark.parametrize("score", [0, 6, -1, 10])
async def test_csat_out_of_range(csat_env, score: int) -> None:  # type: ignore[no-untyped-def]
    event = {"customerId": "cust-1", "contactId": "contact-1", "score": score}
    with pytest.raises(ValidationError):
        await csat_handler.handler(event, None)


async def test_csat_missing_ids(csat_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        await csat_handler.handler({"score": 3}, None)

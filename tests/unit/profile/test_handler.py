"""Unit tests for :mod:`src.profile.handler` (moto DynamoDB)."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.profile import handler as h
from src.profile.hasher import IdentityHasher

TABLE_NAME = "customer-history-test"


@pytest.fixture()
def history_table(monkeypatch):  # type: ignore[no-untyped-def]
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
            GlobalSecondaryIndexes=[
                {
                    "IndexName": h.CUSTOMER_ID_GSI,
                    "KeySchema": [
                        {"AttributeName": "customerId", "KeyType": "HASH"},
                        {"AttributeName": "sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", TABLE_NAME)
        yield table


def _event(au_id: str | None) -> dict:
    attributes = {} if au_id is None else {"auId": au_id}
    return {"Details": {"ContactData": {"Attributes": attributes}}}


async def test_known_customer_returns_tier(history_table) -> None:  # type: ignore[no-untyped-def]
    au_id = "au-user-123"
    customer_id = IdentityHasher.hash_au_id(au_id)
    history_table.put_item(
        Item={"customerId": customer_id, "sk": h.PROFILE_SK, "tier": "gold"}
    )
    result = await h.handler(_event(au_id), None)
    assert result == {"customer_id": customer_id, "tier": "gold", "found": True}


async def test_known_customer_without_profile_item(history_table) -> None:  # type: ignore[no-untyped-def]
    au_id = "au-user-999"
    customer_id = IdentityHasher.hash_au_id(au_id)
    result = await h.handler(_event(au_id), None)
    assert result == {"customer_id": customer_id, "tier": None, "found": False}


async def test_missing_au_id_is_anonymous(history_table) -> None:  # type: ignore[no-untyped-def]
    result = await h.handler(_event(None), None)
    assert result == {"customer_id": "anonymous", "tier": None, "found": False}


async def test_empty_au_id_is_anonymous(history_table) -> None:  # type: ignore[no-untyped-def]
    result = await h.handler(_event("   "), None)
    assert result == {"customer_id": "anonymous", "tier": None, "found": False}


async def test_dynamo_failure_degrades_gracefully(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # No table / bad env -> lookup raises -> handler returns not-found, not raise.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("CUSTOMER_HISTORY_TABLE_NAME", "does-not-exist")
    with mock_aws():
        au_id = "au-user-err"
        customer_id = IdentityHasher.hash_au_id(au_id)
        result = await h.handler(_event(au_id), None)
    assert result == {"customer_id": customer_id, "tier": None, "found": False}


async def test_plaintext_au_id_never_in_result(history_table) -> None:  # type: ignore[no-untyped-def]
    au_id = "au-secret-id"
    result = await h.handler(_event(au_id), None)
    assert au_id not in result["customer_id"]
    assert result["customer_id"] == IdentityHasher.hash_au_id(au_id)


def test_lambda_handler_sync_wrapper(history_table) -> None:  # type: ignore[no-untyped-def]
    result = h.lambda_handler(_event(None), None)
    assert result["customer_id"] == "anonymous"

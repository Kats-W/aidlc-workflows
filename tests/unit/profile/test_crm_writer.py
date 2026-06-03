"""Unit tests for :mod:`src.profile.crm_writer` (httpx mock + moto SQS)."""

from __future__ import annotations

import json
from unittest.mock import patch

import boto3
import httpx
import pytest
from moto import mock_aws

from src.common.errors import ConfigError, CrmApiError
from src.profile import crm_writer as cw
from src.profile.crm_writer import CrmClient


class _FakeSecrets:
    """Minimal Secrets Manager stub returning a fixed API key."""

    def __init__(self, key: str = "test-api-key") -> None:
        self.calls = 0
        self._key = key

    def get_secret_value(self, SecretId: str) -> dict:
        self.calls += 1
        return {"SecretString": self._key}


def _client(handler: httpx.MockTransport, secrets: _FakeSecrets | None = None) -> CrmClient:
    transport = handler
    http = httpx.AsyncClient(transport=transport)
    return CrmClient(
        endpoint="https://crm.example/api/summaries",
        secret_arn="arn:aws:secretsmanager:ap-northeast-1:1:secret:crm",
        http_client=http,
        secrets_client=secrets or _FakeSecrets(),
    )


def _payload(customer_id: str = "cust-abc") -> dict:
    return {
        "customerId": customer_id,
        "contactId": "ct-1",
        "summary": "balance inquiry resolved",
        "channel": "chat",
        "timestamp": "2026-06-03T00:00:00Z",
    }


async def test_post_summary_success_returns_record_id() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["customerId"] == "cust-abc"
        assert request.headers["Authorization"] == "Bearer test-api-key"
        return httpx.Response(201, json={"id": "crm-777"})

    client = _client(httpx.MockTransport(handle))
    record_id = await client.post_summary(_payload())
    assert record_id == "crm-777"


async def test_api_key_is_cached() -> None:
    secrets = _FakeSecrets()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "r1"})

    client = _client(httpx.MockTransport(handle), secrets=secrets)
    await client.post_summary(_payload())
    await client.post_summary(_payload())
    assert secrets.calls == 1  # fetched once, then cached


async def test_4xx_is_terminal_no_retry() -> None:
    attempts = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(CrmApiError):
        await client.post_summary(_payload())
    assert attempts["n"] == 1  # no retry on 4xx


async def test_5xx_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": "r-after-retry"})

    client = _client(httpx.MockTransport(handle))
    with patch.object(cw.asyncio, "sleep") as sleep:
        record_id = await client.post_summary(_payload())
    assert record_id == "r-after-retry"
    assert attempts["n"] == 3
    # back-off applied after attempts 1 and 2: 2s, 4s.
    assert [c.args[0] for c in sleep.call_args_list] == [2.0, 4.0]


async def test_5xx_exhausts_retries_raises() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(httpx.MockTransport(handle))
    with patch.object(cw.asyncio, "sleep"), pytest.raises(CrmApiError):
        await client.post_summary(_payload())


async def test_missing_endpoint_raises_config_error() -> None:
    client = CrmClient(endpoint="", secret_arn="arn:x")
    with pytest.raises(ConfigError):
        await client.post_summary(_payload())


# --------------------------------------------------------------------------- #
# Handler-level tests
# --------------------------------------------------------------------------- #
def _sqs_event(*bodies: dict) -> dict:
    return {"Records": [{"body": json.dumps(b)} for b in bodies]}


async def test_handler_writes_summary() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "crm-1"})

    fake = _client(httpx.MockTransport(handle))
    with patch.object(cw, "CrmClient", return_value=fake):
        result = await cw.handler(_sqs_event(_payload()), None)
    assert result == {"written": True, "crm_record_id": "crm-1"}


async def test_handler_skips_anonymous() -> None:
    called = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"id": "x"})

    fake = _client(httpx.MockTransport(handle))
    with patch.object(cw, "CrmClient", return_value=fake):
        result = await cw.handler(_sqs_event(_payload(customer_id="anonymous")), None)
    assert result == {"written": False, "crm_record_id": None}
    assert called["n"] == 0  # no CRM call for anonymous


async def test_handler_dlqs_on_terminal_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        sqs = boto3.client("sqs", region_name="ap-northeast-1")
        dlq_url = sqs.create_queue(QueueName="crm-dlq")["QueueUrl"]
        monkeypatch.setenv("CRM_DLQ_URL", dlq_url)

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "bad"})

        fake = _client(httpx.MockTransport(handle))
        with patch.object(cw, "CrmClient", return_value=fake):
            result = await cw.handler(_sqs_event(_payload()), None)
        assert result == {"written": False, "crm_record_id": None}

        msgs = sqs.receive_message(QueueUrl=dlq_url).get("Messages", [])
        assert len(msgs) == 1
        assert json.loads(msgs[0]["Body"])["contactId"] == "ct-1"


async def test_handler_malformed_body_dlqs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
        sqs = boto3.client("sqs", region_name="ap-northeast-1")
        dlq_url = sqs.create_queue(QueueName="crm-dlq-2")["QueueUrl"]
        monkeypatch.setenv("CRM_DLQ_URL", dlq_url)
        result = await cw.handler({"Records": [{"body": "not-json{"}]}, None)
    assert result == {"written": False, "crm_record_id": None}


def test_lambda_handler_empty_batch() -> None:
    result = cw.lambda_handler({"Records": []}, None)
    assert result == {"written": False, "crm_record_id": None}

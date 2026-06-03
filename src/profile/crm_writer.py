"""CrmWriterLambda + CrmClient — asynchronous CRM summary write-back (US-6.3).

After a contact ends, a conversation summary is fanned out (DynamoDB Streams ->
EventBridge Pipe -> SQS) to this Lambda, which POSTs the summary to the external
CRM. The write is deliberately decoupled from the live contact path so CRM
latency or outages never affect the customer experience.

Design rules:
  - SQS-triggered. Each record body is JSON:
    ``{customerId, contactId, summary, channel, timestamp}``.
  - ``anonymous`` customers are skipped (nothing is written to the CRM).
  - The CRM API key is fetched once from Secrets Manager and cached for the
    lifetime of the warm Lambda (never hard-coded, never logged).
  - The POST is retried with exponential back-off (2s -> 4s -> 8s, max 3
    attempts) for transient (5xx / network) failures.
  - A terminal failure (4xx, or retries exhausted) raises :class:`CrmApiError`;
    the offending message is forwarded to the SQS dead-letter queue so the batch
    can still make progress.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import boto3
import httpx
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import ConfigError, CrmApiError, SecretsError

logger = Logger()

#: Sentinel customer id that must never be written to the CRM.
ANONYMOUS: str = "anonymous"

#: Maximum number of POST attempts before giving up.
MAX_ATTEMPTS: int = 3

#: Back-off schedule (seconds) applied *after* attempts 1 and 2: 2s, 4s, 8s.
_BACKOFF_BASE_SECONDS: float = 2.0

#: HTTP request timeout for a single CRM POST attempt.
_REQUEST_TIMEOUT_SECONDS: float = 10.0


class CrmClient:
    """Thin async CRM API client with a cached Secrets Manager API key."""

    def __init__(
        self,
        endpoint: str | None = None,
        secret_arn: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        """Args:
        endpoint: CRM endpoint URL; falls back to ``CRM_ENDPOINT``.
        secret_arn: API-key secret ARN; falls back to ``CRM_API_KEY_ARN``.
        http_client: Optional pre-built :class:`httpx.AsyncClient` (tests).
        secrets_client: Optional pre-built boto3 Secrets Manager client (tests).
        """
        self._endpoint = endpoint or os.environ.get("CRM_ENDPOINT", "")
        self._secret_arn = secret_arn or os.environ.get("CRM_API_KEY_ARN", "")
        self._http_client = http_client
        self._secrets_client = secrets_client
        #: Cached API key (populated on first use; never logged).
        self._api_key: str | None = None

    def _get_api_key(self) -> str:
        """Return the CRM API key, fetching+caching it from Secrets Manager."""
        if self._api_key is not None:
            return self._api_key
        if not self._secret_arn:
            raise ConfigError("CRM_API_KEY_ARN is not configured")
        client = self._secrets_client or boto3.client("secretsmanager")
        try:
            secret = client.get_secret_value(SecretId=self._secret_arn)
        except ClientError as exc:
            raise SecretsError("failed to fetch CRM API key") from exc
        self._api_key = str(secret["SecretString"])
        return self._api_key

    async def post_summary(self, payload: dict[str, Any]) -> str:
        """POST a conversation summary to the CRM with exponential back-off.

        Args:
            payload: ``{customerId, contactId, summary, channel, timestamp}``.

        Returns:
            The CRM record id assigned to the written summary.

        Raises:
            ConfigError: If the CRM endpoint is not configured.
            CrmApiError: On a 4xx response, or after exhausting all retries.
        """
        if not self._endpoint:
            raise ConfigError("CRM_ENDPOINT is not configured")

        api_key = self._get_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        try:
            return await self._post_with_retries(client, headers, payload)
        finally:
            if owns_client:
                await client.aclose()

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> str:
        """Drive the retry loop; raises :class:`CrmApiError` on terminal failure."""
        contact_id = str(payload.get("contactId", ""))
        last_exc: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    self._endpoint, json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                # Network / transport error -> transient; retry if budget remains.
                last_exc = exc
                logger.warning(
                    "crm post transport error",
                    extra={"contact_id": contact_id, "attempt": attempt},
                )
            else:
                status = response.status_code
                if 200 <= status < 300:
                    record_id = self._extract_record_id(response)
                    logger.info(
                        "crm summary written",
                        extra={"contact_id": contact_id, "crm_record_id": record_id},
                    )
                    return record_id
                if 400 <= status < 500:
                    # Client error -> terminal, do not retry.
                    raise CrmApiError(
                        f"CRM rejected summary for {contact_id} (status {status})"
                    )
                # 5xx -> transient; retry if budget remains.
                logger.warning(
                    "crm post server error",
                    extra={"contact_id": contact_id, "attempt": attempt, "status": status},
                )
                last_exc = CrmApiError(
                    f"CRM server error for {contact_id} (status {status})"
                )

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

        raise CrmApiError(
            f"CRM write failed for {contact_id} after {MAX_ATTEMPTS} attempts"
        ) from last_exc

    @staticmethod
    def _extract_record_id(response: httpx.Response) -> str:
        """Pull the CRM record id out of a successful response body."""
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            return ""
        if isinstance(body, dict):
            return str(body.get("id") or body.get("recordId") or "")
        return ""


def _send_to_dlq(body: str) -> None:
    """Forward a failed message body to the SQS dead-letter queue."""
    dlq_url = os.environ.get("CRM_DLQ_URL", "")
    if not dlq_url:
        logger.error("CRM_DLQ_URL not configured; cannot DLQ failed message")
        return
    sqs = boto3.client("sqs")
    try:
        sqs.send_message(QueueUrl=dlq_url, MessageBody=body)
    except ClientError:
        logger.exception("failed to forward message to DLQ")


async def _process_record(record: dict[str, Any], client: CrmClient) -> dict[str, Any]:
    """Process a single SQS record; returns the write result.

    On a terminal CRM failure the raw record body is forwarded to the DLQ and a
    ``{"written": False, "crm_record_id": None}`` result is returned so the rest
    of the batch can proceed.
    """
    body = record.get("body", "{}")
    try:
        message = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        logger.error("malformed SQS record body; sending to DLQ")
        _send_to_dlq(body if isinstance(body, str) else json.dumps(body))
        return {"written": False, "crm_record_id": None}

    customer_id = str(message.get("customerId") or "").strip()
    if not customer_id or customer_id == ANONYMOUS:
        logger.info("skipping CRM write for anonymous customer")
        return {"written": False, "crm_record_id": None}

    try:
        record_id = await client.post_summary(message)
    except CrmApiError as exc:
        logger.warning(
            "crm write terminal failure; sending to DLQ",
            extra={"code": exc.code, "detail": exc.message},
        )
        _send_to_dlq(body)
        return {"written": False, "crm_record_id": None}

    return {"written": True, "crm_record_id": record_id or None}


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS-triggered entry point for CRM summary write-back.

    Processes every record in the batch. Each record is independently written to
    the CRM (or skipped/DLQ'd) so a single bad message never fails the batch.

    Returns the result of the *last* processed record::

        {"written": bool, "crm_record_id": str | None}
    """
    records = event.get("Records", [])
    client = CrmClient()
    result: dict[str, Any] = {"written": False, "crm_record_id": None}
    for record in records:
        result = await _process_record(record, client)
    return result


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

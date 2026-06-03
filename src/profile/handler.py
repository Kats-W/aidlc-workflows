"""CustomerProfileLambda — Amazon Connect customer profile attribution (US-5.2).

Invoked from a Connect contact-flow Lambda block at the start of a chat/voice
contact. It derives a stable ``customerId`` from the caller's au ID (passed as a
Connect contact attribute), then looks up the customer's profile (e.g. loyalty
``tier``) in the CustomerHistory table so the contact flow can personalize the
experience.

au ID handling rules (US-5.1 / US-5.2):
  - The au ID arrives at ``event.Details.ContactData.Attributes.auId``.
  - A present, non-empty au ID is hashed to a ``customerId`` via
    :class:`~src.profile.hasher.IdentityHasher`.
  - A missing au ID, or one that fails validation, yields the ``anonymous``
    sentinel (the contact flow still proceeds, just without personalization).
  - The plaintext au ID is NEVER logged.

The whole lookup is bounded by a 6-second budget (Connect allows 8s) and the
handler never raises to Connect: any profile-lookup failure degrades to an
anonymous, not-found result.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.errors import AppError, DynamoAccessError, ValidationError
from src.profile.hasher import IdentityHasher

logger = Logger()

#: Sentinel customer id for callers that cannot be identified.
ANONYMOUS: str = "anonymous"

#: GSI on the CustomerHistory table keyed by ``customerId`` for profile lookups.
CUSTOMER_ID_GSI: str = "gsi-customer-id"

#: Sort key of the profile item within a customer's partition.
PROFILE_SK: str = "PROFILE"

#: Time budget for the profile lookup (Connect allows 8s; keep headroom).
LOOKUP_BUDGET_SECONDS: float = 6.0


def _resolve_customer_id(event: dict[str, Any]) -> str:
    """Derive a ``customerId`` from the Connect event's au ID attribute.

    Returns :data:`ANONYMOUS` when the au ID is absent, empty, or invalid. The
    plaintext au ID is never logged.
    """
    attributes = (
        event.get("Details", {}).get("ContactData", {}).get("Attributes", {})
    )
    au_id = str(attributes.get("auId") or "").strip()
    if not au_id:
        logger.info("no auId on contact; treating caller as anonymous")
        return ANONYMOUS
    try:
        customer_id = IdentityHasher.hash_au_id(au_id)
    except ValidationError as exc:
        # Do not log the raw au_id — only the error code.
        logger.warning("auId failed validation", extra={"code": exc.code})
        return ANONYMOUS
    return customer_id


async def _lookup_profile(customer_id: str) -> dict[str, Any]:
    """Fetch the customer's profile item from CustomerHistory via the GSI."""
    table_name = os.environ["CUSTOMER_HISTORY_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)

    def _query() -> dict[str, Any]:
        try:
            response = table.query(
                IndexName=CUSTOMER_ID_GSI,
                KeyConditionExpression=Key("customerId").eq(customer_id)
                & Key("sk").eq(PROFILE_SK),
                Limit=1,
            )
        except ClientError as exc:
            raise DynamoAccessError(
                f"failed to look up profile for {customer_id}"
            ) from exc
        items = response.get("Items", [])
        return items[0] if items else {}

    return await asyncio.to_thread(_query)


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Connect contact-flow entry point for customer profile attribution.

    Returns a payload the contact flow can set as contact attributes::

        {"customer_id": str, "tier": str | None, "found": bool}

    Never raises to Connect: an anonymous caller, or any lookup failure, yields
    ``{"customer_id": <id-or-anonymous>, "tier": None, "found": False}``.
    """
    customer_id = _resolve_customer_id(event)

    if customer_id == ANONYMOUS:
        return {"customer_id": ANONYMOUS, "tier": None, "found": False}

    try:
        item = await asyncio.wait_for(
            _lookup_profile(customer_id), timeout=LOOKUP_BUDGET_SECONDS
        )
    except TimeoutError:
        logger.warning("profile lookup timed out", extra={"customer_id": customer_id})
        return {"customer_id": customer_id, "tier": None, "found": False}
    except AppError as exc:
        logger.warning(
            "profile lookup failed",
            extra={"customer_id": customer_id, "code": exc.code},
        )
        return {"customer_id": customer_id, "tier": None, "found": False}

    if not item:
        logger.info("no profile found", extra={"customer_id": customer_id})
        return {"customer_id": customer_id, "tier": None, "found": False}

    tier = item.get("tier")
    logger.info(
        "profile resolved",
        extra={"customer_id": customer_id, "found": True},
    )
    return {
        "customer_id": customer_id,
        "tier": str(tier) if tier is not None else None,
        "found": True,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

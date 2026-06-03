"""EscalationLambda — route low-confidence contacts to a human agent (US-1.3).

Invoked from the Connect contact flow when the RAG handler reported ``hit=False``
(no usable answer). Returns the contact attributes that the flow uses to transfer
the caller to the human escalation queue.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from aws_lambda_powertools import Logger

logger = Logger()

#: Default reason recorded when none is supplied on the event.
_DEFAULT_REASON: str = "no_knowledge_match"


async def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return escalation attributes for the contact flow.

    Reads the target queue ARN from ``ESCALATION_QUEUE_ARN`` and echoes a reason
    (defaulting to ``no_knowledge_match``) so the flow can transfer the caller.
    """
    reason = str(event.get("reason") or _DEFAULT_REASON)
    queue_arn = os.environ.get("ESCALATION_QUEUE_ARN", "")
    logger.info(
        "escalating contact",
        extra={"reason": reason, "contact_id": event.get("contactId", "")},
    )
    return {"escalate": True, "queue_arn": queue_arn, "reason": reason}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Synchronous Lambda entry point (wraps the async :func:`handler`)."""
    return asyncio.run(handler(event, context))

"""Persisted BFS crawl state (queue + visited set) across Lambda invocations.

CrawlerLambda runs on a weekly schedule with a ~14-minute budget per
invocation, far too short to traverse the entire site graph in one pass.
:class:`CrawlStateStore` persists the BFS frontier (``queue``) and the set of
URLs already visited in the current crawl cycle to S3, so each invocation
resumes the BFS where the previous one left off instead of restarting from the
seed URLs every time. When the queue is exhausted (a full cycle has visited
every reachable page), the caller starts a fresh cycle from the seed URLs to
re-crawl the site for diff detection.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from src.common.errors import S3AccessError

logger = Logger()

#: Object key under which the BFS state JSON document is stored.
_STATE_KEY = "_state/bfs_state.json"


class CrawlStateStore:
    """Persists BFS ``queue``/``visited`` state to S3 between invocations."""

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        """Args:
        bucket: Target S3 bucket name (the crawl-content bucket).
        client: Optional pre-built boto3 S3 client (injected in tests).
        """
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    async def load(self) -> tuple[deque[str], set[str]] | None:
        """Return the persisted ``(queue, visited)``, or ``None`` if absent or invalid."""

        def _load() -> tuple[deque[str], set[str]] | None:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=_STATE_KEY)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    return None
                logger.exception(
                    "failed to load BFS state",
                    extra={"bucket": self._bucket, "key": _STATE_KEY, "error_code": code},
                )
                raise S3AccessError("failed to load BFS state") from exc

            try:
                data = json.loads(response["Body"].read().decode("utf-8"))
                return deque(data["queue"]), set(data["visited"])
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("BFS state object is corrupt; starting a new crawl cycle")
                return None

        return await asyncio.to_thread(_load)

    async def save(self, queue: deque[str], visited: set[str]) -> None:
        """Persist ``queue``/``visited`` for the next invocation."""

        def _save() -> None:
            body = json.dumps({"queue": list(queue), "visited": sorted(visited)}).encode("utf-8")
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=_STATE_KEY,
                    Body=body,
                    ContentType="application/json",
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                logger.exception(
                    "failed to save BFS state",
                    extra={"bucket": self._bucket, "key": _STATE_KEY, "error_code": code},
                )
                raise S3AccessError("failed to save BFS state") from exc

        await asyncio.to_thread(_save)
        logger.info(
            "BFS state saved", extra={"queue_size": len(queue), "visited_size": len(visited)}
        )

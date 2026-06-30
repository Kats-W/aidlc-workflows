"""Unit tests for :mod:`src.crawler.handler` URL normalization and BFS state."""

from __future__ import annotations

import json
from collections import deque
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from src.crawler.differ import DiffResult
from src.crawler.handler import (
    _EMBEDDER_BATCH_SIZE,
    _initial_state,
    _invoke_embedder,
    _load_state,
    _normalize_url,
)
from src.crawler.parser import ContentChunk
from src.crawler.state_store import CrawlStateStore


def test_normalize_strips_fragment() -> None:
    assert _normalize_url("https://www.jibunbank.co.jp/path#section") == (
        "https://www.jibunbank.co.jp/path"
    )


def test_normalize_defaults_empty_path_to_root() -> None:
    assert _normalize_url("https://www.jibunbank.co.jp") == "https://www.jibunbank.co.jp/"


def test_normalize_forces_https_scheme() -> None:
    # http and https of the same page canonicalize to one https URL (no twins).
    assert _normalize_url("http://www.jibunbank.co.jp/pc/x.html") == (
        "https://www.jibunbank.co.jp/pc/x.html"
    )


def test_normalize_strips_query_on_main_site() -> None:
    assert _normalize_url("https://www.jibunbank.co.jp/news/?utm_source=top&page=2") == (
        "https://www.jibunbank.co.jp/news/"
    )


def test_normalize_keeps_id_param_on_faq_host() -> None:
    assert _normalize_url("https://help.jibunbank.co.jp/?id=1234") == (
        "https://help.jibunbank.co.jp/?id=1234"
    )


def test_normalize_drops_non_id_params_on_faq_host() -> None:
    assert _normalize_url("https://help.jibunbank.co.jp/?id=1234&utm_source=search") == (
        "https://help.jibunbank.co.jp/?id=1234"
    )


def test_normalize_drops_query_entirely_when_no_id_on_faq_host() -> None:
    assert _normalize_url("https://help.jibunbank.co.jp/?category=account") == (
        "https://help.jibunbank.co.jp/"
    )


_SEEDS = ["https://www.jibunbank.co.jp/", "https://help.jibunbank.co.jp/"]


def test_initial_state_starts_fresh_when_no_state_persisted() -> None:
    queue, visited = _initial_state(None, _SEEDS)
    assert list(queue) == [_normalize_url(u) for u in _SEEDS]
    assert visited == set()


def test_initial_state_resumes_from_persisted_state() -> None:
    loaded = (deque(["https://www.jibunbank.co.jp/news/"]), {"https://www.jibunbank.co.jp/"})
    queue, visited = _initial_state(loaded, _SEEDS)
    assert queue == loaded[0]
    assert visited == loaded[1]


def test_initial_state_starts_new_cycle_when_persisted_queue_is_empty() -> None:
    loaded = (deque[str](), {"https://www.jibunbank.co.jp/", "https://help.jibunbank.co.jp/"})
    queue, visited = _initial_state(loaded, _SEEDS)
    assert list(queue) == [_normalize_url(u) for u in _SEEDS]
    assert visited == set()


class _DenyingS3Client:
    """Fake S3 client whose ``get_object`` always fails with AccessDenied."""

    def get_object(self, **kwargs: object) -> dict[str, object]:
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")


async def test_load_state_falls_back_to_fresh_cycle_on_s3_access_error() -> None:
    store = CrawlStateStore(bucket="any-bucket", client=_DenyingS3Client())
    queue, visited = await _load_state(store, _SEEDS)
    assert list(queue) == [_normalize_url(u) for u in _SEEDS]
    assert visited == set()


def test_invoke_embedder_batches_upserts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EMBEDDER_FUNCTION_NAME", "embedder-fn")
    added = [
        ContentChunk(
            chunk_id=f"page#{i}",
            source_url="https://x/faq",
            index=i,
            text=f"chunk {i}",
            content_hash=f"h{i}",
        )
        for i in range(_EMBEDDER_BATCH_SIZE + 1)  # spans 2 batches
    ]
    result = DiffResult(added=added, changed=[], deleted=[])

    lambda_client = MagicMock()
    with patch("src.crawler.handler.boto3.client", return_value=lambda_client):
        _invoke_embedder(result)

    assert lambda_client.invoke.call_count == 2
    payloads = [
        json.loads(call.kwargs["Payload"].decode("utf-8"))
        for call in lambda_client.invoke.call_args_list
    ]
    assert len(payloads[0]["upsert"]) == _EMBEDDER_BATCH_SIZE
    assert len(payloads[1]["upsert"]) == 1
    assert "rebuildCache" not in payloads[0]
    assert "rebuildCache" not in payloads[1]

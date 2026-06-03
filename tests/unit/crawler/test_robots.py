"""Unit tests for :mod:`src.crawler.robots`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.common.errors import FetchTimeoutError
from src.crawler.robots import RobotsTxtGuard

ROBOTS_BODY = """
User-agent: AuJibunBankBot
Disallow: /private/

User-agent: *
Disallow: /admin/
""".strip()


def _mock_client(response: httpx.Response) -> MagicMock:
    """Build a mock AsyncClient context manager returning ``response``."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def test_load_and_allow() -> None:
    response = httpx.Response(200, text=ROBOTS_BODY)
    guard = RobotsTxtGuard()
    with patch("src.crawler.robots.httpx.AsyncClient", return_value=_mock_client(response)):
        await guard.load("https://www.jibunbank.co.jp/faq/")
    assert guard.is_allowed("https://www.jibunbank.co.jp/faq/") is True


async def test_disallow_rule() -> None:
    response = httpx.Response(200, text=ROBOTS_BODY)
    guard = RobotsTxtGuard()
    with patch("src.crawler.robots.httpx.AsyncClient", return_value=_mock_client(response)):
        await guard.load("https://www.jibunbank.co.jp/")
    assert guard.is_allowed("https://www.jibunbank.co.jp/private/secret") is False


async def test_not_loaded_denies() -> None:
    guard = RobotsTxtGuard()
    # No load() called -> fail-safe deny.
    assert guard.is_allowed("https://www.jibunbank.co.jp/faq/") is False


async def test_404_allows_all() -> None:
    response = httpx.Response(404, text="not found")
    guard = RobotsTxtGuard()
    with patch("src.crawler.robots.httpx.AsyncClient", return_value=_mock_client(response)):
        await guard.load("https://www.jibunbank.co.jp/")
    assert guard.is_allowed("https://www.jibunbank.co.jp/anything") is True


async def test_fetch_failure_raises() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    guard = RobotsTxtGuard()
    with (
        patch("src.crawler.robots.httpx.AsyncClient", return_value=ctx),
        pytest.raises(FetchTimeoutError),
    ):
        await guard.load("https://www.jibunbank.co.jp/")


async def test_5xx_raises() -> None:
    response = httpx.Response(503, text="unavailable")
    guard = RobotsTxtGuard()
    with (
        patch("src.crawler.robots.httpx.AsyncClient", return_value=_mock_client(response)),
        pytest.raises(FetchTimeoutError),
    ):
        await guard.load("https://www.jibunbank.co.jp/")

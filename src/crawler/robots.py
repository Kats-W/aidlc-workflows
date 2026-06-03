"""robots.txt compliance guard for the au Jibun Bank knowledge crawler.

The :class:`RobotsTxtGuard` fetches and parses the ``robots.txt`` of a target
site once and then answers ``is_allowed`` queries for the configured
User-Agent. A network / parse failure raises :class:`FetchTimeoutError` so the
caller can apply its retry / back-off policy purely from the exception type.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from aws_lambda_powertools import Logger

from src.common.errors import FetchTimeoutError

logger = Logger()

#: Full User-Agent string sent in the HTTP ``User-Agent`` header.
USER_AGENT: str = "AuJibunBankBot/1.0"
#: Bare product token used for robots.txt group matching (no version). CPython's
#: ``RobotFileParser`` matches by product token, so the version suffix must be
#: stripped to match a ``User-agent: AuJibunBankBot`` group correctly.
ROBOTS_AGENT: str = "AuJibunBankBot"

#: Timeout (seconds) for the robots.txt fetch.
_ROBOTS_TIMEOUT_SECONDS: float = 10.0


class RobotsTxtGuard:
    """Loads and evaluates ``robots.txt`` rules for a single host.

    Usage::

        guard = RobotsTxtGuard()
        await guard.load("https://www.jibunbank.co.jp/")
        if guard.is_allowed("https://www.jibunbank.co.jp/faq/"):
            ...
    """

    def __init__(self, user_agent: str = USER_AGENT, robots_agent: str = ROBOTS_AGENT) -> None:
        self._user_agent = user_agent
        # Product token (no version) used for robots group matching.
        self._robots_agent = robots_agent
        self._parser = RobotFileParser()
        self._loaded = False

    async def load(self, base_url: str) -> None:
        """Fetch and parse ``{scheme}://{host}/robots.txt`` for ``base_url``.

        Args:
            base_url: Any URL on the target host.

        Raises:
            FetchTimeoutError: If the robots.txt cannot be fetched.
        """
        parsed = urlparse(base_url)
        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        try:
            async with httpx.AsyncClient(
                timeout=_ROBOTS_TIMEOUT_SECONDS,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            ) as client:
                response = await client.get(robots_url)
        except httpx.HTTPError as exc:  # connection / timeout / protocol errors
            logger.warning("robots.txt fetch failed", extra={"url": robots_url, "error": str(exc)})
            raise FetchTimeoutError(f"failed to fetch robots.txt: {robots_url}") from exc

        if response.status_code >= 400:
            # 4xx/5xx: per RFC 9309, treat 4xx (except 429) as "allow all", but a
            # 5xx should block. We conservatively allow on 404 (no robots.txt).
            if response.status_code == 404:
                self._parser.parse([])
                self._loaded = True
                logger.info("no robots.txt found, allowing all", extra={"url": robots_url})
                return
            raise FetchTimeoutError(
                f"robots.txt returned status {response.status_code}: {robots_url}"
            )

        self._parser.parse(response.text.splitlines())
        self._loaded = True
        logger.info("robots.txt loaded", extra={"url": robots_url})

    def is_allowed(self, url: str) -> bool:
        """Return whether ``url`` may be crawled by the configured User-Agent.

        If :meth:`load` has not been called yet, crawling is denied (fail-safe).
        """
        if not self._loaded:
            return False
        return self._parser.can_fetch(self._robots_agent, url)

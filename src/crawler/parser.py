"""HTML content extraction and chunking for the knowledge crawler.

:class:`ContentParser` turns a raw HTML page into a list of
:class:`ContentChunk` records: the visible body text is extracted with
BeautifulSoup, normalised, split into overlapping fixed-size chunks, and each
chunk is fingerprinted with a SHA-256 hash for diff detection.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from aws_lambda_powertools import Logger
from bs4 import BeautifulSoup

from src.common.errors import ParseError

logger = Logger()

#: Default maximum characters per chunk.
DEFAULT_MAX_CHARS: int = 1500
#: Default overlap (characters) carried between consecutive chunks.
DEFAULT_OVERLAP_CHARS: int = 200
#: HTML elements whose contents are never part of the body text.
_NON_CONTENT_TAGS: tuple[str, ...] = ("script", "style", "noscript", "nav", "footer", "header")


@dataclass(frozen=True, slots=True)
class ContentChunk:
    """A single extracted, hashed content chunk ready for embedding.

    Attributes:
        chunk_id: Stable identifier (``{source_url_hash}#{index}``).
        source_url: The page the chunk was extracted from.
        index: Zero-based position of this chunk within the page.
        text: The chunk's plain text.
        content_hash: SHA-256 hex digest of ``text`` (diff fingerprint).
    """

    chunk_id: str
    source_url: str
    index: int
    text: str
    content_hash: str = field(default="")
    #: The page's <title> (for human-readable source attribution in the UI).
    title: str = field(default="")


class ContentParser:
    """Extracts and chunks visible text from crawled HTML pages."""

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    ) -> None:
        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def parse(self, raw_html: bytes | str, source_url: str) -> list[ContentChunk]:
        """Parse ``raw_html`` into a list of hashed :class:`ContentChunk`.

        ``raw_html`` may be raw bytes; BeautifulSoup then detects the page
        encoding from its ``<meta charset>`` (some legacy sections are Shift_JIS
        with no HTTP charset header, which httpx would otherwise mis-decode).

        Raises:
            ParseError: If the HTML yields no extractable body text.
        """
        text = self._extract_text(raw_html)
        if not text:
            raise ParseError(f"no extractable text for {source_url}")
        title = self._extract_title(raw_html)

        url_hash = self.compute_hash(source_url)
        chunks: list[ContentChunk] = []
        for index, piece in enumerate(self._chunk(text, self._max_chars)):
            chunks.append(
                ContentChunk(
                    chunk_id=f"{url_hash}#{index}",
                    source_url=source_url,
                    index=index,
                    text=piece,
                    content_hash=self.compute_hash(piece),
                    title=title,
                )
            )
        logger.debug("parsed page", extra={"source_url": source_url, "chunks": len(chunks)})
        return chunks

    def compute_hash(self, text: str) -> str:
        """Return the SHA-256 hex digest of ``text`` (UTF-8 encoded)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def extract_links(self, html: bytes | str, base_url: str) -> list[str]:
        """Return absolute HTTP/HTTPS links found in ``html``, resolved against ``base_url``.

        Fragments are stripped; query strings are preserved.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return []
        links: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = str(tag["href"]).strip()
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme in ("http", "https"):
                links.append(parsed._replace(fragment="").geturl())
        return links

    def _extract_title(self, html: bytes | str) -> str:
        """Return the page's <title> text (collapsed whitespace), or ""."""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:  # title is optional metadata; never fail on it
            return ""
        if soup.title and soup.title.string:
            return re.sub(r"[\s　]+", " ", soup.title.string).strip()
        return ""

    def _extract_text(self, html: bytes | str) -> str:
        """Extract and normalise the visible body text from ``html``."""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # malformed input the parser cannot recover from
            raise ParseError("failed to parse HTML") from exc

        for tag in soup(list(_NON_CONTENT_TAGS)):
            tag.decompose()

        raw = soup.get_text(separator=" ")
        # Collapse all runs of whitespace (incl. full-width spaces) to one space.
        normalised = re.sub(r"[\s　]+", " ", raw).strip()
        return normalised

    def _chunk(self, text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
        """Split ``text`` into overlapping chunks of at most ``max_chars``."""
        if len(text) <= max_chars:
            return [text]

        step = max_chars - self._overlap_chars
        pieces: list[str] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(start + max_chars, length)
            pieces.append(text[start:end])
            if end >= length:
                break
            start += step
        return pieces

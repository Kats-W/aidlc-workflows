"""Unit tests for :mod:`src.crawler.parser`."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.common.errors import ParseError
from src.crawler.parser import ContentChunk, ContentParser

SAMPLE_HTML = """
<html><head><title>FAQ</title><style>.x{}</style></head>
<body>
  <nav>menu</nav>
  <h1>口座開設について</h1>
  <p>口座開設はオンラインで完結します。</p>
  <script>console.log('x')</script>
  <footer>copyright</footer>
</body></html>
"""


def test_parse_produces_chunks() -> None:
    parser = ContentParser()
    chunks = parser.parse(SAMPLE_HTML, "https://www.jibunbank.co.jp/faq/")
    assert chunks
    assert all(isinstance(c, ContentChunk) for c in chunks)
    text = chunks[0].text
    # Body content present; nav/script/style/footer stripped.
    assert "口座開設はオンラインで完結します" in text
    assert "console.log" not in text
    assert "menu" not in text
    assert "copyright" not in text


def test_chunk_ids_and_hashes() -> None:
    parser = ContentParser()
    chunks = parser.parse(SAMPLE_HTML, "https://www.jibunbank.co.jp/faq/")
    assert chunks[0].index == 0
    assert chunks[0].chunk_id.endswith("#0")
    assert chunks[0].content_hash == parser.compute_hash(chunks[0].text)


def test_compute_hash_matches_sha256() -> None:
    parser = ContentParser()
    text = "テスト本文"
    assert parser.compute_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_empty_html_raises_parse_error() -> None:
    parser = ContentParser()
    with pytest.raises(ParseError):
        parser.parse("<html><body>   </body></html>", "https://x/")


def test_long_text_chunked_with_overlap() -> None:
    parser = ContentParser(max_chars=100, overlap_chars=20)
    long = "あ" * 500
    pieces = parser._chunk(long, 100)
    assert len(pieces) > 1
    assert all(len(p) <= 100 for p in pieces)
    # Consecutive chunks overlap (end of one == start of next for 20 chars).
    assert pieces[0][-20:] == pieces[1][:20]


@given(st.text(min_size=1, max_size=2000))
def test_compute_hash_deterministic(text: str) -> None:
    parser = ContentParser()
    assert parser.compute_hash(text) == parser.compute_hash(text)
    assert len(parser.compute_hash(text)) == 64


LINK_HTML = """
<html><body>
  <a href="/products/">製品</a>
  <a href="https://www.jibunbank.co.jp/faq/">FAQ</a>
  <a href="https://external.example.com/other">外部</a>
  <a href="https://www.jibunbank.co.jp/page#section">アンカー付き</a>
  <a href="mailto:info@example.com">メール</a>
</body></html>
"""

BASE = "https://www.jibunbank.co.jp/"


def test_extract_links_returns_absolute_same_host() -> None:
    parser = ContentParser()
    links = parser.extract_links(LINK_HTML, BASE)
    assert "https://www.jibunbank.co.jp/products/" in links
    assert "https://www.jibunbank.co.jp/faq/" in links


def test_extract_links_includes_external() -> None:
    # extract_links returns all HTTP/HTTPS links; host filtering is the caller's job.
    parser = ContentParser()
    links = parser.extract_links(LINK_HTML, BASE)
    assert any("external.example.com" in link for link in links)


def test_extract_links_strips_fragment() -> None:
    parser = ContentParser()
    links = parser.extract_links(LINK_HTML, BASE)
    assert all("#" not in link for link in links)


def test_extract_links_excludes_mailto() -> None:
    parser = ContentParser()
    links = parser.extract_links(LINK_HTML, BASE)
    assert not any("mailto" in link for link in links)


def test_extract_links_empty_on_bad_html() -> None:
    parser = ContentParser()
    # Should not raise even on garbage input.
    links = parser.extract_links("", BASE)
    assert isinstance(links, list)

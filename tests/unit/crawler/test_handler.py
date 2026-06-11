"""Unit tests for :mod:`src.crawler.handler` URL normalization and BFS state."""

from __future__ import annotations

from collections import deque

from src.crawler.handler import _initial_state, _normalize_url


def test_normalize_strips_fragment() -> None:
    assert _normalize_url("https://www.jibunbank.co.jp/path#section") == (
        "https://www.jibunbank.co.jp/path"
    )


def test_normalize_defaults_empty_path_to_root() -> None:
    assert _normalize_url("https://www.jibunbank.co.jp") == "https://www.jibunbank.co.jp/"


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

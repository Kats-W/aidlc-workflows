"""Regex-based PII detection for Japanese text.

Amazon Comprehend's ``DetectPiiEntities`` API only supports ``LanguageCode``
values ``en`` and ``es`` — calling it with ``ja`` raises ``ValidationException``
(see https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html).
Japanese customer utterances are therefore scanned with a small set of regular
expressions covering the structured PII most likely to appear in a banking
conversation: phone numbers, email addresses, postal codes, My Number (個人番号),
and credit/debit card numbers.

This is necessarily a best-effort, pattern-based approach — it will not catch
free-form PII such as names or addresses written in natural language. It exists
to keep the U-03 "mask before it leaves the pipeline" rule in effect for
Japanese input, which Comprehend cannot do directly.
"""

from __future__ import annotations

import re
from typing import Any

#: Replacement token written in place of every detected PII span.
MASK_TOKEN: str = "[MASKED]"

#: ``(entity_type, pattern)`` pairs, most specific first. All patterns use
#: ``\d`` boundary lookarounds so they only match digit runs of the exact
#: expected length (avoiding partial matches inside longer numbers).
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        "CREDIT_DEBIT_NUMBER",
        re.compile(r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)"),
    ),
    ("JP_MY_NUMBER", re.compile(r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)")),
    (
        "PHONE",
        re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{4}(?!\d)|(?<!\d)0\d{9,10}(?!\d)"),
    ),
    ("JP_POSTAL_CODE", re.compile(r"(?<![\d-])〒?\d{3}-\d{4}(?![\d-])")),
]


def detect_japanese_pii(text: str) -> list[tuple[int, int, str]]:
    """Return non-overlapping ``(start, end, entity_type)`` spans found in ``text``.

    Spans are character offsets into ``text``. When multiple patterns match
    overlapping regions, the longest match starting earliest wins.
    """
    candidates: list[tuple[int, int, str]] = []
    for entity_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            candidates.append((m.start(), m.end(), entity_type))

    # Longest match wins ties at the same start position.
    candidates.sort(key=lambda c: (c[0], c[0] - c[1]))

    selected: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, entity_type in candidates:
        if start >= last_end:
            selected.append((start, end, entity_type))
            last_end = end
    return selected


def mask_japanese_pii(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Mask structured PII in Japanese ``text`` using :data:`MASK_TOKEN`.

    Returns:
        ``(masked_text, entities)`` where ``entities`` mirrors the shape of
        Comprehend ``DetectPiiEntities`` results (``BeginOffset``,
        ``EndOffset``, ``Type``, ``Score``), except offsets are character-based
        rather than byte-based.
    """
    spans = detect_japanese_pii(text)
    entities = [
        {"BeginOffset": start, "EndOffset": end, "Type": entity_type, "Score": 1.0}
        for start, end, entity_type in spans
    ]

    masked = text
    for start, end, _entity_type in sorted(spans, reverse=True):
        masked = masked[:start] + MASK_TOKEN + masked[end:]
    return masked, entities

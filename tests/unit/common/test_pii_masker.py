"""Unit tests for :mod:`src.common.pii_masker` (Comprehend mocked)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from hypothesis import given
from hypothesis import strategies as st

from src.common.errors import ComprehendError
from src.common.pii_masker import MASK_TOKEN, PiiMasker


def _entity(begin: int, end: int, etype: str = "NAME") -> dict[str, Any]:
    return {"BeginOffset": begin, "EndOffset": end, "Type": etype, "Score": 0.99}


def _fake_comprehend(entities: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.detect_pii_entities.return_value = {"Entities": entities}
    return client


async def test_mask_replaces_detected_pii() -> None:
    text = "my name is John Smith"
    begin = len("my name is ")
    end = begin + len("John Smith")
    masker = PiiMasker(client=_fake_comprehend([_entity(begin, end)]))

    masked, entities = await masker.mask(text, lang="en")

    assert "John Smith" not in masked
    assert MASK_TOKEN in masked
    assert masker.contains_pii(entities) is True


async def test_mask_passthrough_when_no_pii() -> None:
    masker = PiiMasker(client=_fake_comprehend([]))
    masked, entities = await masker.mask("残高を教えてください")
    assert masked == "残高を教えてください"
    assert entities == []
    assert masker.contains_pii(entities) is False


async def test_mask_japanese_does_not_call_comprehend() -> None:
    """Comprehend's DetectPiiEntities does not support ``ja`` (ValidationException)."""
    client = _fake_comprehend([])
    masker = PiiMasker(client=client)
    masked, entities = await masker.mask("090-1234-5678に電話してください")
    client.detect_pii_entities.assert_not_called()
    assert "090-1234-5678" not in masked
    assert MASK_TOKEN in masked
    assert masker.contains_pii(entities) is True


async def test_mask_empty_input_short_circuits() -> None:
    client = _fake_comprehend([])
    masker = PiiMasker(client=client)
    _masked, entities = await masker.mask("   ")
    assert entities == []
    client.detect_pii_entities.assert_not_called()


async def test_mask_wraps_client_error() -> None:
    client = MagicMock()
    client.detect_pii_entities.side_effect = ClientError(
        {"Error": {"Code": "InternalServerException"}}, "DetectPiiEntities"
    )
    masker = PiiMasker(client=client)
    with pytest.raises(ComprehendError):
        await masker.mask("some text", lang="en")


@given(
    prefix=st.text(alphabet="abcdefg ", max_size=20),
    pii=st.text(alphabet="XYZ0123456789", min_size=1, max_size=12),
    suffix=st.text(alphabet="hijklmn ", max_size=20),
)
async def test_masked_text_never_contains_pii(
    prefix: str, pii: str, suffix: str
) -> None:
    # Ensure the PII span is unique so containment is a meaningful property.
    text = prefix + pii + suffix
    begin = len(prefix.encode())
    end = begin + len(pii.encode())
    masker = PiiMasker(client=_fake_comprehend([_entity(begin, end)]))

    masked, _ = await masker.mask(text, lang="en")

    if pii not in prefix and pii not in suffix:
        assert pii not in masked
    assert MASK_TOKEN in masked

"""Unit tests for :mod:`src.common.ja_pii_patterns`."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from src.common.ja_pii_patterns import MASK_TOKEN, mask_japanese_pii


def test_mobile_phone_number_masked() -> None:
    masked, entities = mask_japanese_pii("090-1234-5678に電話してください")
    assert "090-1234-5678" not in masked
    assert MASK_TOKEN in masked
    assert entities[0]["Type"] == "PHONE"


def test_landline_phone_number_masked() -> None:
    masked, entities = mask_japanese_pii("固定電話は03-1234-5678です")
    assert "03-1234-5678" not in masked
    assert entities[0]["Type"] == "PHONE"


def test_email_masked() -> None:
    masked, entities = mask_japanese_pii("連絡先はtaro.yamada@example.comです")
    assert "taro.yamada@example.com" not in masked
    assert entities[0]["Type"] == "EMAIL"


def test_postal_code_masked() -> None:
    masked, entities = mask_japanese_pii("住所は〒123-4567です")
    assert "123-4567" not in masked
    assert entities[0]["Type"] == "JP_POSTAL_CODE"


def test_credit_card_number_masked() -> None:
    masked, entities = mask_japanese_pii("カード番号は1234-5678-9012-3456です")
    assert "1234-5678-9012-3456" not in masked
    assert entities[0]["Type"] == "CREDIT_DEBIT_NUMBER"


def test_my_number_masked() -> None:
    masked, entities = mask_japanese_pii("マイナンバーは1234-5678-9012です")
    assert "1234-5678-9012" not in masked
    assert entities[0]["Type"] == "JP_MY_NUMBER"


def test_no_pii_passthrough() -> None:
    text = "auじぶん銀行の口座開設に必要なものを教えてください"
    masked, entities = mask_japanese_pii(text)
    assert masked == text
    assert entities == []


@given(
    area=st.integers(min_value=70, max_value=90).map(lambda d: f"0{d}"),
    mid=st.integers(min_value=0, max_value=9999).map(lambda n: f"{n:04d}"),
    last=st.integers(min_value=0, max_value=9999).map(lambda n: f"{n:04d}"),
)
def test_mobile_numbers_are_always_masked(area: str, mid: str, last: str) -> None:
    phone = f"{area}-{mid}-{last}"
    text = f"電話番号は{phone}までお願いします"

    masked, entities = mask_japanese_pii(text)

    assert phone not in masked
    assert MASK_TOKEN in masked
    assert any(e["Type"] == "PHONE" for e in entities)

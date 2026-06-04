"""Property-based + unit tests for :mod:`src.profile.hasher`."""

from __future__ import annotations

import hashlib
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.common.errors import ValidationError
from src.profile.hasher import IdentityHasher

#: 64-character lowercase hex digest.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# A strategy for "non-empty after strip" strings: contains at least one
# non-whitespace character so it is a valid au ID.
_valid_au_ids = st.text(min_size=1).filter(lambda s: s.strip() != "")


@given(au_id=_valid_au_ids)
def test_output_is_64_char_hex(au_id: str) -> None:
    digest = IdentityHasher.hash_au_id(au_id)
    assert _HEX64.match(digest)


@given(au_id=_valid_au_ids)
def test_deterministic_same_input_same_output(au_id: str) -> None:
    assert IdentityHasher.hash_au_id(au_id) == IdentityHasher.hash_au_id(au_id)


@given(data=st.data())
def test_distinct_inputs_distinct_hashes(data: st.DataObject) -> None:
    a = data.draw(_valid_au_ids)
    b = data.draw(_valid_au_ids.filter(lambda s: s != a))
    assert IdentityHasher.hash_au_id(a) != IdentityHasher.hash_au_id(b)


@given(au_id=_valid_au_ids)
def test_matches_reference_sha256(au_id: str) -> None:
    expected = hashlib.sha256(au_id.encode("utf-8")).hexdigest()
    assert IdentityHasher.hash_au_id(au_id) == expected


@pytest.mark.parametrize("bad", ["", " ", "\t", "\n", "   \t  "])
def test_empty_or_whitespace_raises(bad: str) -> None:
    with pytest.raises(ValidationError):
        IdentityHasher.hash_au_id(bad)


def test_validation_error_does_not_leak_plaintext() -> None:
    # The error message must not echo the input back.
    try:
        IdentityHasher.hash_au_id("")
    except ValidationError as exc:
        assert "must not be empty" in exc.message

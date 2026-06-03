"""Property-based tests for the application error hierarchy (hypothesis)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from src.common import errors
from src.common.errors import AppError

# Every concrete AppError subclass exported by the module (excluding AppError).
ERROR_CLASSES: list[type[AppError]] = [
    getattr(errors, name)
    for name in errors.__all__
    if isinstance(getattr(errors, name), type)
    and issubclass(getattr(errors, name), AppError)
]

ERROR_CLASS_STRATEGY = st.sampled_from(ERROR_CLASSES)


@given(cls=ERROR_CLASS_STRATEGY)
def test_every_class_is_exception_subclass(cls: type[AppError]) -> None:
    """Property: any AppError class is always an Exception subclass."""
    assert issubclass(cls, Exception)
    assert issubclass(cls, AppError)


@given(cls=ERROR_CLASS_STRATEGY, message=st.text())
def test_code_and_message_are_strings(cls: type[AppError], message: str) -> None:
    """Property: code and message are always str for any message input."""
    err = cls(message)
    assert isinstance(err.code, str)
    assert isinstance(err.message, str)
    assert err.message == message


@given(cls=ERROR_CLASS_STRATEGY, message=st.text(), code=st.text(min_size=1))
def test_custom_code_is_preserved(cls: type[AppError], message: str, code: str) -> None:
    """Property: a constructor-supplied code is always preserved as a str."""
    err = cls(message, code=code)
    assert err.code == code
    assert isinstance(err.code, str)


@given(cls=ERROR_CLASS_STRATEGY, message=st.text())
def test_hierarchy_is_idempotent(cls: type[AppError], message: str) -> None:
    """Property: raising the same class twice yields equivalent class/type behaviour.

    The exception type, code default and retryable flag must be stable across
    repeated instantiation and raising of the same class.
    """
    first = cls(message)
    second = cls(message)

    assert type(first) is type(second)
    assert first.code == second.code
    assert first.retryable == second.retryable
    assert isinstance(first, AppError) and isinstance(second, AppError)

    for instance in (first, second):
        try:
            raise instance
        except AppError as caught:
            assert type(caught) is cls
            assert caught.code == cls.code if cls is not AppError or caught.code else True
            assert caught.message == message


@given(cls=ERROR_CLASS_STRATEGY)
def test_retryable_is_bool(cls: type[AppError]) -> None:
    """Property: the retryable flag is always a bool."""
    assert isinstance(cls.retryable, bool)
    assert isinstance(cls("x").retryable, bool)
